"""Copy authoritative offline resources into build output, never into src/."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class ResourceBuildPy(build_py):
    """Build code normally and add this release's offline knowledge resources."""

    def run(self) -> None:
        super().run()
        source = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / "media_kit"
        for directory in ("docs", "examples"):
            target = destination / directory
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(
                source / directory,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
            )
        for filename in ("README.md", "README.en.md", "AGENTS.md", "NOTICE.md", "LICENSE"):
            shutil.copy2(source / filename, destination / filename)
        resources = [destination / "py.typed"]
        resources.extend(
            destination / name
            for name in ("README.md", "README.en.md", "AGENTS.md", "NOTICE.md", "LICENSE")
        )
        for directory in ("docs", "examples"):
            resources.extend(
                path for path in (destination / directory).rglob("*") if path.is_file()
            )
        manifest = {
            "version": self.distribution.get_version(),
            "files": {
                path.relative_to(destination).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(resources)
            },
        }
        (destination / "offline-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


setup(cmdclass={"build_py": ResourceBuildPy})
