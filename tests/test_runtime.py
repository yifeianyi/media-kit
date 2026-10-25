"""Persistent task, lease, publication, and bounded synchronous-wait contracts."""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from media_kit.api import MediaStore
from media_kit.config import StoreConfig
from media_kit.errors import ProcessingError, StorageError
from media_kit.models import Profile
from media_kit.tasks import runtime


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
    path = tmp_path / "source.png"
    Image.new("RGB", (120, 60), (20, 100, 200)).save(path)
    return path


def _expire(store: MediaStore, task_id: str) -> None:
    """Simulate elapsed wall time without delaying a test for the 300s lease."""
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE tasks SET lease_until=0 WHERE task_id=?", (task_id,))


def _variant_rows(store: MediaStore) -> list[tuple[object, ...]]:
    with sqlite3.connect(store.database_path) as connection:
        return connection.execute(
            "SELECT asset_id,revision,profile,fingerprint,path FROM variants"
        ).fetchall()


def _until(predicate, *, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("background task did not reach its expected state")
        time.sleep(0.01)


@contextmanager
def _blocked_worker(store: MediaStore, asset_id: str, monkeypatch: pytest.MonkeyPatch):
    """Pause with a complete private result, before the publication decision."""
    converted, release = threading.Event(), threading.Event()
    original = runtime.convert_thumbnail

    def block_after_conversion(*args, **kwargs):
        result = original(*args, **kwargs)
        converted.set()
        if not release.wait(5):
            raise RuntimeError("test did not release its blocked conversion")
        return result

    monkeypatch.setattr(runtime, "convert_thumbnail", block_after_conversion)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.run_once, store, asset_id)
        try:
            assert converted.wait(3), "worker did not reach private conversion completion"
            yield future
        finally:
            release.set()
            assert future.result(timeout=3) == 1


def test_claim_competition_has_one_owner_and_no_duplicate_attempt(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
) -> None:
    asset = store.save(picture)
    barrier = threading.Barrier(2)

    def compete():
        with MediaStore.open(config) as other:
            barrier.wait(timeout=3)
            return runtime.claim(other, asset.asset_id, now=1000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: compete(), range(2)))
    claimed = [row for row in claims if row is not None]
    assert len(claimed) == 1
    assert claimed[0]["claim_token"]
    assert claimed[0]["attempts"] == 1
    assert claimed[0]["lease_until"] == 1300
    assert store.tasks(asset.asset_id)[0].status == "running"


def test_expired_lease_reclaims_new_token_and_old_heartbeat_cannot_extend_it(
    store: MediaStore,
    picture: Path,
) -> None:
    assert runtime.LEASE_SECONDS == 300 and runtime.HEARTBEAT_SECONDS == 30
    asset = store.save(picture)
    first = runtime.claim(store, asset.asset_id, now=1000)
    assert runtime.claim(store, asset.asset_id, now=1299) is None
    assert runtime.heartbeat(store, first["task_id"], first["claim_token"], now=1020)
    assert store.tasks(asset.asset_id)[0].lease_until == 1320
    assert not runtime.heartbeat(store, first["task_id"], "not-the-owner", now=1021)
    assert runtime.claim(store, asset.asset_id, now=1319) is None
    second = runtime.claim(store, asset.asset_id, now=1320)
    assert second["task_id"] == first["task_id"]
    assert second["claim_token"] != first["claim_token"]
    assert second["attempts"] == 2
    assert not runtime.heartbeat(store, first["task_id"], first["claim_token"], now=1321)
    assert runtime.heartbeat(store, second["task_id"], second["claim_token"], now=1321)


def test_expired_owner_cannot_publish_over_a_new_claim(
    store: MediaStore,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = store.save(picture)
    with _blocked_worker(store, asset.asset_id, monkeypatch):
        old_task = store.tasks(asset.asset_id)[0]
        replacement = runtime.claim(store, asset.asset_id, now=time.time() + 301)
        assert replacement is not None and replacement["attempts"] == 2
    assert _variant_rows(store) == []
    assert store.resolve(asset.asset_id).variants[0].path is None
    assert store.tasks(asset.asset_id)[0].status == "running"
    _expire(store, old_task.task_id)
    assert runtime.run_once(store, asset.asset_id) == 1
    assert store.resolve(asset.asset_id).variants[0].status == "ready"


@pytest.mark.parametrize("change", ["replace", "delete", "rebuild"])
def test_worker_rejects_result_after_asset_or_current_recipe_changes(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    asset = store.save(picture)
    initial_fingerprint = asset.variants[0].recipe_fingerprint
    with _blocked_worker(store, asset.asset_id, monkeypatch):
        if change == "replace":
            new_input = tmp_path / "replacement.png"
            Image.new("RGB", (60, 100), "red").save(new_input)
            current = store.replace(asset.asset_id, new_input, expected_revision=1)
            assert current.revision == 2
        elif change == "delete":
            assert store.delete(asset.asset_id, expected_revision=1).status == "deleted"
        else:
            store.config = replace(config, profiles=(Profile("small", 32, quality=90),))
            current = store.rebuild(asset.asset_id)
            assert current.variants[0].recipe_fingerprint != initial_fingerprint
    assert _variant_rows(store) == []
    assert not list((config.root / config.variant_dir).rglob("*.webp"))
    old_task = store.tasks(asset.asset_id)[0]
    assert old_task.status in {"superseded", "cancelled"}
    assert old_task.error
    current = store.resolve(asset.asset_id)
    assert all(variant.path is None for variant in current.variants)
    if change == "delete":
        assert current.status == "deleted" and current.original_path is None
        assert runtime.run_once(store, asset.asset_id) == 0
    else:
        assert current.variants[0].status == "pending"
        assert runtime.run_once(store, asset.asset_id) == 1
        ready = store.resolve(asset.asset_id)
        assert ready.variants[0].status == "ready"
        assert f"/{ready.revision}/" in ready.variants[0].path


def test_equal_recipes_with_distinct_profile_names_get_distinct_results(
    config: StoreConfig,
    picture: Path,
) -> None:
    config = replace(config, profiles=(Profile("card", 32), Profile("avatar", 32)))
    with MediaStore.init(config) as store:
        asset = store.save(picture)
        assert len(store.tasks(asset.asset_id)) == 2
        assert len({task.recipe_fingerprint for task in store.tasks(asset.asset_id)}) == 1
        assert store.worker(once=True) == 2
        ready = store.resolve(asset.asset_id)
        assert {variant.profile for variant in ready.variants} == {"card", "avatar"}
        assert all(variant.status == "ready" for variant in ready.variants)
        assert len({variant.path for variant in ready.variants}) == 2
        assert store.worker(once=True) == 0


def test_repeated_identical_rebuild_reuses_task_and_valid_published_file(
    store: MediaStore,
    picture: Path,
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    task = store.tasks(asset.asset_id)[0]
    ready = store.resolve(asset.asset_id).variants[0]
    published = store.local_path(ready.path)
    previous_bytes, previous_mtime = published.read_bytes(), published.stat().st_mtime_ns
    for _ in range(3):
        rebuilt = store.rebuild(asset.asset_id)
        assert rebuilt.variants[0] == ready
        assert store.worker(once=True) == 0
    assert len(store.tasks(asset.asset_id)) == 1
    assert store.tasks(asset.asset_id)[0].task_id == task.task_id
    assert store.tasks(asset.asset_id)[0].attempts == 1
    assert published.read_bytes() == previous_bytes
    assert published.stat().st_mtime_ns == previous_mtime


def test_partial_failure_persists_and_retry_uses_old_snapshot_after_config_change(
    config: StoreConfig,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = (Profile("small", 16), Profile("medium", 32), Profile("large", 64))
    config = replace(config, profiles=profiles)
    original = runtime.convert_thumbnail
    failing = True
    used_profiles: list[Profile] = []

    def convert(*args, **kwargs):
        profile = args[2]
        used_profiles.append(profile)
        if profile.name == "medium" and failing:
            raise ProcessingError("injected medium-profile encoder failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "convert_thumbnail", convert)
    with MediaStore.init(config) as store:
        asset = store.save(picture)
        assert store.worker(once=True) == 3
        partial = store.resolve(asset.asset_id)
        assert partial.processing_status == "partial"
        states = {variant.profile: variant for variant in partial.variants}
        assert states["medium"].status == "failed" and states["medium"].error
        assert states["small"].status == states["large"].status == "ready"
        stable = {
            name: store.local_path(value.path).read_bytes()
            for name, value in states.items()
            if value.path
        }
        old_identity = {
            task.profile: task.recipe_fingerprint for task in store.tasks(asset.asset_id)
        }
        original_bytes = store.local_path(partial.original_path).read_bytes()
    changed = replace(config, profiles=tuple(replace(profile, quality=95) for profile in profiles))
    failing = False
    with MediaStore.open(changed) as reopened:
        failed = next(task for task in reopened.tasks(asset.asset_id) if task.profile == "medium")
        assert failed.status == "failed" and "injected" in failed.error
        assert failed.revision == 1 and failed.recipe.quality == 80
        assert reopened.retry(asset.asset_id).processing_status == "pending"
        assert reopened.worker(once=True) == 1
        ready = reopened.resolve(asset.asset_id)
        assert ready.processing_status == "ready"
        assert used_profiles[-1].quality == 80
        assert {
            task.profile: task.recipe_fingerprint for task in reopened.tasks(asset.asset_id)
        } == old_identity
        assert {task.profile: task.attempts for task in reopened.tasks(asset.asset_id)} == {
            "small": 1,
            "medium": 2,
            "large": 1,
        }
        for variant in ready.variants:
            if variant.profile in stable:
                assert reopened.local_path(variant.path).read_bytes() == stable[variant.profile]
        assert reopened.local_path(ready.original_path).read_bytes() == original_bytes


def test_rebuild_only_changes_selected_profile_and_retry_never_reactivates_history(
    config: StoreConfig,
    picture: Path,
) -> None:
    config = replace(config, profiles=(Profile("card", 32), Profile("avatar", 16)))
    with MediaStore.init(config) as store:
        asset = store.save(picture)
        store.worker(once=True)
        initial = {variant.profile: variant for variant in store.resolve(asset.asset_id).variants}
        store.config = replace(
            config, profiles=(Profile("card", 32, quality=95), Profile("avatar", 16, quality=95))
        )
        requested = store.rebuild(asset.asset_id, profiles=["card"])
        current = {variant.profile: variant for variant in requested.variants}
        assert current["card"].recipe_fingerprint != initial["card"].recipe_fingerprint
        assert current["card"].path is None and current["card"].status == "pending"
        assert current["avatar"] == initial["avatar"]
        store.retry(asset.asset_id)
        assert store.worker(once=True) == 1
        assert store.resolve(asset.asset_id).processing_status == "ready"
        assert len(store.tasks(asset.asset_id)) == 3
        old_card = next(
            task
            for task in store.tasks(asset.asset_id)
            if task.profile == "card"
            and task.recipe_fingerprint == initial["card"].recipe_fingerprint
        )
        assert old_card.status != "pending" and old_card.status != "running"
        assert sum(task.status == "succeeded" for task in store.tasks(asset.asset_id)) >= 2


@pytest.mark.parametrize("damage", ["missing", "hash"])
def test_retry_repairs_current_missing_or_corrupt_variant_only(
    store: MediaStore,
    picture: Path,
    damage: str,
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    ready = store.resolve(asset.asset_id).variants[0]
    target = store.local_path(ready.path)
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"corrupt external replacement")
    retried = store.retry(asset.asset_id)
    assert retried.variants[0].status == "pending" and retried.variants[0].path is None
    assert store.worker(once=True) == 1
    assert store.resolve(asset.asset_id).variants[0].recipe_fingerprint == ready.recipe_fingerprint
    assert store.check().ok
    assert store.tasks(asset.asset_id)[0].attempts == 2


def test_source_hash_mismatch_fails_without_publishing(store: MediaStore, picture: Path) -> None:
    asset = store.save(picture)
    store.local_path(asset.original_path).write_bytes(b"externally corrupted immutable source")
    assert store.worker_once() == 1
    task = store.tasks(asset.asset_id)[0]
    assert task.status == "failed" and "hash" in task.error.lower()
    assert _variant_rows(store) == []
    assert store.resolve(asset.asset_id).original_path is None
    assert store.check().content_mismatches == (asset.original_path,)


@pytest.mark.parametrize("point", ["variant_converted", "variant_installed"])
@pytest.mark.parametrize("failure", [RuntimeError, SystemExit])
def test_variant_interruption_does_not_announce_ready_and_can_recover_same_identity(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    point: str,
    failure: type[BaseException],
) -> None:
    asset = store.save(picture)

    def interrupt(actual: str) -> None:
        if actual == point:
            raise failure("simulated worker interruption")

    store._fault = interrupt
    if failure is SystemExit:
        with pytest.raises(SystemExit):
            runtime.run_once(store, asset.asset_id)
    else:
        assert runtime.run_once(store, asset.asset_id) == 1
    store._fault = lambda point: None
    incomplete = store.resolve(asset.asset_id)
    assert incomplete.variants[0].status != "ready" and incomplete.variants[0].path is None
    assert _variant_rows(store) == []
    old_task = store.tasks(asset.asset_id)[0]
    with MediaStore.open(config) as reopened:
        if failure is SystemExit:
            if point == "variant_converted":
                assert reopened.tasks(asset.asset_id)[0].status == "running"
                _expire(reopened, old_task.task_id)
            else:
                assert reopened.tasks(asset.asset_id)[0].status == "pending"
        else:
            assert reopened.tasks(asset.asset_id)[0].status == "failed"
            reopened.retry(asset.asset_id)
        assert reopened.worker(once=True) == 1
        ready = reopened.resolve(asset.asset_id)
        assert ready.variants[0].status == "ready"
        assert ready.variants[0].recipe_fingerprint == asset.variants[0].recipe_fingerprint
        assert reopened.tasks(asset.asset_id)[0].task_id == old_task.task_id
        assert reopened.check().ok


def test_process_timeout_returns_before_blocked_conversion_and_reuses_one_driver(
    store: MediaStore,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = store.save(picture)
    source_bytes = store.local_path(asset.original_path).read_bytes()
    entered, release = threading.Event(), threading.Event()
    original = runtime.convert_thumbnail
    call_count = 0

    def blocked(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release its blocked decoder")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "convert_thumbnail", blocked)
    try:
        started = time.monotonic()
        result = store.process(asset.asset_id, timeout=0.05)
        assert time.monotonic() - started < 0.75
        assert entered.is_set() and not release.is_set()
        assert result.timed_out, (result.asset.processing_status, store.tasks(asset.asset_id))
        assert result.asset.asset_id == asset.asset_id
        driver = store._process_thread
        assert driver is not None and driver.is_alive()
        for _ in range(4):
            assert store.process(asset.asset_id, timeout=0.01).timed_out
            assert store._process_thread is driver
        assert call_count == 1
        assert store.worker_once(asset.asset_id) == 0
        assert store.local_path(asset.original_path).read_bytes() == source_bytes
        assert store.tasks(asset.asset_id)[0].status == "running"
    finally:
        release.set()
    completed = store.process(asset.asset_id, timeout=3)
    assert not completed.timed_out and completed.asset.processing_status == "ready"
    assert call_count == 1


def test_running_worker_renews_lease_during_conversion(
    store: MediaStore,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = store.save(picture)
    real_heartbeat = runtime.heartbeat
    renewed = threading.Event()

    def heartbeat(*args, **kwargs):
        result = real_heartbeat(*args, **kwargs)
        if result:
            renewed.set()
        return result

    monkeypatch.setattr(runtime, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    with _blocked_worker(store, asset.asset_id, monkeypatch):
        assert renewed.wait(2)
    assert store.tasks(asset.asset_id)[0].status == "succeeded"


def test_close_without_wait_rejects_new_work_and_leaves_inflight_task_recoverable(
    store: MediaStore,
    config: StoreConfig,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = store.save(picture)
    entered, release = threading.Event(), threading.Event()
    original = runtime.convert_thumbnail

    def blocked(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release conversion before closing")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "convert_thumbnail", blocked)
    try:
        assert store.process(asset.asset_id, timeout=0.05).timed_out
        assert entered.is_set()
        driver = store._process_thread
        started = time.monotonic()
        store.close(wait=False)
        assert time.monotonic() - started < 0.75
        with pytest.raises(StorageError):
            store.save(picture)
        with pytest.raises(StorageError):
            store.process(asset.asset_id, timeout=0)
    finally:
        release.set()
    driver.join(timeout=3)
    assert not driver.is_alive()
    with MediaStore.open(config) as reopened:
        task = reopened.tasks(asset.asset_id)[0]
        if task.status == "running":
            _expire(reopened, task.task_id)
        elif task.status == "failed":
            reopened.retry(asset.asset_id)
        reopened.worker(once=True)
        assert reopened.resolve(asset.asset_id).processing_status == "ready"


def test_stopped_worker_leaves_queue_pending_and_restart_continues_completed_results(
    config: StoreConfig,
    picture: Path,
) -> None:
    config = replace(config, profiles=(Profile("small", 16), Profile("large", 64)))
    with MediaStore.init(config) as store:
        asset = store.save(picture)
        stopped = threading.Event()
        stopped.set()
        assert store.worker(stop_event=stopped) == 0
        assert all(task.status == "pending" for task in store.tasks(asset.asset_id))
        assert store.worker_once() == 1
        completed = next(
            variant
            for variant in store.resolve(asset.asset_id).variants
            if variant.status == "ready"
        )
        completed_bytes = store.local_path(completed.path).read_bytes()
    with MediaStore.open(config) as reopened:
        assert reopened.worker(once=True) == 1
        assert reopened.resolve(asset.asset_id).processing_status == "ready"
        assert reopened.local_path(completed.path).read_bytes() == completed_bytes
        assert reopened.worker(once=True) == 0
