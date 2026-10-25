"""Pillow-based image inspection and atomic WebP conversion.

No function in this module creates a MediaStore, SQLite database, or worker.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import ImageValidationError, TransformIOError
from .models import (
    FormatCapability,
    ImageInfo,
    ImageLimits,
    TransformProfile,
    TransformResult,
)

_SUPPORTED_INPUTS: dict[str, tuple[str, ...]] = {
    "JPEG": (".jpg", ".jpeg"),
    "PNG": (".png",),
    "GIF": (".gif",),
    "WEBP": (".webp",),
    "AVIF": (".avif",),
}
_EXTENSION_FORMAT = {extension: image_format for image_format, extensions in _SUPPORTED_INPUTS.items() for extension in extensions}
_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp", "AVIF": "image/avif"}


def default_profiles(*, algorithm_version: str = "webp-v1") -> tuple[TransformProfile, ...]:
    """Return the package's standard 320/640/1280 pixel WebP profiles."""
    return tuple(
        TransformProfile(str(size), size, quality=80, method=4, algorithm_version=algorithm_version)
        for size in (320, 640, 1280)
    )


def validate_profiles(profiles: tuple[TransformProfile, ...] | list[TransformProfile]) -> tuple[TransformProfile, ...]:
    """Validate a profile set, including unique names and safe path segments."""
    from .errors import TransformConfigurationError

    result = tuple(profiles)
    names = [profile.name for profile in result]
    if len(names) != len(set(names)):
        raise TransformConfigurationError("profile names must be unique")
    if not result:
        raise TransformConfigurationError("at least one transform profile is required")
    return result


def recipe_fingerprint(profile: TransformProfile) -> str:
    """Return the SHA-256 identity of all values that can change output bytes."""
    canonical = json.dumps(profile.recipe_snapshot(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def format_capabilities() -> tuple[FormatCapability, ...]:
    """Report which package-supported formats the installed Pillow can decode."""
    Image.init()
    supported = set(Image.registered_extensions().values())
    return tuple(
        FormatCapability(
            format=image_format,
            can_decode=image_format in supported,
            extensions=extensions,
        )
        for image_format, extensions in _SUPPORTED_INPUTS.items()
    )


def _inspect(source: Path, limits: ImageLimits, expected_format: str | None = None) -> tuple[ImageInfo, str]:
    try:
        byte_size = source.stat().st_size
    except OSError as error:
        raise ImageValidationError(f"cannot read image source: {error}") from error
    if byte_size > limits.max_bytes:
        raise ImageValidationError(f"image exceeds byte limit ({byte_size} > {limits.max_bytes})")
    if byte_size == 0:
        raise ImageValidationError("image input is empty")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                actual = (image.format or "").upper()
                if actual not in _SUPPORTED_INPUTS:
                    raise ImageValidationError(f"unsupported image format: {actual or 'unknown'}")
                declared = expected_format.upper() if expected_format else None
                suffix_format = _EXTENSION_FORMAT.get(source.suffix.lower())
                if declared and declared != actual:
                    raise ImageValidationError(f"declared image format {declared} does not match content {actual}")
                if suffix_format and suffix_format != actual:
                    raise ImageValidationError(f"file extension {source.suffix} does not match image content {actual}")
                if image.width <= 0 or image.height <= 0 or image.width * image.height > limits.max_pixels:
                    raise ImageValidationError(
                        f"image exceeds pixel limit or has invalid dimensions ({image.width}x{image.height}; max {limits.max_pixels})"
                    )
                frames = int(getattr(image, "n_frames", 1))
                transparency = "transparency" in image.info or "A" in image.getbands()
                dimensions = (image.width, image.height)
                image.verify()
                return ImageInfo(actual, dimensions[0], dimensions[1], byte_size, frames, transparency), actual
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageValidationError(f"image exceeds Pillow decompression safety limits: {error}") from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise ImageValidationError(f"invalid or unsupported image input: {error}") from error


def inspect_image(source: str | os.PathLike[str], *, limits: ImageLimits = ImageLimits(), expected_format: str | None = None) -> ImageInfo:
    """Inspect actual image content and enforce byte/pixel limits before decode.

    The file extension and optional caller-declared format are checked against
    the decoder's content detection whenever they identify a supported format.
    """
    info, _ = _inspect(Path(source), limits, expected_format)
    return info


def convert_image(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    profile: TransformProfile,
    *,
    limits: ImageLimits = ImageLimits(),
    expected_format: str | None = None,
) -> TransformResult:
    """Convert frame zero from an input image and atomically publish WebP.

    The source is never modified. Output is first fully encoded to a sibling
    temporary file and replaces the target only after successful conversion.
    """
    source_path = Path(source)
    output_path = Path(output)
    try:
        source_resolved = source_path.resolve(strict=True)
        output_resolved = output_path.resolve(strict=False)
    except OSError as error:
        raise ImageValidationError(f"cannot resolve source or output path: {error}") from error
    if source_resolved == output_resolved:
        raise ImageValidationError("output target must not overwrite the source image")

    info, _ = _inspect(source_path, limits, expected_format)
    temporary: Path | None = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source_path) as original:
                original.seek(0)
                oriented = ImageOps.exif_transpose(original)
                has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                converted = oriented.convert("RGBA" if has_alpha else "RGB")
                converted.thumbnail((profile.max_edge, profile.max_edge), Image.Resampling.LANCZOS)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w+b", prefix=".media-kit-transform-", suffix=".webp",
                    dir=output_path.parent, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    converted.save(stream, format="WEBP", quality=profile.quality, method=profile.method)
                    stream.flush()
                    os.fsync(stream.fileno())
                payload = temporary.read_bytes()
                os.replace(temporary, output_path)
                temporary = None
                try:
                    directory_fd = os.open(output_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # Directory fsync is not available on every supported OS.
                    pass
                return TransformResult(
                    path=output_path,
                    profile=profile.name,
                    width=converted.width,
                    height=converted.height,
                    file_size=len(payload),
                    content_hash=hashlib.sha256(payload).hexdigest(),
                )
    except (ImageValidationError, TransformIOError):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as error:
        raise TransformIOError(f"image conversion failed: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
