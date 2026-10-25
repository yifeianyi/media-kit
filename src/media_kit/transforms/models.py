"""Public, storage-independent models for image transformation.

This module deliberately has no import-time dependency on a MediaStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .errors import TransformConfigurationError

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ImageLimits:
    """Resource limits applied before full image decode.

    These limits validate whether an image can be processed; they are not part
    of a transformation recipe because changing them does not change output.
    """

    max_pixels: int = 40_000_000
    max_bytes: int = 20 * 1024 * 1024

    def __post_init__(self) -> None:
        if isinstance(self.max_pixels, bool) or not isinstance(self.max_pixels, int) or self.max_pixels <= 0:
            raise TransformConfigurationError("max_pixels must be a positive integer")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes <= 0:
            raise TransformConfigurationError("max_bytes must be a positive integer")


@dataclass(frozen=True, slots=True)
class TransformProfile:
    """A named, immutable WebP transformation recipe.

    ``max_edge`` is the longest output edge. Images are never enlarged or
    cropped. A profile name is task identity, while its normalized recipe
    fingerprint depends only on output-affecting values.
    """

    name: str
    max_edge: int
    format: str = "WEBP"
    quality: int = 80
    method: int = 4
    algorithm_version: str = "webp-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _PROFILE_NAME.fullmatch(self.name):
            raise TransformConfigurationError("profile name is invalid for a safe path segment")
        if isinstance(self.max_edge, bool) or not isinstance(self.max_edge, int) or self.max_edge <= 0:
            raise TransformConfigurationError("max_edge must be a positive integer")
        if not isinstance(self.format, str) or self.format.upper() != "WEBP":
            raise TransformConfigurationError("only WebP output is currently supported")
        if isinstance(self.quality, bool) or not isinstance(self.quality, int) or not 0 <= self.quality <= 100:
            raise TransformConfigurationError("WebP quality must be an integer from 0 to 100")
        if isinstance(self.method, bool) or not isinstance(self.method, int) or not 0 <= self.method <= 6:
            raise TransformConfigurationError("WebP method must be an integer from 0 to 6")
        if (not isinstance(self.algorithm_version, str) or not self.algorithm_version.strip()
                or len(self.algorithm_version) > 128 or "\x00" in self.algorithm_version):
            raise TransformConfigurationError("algorithm_version must be a non-empty string of at most 128 characters")

    def recipe_snapshot(self) -> dict[str, object]:
        """Return canonical, output-affecting values, excluding task identity."""
        return {
            "algorithm_version": self.algorithm_version,
            "animation": "first-frame",
            "format": "webp",
            "max_edge": self.max_edge,
            "method": self.method,
            "orientation": "exif-transpose",
            "quality": self.quality,
            "resize": "fit-no-enlarge",
            "transparency": "preserve",
        }


@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: str
    width: int
    height: int
    byte_size: int
    frame_count: int
    has_transparency: bool


@dataclass(frozen=True, slots=True)
class FormatCapability:
    format: str
    can_decode: bool
    extensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransformResult:
    path: Path
    profile: str
    width: int
    height: int
    file_size: int
    content_hash: str
    mime_type: str = "image/webp"
