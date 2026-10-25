"""Typed public values shared by image conversion and managed storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

AssetStatus = Literal["active", "deleting", "deleted"]
VariantStatus = Literal["pending", "running", "ready", "failed", "superseded"]
TaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "superseded"]


@dataclass(frozen=True, slots=True)
class Profile:
    """Named longest-edge thumbnail recipe."""

    name: str
    size: int
    format: str = "webp"
    quality: int = 80
    method: int = 4
    algorithm_version: str = "webp-v1"


@dataclass(frozen=True, slots=True)
class TransformRecipe:
    """Immutable output-affecting transform settings for one request."""

    size: int
    format: str = "webp"
    quality: int = 80
    method: int = 4
    orientation: str = "exif-transpose"
    animation: str = "first-frame"
    scaling: str = "fit-no-upscale"
    algorithm_version: str = "webp-v1"


@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: str
    mime_type: str
    width: int
    height: int
    file_size: int
    content_hash: str
    animated: bool = False


@dataclass(frozen=True, slots=True)
class TransformResult:
    path: Path
    size: int
    width: int
    height: int
    file_size: int
    content_hash: str
    mime_type: str = "image/webp"


@dataclass(frozen=True, slots=True)
class VariantView:
    profile: str
    status: VariantStatus
    recipe_fingerprint: str
    path: str | None = None
    url: str | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    content_hash: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AssetView:
    asset_id: str
    key: str
    revision: int
    status: AssetStatus
    original_path: str | None
    original_url: str | None
    original: ImageInfo | None
    variants: tuple[VariantView, ...] = ()
    processing_status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None
    operation_revision: int | None = None


@dataclass(frozen=True, slots=True)
class AssetPage:
    items: tuple[AssetView, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: str
    asset_id: str
    revision: int
    profile: str
    recipe_fingerprint: str
    status: TaskStatus
    attempts: int = 0
    error: str | None = None
    recipe: TransformRecipe | None = None
    lease_until: float | None = None


@dataclass(frozen=True, slots=True)
class DeleteResult:
    asset_id: str
    status: AssetStatus
    files_removed: int = 0
    pending_cleanup: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckReport:
    checked_assets: int
    missing_files: tuple[str, ...] = ()
    content_mismatches: tuple[str, ...] = ()
    unknown_files: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing_files or self.content_mismatches or self.unknown_files)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    asset: AssetView
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class TransformConfig:
    profiles: tuple[Profile, ...] = field(
        default_factory=lambda: (
            Profile("320", 320),
            Profile("640", 640),
            Profile("1280", 1280),
        )
    )
    max_pixels: int = 40_000_000
    max_bytes: int = 20 * 1024 * 1024
