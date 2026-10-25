from pathlib import Path

import pytest

from media_kit.errors import StorageError
from media_kit.storage.paths import original_path, public_url, safe_join, validate_key, variant_path

ASSET = "12345678-1234-5678-1234-567812345678"
FINGERPRINT = "a" * 64


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a//b", "a/./b", "a\\b", "", "a/../b"])
def test_key_rejects_ambiguous_or_unsafe_paths(key: str) -> None:
    with pytest.raises(StorageError):
        validate_key(key)


def test_paths_use_asset_revision_and_fingerprint(tmp_path: Path) -> None:
    original = original_path(tmp_path, "src", ASSET, 2, "photo.jpg")
    variant = variant_path(tmp_path, "thu", ASSET, 2, FINGERPRINT, "small")
    assert original == tmp_path / "src" / ASSET / "2" / "photo.jpg"
    assert variant == tmp_path / "thu" / ASSET / "2" / FINGERPRINT / "small.webp"


def test_symlink_cannot_escape_managed_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        safe_join(tmp_path, "link", "file")


def test_public_url_encodes_each_path_segment() -> None:
    assert (
        public_url("/media/src/", "asset id/photo #1.jpg")
        == "/media/src/asset%20id/photo%20%231.jpg"
    )
