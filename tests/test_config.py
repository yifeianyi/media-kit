from pathlib import Path

import pytest

from media_kit import ConfigurationError, Profile, StoreConfig


def config(tmp_path: Path, **kwargs) -> StoreConfig:
    return StoreConfig(root=tmp_path / "media", state_dir=tmp_path / "state", **kwargs)


def test_default_config_and_profiles(tmp_path: Path) -> None:
    value = config(tmp_path)
    assert value.original_dir == "src"
    assert [profile.size for profile in value.profiles] == [320, 640, 1280]


def test_rejects_nested_root_and_state(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        StoreConfig(root=tmp_path / "media", state_dir=tmp_path / "media" / "state")


@pytest.mark.parametrize(
    "field,value",
    [("original_dir", "../source"), ("variant_dir", "/absolute"), ("original_dir", "src\\outside")],
)
def test_rejects_unsafe_relative_directory(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        config(tmp_path, **{field: value})


def test_rejects_duplicate_profiles(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        config(tmp_path, profiles=(Profile("small", 100), Profile("small", 200)))


def test_from_mapping_accepts_named_profiles(tmp_path: Path) -> None:
    value = StoreConfig.from_mapping(
        {
            "storage": {"root": str(tmp_path / "media"), "state_dir": str(tmp_path / "state")},
            "thumbnails": {"profiles": [{"name": "card", "size": 400, "quality": 75}]},
            "urls": {"original_base": "/assets/"},
        }
    )
    assert value.profiles[0].name == "card"
    assert value.profiles[0].quality == 75
    assert value.urls.original_base == "/assets/"


@pytest.mark.parametrize(
    "profile",
    [
        Profile("../bad", 10),
        Profile("", 10),
        Profile("bad/name", 10),
        Profile("bad\\name", 10),
        Profile("small", 0),
        Profile("small", -1),
        Profile("small", True),
        Profile("small", 1.5),
        Profile("small", 10, quality=-1),
        Profile("small", 10, quality=101),
        Profile("small", 10, quality=True),
        Profile("small", 10, method=-1),
        Profile("small", 10, method=7),
        Profile("small", 10, method=2.5),
        Profile("small", 10, format="png"),
        Profile("small", 10, algorithm_version=""),
    ],
)
def test_invalid_profile_is_rejected_before_storage_exists(
    tmp_path: Path, profile: Profile
) -> None:
    with pytest.raises(ConfigurationError):
        config(tmp_path, profiles=(profile,))
    assert not (tmp_path / "media").exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_bytes", 0),
        ("max_pixels", -1),
        ("max_pixels", True),
        ("max_bytes", 3.5),
    ],
)
def test_resource_limits_must_be_positive_integers(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ConfigurationError):
        config(tmp_path, **{field: value})


def test_normalized_paths_cannot_hide_nesting(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="overlap"):
        StoreConfig(
            root=tmp_path / "media", state_dir=tmp_path / "other" / ".." / "media" / "state"
        )


def test_symlink_paths_cannot_hide_nesting(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (tmp_path / "alias").symlink_to(media, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="overlap"):
        StoreConfig(root=media, state_dir=tmp_path / "alias" / "state")


@pytest.mark.parametrize("section", ["storage", "thumbnails", "urls"])
def test_mapping_sections_must_be_tables(tmp_path: Path, section: str) -> None:
    document = {"storage": {"root": str(tmp_path / "media"), "state_dir": str(tmp_path / "state")}}
    document[section] = []
    with pytest.raises(ConfigurationError):
        StoreConfig.from_mapping(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", "10"),
        ("size", True),
        ("quality", 1.5),
        ("method", "4"),
        ("name", 10),
    ],
)
def test_mapping_does_not_silently_coerce_invalid_profile_types(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    profile = {"name": "card", "size": 100, field: value}
    with pytest.raises(ConfigurationError):
        StoreConfig.from_mapping(
            {
                "storage": {"root": str(tmp_path / "media"), "state_dir": str(tmp_path / "state")},
                "thumbnails": {"profiles": [profile]},
            }
        )


def test_toml_configuration_is_executable_and_writes_no_storage(tmp_path: Path) -> None:
    settings = tmp_path / "media.toml"
    settings.write_text(
        f'[storage]\nroot = "{tmp_path / "media"}"\nstate_dir = "{tmp_path / "state"}"\n'
        '[[thumbnails.profiles]]\nname = "card"\nsize = 400\nquality = 0\nmethod = 6\n'
        '[urls]\nvariant_base = "https://cdn.example.test/thumbnails/"\n',
        encoding="utf-8",
    )
    value = StoreConfig.from_toml(settings)
    assert value.profiles[0].quality == 0
    assert value.urls.variant_base == "https://cdn.example.test/thumbnails/"
    assert not value.root.exists()
    assert not value.state_dir.exists()


@pytest.mark.parametrize(
    "prefix",
    [
        "relative/",
        "//cdn.example.test/images/",
        "file:///images/",
        "https:///images/",
        "/images/?q=1",
        "/images/#fragment",
        "/im ages/",
        "/images/\x00",
        "/images",
    ],
)
def test_rejects_invalid_url_prefix(tmp_path: Path, prefix: str) -> None:
    from media_kit import URLMapping

    with pytest.raises(ConfigurationError):
        config(tmp_path, urls=URLMapping(original_base=prefix))


@pytest.mark.parametrize(
    "directory",
    [
        ".media-kit-private",
        ".media-kit-private/images",
        ".media-kit.json",
        ".media-kit.json/images",
    ],
)
def test_public_directories_cannot_overlap_reserved_paths(tmp_path: Path, directory: str) -> None:
    with pytest.raises(ConfigurationError, match="reserved"):
        config(tmp_path, original_dir=directory)
    with pytest.raises(ConfigurationError, match="reserved"):
        config(tmp_path, variant_dir=directory)


@pytest.mark.parametrize(
    "prefix", ["https://[invalid/", "https://example.test:invalid/", "http://:/"]
)
def test_malformed_url_raises_configuration_error(tmp_path: Path, prefix: str) -> None:
    from media_kit import URLMapping

    with pytest.raises(ConfigurationError):
        config(tmp_path, urls=URLMapping(variant_base=prefix))
