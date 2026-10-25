"""Validate offline resources and examples from an installed wheel/sdist environment.

Run this script with the interpreter of a clean, non-editable installation. It refuses
to fall back to the source tree, so missing package data cannot be hidden by checkout files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from importlib import metadata, resources
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlsplit

REQUIRED_DOCS = {
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
REQUIRED_EXAMPLES = {"lifecycle.py", "convert.py", "static_server.py"}


def fail(message: str) -> NoReturn:
    raise SystemExit(f"发行物检查失败: {message}")


def main() -> int:
    package_location = __import__("media_kit").__file__
    if package_location is None:
        fail("media_kit 缺少可验证的包文件位置")
    package_file = Path(package_location).resolve()
    site_roots = [Path(value).resolve() for value in sys.path if value and Path(value).is_dir()]
    if not any(package_file.is_relative_to(root) for root in site_roots):
        fail(f"media_kit 不是从安装目录加载: {package_file}")
    distribution = metadata.distribution("media-kit")
    direct_url = distribution.read_text("direct_url.json")
    if direct_url and json.loads(direct_url).get("dir_info", {}).get("editable"):
        fail("不能从 editable 安装检查发行物资源")

    package = resources.files("media_kit")
    docs = package.joinpath("docs")
    examples = package.joinpath("examples")
    if not docs.is_dir() or not examples.is_dir():
        fail("wheel/sdist 安装缺少 docs 或 examples 包资源")
    doc_names = {item.name for item in docs.iterdir() if item.name.endswith(".md")}
    example_names = {item.name for item in examples.iterdir() if item.name.endswith(".py")}
    if difference := sorted(REQUIRED_DOCS - doc_names):
        fail(f"缺少文档: {difference}")
    if difference := sorted(REQUIRED_EXAMPLES - example_names):
        fail(f"缺少示例: {difference}")
    if not package.joinpath("py.typed").is_file():
        fail("缺少 py.typed 类型标记")
    files = tuple(distribution.files or ())
    if not any(path.as_posix().endswith(".dist-info/licenses/LICENSE") for path in files):
        fail("安装元数据未包含项目许可证")
    if not any(path.as_posix().endswith(".dist-info/licenses/NOTICE.md") for path in files):
        fail("安装元数据未包含 NOTICE.md 来源声明")

    for item in docs.iterdir():
        if not item.name.endswith(".md"):
            continue
        content = item.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            relative = unquote(parsed.path)
            if not docs.joinpath(relative).is_file() and not examples.joinpath(relative).is_file():
                fail(f"{item.name} 存在无效发行物链接: {target}")

    for script in ("lifecycle.py", "convert.py"):
        with tempfile.TemporaryDirectory(prefix="media-kit-dist-check-") as directory:
            result = subprocess.run(
                [sys.executable, str(examples.joinpath(script))],
                cwd=directory,
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode:
                fail(f"{script} 无法在源码树外运行:\n{result.stdout}{result.stderr}")
    result = subprocess.run(
        [sys.executable, "-m", "media_kit.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode or "worker" not in result.stdout or "status" not in result.stdout:
        fail("安装后的 CLI --help 无法运行或命令不完整")
    print(f"发行物资源完整：{len(doc_names)} 篇文档，{len(example_names)} 个示例")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
