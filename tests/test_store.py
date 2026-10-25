"""Managed-store contracts, including durable file/metadata interruption points."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from media_kit.api import MediaStore
from media_kit.config import StoreConfig, URLMapping
from media_kit.errors import (
    AssetConflictError,
    AssetNotFoundError,
    ConfigurationError,
    IdempotencyConflictError,
    InputValidationError,
    RevisionConflictError,
    StorageCompatibilityError,
    StorageError,
)
from media_kit.models import Profile


@pytest.fixture
def config(tmp_path: Path) -> StoreConfig:
    return StoreConfig(tmp_path / "media", tmp_path / "state", profiles=(Profile("small", 32),))


@pytest.fixture
def store(config: StoreConfig):
    value = MediaStore.init(config)
    yield value
    value.close()


@pytest.fixture
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "picture.png"
    Image.new("RGBA", (80, 40), (20, 100, 200, 128)).save(path)
    return path


def _picture(path: Path, color: str) -> Path:
    Image.new("RGB", (50, 30), color).save(path)
    return path


def _snapshot(config: StoreConfig) -> dict[str, bytes]:
    return {
        f"{label}/{path.relative_to(base).as_posix()}": path.read_bytes()
        for label, base in (("root", config.root), ("state", config.state_dir))
        if base.exists()
        for path in base.rglob("*")
        if path.is_file()
    }


def _rows(store: MediaStore, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(store.database_path) as connection:
        return connection.execute(sql).fetchall()


def _crash(point: str):
    def fault(actual: str) -> None:
        if actual == point:
            raise SystemExit(f"simulated interruption: {point}")

    return fault


def test_init_open_and_repeat_init_keep_one_identity(config: StoreConfig) -> None:
    with MediaStore.init(config) as first:
        identity = first.store_id
        marker = json.loads((config.root / ".media-kit.json").read_text())
        assert marker["store_id"] == identity
        assert config.state_dir not in config.root.parents
    with MediaStore.open(config) as opened, MediaStore.init(config) as repeated:
        assert opened.store_id == repeated.store_id == identity
        assert opened.list().total == 0
        assert opened.worker(once=True) == 0


@pytest.mark.parametrize("occupied", ["root", "state_dir"])
def test_init_rejects_unknown_nonempty_directory_without_changes(
    config: StoreConfig,
    occupied: str,
) -> None:
    directory = getattr(config, occupied)
    directory.mkdir()
    unknown = directory / "unmanaged.png"
    unknown.write_bytes(b"keep this unmanaged file")
    before = _snapshot(config)
    with pytest.raises(StorageCompatibilityError):
        MediaStore.init(config)
    assert _snapshot(config) == before


def test_open_rejects_layout_drift_without_creating_alternate_directories(
    config: StoreConfig,
) -> None:
    MediaStore.init(config).close()
    before = _snapshot(config)
    with pytest.raises(StorageCompatibilityError):
        MediaStore.open(replace(config, original_dir="different"))
    assert _snapshot(config) == before
    assert not (config.root / "different").exists()


@pytest.mark.parametrize(
    "damage", ["marker_identity", "database_identity", "schema", "layout_version"]
)
def test_incompatible_identity_and_versions_are_rejected_without_writes(
    config: StoreConfig,
    damage: str,
) -> None:
    MediaStore.init(config).close()
    if damage == "marker_identity":
        path = config.state_dir / "store.json"
        value = json.loads(path.read_text())
        value["store_id"] = str(uuid4())
        path.write_text(json.dumps(value))
    elif damage == "database_identity":
        with sqlite3.connect(config.state_dir / "media.sqlite3") as connection:
            connection.execute("UPDATE metadata SET value=? WHERE key='store_id'", (str(uuid4()),))
    elif damage == "schema":
        with sqlite3.connect(config.state_dir / "media.sqlite3") as connection:
            connection.execute("PRAGMA user_version=999")
    else:
        for path in (config.root / ".media-kit.json", config.state_dir / "store.json"):
            value = json.loads(path.read_text())
            value["layout_version"] = 999
            path.write_text(json.dumps(value))
    before = _snapshot(config)
    for opener in (MediaStore.open, MediaStore.readonly):
        with pytest.raises(StorageCompatibilityError):
            opener(config)
        assert _snapshot(config) == before


def test_readonly_does_not_initialize_recover_or_accept_mutations(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    store._fault = _crash("original_installed")
    with pytest.raises(SystemExit):
        store.save(picture)
    store._fault = lambda point: None
    before = _snapshot(config)
    with MediaStore.readonly(config) as readonly:
        assert readonly.list().total == 0
        assert readonly.check().unknown_files
        with pytest.raises(StorageError):
            readonly.save(picture)
    assert _snapshot(config) == before
    with MediaStore.open(config) as reopened:
        assert reopened.list().total == 0
        assert not reopened.check().unknown_files
    assert not list((config.root / config.original_dir).rglob("*.png"))


def test_readonly_missing_store_has_no_side_effects(config: StoreConfig) -> None:
    with pytest.raises(StorageCompatibilityError):
        MediaStore.readonly(config)
    assert not config.root.exists()
    assert not config.state_dir.exists()


def test_save_copies_file_bytes_and_streams_and_persists_pending_tasks(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    source = picture.read_bytes()
    first = store.save(picture, key="articles/42/cover.png")
    second_input = io.BytesIO(source)
    second = store.save(second_input)
    assert not second_input.closed
    assert first.asset_id != second.asset_id
    assert first.key == "articles/42/cover.png" and second.key
    assert first.revision == second.revision == 1
    assert first.status == "active" and first.processing_status == "pending"
    assert first.original_path and first.asset_id in first.original_path
    assert "/1/" in first.original_path and "articles/42" not in first.original_path
    assert store.local_path(first.original_path).read_bytes() == source
    assert store.local_path(second.original_path).read_bytes() == source
    assert picture.read_bytes() == source
    with MediaStore.open(config) as reopened:
        assert reopened.get(first.asset_id).original_path == first.original_path
        assert [task.status for task in reopened.tasks(first.asset_id)] == ["pending"]


def test_interrupted_binary_stream_never_publishes_partial_original(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    class InterruptedInput:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return picture.read_bytes()[:30]
            raise OSError("simulated input stream disconnection")

    with pytest.raises(StorageError):
        store.save(InterruptedInput())
    assert store.list().total == 0 and store.tasks() == ()
    assert not list((config.root / config.original_dir).rglob("*"))
    assert not list((config.root / ".media-kit-private/staging").iterdir())
    assert picture.is_file()


def test_host_database_rollback_does_not_rollback_committed_media(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    with sqlite3.connect(":memory:") as host:
        host.execute("CREATE TABLE articles (asset_id TEXT UNIQUE)")
        asset = store.save(picture)
        with pytest.raises(sqlite3.IntegrityError), host:
            host.execute("INSERT INTO articles VALUES (?)", (asset.asset_id,))
            host.execute("INSERT INTO articles VALUES (?)", (asset.asset_id,))
        assert host.execute("SELECT count(*) FROM articles").fetchone()[0] == 0
    with MediaStore.open(config) as reopened:
        assert reopened.get(asset.asset_id).status == "active"
        assert reopened.local_path(asset.original_path).read_bytes() == picture.read_bytes()


@pytest.mark.parametrize(
    "key", ["../outside.png", "/absolute.png", "a//b", "a/./b", "a\\b", "a\x00b", ""]
)
def test_invalid_keys_do_not_register_assets_or_tasks(
    store: MediaStore,
    picture: Path,
    key: str,
) -> None:
    with pytest.raises(StorageError):
        store.save(picture, key=key)
    assert store.list().total == 0
    assert store.tasks() == ()


def test_key_conflict_does_not_overwrite_original(store: MediaStore, picture: Path) -> None:
    original = store.save(picture, key="same/key")
    original_bytes = store.local_path(original.original_path).read_bytes()
    with pytest.raises(AssetConflictError):
        store.save(picture, key="same/key")
    assert store.list().total == 1
    assert (
        store.local_path(store.get(original.asset_id).original_path).read_bytes() == original_bytes
    )


def test_idempotency_replays_save_and_replace_before_revision_checks(
    store: MediaStore,
    picture: Path,
    tmp_path: Path,
) -> None:
    first = store.save(picture, key="cover", idempotency_key="create")
    replay = store.save(picture, key="cover", idempotency_key="create")
    assert replay.asset_id == first.asset_id and replay.operation_revision == 1
    assert store.list().total == 1
    other = _picture(tmp_path / "other.png", "red")
    with pytest.raises(IdempotencyConflictError):
        store.save(other, key="cover", idempotency_key="create")
    replacement = store.replace(
        first.asset_id, other, expected_revision=1, idempotency_key="replace"
    )
    replayed_replace = store.replace(
        first.asset_id, other, expected_revision=1, idempotency_key="replace"
    )
    assert replacement.revision == replayed_replace.revision == 2
    assert replayed_replace.operation_revision == 2
    assert len(_rows(store, "SELECT * FROM revisions")) == 2
    with pytest.raises(IdempotencyConflictError):
        store.replace(first.asset_id, picture, expected_revision=1, idempotency_key="replace")


def test_replace_keeps_immutable_history_and_rejects_bad_input(
    store: MediaStore,
    picture: Path,
    tmp_path: Path,
) -> None:
    first = store.save(picture)
    old_path = store.local_path(first.original_path)
    old_bytes = old_path.read_bytes()
    other = _picture(tmp_path / "replacement.png", "orange")
    second = store.replace(first.asset_id, other, expected_revision=1)
    assert second.asset_id == first.asset_id and second.key == first.key
    assert second.revision == 2 and second.original_path != first.original_path
    assert old_path.read_bytes() == old_bytes
    assert store.local_path(second.original_path).read_bytes() == other.read_bytes()
    with pytest.raises(RevisionConflictError):
        store.replace(first.asset_id, picture, expected_revision=1)
    damaged = tmp_path / "broken.png"
    damaged.write_bytes(b"not image content")
    with pytest.raises(InputValidationError):
        store.replace(first.asset_id, damaged, expected_revision=2)
    assert store.get(first.asset_id).original_path == second.original_path
    assert store.get(first.asset_id).revision == 2


def test_concurrent_replacements_accept_exactly_one_expected_revision(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
) -> None:
    asset = store.save(picture)
    candidates = [_picture(tmp_path / "red.png", "red"), _picture(tmp_path / "blue.png", "blue")]
    barrier = threading.Barrier(2)

    def replace_at_once(path: Path):
        with MediaStore.open(config) as other:
            barrier.wait(timeout=5)
            try:
                return other.replace(asset.asset_id, path, expected_revision=1)
            except RevisionConflictError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replace_at_once, candidates))
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, RevisionConflictError) for item in results) == 1
    assert store.get(asset.asset_id).revision == 2
    assert len(_rows(store, "SELECT * FROM revisions")) == 2


def test_batch_resolution_preserves_input_order_and_stable_pagination(
    store: MediaStore,
    picture: Path,
) -> None:
    assets = [store.save(picture, key=f"posts/{index}") for index in range(5)]
    ids = [assets[2].asset_id, str(uuid4()), assets[0].asset_id, assets[2].asset_id]
    values = store.resolve_many(ids)
    assert [None if value is None else value.asset_id for value in values] == [
        ids[0],
        None,
        ids[2],
        ids[3],
    ]
    pages = [store.list(prefix="posts/", page=page, page_size=2) for page in (1, 2, 3)]
    assert all(page.total == 5 for page in pages)
    ordered_ids = [item.asset_id for page in pages for item in page.items]
    assert ordered_ids == [asset.asset_id for asset in assets]
    assert len(set(ordered_ids)) == 5
    with pytest.raises(ConfigurationError):
        store.resolve(assets[0].asset_id, profiles=["unknown"])
    with pytest.raises(AssetNotFoundError):
        store.get(str(uuid4()))


def test_url_configuration_changes_coordinates_without_file_changes(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    asset = store.save(picture)
    assert asset.original_url is None
    before = _snapshot(config)
    changed = replace(config, urls=URLMapping("/public/originals/", "/public/variants/"))
    with MediaStore.open(changed) as mapped:
        value = mapped.resolve(asset.asset_id)
        assert value.original_path == asset.original_path
        assert value.original_url.startswith("/public/originals/")
        assert value.asset_id == asset.asset_id and value.revision == asset.revision
        with pytest.raises(StorageError):
            mapped.local_path(".media-kit-private/staging/private.png")
    assert _snapshot(config) == before


def test_check_reports_unknown_missing_and_changed_files_without_repairing(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    missing, changed = store.save(picture), store.save(picture)
    store.local_path(missing.original_path).unlink()
    store.local_path(changed.original_path).write_bytes(b"changed externally")
    unknown = config.root / config.original_dir / "unknown.png"
    unknown.write_bytes(b"unknown external file")
    before = _snapshot(config)
    with MediaStore.readonly(config) as readonly:
        report = readonly.check()
        assert report.missing_files == (missing.original_path,)
        assert report.content_mismatches == (changed.original_path,)
        assert report.unknown_files == (unknown.relative_to(config.root).as_posix(),)
        assert report.checked_assets == 2 and not report.ok
    assert _snapshot(config) == before
    assert store.list().total == 2


@pytest.mark.parametrize(
    "point", ["intent_committed", "original_installed", "before_asset_commit", "asset_committed"]
)
def test_save_crash_recovers_idempotently_at_file_and_commit_boundaries(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    point: str,
) -> None:
    store._fault = _crash(point)
    with pytest.raises(SystemExit):
        store.save(picture, key="crash/key", idempotency_key="lost-response")
    store._fault = lambda name: None
    with MediaStore.open(config) as reopened:
        committed = point == "asset_committed"
        assert reopened.list().total == int(committed)
        assert len(reopened.tasks()) == int(committed)
        assert reopened.check().ok
        assert reopened.recover() == {"recovered": 0}
        saved = reopened.save(picture, key="crash/key", idempotency_key="lost-response")
        assert saved.revision == 1
        assert reopened.list().total == 1
        assert reopened.local_path(saved.original_path).read_bytes() == picture.read_bytes()
        assert reopened.recover() == {"recovered": 0}


@pytest.mark.parametrize(
    "point", ["intent_committed", "original_installed", "before_asset_commit", "asset_committed"]
)
def test_replace_recovery_cannot_override_later_committed_revision(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
    point: str,
) -> None:
    first = store.save(picture)
    replacement = _picture(tmp_path / "replacement.png", "green")
    store._fault = _crash(point)
    with pytest.raises(SystemExit):
        store.replace(first.asset_id, replacement, expected_revision=1, idempotency_key="change")
    store._fault = lambda name: None
    with MediaStore.open(config) as reopened:
        current = reopened.get(first.asset_id)
        assert current.revision == (2 if point == "asset_committed" else 1)
        later_source = _picture(tmp_path / "later.png", "purple")
        later = reopened.replace(first.asset_id, later_source, expected_revision=current.revision)
        assert reopened.recover() == {"recovered": 0}
        assert reopened.get(first.asset_id).revision == later.revision
        assert reopened.local_path(later.original_path).read_bytes() == later_source.read_bytes()
        assert reopened.check().ok


def test_stale_replacement_intent_cannot_remove_a_later_committed_file(
    store: MediaStore,
    picture: Path,
    tmp_path: Path,
) -> None:
    """A later operation may own the exact immutable path an old intent named."""
    first = store.save(picture)
    replacement = _picture(tmp_path / "replacement.png", "green")
    store._fault = _crash("intent_committed")
    with pytest.raises(SystemExit):
        store.replace(first.asset_id, replacement, expected_revision=1)
    store._fault = lambda point: None
    # Continue in the surviving process before an explicit recovery. The old
    # intent installed nothing, so a later operation can commit revision 2.
    later = store.replace(first.asset_id, replacement, expected_revision=1)
    later_path = store.local_path(later.original_path)
    committed_bytes = later_path.read_bytes()
    store.recover()
    assert store.get(first.asset_id).revision == later.revision
    assert later_path.read_bytes() == committed_bytes
    assert store.check().ok
    assert store.recover() == {"recovered": 0}


@pytest.mark.parametrize(
    "point", ["delete_decided", "delete_originals_moved", "delete_variants_moved"]
)
def test_delete_crash_recovery_removes_all_history_and_is_repeatable(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
    point: str,
) -> None:
    asset = store.save(picture, key="reusable")
    store.worker(once=True)
    asset = store.replace(
        asset.asset_id, _picture(tmp_path / "new.png", "cyan"), expected_revision=1
    )
    store.worker(once=True)
    store._fault = _crash(point)
    with pytest.raises(SystemExit):
        store.delete(asset.asset_id, expected_revision=2, idempotency_key="delete")
    store._fault = lambda name: None
    assert store.resolve(asset.asset_id).original_path is None
    with MediaStore.open(config) as reopened:
        assert reopened.get(asset.asset_id).status == "deleted"
        assert not (config.root / config.original_dir / asset.asset_id).exists()
        assert not (config.root / config.variant_dir / asset.asset_id).exists()
        assert reopened.recover() == {"recovered": 0}
        assert (
            reopened.delete(asset.asset_id, expected_revision=2, idempotency_key="delete").status
            == "deleted"
        )
        fresh = reopened.save(picture, key="reusable")
        assert fresh.asset_id != asset.asset_id


def test_delete_partial_failure_stays_unavailable_and_cleanup_is_separate(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from media_kit import api

    asset = store.save(picture, key="blocked")
    store.worker(once=True)
    registered_variant = store.resolve(asset.asset_id).variants[0].path
    assert registered_variant is not None
    variant_path = store.local_path(registered_variant)
    unknown = variant_path.parent / "manual-note.txt"
    unknown.write_text("preserve unmanaged content")
    original_move = api.atomic_move

    def fail_variant_move(source: Path, target: Path) -> None:
        if source == variant_path:
            raise OSError("injected public-file move failure")
        original_move(source, target)

    monkeypatch.setattr(api, "atomic_move", fail_variant_move)
    result = store.delete(asset.asset_id, expected_revision=1)
    assert result.status == "deleting" and result.errors
    assert store.get(asset.asset_id).status == "deleting"
    unavailable = store.resolve_many([asset.asset_id])[0]
    assert unavailable.original_path is None
    assert all(variant.path is None for variant in unavailable.variants)
    assert variant_path.exists()
    with pytest.raises(AssetConflictError):
        store.save(picture, key="blocked")
    monkeypatch.setattr(api, "atomic_move", original_move)
    done = store.delete(asset.asset_id, expected_revision=1)
    assert done.status == "deleted" and done.pending_cleanup
    assert unknown.read_text() == "preserve unmanaged content"
    assert variant_path.parent.exists()
    assert any((config.root / ".media-kit-private/quarantine").iterdir())
    original_remove = api.remove_tree

    def fail_cleanup(path: Path) -> None:
        if "quarantine" in path.parts:
            raise OSError("injected quarantine cleanup failure")
        original_remove(path)

    monkeypatch.setattr(api, "remove_tree", fail_cleanup)
    assert store.cleanup()["failed"] == 1
    assert store.get(asset.asset_id).status == "deleted"
    assert _rows(store, "SELECT cleanup FROM operations WHERE kind='delete'") == [("failed",)]
    monkeypatch.setattr(api, "remove_tree", original_remove)
    assert store.cleanup() == {"complete": 1, "failed": 0}
    assert store.cleanup() == {"complete": 0, "failed": 0}
    assert not list((config.root / ".media-kit-private/quarantine").iterdir())
    assert unknown.read_text() == "preserve unmanaged content"


def test_cleanup_reclaims_only_registered_history_and_is_idempotent(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    first = store.local_path(asset.original_path)
    unknown_original = first.parent / "unmanaged.txt"
    unknown_original.write_text("keep")
    replacement = _picture(tmp_path / "replacement.png", "orange")
    current = store.replace(asset.asset_id, replacement, expected_revision=1)
    assert store.local_path(current.original_path).is_file()

    assert store.cleanup() == {"complete": 1, "failed": 0}
    assert not first.exists()
    assert unknown_original.read_text() == "keep"
    assert store.check().unknown_files == (unknown_original.relative_to(config.root).as_posix(),)
    assert store.cleanup() == {"complete": 0, "failed": 0}
    assert _rows(
        store,
        "SELECT count(*) FROM operations WHERE kind='reclaim' AND phase='prepared'",
    ) == [(0,)]
    deleted = store.delete(current.asset_id, expected_revision=current.revision)
    assert deleted.status == "deleted"
    assert unknown_original.read_text() == "keep"


def test_reclaim_recovery_resumes_registered_files_without_moving_unknown_content(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    old_original = store.local_path(asset.original_path)
    old_variant_ref = store.resolve(asset.asset_id).variants[0].path
    assert old_variant_ref is not None
    old_variant = store.local_path(old_variant_ref)
    unknown = old_variant.parent / "operator-note.txt"
    unknown.write_text("must remain")
    current = store.replace(
        asset.asset_id, _picture(tmp_path / "next.png", "navy"), expected_revision=1
    )

    store._fault = _crash("reclaim_moved")
    with pytest.raises(SystemExit):
        store.cleanup()
    store._fault = lambda point: None
    assert not old_original.exists()
    assert old_variant.exists()

    with MediaStore.open(config) as reopened:
        assert reopened.get(asset.asset_id).revision == current.revision
        assert not old_variant.exists()
        assert unknown.read_text() == "must remain"
        assert reopened.local_path(current.original_path).is_file()
        report = reopened.check()
        assert report.missing_files == ()
        assert unknown.relative_to(config.root).as_posix() in report.unknown_files
        assert reopened.recover() == {"recovered": 0}


def test_reclaim_failure_is_reported_and_not_marked_complete(
    store: MediaStore, picture: Path, tmp_path: Path
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    old_path = store.local_path(asset.original_path)
    current = store.replace(
        asset.asset_id, _picture(tmp_path / "latest.png", "teal"), expected_revision=1
    )
    old_path.unlink()

    result = store.cleanup()
    assert result["failed"] == 1
    assert store.get(asset.asset_id).revision == current.revision
    assert _rows(
        store,
        "SELECT phase FROM operations WHERE kind='reclaim'",
    ) == [("prepared",)]
    assert _rows(
        store,
        "SELECT reclaimed FROM revisions WHERE asset_id=(SELECT asset_id FROM assets WHERE revision=2) AND revision=1",
    ) == [(0,)]
    assert store.cleanup()["failed"] == 1
    assert _rows(store, "SELECT count(*) FROM operations WHERE kind='reclaim'") == [(1,)]
    assert store.recover() == {"recovered": 0, "failed": 1}


def test_delete_refuses_missing_registered_file_and_retries_after_repair(
    store: MediaStore, picture: Path
) -> None:
    asset = store.save(picture)
    original = store.local_path(asset.original_path)
    original_bytes = original.read_bytes()
    original.unlink()

    result = store.delete(asset.asset_id, expected_revision=1)
    assert result.status == "deleting"
    assert any("registered media file is missing" in error for error in result.errors)
    assert store.get(asset.asset_id).status == "deleting"

    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(original_bytes)
    retried = store.delete(asset.asset_id, expected_revision=1)
    assert retried.status == "deleted"
    assert not original.exists()


def test_delete_does_not_overwrite_conflicting_quarantine_file(
    store: MediaStore, config: StoreConfig, picture: Path
) -> None:
    asset = store.save(picture)
    store._fault = _crash("delete_decided")
    with pytest.raises(SystemExit):
        store.delete(asset.asset_id, expected_revision=1)
    store._fault = lambda point: None
    operation_id = _rows(store, "SELECT operation_id FROM operations WHERE kind='delete'")[0][0]
    operation = _rows(store, "SELECT detail FROM operations WHERE kind='delete'")[0][0]
    detail = json.loads(operation)
    source = store.local_path(detail["originals"][0])
    target = config.root / ".media-kit-private/quarantine" / str(operation_id) / "originals/0"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"preserve conflict")
    source_bytes = source.read_bytes()

    result = store.delete(asset.asset_id, expected_revision=1)
    assert result.status == "deleting"
    assert any("conflicting source and quarantine" in error for error in result.errors)
    assert source.read_bytes() == source_bytes
    assert target.read_bytes() == b"preserve conflict"


def test_save_replay_after_delete_does_not_revive_asset(store: MediaStore, picture: Path) -> None:
    asset = store.save(picture, key="once", idempotency_key="creation")
    store.delete(asset.asset_id, expected_revision=1)
    replayed = store.save(picture, key="once", idempotency_key="creation")
    assert replayed.asset_id == asset.asset_id and replayed.status == "deleted"
    assert replayed.original_path is None
    assert replayed.operation_revision == 1


def test_delete_idempotency_conflict_does_not_delete_another_asset(
    store: MediaStore,
    picture: Path,
) -> None:
    first, other = store.save(picture), store.save(picture)
    assert (
        store.delete(first.asset_id, expected_revision=1, idempotency_key="remove").status
        == "deleted"
    )
    assert (
        store.delete(first.asset_id, expected_revision=1, idempotency_key="remove").status
        == "deleted"
    )
    with pytest.raises(IdempotencyConflictError):
        store.delete(other.asset_id, expected_revision=1, idempotency_key="remove")
    assert store.get(other.asset_id).status == "active"
    assert store.local_path(other.original_path).is_file()
