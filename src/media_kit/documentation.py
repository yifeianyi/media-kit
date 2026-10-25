"""Locate the offline documentation shipped with a media-kit distribution."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from media_kit.errors import StorageError


def _resource_directory(name: str) -> Path:
    resource = resources.files("media_kit").joinpath(name)
    # Normal wheel installs expose package resources as real filesystem paths.
    packaged = Path(str(resource))
    if packaged.is_dir():
        return packaged
    source_root = Path(__file__).resolve().parents[2]
    source_tree = source_root / name
    if (
        source_root.joinpath("pyproject.toml").is_file()
        and source_root.joinpath("src", "media_kit") == Path(__file__).resolve().parent
        and source_tree.is_dir()
    ):
        return source_tree
    raise StorageError(f"installed media-kit is missing its offline {name} resources")


def docs_path() -> Path:
    """Return the directory containing this release's Chinese offline manuals."""
    return _resource_directory("docs")


def examples_path() -> Path:
    """Return the directory containing this release's runnable examples."""
    return _resource_directory("examples")


def read_document(name: str = "index.md") -> str:
    """Read a single manual without allowing paths outside the manuals directory."""
    if not name.endswith(".md") or Path(name).name != name or "\\" in name:
        raise ValueError("document name must be a single Markdown filename")
    return (docs_path() / name).read_text(encoding="utf-8")


def list_documents() -> tuple[str, ...]:
    """List locally available manuals in a stable order."""
    return tuple(sorted(path.name for path in docs_path().glob("*.md")))
