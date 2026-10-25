from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path

import pytest
from PIL import Image

from media_kit import InputValidationError, Profile, TransformRecipe
from media_kit.transforms import convert_thumbnail, fingerprint, inspect_image, recipe_for


def test_convert_landscape_preserves_ratio_and_original(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (1600, 900), "red").save(source)
    before = source.read_bytes()
    output = tmp_path / "variants" / "320.webp"
    result = convert_thumbnail(source, output, Profile("320", 320))
    assert (result.width, result.height) == (320, 180)
    assert output.is_file()
    assert source.read_bytes() == before


def test_small_transparent_image_is_not_upscaled(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGBA", (100, 60), (255, 0, 0, 80)).save(source)
    output = tmp_path / "out.webp"
    convert_thumbnail(source, output, Profile("320", 320))
    with Image.open(output) as image:
        assert image.size == (100, 60)
        assert "A" in image.getbands()


def test_animation_uses_first_frame(tmp_path: Path) -> None:
    source = tmp_path / "animated.gif"
    first = Image.new("RGB", (32, 24), "red")
    second = Image.new("RGB", (32, 24), "blue")
    first.save(source, save_all=True, append_images=[second], duration=100, loop=0)
    result = convert_thumbnail(source, tmp_path / "out.webp", Profile("small", 64))
    assert (result.width, result.height) == (32, 24)
    with Image.open(result.path) as output:
        assert output.format == "WEBP"
        assert not getattr(output, "is_animated", False)
        assert output.convert("RGB").getpixel((5, 5))[0] > 150


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    result = convert_thumbnail(source, tmp_path / "out.webp", Profile("small", 100))
    assert (result.width, result.height) == (20, 40)


@pytest.mark.parametrize("contents", [b"not an image", b""])
def test_rejects_invalid_image_without_output(tmp_path: Path, contents: bytes) -> None:
    source = tmp_path / "fake.png"
    source.write_bytes(contents)
    output = tmp_path / "out.webp"
    with pytest.raises(InputValidationError):
        convert_thumbnail(source, output, Profile("small", 100))
    assert not output.exists()
    assert not list(tmp_path.glob(".media-kit-*"))


def test_rejects_byte_and_pixel_limits(tmp_path: Path) -> None:
    source = tmp_path / "large.png"
    Image.new("RGB", (100, 100)).save(source)
    with pytest.raises(InputValidationError, match="byte limit"):
        inspect_image(source, max_bytes=4)
    with pytest.raises(InputValidationError, match="pixel limit"):
        inspect_image(source, max_pixels=10)


def test_inspect_seekable_stream_and_reset(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8)).save(buffer, format="PNG")
    buffer.seek(3)
    info = inspect_image(buffer)
    assert info.format == "PNG"
    assert buffer.tell() == 0


def test_recipe_fingerprint_is_canonical_and_includes_algorithm() -> None:
    profile = Profile("small", 64, algorithm_version="custom-v1")
    recipe = recipe_for(profile)
    assert fingerprint(recipe) == fingerprint(TransformRecipe(**asdict(recipe)))
    assert fingerprint(recipe) != fingerprint(
        recipe_for(Profile("small", 64, algorithm_version="custom-v2"))
    )


def test_extension_must_match_actual_content(tmp_path: Path) -> None:
    source = tmp_path / "disguised.jpg"
    Image.new("RGB", (20, 10)).save(source, format="PNG")
    with pytest.raises(InputValidationError, match="extension"):
        convert_thumbnail(source, tmp_path / "variants" / "out.webp", Profile("small", 10))
    assert not (tmp_path / "variants").exists()


def test_unknown_extension_is_an_invalid_claim(tmp_path: Path) -> None:
    source = tmp_path / "image.txt"
    Image.new("RGB", (20, 10)).save(source, format="PNG")
    with pytest.raises(InputValidationError, match="extension"):
        inspect_image(source)


def test_extensionless_staging_file_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "input"
    Image.new("RGB", (20, 10)).save(source, format="PNG")
    assert inspect_image(str(source)).format == "PNG"


def test_declared_format_must_match_actual_content(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10)).save(source)
    assert inspect_image(source, declared_format="image/png").format == "PNG"
    with pytest.raises(InputValidationError, match="declared"):
        inspect_image(source, declared_format="JPEG")


def test_truncated_jpeg_rejected_after_structural_verify(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    Image.new("RGB", (128, 128), "red").save(source)
    source.write_bytes(source.read_bytes()[:-8])
    with pytest.raises(InputValidationError):
        convert_thumbnail(source, tmp_path / "out.webp", Profile("small", 64))
    assert not (tmp_path / "out.webp").exists()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_output_cannot_alias_input(tmp_path: Path, alias: str) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10), "red").save(source)
    before = source.read_bytes()
    output = source if alias == "same" else tmp_path / "alias.webp"
    if alias == "symlink":
        output.symlink_to(source)
    elif alias == "hardlink":
        output.hardlink_to(source)
    with pytest.raises(InputValidationError, match="overwrite"):
        convert_thumbnail(source, output, Profile("small", 10))
    assert source.read_bytes() == before


def test_open_file_input_cannot_be_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10), "red").save(source)
    with source.open("rb") as stream:
        with pytest.raises(InputValidationError, match="overwrite"):
            convert_thumbnail(stream, source, Profile("small", 10))
        assert not stream.closed


def test_failure_preserves_existing_output_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from media_kit import ProcessingError

    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10), "red").save(source)
    output = tmp_path / "out.webp"
    output.write_bytes(b"previous output")
    before = source.read_bytes()

    def fail_save(*args: object, **kwargs: object) -> None:
        raise OSError("injected encoder failure")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    with pytest.raises(ProcessingError, match="encoder failure"):
        convert_thumbnail(source, output, Profile("small", 10))
    assert output.read_bytes() == b"previous output"
    assert source.read_bytes() == before
    assert not list(tmp_path.glob(".media-kit-*"))


def test_default_profiles_decode_original_independently(tmp_path: Path) -> None:
    from media_kit import TransformConfig

    source = tmp_path / "source.png"
    Image.new("RGB", (1600, 800), "red").save(source)
    original = source.read_bytes()
    config = TransformConfig()
    results = [
        convert_thumbnail(source, tmp_path / f"{p.name}.webp", p, config) for p in config.profiles
    ]
    assert [(result.width, result.height) for result in results] == [
        (320, 160),
        (640, 320),
        (1280, 640),
    ]
    assert source.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "1280.webp",
        "320.webp",
        "640.webp",
        "source.png",
    ]


def test_transparent_animation_uses_transparent_first_frame(tmp_path: Path) -> None:
    source = tmp_path / "animated.gif"
    first = Image.new("RGBA", (32, 24), (0, 0, 0, 0))
    first.putpixel((8, 8), (255, 0, 0, 255))
    second = Image.new("RGBA", (32, 24), (0, 0, 255, 255))
    first.save(
        source,
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
        transparency=0,
        disposal=2,
    )
    original = source.read_bytes()
    result = convert_thumbnail(source, tmp_path / "out.webp", Profile("small", 64))
    with Image.open(result.path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((8, 8))[0] > 150
    assert source.read_bytes() == original


def test_format_capabilities_are_queryable_without_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_kit.transforms import format_capabilities

    monkeypatch.chdir(tmp_path)
    capabilities = format_capabilities()
    assert capabilities["PNG"]["decode"] is True
    assert capabilities["WEBP"]["encode"] is True
    assert capabilities["JPEG"]["encode"] is False
    assert not list(tmp_path.iterdir())


def test_profile_and_policy_validation_precedes_output_creation(tmp_path: Path) -> None:
    from media_kit import ConfigurationError, TransformConfig

    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10)).save(source)
    output = tmp_path / "not-created" / "out.webp"
    with pytest.raises(ConfigurationError):
        convert_thumbnail(source, output, Profile("../unsafe", 10))
    with pytest.raises(ConfigurationError):
        convert_thumbnail(source, output, Profile("small", 10, method=7))
    with pytest.raises(ConfigurationError):
        convert_thumbnail(source, output, Profile("small", 10), TransformConfig(max_bytes=True))
    assert not output.parent.exists()


@pytest.mark.parametrize(
    "changed",
    [
        {"size": 65},
        {"quality": 79},
        {"method": 3},
        {"algorithm_version": "custom-v2"},
    ],
)
def test_each_output_parameter_changes_fingerprint(changed: dict[str, object]) -> None:
    from dataclasses import replace

    original = recipe_for(Profile("card", 64, algorithm_version="custom-v1"))
    assert fingerprint(original) != fingerprint(replace(original, **changed))


def test_recipe_fingerprint_normalizes_output_format_and_ignores_profile_name() -> None:
    first = recipe_for(Profile("first", 64, format="WEBP"))
    second = recipe_for(Profile("second", 64, format="webp"))
    assert first.format == "webp"
    assert fingerprint(first) == fingerprint(second)


def test_failed_publication_keeps_previous_output_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from media_kit import ProcessingError
    from media_kit.transforms import converter

    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10)).save(source)
    output = tmp_path / "out.webp"
    output.write_bytes(b"previous output")

    def fail_replace(*args: object) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(converter.os, "replace", fail_replace)
    with pytest.raises(ProcessingError, match="publication failure"):
        convert_thumbnail(source, output, Profile("small", 10))
    assert output.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".media-kit-*"))


def test_failed_profile_does_not_change_completed_profile(tmp_path: Path) -> None:
    from media_kit import ConfigurationError

    source = tmp_path / "source.png"
    Image.new("RGB", (160, 80), "red").save(source)
    success = tmp_path / "small.webp"
    convert_thumbnail(source, success, Profile("small", 32))
    before = success.read_bytes()
    with pytest.raises(ConfigurationError):
        convert_thumbnail(source, tmp_path / "broken.webp", Profile("broken", 64, quality=101))
    assert success.read_bytes() == before
    assert not (tmp_path / "broken.webp").exists()


def test_decode_budget_limits_total_animation_work(tmp_path: Path) -> None:
    source = tmp_path / "animated.gif"
    first = Image.new("RGB", (20, 20), "red")
    second = Image.new("RGB", (20, 20), "blue")
    first.save(source, save_all=True, append_images=[second], duration=100)
    with pytest.raises(InputValidationError, match="decoded pixel limit"):
        inspect_image(source, max_pixels=600)
