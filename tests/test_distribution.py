from __future__ import annotations

from pathlib import Path

from tools.distribution_checks import (
    EXAMPLES,
    ROOT_RESOURCES,
    archive_errors,
    markdown_link_errors,
    required_resources,
    resource_errors,
)


def test_source_resources_and_local_navigation_are_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    source_resources = required_resources() - {"offline-manifest.json", "py.typed"}
    assert source_resources <= files
    assert not resource_errors(root, installed=False)
    assert not markdown_link_errors(root)


def test_examples_are_pure_python_files() -> None:
    root = Path(__file__).resolve().parents[1]
    assert all((root / "examples" / name).is_file() for name in EXAMPLES)
    assert all((root / filename).is_file() for filename in ROOT_RESOURCES if filename != "py.typed")


def test_distribution_checker_reports_absent_wheel(tmp_path: Path) -> None:
    archive = tmp_path / "missing.whl"
    archive.touch()
    errors = archive_errors(archive)
    assert errors == ["missing.whl: invalid wheel archive"]
