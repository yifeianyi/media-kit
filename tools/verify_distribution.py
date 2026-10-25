"""Build and verify wheel/sdist installs outside the checkout without a registry."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from .distribution_checks import archive_errors, assert_valid
else:
    from distribution_checks import archive_errors, assert_valid


def command(
    *arguments: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=300
    )
    if result.returncode:
        raise RuntimeError(f"command failed: {arguments}\n{result.stdout}\n{result.stderr}")
    return result


def environment_for(path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PATH"] = str(path / "bin") + os.pathsep + environment.get("PATH", "")
    return environment


def create_environment(path: Path, wheel: Path, python: str) -> Path:
    command(python, "-m", "venv", "--clear", str(path), cwd=path.parent)
    executable = path / "bin" / "python"
    command(
        str(executable),
        "-m",
        "pip",
        "install",
        str(wheel),
        cwd=path,
        env=environment_for(path),
    )
    command(str(executable), "-m", "pip", "check", cwd=path, env=environment_for(path))
    command(
        str(executable), "-m", "pip", "install", "mypy>=1.13", cwd=path, env=environment_for(path)
    )
    return executable


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--artifacts", type=Path, default=Path("dist"))
    parser.add_argument("--report", type=Path, default=Path("dist/acceptance.json"))
    args = parser.parse_args()
    requested_python = Path(args.python)
    if requested_python.exists():
        python = str(requested_python.resolve())
    else:
        python = shutil.which(args.python) or str(requested_python.resolve())
    project = Path(__file__).resolve().parents[1]
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    command("uv", "build", "--clear", "--out-dir", str(artifacts), "--python", python, cwd=project)
    wheels = sorted(artifacts.glob("media_kit-*.whl"))
    sdists = sorted(artifacts.glob("media_kit-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"expected one wheel and sdist; found {wheels}, {sdists}")
    wheel, sdist = wheels[0], sdists[0]
    assert_valid(archive_errors(wheel) + archive_errors(sdist))
    with (
        tempfile.TemporaryDirectory(prefix="media-kit-wheel-") as wheel_temp,
        tempfile.TemporaryDirectory(prefix="media-kit-sdist-") as source_temp,
        tempfile.TemporaryDirectory(prefix="media-kit-built-wheel-") as built_temp,
        tempfile.TemporaryDirectory(prefix="media-kit-venv-wheel-") as wheel_env,
        tempfile.TemporaryDirectory(prefix="media-kit-venv-sdist-") as sdist_env,
        tempfile.TemporaryDirectory(prefix="media-kit-outside-") as outside_temp,
    ):
        wheel_root, source_root, built_root, outside = map(
            Path, (wheel_temp, source_temp, built_temp, outside_temp)
        )
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(wheel_root)
        with tarfile.open(sdist, "r:gz") as archive:
            archive.extractall(source_root, filter="data")
        extracted = next(source_root.iterdir())
        command(
            "uv",
            "build",
            "--wheel",
            "--no-sources",
            "--out-dir",
            str(built_root),
            "--python",
            python,
            str(extracted),
            cwd=source_root,
        )
        sdist_wheels = sorted(built_root.glob("media_kit-*.whl"))
        if len(sdist_wheels) != 1:
            raise RuntimeError("sdist did not produce exactly one installable wheel")
        sdist_wheel = sdist_wheels[0]
        assert_valid(archive_errors(sdist_wheel))
        reports: list[dict[str, object]] = []
        for label, artifact, environment_path in (
            ("wheel", wheel, Path(wheel_env)),
            ("sdist", sdist_wheel, Path(sdist_env)),
        ):
            executable = create_environment(environment_path, artifact, python)
            report_path = Path(wheel_temp) / f"{label}-report.json"
            command(
                str(executable),
                "-I",
                str(project / "tools/installed_probe.py"),
                "--forbidden-root",
                str(project),
                "--report",
                str(report_path),
                cwd=outside,
                env=environment_for(environment_path),
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.update({"artifact": label, "dist": str(artifact), "outside_cwd": str(outside)})
            reports.append(report)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"checks": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(args.report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
