"""Validated configuration for an explicitly managed media store."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from media_kit.errors import ConfigurationError
from media_kit.models import Profile
from media_kit.transforms.fingerprint import validate_profiles


@dataclass(frozen=True, slots=True)
class URLMapping:
    """Optional public URL prefixes; omitted mappings produce no URL."""

    original_base: str | None = None
    variant_base: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("original_base", self.original_base),
            ("variant_base", self.variant_base),
        ):
            if value is not None:
                if (
                    not isinstance(value, str)
                    or not value
                    or "\\" in value
                    or any(character.isspace() or ord(character) < 32 for character in value)
                    or "?" in value
                    or "#" in value
                ):
                    raise ConfigurationError(f"{label} must be a non-empty URL path prefix")
                if not value.endswith("/"):
                    raise ConfigurationError(f"{label} must end with '/'")
                try:
                    parsed = urlsplit(value)
                    hostname = parsed.hostname
                    _ = parsed.port  # Access validates malformed/out-of-range ports.
                except ValueError as error:
                    raise ConfigurationError(
                        f"{label} is not a valid URL prefix: {error}"
                    ) from error
                if parsed.scheme:
                    if parsed.scheme not in {"http", "https"} or not hostname:
                        raise ConfigurationError(f"{label} must be an HTTP(S) URL or root path")
                elif not value.startswith("/") or value.startswith("//"):
                    raise ConfigurationError(f"{label} must be an HTTP(S) URL or root path")


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """Filesystem and transformation configuration recorded at initialization."""

    root: Path
    state_dir: Path
    original_dir: str = "src"
    variant_dir: str = "thu"
    profiles: tuple[Profile, ...] = field(
        default_factory=lambda: (
            Profile(name="320", size=320),
            Profile(name="640", size=640),
            Profile(name="1280", size=1280),
        )
    )
    max_pixels: int = 40_000_000
    max_bytes: int = 20 * 1024 * 1024
    urls: URLMapping = field(default_factory=URLMapping)

    def __post_init__(self) -> None:
        try:
            root = Path(self.root).expanduser()
            state = Path(self.state_dir).expanduser()
        except (TypeError, ValueError, RuntimeError) as error:
            raise ConfigurationError(f"invalid storage root or state_dir: {error}") from error
        if not root.is_absolute() or not state.is_absolute():
            raise ConfigurationError("storage root and state_dir must be absolute paths")
        if "\x00" in str(root) or "\x00" in str(state):
            raise ConfigurationError("storage root and state_dir must not contain NUL")
        try:
            root = root.resolve()
            state = state.resolve()
        except (OSError, RuntimeError) as error:
            raise ConfigurationError(
                f"cannot resolve storage root or state_dir: {error}"
            ) from error
        if _within(root, state) or _within(state, root):
            raise ConfigurationError(
                "storage root and state_dir must not overlap or contain one another"
            )
        _validate_relative_directory(self.original_dir, "original_dir")
        _validate_relative_directory(self.variant_dir, "variant_dir")
        original = PurePosixPath(self.original_dir)
        variants = PurePosixPath(self.variant_dir)
        if _posix_within(original, variants) or _posix_within(variants, original):
            raise ConfigurationError(
                "original_dir and variant_dir must not overlap or contain one another"
            )
        profiles = validate_profiles(self.profiles)
        _positive_integer(self.max_pixels, "max_pixels")
        _positive_integer(self.max_bytes, "max_bytes")
        if not isinstance(self.urls, URLMapping):
            raise ConfigurationError("urls must be a URLMapping")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "state_dir", state)
        object.__setattr__(self, "profiles", profiles)

    @property
    def normalized_root(self) -> Path:
        return self.root

    @property
    def normalized_state_dir(self) -> Path:
        return self.state_dir

    @classmethod
    def from_toml(cls, path: str | Path) -> StoreConfig:
        try:
            with Path(path).open("rb") as stream:
                document = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigurationError(f"cannot read configuration: {error}") from error
        return cls.from_mapping(document)

    @classmethod
    def from_mapping(cls, document: dict[str, Any]) -> StoreConfig:
        try:
            storage = _section(document["storage"], "storage")
            thumbnails = _section(document.get("thumbnails", {}), "thumbnails")
            urls = _section(document.get("urls", {}), "urls")
            profiles = _profiles_from(thumbnails)
            return cls(
                root=Path(storage["root"]),
                state_dir=Path(storage["state_dir"]),
                original_dir=storage.get("original_dir", "src"),
                variant_dir=storage.get("variant_dir", "thu"),
                profiles=profiles,
                max_pixels=_positive_integer(
                    thumbnails.get("max_pixels", 40_000_000), "max_pixels"
                ),
                max_bytes=_positive_integer(
                    thumbnails.get("max_bytes", 20 * 1024 * 1024), "max_bytes"
                ),
                urls=URLMapping(urls.get("original_base"), urls.get("variant_base")),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ConfigurationError(f"invalid media configuration: {error}") from error


def _profiles_from(thumbnails: dict[str, Any]) -> tuple[Profile, ...]:
    raw_profiles = thumbnails.get("profiles")
    if raw_profiles is None:
        sizes = thumbnails.get("sizes", (320, 640, 1280))
        if not isinstance(sizes, (list, tuple)):
            raise ConfigurationError("thumbnails.sizes must be an array of integers")
        raw_profiles = [{"name": str(size), "size": size} for size in sizes]
    if not isinstance(raw_profiles, list):
        raise ConfigurationError("thumbnails.profiles must be an array of tables")
    defaults = {
        "format": thumbnails.get("format", "webp"),
        "quality": thumbnails.get("quality", 80),
        "method": thumbnails.get("method", 4),
        "algorithm_version": thumbnails.get("algorithm_version", "webp-v1"),
    }
    result: list[Profile] = []
    for item in raw_profiles:
        if not isinstance(item, dict):
            raise ConfigurationError("each thumbnail profile must be a table")
        result.append(
            Profile(
                name=_string(item["name"], "profile name"),
                size=_positive_integer(item["size"], "profile size"),
                format=_string(item.get("format", defaults["format"]), "profile format"),
                quality=_integer(item.get("quality", defaults["quality"]), "profile quality"),
                method=_integer(item.get("method", defaults["method"]), "profile method"),
                algorithm_version=_string(
                    item.get("algorithm_version", defaults["algorithm_version"]),
                    "algorithm_version",
                ),
            )
        )
    return tuple(result)


def _validate_relative_directory(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be a safe relative directory")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ConfigurationError(f"{label} must be a safe relative directory")
    if path.parts[0].casefold() in {".media-kit-private", ".media-kit.json"}:
        raise ConfigurationError(f"{label} overlaps a reserved media-kit path")


def _within(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _posix_within(parent: PurePosixPath, child: PurePosixPath) -> bool:
    return child == parent or parent in child.parents


def _section(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a TOML table")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ConfigurationError(f"{label} must be an integer")
    return value


def _positive_integer(value: Any, label: str) -> int:
    number = _integer(value, label)
    if number <= 0:
        raise ConfigurationError(f"{label} must be a positive integer")
    return number


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be a string")
    return value
