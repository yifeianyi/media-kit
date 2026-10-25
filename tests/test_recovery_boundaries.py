"""Regression contracts for recovery ownership and ordinary binary stream input."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from media_kit import InputValidationError, MediaStore, Profile, StorageError, StoreConfig


@pytest.fixture
def store(tmp_path: Path) -> Iterator[MediaStore]:
    config = StoreConfig(
        tmp_path / "media", tmp_path / "state", profiles=(Profile("card", 32, quality=70),)
    )
    with MediaStore.init(config) as value:
        yield value


@pytest.fixture
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "source.png"
    Image.new("RGB", (80, 50), "teal").save(path)
    return path


def _use_quality(store: MediaStore, quality: int) -> None:
    store.config = replace(store.config, profiles=(Profile("card", 32, quality=quality),))


@pytest.mark.parametrize("mutation", ["rebuild", "retry", "replace", "delete"])
def test_unfinished_reclaim_blocks_conflicting_asset_writes_until_recovery(
    store: MediaStore,
    picture: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from media_kit import api

    asset = store.save(picture)
    store.worker(once=True)
    first = store.resolve(asset.asset_id).variants[0]
    assert first.path is not None
    first_path = store.local_path(first.path)
    first_bytes = first_path.read_bytes()
    _use_quality(store, 95)
    store.rebuild(asset.asset_id)
    store.worker(once=True)
    current = store.resolve(asset.asset_id).variants[0]
    assert current.path is not None
    move = api.atomic_move

    def fail_old_variant(source: Path, destination: Path) -> None:
        if source == first_path:
            raise OSError("blocked historical variant")
        move(source, destination)

    monkeypatch.setattr(api, "atomic_move", fail_old_variant)
    assert store.cleanup()["failed"] == 1
    _use_quality(store, 70)

    with pytest.raises(StorageError):
        if mutation == "rebuild":
            store.rebuild(asset.asset_id)
        elif mutation == "retry":
            store.retry(asset.asset_id)
        elif mutation == "replace":
            store.replace(asset.asset_id, picture, expected_revision=1)
        else:
            store.delete(asset.asset_id, expected_revision=1)

    unchanged = store.resolve(asset.asset_id)
    assert unchanged.status == "active" and unchanged.revision == 1
    assert unchanged.variants[0] == current
    assert first_path.read_bytes() == first_bytes
    other = store.save(picture, key="unrelated")
    store.worker(once=True)
    assert store.resolve(other.asset_id).variants[0].status == "ready"

    monkeypatch.setattr(api, "atomic_move", move)
    assert store.recover()["recovered"] == 1
    assert not first_path.exists()
    requested = store.rebuild(asset.asset_id)
    assert requested.variants[0].recipe_fingerprint == first.recipe_fingerprint
    assert requested.variants[0].status == "pending"
    store.worker(once=True)
    assert store.resolve(asset.asset_id).variants[0].status == "ready"
    assert store.delete(asset.asset_id, expected_revision=1).status == "deleted"
    assert not first_path.exists()
    assert store.resolve(other.asset_id).variants[0].status == "ready"


def test_retry_then_delete_still_owns_a_corrupt_variant(store: MediaStore, picture: Path) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    ready = store.resolve(asset.asset_id).variants[0]
    assert ready.path is not None
    path = store.local_path(ready.path)
    path.write_bytes(b"corrupt managed output")

    pending = store.retry(asset.asset_id)
    assert pending.variants[0].status == "pending"
    assert pending.variants[0].path is None
    assert store.delete(asset.asset_id, expected_revision=1).status == "deleted"
    assert not path.exists()
    assert store.check().ok


def test_reclaimed_path_cannot_be_adopted_even_when_external_bytes_match(
    store: MediaStore, picture: Path
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    first = store.resolve(asset.asset_id).variants[0]
    assert first.path is not None
    first_path = store.local_path(first.path)
    first_bytes = first_path.read_bytes()
    _use_quality(store, 95)
    store.rebuild(asset.asset_id)
    store.worker(once=True)
    current = store.resolve(asset.asset_id).variants[0]
    assert store.cleanup()["failed"] == 0
    assert not first_path.exists()

    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(first_bytes)
    _use_quality(store, 70)
    with pytest.raises(StorageError):
        store.rebuild(asset.asset_id)

    assert store.resolve(asset.asset_id).variants[0] == current
    assert first_path.read_bytes() == first_bytes
    assert first.path in store.check().unknown_files
    assert store.delete(asset.asset_id, expected_revision=1).status == "deleted"
    assert first_path.read_bytes() == first_bytes


def test_reclaimed_recipe_can_be_regenerated_and_deleted_when_path_is_empty(
    store: MediaStore, picture: Path
) -> None:
    asset = store.save(picture)
    store.worker(once=True)
    first = store.resolve(asset.asset_id).variants[0]
    assert first.path is not None
    first_path = store.local_path(first.path)
    _use_quality(store, 95)
    store.rebuild(asset.asset_id)
    store.worker(once=True)
    assert store.cleanup()["failed"] == 0
    assert not first_path.exists()

    _use_quality(store, 70)
    request = store.rebuild(asset.asset_id).variants[0]
    assert request.recipe_fingerprint == first.recipe_fingerprint
    assert request.status == "pending" and request.path is None
    store.worker(once=True)
    regenerated = store.resolve(asset.asset_id).variants[0]
    assert regenerated.status == "ready" and regenerated.path == first.path
    assert first_path.is_file()
    assert store.check().ok
    assert store.delete(asset.asset_id, expected_revision=1).status == "deleted"
    assert not first_path.exists()
    assert store.check().ok


def test_save_accepts_an_open_file_and_keeps_the_caller_stream_open(
    store: MediaStore, picture: Path
) -> None:
    original = picture.read_bytes()
    with picture.open("rb") as stream:
        saved = store.save(stream)
        assert not stream.closed
        stream.seek(0)
        assert stream.read() == original
    assert saved.original_path is not None
    assert store.local_path(saved.original_path).read_bytes() == original


def test_named_binary_stream_extension_must_match_its_image_content(
    store: MediaStore, picture: Path, tmp_path: Path
) -> None:
    disguised = tmp_path / "disguised.jpg"
    disguised.write_bytes(picture.read_bytes())
    with disguised.open("rb") as stream:
        with pytest.raises(InputValidationError):
            store.save(stream)
        assert not stream.closed
    assert store.list().total == 0
    assert store.check().ok
