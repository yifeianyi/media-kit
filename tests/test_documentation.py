from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from media_kit.documentation import docs_path, examples_path, list_documents, read_document

MANUALS = {
    "index.md",
    "quickstart.md",
    "configuration.md",
    "API.md",
    "storage-and-lifecycle.md",
    "integration.md",
    "operations.md",
    "upgrading.md",
    "development.md",
    "customization.md",
    "agent-guide.md",
}


def test_offline_documentation_has_complete_local_navigation() -> None:
    root = docs_path()
    assert MANUALS <= set(list_documents())
    assert "文档索引" in read_document()
    for name in list_documents():
        text = (root / name).read_text(encoding="utf-8")
        assert "/home/yifeianyi/" not in text
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            assert (root / unquote(parsed.path)).is_file(), f"{name}: {target}"


def test_document_reader_rejects_paths() -> None:
    for name in ("../README.md", "/etc/passwd.md", "docs/API.md", "API\\escape.md", "index.txt"):
        with pytest.raises(ValueError):
            read_document(name)


@pytest.mark.parametrize("name", ["lifecycle.py", "convert.py"])
def test_installed_example_runs_outside_source_tree(tmp_path: Path, name: str) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(examples_path() / name)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_static_example_help_is_available_without_server(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(examples_path() / "static_server.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and "--config" in result.stdout


def test_quickstart_python_example_is_executable(tmp_path: Path) -> None:
    code = re.search(r"```python\n(.*?)\n```", read_document("quickstart.md"), re.DOTALL)
    assert code is not None
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code.group(1)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []
