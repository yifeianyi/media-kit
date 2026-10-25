"""Path validation and deterministic physical layout for managed media."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from media_kit.errors import StorageError

_ASSET_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_key(key: str) -> str:
    """Return a normalized logical key or reject ambiguous/path-traversal input."""
    if not isinstance(key, str) or not key or "\x00" in key or "\\" in key:
        raise StorageError("logical key must be a non-empty POSIX relative path")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in key.split("/")):
        raise StorageError("logical key must not be absolute or contain unsafe path segments")
    normalized = path.as_posix()
    if normalized != key:
        raise StorageError("logical key is not in canonical POSIX form")
    return normalized


def validate_filename(filename: str) -> str:
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or Path(filename).name != filename
    ):
        raise StorageError("filename must be a single safe path segment")
    return filename


def safe_join(root: Path, *segments: str, must_exist: bool = False) -> Path:
    """Resolve a path and ensure it remains beneath root, including symlinks."""
    base = root.resolve()
    unresolved = base
    for segment in segments:
        validate_key(segment)
        for part in segment.split("/"):
            unresolved = unresolved / part
            if unresolved.is_symlink():
                raise StorageError("symbolic links are not allowed in managed paths")
    candidate = unresolved.resolve(strict=must_exist)
    if candidate != base and base not in candidate.parents:
        raise StorageError("resolved path escapes its managed root")
    return candidate


def original_path(
    root: Path, original_dir: str, asset_id: str, revision: int, filename: str
) -> Path:
    _validate_asset(asset_id)
    _validate_revision(revision)
    validate_filename(filename)
    return safe_join(root, original_dir, asset_id, str(revision), filename)


def variant_path(
    root: Path,
    variant_dir: str,
    asset_id: str,
    revision: int,
    recipe_fingerprint: str,
    profile: str,
) -> Path:
    _validate_asset(asset_id)
    _validate_revision(revision)
    if not re.fullmatch(r"[0-9a-f]{64}", recipe_fingerprint):
        raise StorageError("invalid recipe fingerprint")
    if not _PROFILE.fullmatch(profile):
        raise StorageError("invalid profile name")
    return safe_join(
        root, variant_dir, asset_id, str(revision), recipe_fingerprint, f"{profile}.webp"
    )


def public_url(base: str | None, relative_path: str | Path | None) -> str | None:
    if base is None or relative_path is None:
        return None
    relative = PurePosixPath(str(relative_path))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise StorageError("public media coordinate must be a safe relative path")
    return base.rstrip("/") + "/" + quote(relative.as_posix(), safe="/")


def _validate_asset(asset_id: str) -> None:
    if not _ASSET_ID.fullmatch(asset_id):
        raise StorageError("invalid asset id")


def _validate_revision(revision: int) -> None:
    if not isinstance(revision, int) or revision <= 0:
        raise StorageError("revision must be a positive integer")
