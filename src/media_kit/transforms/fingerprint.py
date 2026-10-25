"""Canonical transform recipe identity used for task and variant isolation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict

from media_kit.errors import ConfigurationError
from media_kit.models import Profile, TransformRecipe

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_profiles(profiles: Iterable[Profile]) -> tuple[Profile, ...]:
    """Validate named recipes before accepting any work or creating files."""
    try:
        result = tuple(profiles)
    except TypeError as error:
        raise ConfigurationError("profiles must be an iterable of Profile values") from error
    if not result:
        raise ConfigurationError("at least one thumbnail profile is required")
    names: set[str] = set()
    for profile in result:
        if not isinstance(profile, Profile):
            raise ConfigurationError("each thumbnail profile must be a Profile")
        if not isinstance(profile.name, str) or not _PROFILE_NAME.fullmatch(profile.name):
            raise ConfigurationError("profile names must be safe single path segments")
        if profile.name in names:
            raise ConfigurationError(f"duplicate profile name: {profile.name}")
        names.add(profile.name)
        recipe_for(profile)
    return result


def validate_recipe(recipe: TransformRecipe) -> TransformRecipe:
    """Return the canonical supported snapshot, rejecting impossible recipes."""
    if type(recipe.size) is not int or recipe.size <= 0:
        raise ConfigurationError("profile size must be a positive integer")
    if type(recipe.quality) is not int or not 0 <= recipe.quality <= 100:
        raise ConfigurationError("WebP quality must be an integer between 0 and 100")
    if type(recipe.method) is not int or not 0 <= recipe.method <= 6:
        raise ConfigurationError("WebP method must be an integer between 0 and 6")
    if not isinstance(recipe.format, str) or recipe.format.lower() != "webp":
        raise ConfigurationError("only WebP output is supported")
    if (
        not isinstance(recipe.algorithm_version, str)
        or not recipe.algorithm_version.strip()
        or "\x00" in recipe.algorithm_version
    ):
        raise ConfigurationError("algorithm_version must be a non-empty string without NUL")
    if (
        recipe.orientation != "exif-transpose"
        or recipe.animation != "first-frame"
        or recipe.scaling != "fit-no-upscale"
    ):
        raise ConfigurationError("unsupported orientation, animation, or scaling rule")
    return TransformRecipe(
        size=recipe.size,
        format="webp",
        quality=recipe.quality,
        method=recipe.method,
        orientation=recipe.orientation,
        animation=recipe.animation,
        scaling=recipe.scaling,
        algorithm_version=recipe.algorithm_version,
    )


def recipe_for(profile: Profile) -> TransformRecipe:
    return validate_recipe(
        TransformRecipe(
            size=profile.size,
            format=profile.format,
            quality=profile.quality,
            method=profile.method,
            algorithm_version=profile.algorithm_version,
        )
    )


def fingerprint(recipe: TransformRecipe) -> str:
    payload = json.dumps(
        asdict(validate_recipe(recipe)), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
