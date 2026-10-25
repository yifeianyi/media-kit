"""Offline resource and archive checks shared by unit tests and release tools."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

DOCUMENTS = (
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
)
EXAMPLES = ("lifecycle.py", "convert.py", "static_server.py")
ROOT_RESOURCES = ("README.md", "AGENTS.md", "NOTICE.md", "LICENSE", "py.typed")
_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+['\"][^)]*)?\)")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", re.MULTILINE)


def required_resources() -> set[str]:
    return {
        *(f"docs/{name}" for name in DOCUMENTS),
        *(f"examples/{name}" for name in EXAMPLES),
        *ROOT_RESOURCES,
        "offline-manifest.json",
    }


def markdown_link_errors(root: Path) -> list[str]:
    """Check relative inline/reference targets without contacting external sites."""
    errors: list[str] = []
    for document in sorted(root.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        for match in (*_LINK.finditer(text), *_REFERENCE.finditer(text)):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            destination = (document.parent / unquote(parsed.path)).resolve()
            if not destination.is_relative_to(root.resolve()):
                errors.append(
                    f"{document.relative_to(root)}: link escapes offline resources: {target}"
                )
            elif not destination.exists():
                errors.append(f"{document.relative_to(root)}: missing link target: {target}")
    return errors


def resource_errors(root: Path, *, installed: bool = True) -> list[str]:
    expected_resources = (
        required_resources()
        if installed
        else required_resources() - {"py.typed", "offline-manifest.json"}
    )
    errors = [
        f"missing resource: {name}"
        for name in sorted(expected_resources)
        if not (root / name).is_file()
    ]
    manifest_file = root / "offline-manifest.json"
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        files = manifest.get("files", {})
        for name in sorted(required_resources() - {"offline-manifest.json", "py.typed"}):
            if name not in files:
                errors.append(f"resource absent from manifest: {name}")
        for name, expected_hash in files.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append(f"resource manifest escapes package: {name}")
            elif not target.is_file():
                errors.append(f"manifest resource missing: {name}")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
                errors.append(f"manifest hash mismatch: {name}")
    errors.extend(markdown_link_errors(root))
    return errors


def archive_errors(archive: Path) -> list[str]:
    if archive.suffix == ".whl":
        try:
            with zipfile.ZipFile(archive) as wheel:
                names = set(wheel.namelist())
        except (OSError, zipfile.BadZipFile):
            return [f"{archive.name}: invalid wheel archive"]
        expected = {f"media_kit/{name}" for name in required_resources()}
    else:
        try:
            with tarfile.open(archive, "r:gz") as source:
                members = [name for name in source.getnames() if "/" in name]
        except (OSError, tarfile.TarError):
            return [f"{archive.name}: invalid source archive"]
        names = {name.split("/", 1)[1] for name in members}
        expected = {
            "pyproject.toml",
            "setup.py",
            "MANIFEST.in",
            "README.md",
            "AGENTS.md",
            "NOTICE.md",
            "LICENSE",
            "src/media_kit/py.typed",
            "src/media_kit/__init__.py",
            "tests/test_distribution.py",
            "tools/verify_distribution.py",
            "tools/verify_customization.py",
            *(f"docs/{name}" for name in DOCUMENTS),
            *(f"examples/{name}" for name in EXAMPLES),
        }
    errors = [f"{archive.name}: missing archive member {name}" for name in sorted(expected - names)]
    errors.extend(
        f"{archive.name}: unexpected development cache {name}"
        for name in sorted(names)
        if "__pycache__" in name or "/.venv/" in name or name.endswith((".pyc", ".pyo"))
    )
    return errors


def assert_valid(errors: list[str]) -> None:
    if errors:
        raise RuntimeError("\n".join(errors))
