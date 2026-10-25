"""Standalone Pillow-based image inspection and atomic thumbnail conversion."""

from __future__ import annotations

import hashlib
import os
import tempfile
import warnings
from pathlib import Path
from typing import BinaryIO, TypeAlias

from PIL import Image, ImageOps, UnidentifiedImageError, features

from media_kit.errors import ConfigurationError, InputValidationError, ProcessingError
from media_kit.models import ImageInfo, Profile, TransformConfig, TransformResult
from media_kit.storage.files import fsync_directory
from media_kit.transforms.fingerprint import recipe_for, validate_profiles

ImageSource: TypeAlias = str | Path | BinaryIO
_SUPPORTED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "AVIF"}
_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "AVIF": "image/avif",
}


def format_capabilities() -> dict[str, dict[str, bool]]:
    """Report package-supported formats available in this Pillow installation.

    ``decode`` describes accepted inputs. Only WebP is an output format of this
    package, so ``encode`` is false for the other formats even if Pillow can
    encode them. Calling this helper does not initialize a managed store.
    """
    Image.init()
    available = set(features.get_supported())
    codecs = {"JPEG": "jpg", "PNG": "zlib", "WEBP": "webp", "AVIF": "avif"}
    return {
        name: {
            "decode": name in Image.OPEN and (name not in codecs or codecs[name] in available),
            "encode": name == "WEBP" and name in Image.SAVE and "webp" in available,
        }
        for name in sorted(_SUPPORTED_FORMATS)
    }


def content_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(
    source: ImageSource,
    *,
    max_bytes: int = 20 * 1024 * 1024,
    max_pixels: int = 40_000_000,
    declared_format: str | None = None,
) -> ImageInfo:
    """Verify and fully decode an input without modifying its bytes.

    A filename with an extension is an explicit format claim and must match
    its actual image content. Extensionless files and anonymous binary streams
    have no filename claim. Seekable streams are left positioned at zero.
    """
    _validate_limits(max_bytes, max_pixels)
    path = Path(source) if isinstance(source, (str, Path)) else None
    try:
        byte_size = path.stat().st_size if path is not None else _stream_size(source)
        if byte_size > max_bytes:
            raise InputValidationError(f"image exceeds byte limit ({byte_size} > {max_bytes})")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with _open_image(source) as image:
                image_format = image.format or ""
                width, height = image.size
                animated = bool(getattr(image, "is_animated", False))
                if image_format not in _SUPPORTED_FORMATS:
                    raise InputValidationError(
                        f"unsupported image format: {image_format or 'unknown'}"
                    )
                _validate_claims(source, image_format, declared_format)
                _validate_dimensions(width, height, max_pixels)
                image.verify()
            # verify() alone misses decoder failures such as truncated JPEG
            # scan data. Reopen and load each frame before accepting the input.
            with _open_image(source) as image:
                frame = 0
                decoded_pixels = 0
                while True:
                    try:
                        image.seek(frame)
                    except EOFError:
                        break
                    _validate_dimensions(image.width, image.height, max_pixels)
                    decoded_pixels += image.width * image.height
                    if decoded_pixels > max_pixels:
                        raise InputValidationError("image exceeds total decoded pixel limit")
                    image.load()
                    frame += 1
        if path is not None:
            digest = content_hash(path)
        else:
            assert not isinstance(source, (str, Path))
            source.seek(0)
            digest_builder = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest_builder.update(chunk)
            digest = digest_builder.hexdigest()
        return ImageInfo(
            image_format, _MIME_TYPES[image_format], width, height, byte_size, digest, animated
        )
    except InputValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
        EOFError,
        SyntaxError,
    ) as error:
        raise InputValidationError(f"invalid or unsafe image: {error}") from error
    finally:
        if not isinstance(source, (str, Path)) and not source.closed and source.seekable():
            source.seek(0)


def convert_thumbnail(
    source: ImageSource,
    output_path: Path,
    profile: Profile,
    config: TransformConfig | None = None,
    *,
    declared_format: str | None = None,
) -> TransformResult:
    """Create one WebP result with a complete-file atomic replacement.

    Each call decodes the original source, never a previous thumbnail. Failed
    conversion preserves an existing output and removes its private temporary
    file. The source and output must refer to different files.
    """
    policy = config or TransformConfig(profiles=(profile,))
    validate_profiles(policy.profiles)
    _validate_limits(policy.max_bytes, policy.max_pixels)
    if profile not in policy.profiles:
        raise ConfigurationError(f"profile is not enabled: {profile.name}")
    recipe = recipe_for(profile)
    if not format_capabilities()["WEBP"]["encode"]:
        raise ConfigurationError("this Pillow installation cannot encode WebP")
    output_path = Path(output_path)
    _reject_source_output_alias(source, output_path)
    inspect_image(
        source,
        max_bytes=policy.max_bytes,
        max_pixels=policy.max_pixels,
        declared_format=declared_format,
    )
    temporary: Path | None = None
    try:
        parent = output_path.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with _open_image(source) as original:
                original.seek(0)
                oriented = ImageOps.exif_transpose(original)
                try:
                    has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                    with oriented.convert("RGBA" if has_alpha else "RGB") as image:
                        image.thumbnail((recipe.size, recipe.size), Image.Resampling.LANCZOS)
                        with tempfile.NamedTemporaryFile(
                            dir=parent,
                            prefix=".media-kit-",
                            suffix=".tmp",
                            delete=False,
                        ) as stream:
                            temporary = Path(stream.name)
                            image.save(
                                stream, format="WEBP", quality=recipe.quality, method=recipe.method
                            )
                            stream.flush()
                            os.fsync(stream.fileno())
                        result = TransformResult(
                            path=output_path,
                            size=recipe.size,
                            width=image.width,
                            height=image.height,
                            file_size=temporary.stat().st_size,
                            content_hash=content_hash(temporary),
                        )
                        _reject_source_output_alias(source, output_path)
                        os.replace(temporary, output_path)
                        fsync_directory(parent)
                        temporary = None
                        return result
                finally:
                    oriented.close()
    except InputValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
        EOFError,
        SyntaxError,
    ) as error:
        raise ProcessingError(f"thumbnail conversion failed: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if not isinstance(source, (str, Path)) and not source.closed and source.seekable():
            source.seek(0)


def _open_image(source: ImageSource) -> Image.Image:
    if isinstance(source, (str, Path)):
        return Image.open(source)
    if not source.seekable():
        raise InputValidationError("image stream must be seekable for validation")
    source.seek(0)
    return Image.open(source)


def _stream_size(source: ImageSource) -> int:
    if isinstance(source, (str, Path)) or not source.seekable():
        raise InputValidationError("image stream must be seekable for validation")
    position = source.tell()
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(position)
    return size


def _validate_limits(max_bytes: int, max_pixels: int) -> None:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ConfigurationError("max_bytes must be a positive integer")
    if type(max_pixels) is not int or max_pixels <= 0:
        raise ConfigurationError("max_pixels must be a positive integer")


def _validate_dimensions(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise InputValidationError(f"image exceeds pixel limit ({width}x{height})")


def _validate_claims(source: ImageSource, actual_format: str, declared_format: str | None) -> None:
    if declared_format is not None:
        if not isinstance(declared_format, str):
            raise InputValidationError("declared image format must be a string")
        declared = declared_format.upper()
        aliases = {"JPG": "JPEG", **{mime.upper(): name for name, mime in _MIME_TYPES.items()}}
        declared = aliases.get(declared, declared)
        if declared != actual_format:
            raise InputValidationError("declared image format does not match actual content")
    filename = str(source) if isinstance(source, (str, Path)) else getattr(source, "name", None)
    if isinstance(filename, str):
        suffix = Path(filename).suffix.lower()
        if suffix:
            claimed = Image.registered_extensions().get(suffix)
            if claimed not in _SUPPORTED_FORMATS:
                raise InputValidationError(f"unsupported image filename extension: {suffix}")
            if claimed != actual_format:
                raise InputValidationError("image filename extension does not match actual content")


def _reject_source_output_alias(source: ImageSource, output: Path) -> None:
    candidate = source if isinstance(source, (str, Path)) else getattr(source, "name", None)
    if isinstance(candidate, (str, Path)):
        source_path = Path(candidate)
        if source_path.resolve() == output.resolve():
            raise InputValidationError("output must not overwrite the input image")
        if source_path.exists() and output.exists() and source_path.samefile(output):
            raise InputValidationError("output must not overwrite the input image")
    elif not isinstance(source, (str, Path)) and output.exists():
        try:
            source_stat = os.fstat(source.fileno())
        except (OSError, ValueError):
            return
        if (source_stat.st_dev, source_stat.st_ino) == (output.stat().st_dev, output.stat().st_ino):
            raise InputValidationError("output must not overwrite the input image")
