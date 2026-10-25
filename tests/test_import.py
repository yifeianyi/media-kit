"""Core import remains side-effect free and exposes typed compatibility metadata."""

import importlib
from pathlib import Path


def test_import_does_not_create_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("media_kit")
    assert module.VERSION == "0.1.0"
    assert not list(tmp_path.iterdir())


def test_public_metadata_and_errors_are_exported() -> None:
    import media_kit

    assert media_kit.STORAGE_LAYOUT_VERSION == 1
    assert media_kit.SCHEMA_VERSION == 1
    assert issubclass(media_kit.ConfigurationError, ValueError)
    assert issubclass(media_kit.RevisionConflictError, media_kit.AssetConflictError)
