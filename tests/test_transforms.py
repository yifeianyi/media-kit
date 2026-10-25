from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from media_kit.transforms import (
    ImageLimits,
    ImageValidationError,
    TransformConfigurationError,
    TransformProfile,
    convert_image,
    default_profiles,
    format_capabilities,
    inspect_image,
    recipe_fingerprint,
    validate_profiles,
)


def test_profile_defaults_and_fingerprints_are_stable() -> None:
    profiles = default_profiles()
    assert [(p.name, p.max_edge, p.quality, p.method) for p in profiles] == [
        ("320", 320, 80, 4), ("640", 640, 80, 4), ("1280", 1280, 80, 4),
    ]
    assert recipe_fingerprint(profiles[0]) == recipe_fingerprint(profiles[0])
    assert recipe_fingerprint(profiles[0]) != recipe_fingerprint(TransformProfile("320", 320, quality=90))
    assert recipe_fingerprint(profiles[0]) != recipe_fingerprint(TransformProfile("320", 320, algorithm_version="webp-v2"))
    assert recipe_fingerprint(TransformProfile("small", 320)) == recipe_fingerprint(TransformProfile("other-name", 320))


@pytest.mark.parametrize(
    "values",
    [
        {"name": "../escape", "max_edge": 320},
        {"name": "valid", "max_edge": 0},
        {"name": "valid", "max_edge": 320, "quality": 101},
        {"name": "valid", "max_edge": 320, "method": 7},
        {"name": "valid", "max_edge": 320, "format": "PNG"},
    ],
)
def test_invalid_profile_rejected(values: dict[str, object]) -> None:
    with pytest.raises(TransformConfigurationError):
        TransformProfile(**values)


def test_profile_set_rejects_duplicate_name() -> None:
    with pytest.raises(TransformConfigurationError):
        validate_profiles([TransformProfile("same", 320), TransformProfile("same", 640)])


def test_inspection_checks_content_limits_and_extension(tmp_path: Path) -> None:
    path = tmp_path / "source.png"
    Image.new("RGB", (12, 8), "red").save(path)
    info = inspect_image(path)
    assert (info.format, info.width, info.height, info.frame_count) == ("PNG", 12, 8, 1)
    with pytest.raises(ImageValidationError, match="byte limit"):
        inspect_image(path, limits=ImageLimits(max_bytes=1))
    with pytest.raises(ImageValidationError, match="pixel limit"):
        inspect_image(path, limits=ImageLimits(max_pixels=10))
    bad_extension = tmp_path / "source.jpg"
    bad_extension.write_bytes(path.read_bytes())
    with pytest.raises(ImageValidationError, match="extension"):
        inspect_image(bad_extension)


def test_rejects_invalid_image_without_publishing_output(tmp_path: Path) -> None:
    source = tmp_path / "fake.png"
    source.write_bytes(b"not an image")
    output = tmp_path / "nested" / "output.webp"
    with pytest.raises(ImageValidationError):
        convert_image(source, output, TransformProfile("small", 320))
    assert not output.exists()
    assert list(output.parent.glob(".media-kit-transform-*")) == [] if output.parent.exists() else True


@pytest.mark.parametrize(
    ("size", "expected"),
    [((40, 20), (40, 20)), ((160, 80), (100, 50)), ((20, 40), (20, 40))],
)
def test_conversion_preserves_aspect_and_does_not_enlarge(
    tmp_path: Path, size: tuple[int, int], expected: tuple[int, int]
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", size, "blue").save(source)
    original = source.read_bytes()
    result = convert_image(source, tmp_path / "out.webp", TransformProfile("small", 100))
    assert (result.width, result.height) == expected
    assert source.read_bytes() == original
    assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.content_hash
    with Image.open(result.path) as generated:
        assert generated.format == "WEBP"
        assert generated.size == expected


def test_transparency_and_animation_first_frame(tmp_path: Path) -> None:
    transparent = tmp_path / "transparent.png"
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((2, 2, 8, 8), fill=(0, 255, 0, 128))
    image.save(transparent)
    result = convert_image(transparent, tmp_path / "alpha.webp", TransformProfile("small", 32))
    with Image.open(result.path) as output:
        assert output.mode == "RGBA"
        assert output.getpixel((0, 0))[3] < 10
        assert output.getpixel((4, 4))[3] > 100

    first = Image.new("RGB", (12, 12), "red")
    second = Image.new("RGB", (12, 12), "blue")
    animated = tmp_path / "animated.gif"
    first.save(animated, save_all=True, append_images=[second], duration=100, loop=0)
    info = inspect_image(animated)
    result = convert_image(animated, tmp_path / "first.webp", TransformProfile("small", 32))
    with Image.open(result.path) as output:
        assert info.frame_count == 2
        red, green, blue = output.convert("RGB").getpixel((5, 5))
        assert red > blue


def test_output_cannot_replace_source(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8)).save(source)
    before = source.read_bytes()
    with pytest.raises(ImageValidationError, match="must not overwrite"):
        convert_image(source, source, TransformProfile("small", 32))
    assert source.read_bytes() == before


def test_format_capabilities_are_explicit() -> None:
    capabilities = {item.format: item.can_decode for item in format_capabilities()}
    assert capabilities["JPEG"] and capabilities["PNG"] and capabilities["GIF"] and capabilities["WEBP"]
    assert "AVIF" in capabilities
