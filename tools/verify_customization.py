"""Validate a customized source tree and its independently installed wheel."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

if __package__:
    from .distribution_checks import archive_errors, assert_valid
else:
    from distribution_checks import archive_errors, assert_valid

FORK_DISTRIBUTION = "media-kit-fork-example"
FORK_VERSION = "0.1.0+fork.bicubic"
FORK_ALGORITHM = "fork-bicubic-v1"


def command(label: str, *arguments: str, cwd: Path, env: dict[str, str], timeout: int = 300) -> str:
    """Keep subprocess failures bounded and show useful output without build noise."""
    print(f"[定制验收] {label}", flush=True)
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{label}: exceeded {timeout} seconds") from error
    if result.returncode:
        raise RuntimeError(f"{label} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def environment_for(path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["PATH"] = str(path / "bin") + os.pathsep + environment.get("PATH", "")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def replace_exact(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(f"expected exactly one {old!r} in {path}")
    path.write_text(content.replace(old, new), encoding="utf-8")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="media-kit-fork-") as temporary:
        root = Path(temporary)
        fork, reference, outside = root / "fork", root / "reference", root / "outside"
        outside.mkdir()
        ignore = shutil.ignore_patterns(
            ".git",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "build",
            "dist",
            "*.egg-info",
        )
        shutil.copytree(project, fork, ignore=ignore)
        shutil.copytree(project, reference, ignore=ignore)

        replace_exact(
            fork / "src/media_kit/transforms/converter.py",
            "Image.Resampling.LANCZOS",
            "Image.Resampling.BICUBIC",
        )
        for path in (
            fork / "src/media_kit/models.py",
            fork / "src/media_kit/config.py",
            fork / "src/media_kit/_version.py",
        ):
            contents = path.read_text(encoding="utf-8")
            if "webp-v1" not in contents:
                raise RuntimeError(f"algorithm default not found in {path}")
            path.write_text(contents.replace("webp-v1", FORK_ALGORITHM), encoding="utf-8")
        replace_exact(
            fork / "pyproject.toml", 'name = "media-kit"', f'name = "{FORK_DISTRIBUTION}"'
        )
        replace_exact(fork / "pyproject.toml", 'version = "0.1.0"', f'version = "{FORK_VERSION}"')
        replace_exact(
            fork / "src/media_kit/_version.py", 'VERSION = "0.1.0"', f'VERSION = "{FORK_VERSION}"'
        )
        # Only the temporary fork changes its release-specific version assertion.
        replace_exact(
            fork / "tests/test_import.py",
            'assert module.VERSION == "0.1.0"',
            f'assert module.VERSION == "{FORK_VERSION}"',
        )

        development = root / "development-env"
        dev_env = environment_for(development)
        command(
            "建立定制源码环境",
            sys.executable,
            "-m",
            "venv",
            str(development),
            cwd=root,
            env=dev_env,
        )
        python = str(development / "bin/python")
        command(
            "安装定制源码与开发依赖",
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "-e",
            f"{fork}[dev]",
            cwd=root,
            env=dev_env,
        )
        test_output = command(
            "运行定制源码全套 pytest", python, "-m", "pytest", "-q", cwd=fork, env=dev_env
        )
        print(test_output.strip(), flush=True)
        command("检查定制源码类型", python, "-m", "mypy", "src/media_kit", cwd=fork, env=dev_env)
        command(
            "检查定制源码 Ruff",
            python,
            "-m",
            "ruff",
            "check",
            "src",
            "tests",
            "tools",
            "examples",
            cwd=fork,
            env=dev_env,
        )
        command(
            "检查定制源码格式",
            python,
            "-m",
            "ruff",
            "format",
            "--check",
            "src",
            "tests",
            "tools",
            "examples",
            cwd=fork,
            env=dev_env,
        )

        script = outside / "probe.py"
        script.write_text(
            textwrap.dedent("""\
            import contextlib
            import io
            import json
            import runpy
            import sys
            from importlib import metadata, resources
            from pathlib import Path
            from PIL import Image, ImageDraw
            import media_kit
            from media_kit import Profile, convert_thumbnail, fingerprint, recipe_for

            destination = Path(sys.argv[1])
            mode = sys.argv[2]
            profile = Profile('card', 63, quality=90)
            image = Image.new('RGB', (211, 167), 'white')
            draw = ImageDraw.Draw(image)
            for x in range(image.width):
                draw.line((x, 0, x, image.height),
                          fill=(x % 256, (x * 7) % 256, (x * 17) % 256))
            source = destination.with_suffix('.png')
            image.save(source)
            result = convert_thumbnail(source, destination, profile)
            report = {'hash': result.content_hash,
                      'fingerprint': fingerprint(recipe_for(profile)),
                      'version': media_kit.VERSION,
                      'algorithm': media_kit.ALGORITHM_VERSION,
                      'installed_path': str(Path(media_kit.__file__).resolve())}
            if mode == 'wheel':
                distribution_name, expected_version, expected_algorithm = sys.argv[3:6]
                distribution = metadata.distribution(distribution_name)
                assert media_kit.VERSION == media_kit.__version__ == expected_version
                assert distribution.version == expected_version
                assert profile.algorithm_version == media_kit.ALGORITHM_VERSION == expected_algorithm
                assert Path(media_kit.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
                direct_url = json.loads(distribution.read_text('direct_url.json') or '{}')
                assert not direct_url.get('dir_info', {}).get('editable')
                examples = resources.files('media_kit').joinpath('examples')
                report['examples'] = []
                for name in ('lifecycle.py', 'convert.py'):
                    example = Path(str(examples.joinpath(name))).resolve()
                    assert example.is_relative_to(Path(sys.prefix).resolve())
                    with contextlib.redirect_stdout(io.StringIO()):
                        runpy.run_path(str(example), run_name='__main__')
                    report['examples'].append(name)
            print(json.dumps(report))
        """),
            encoding="utf-8",
        )
        baseline = json.loads(
            command(
                "计算原始源码行为基线",
                python,
                str(script),
                str(outside / "baseline.webp"),
                "reference",
                cwd=outside,
                env={**dev_env, "PYTHONPATH": str(reference / "src")},
            )
        )
        if not Path(baseline["installed_path"]).is_relative_to(reference):
            raise RuntimeError("baseline did not load the unmodified reference source")
        customized = json.loads(
            command(
                "计算定制源码行为",
                python,
                str(script),
                str(outside / "editable.webp"),
                "editable",
                cwd=outside,
                env=dev_env,
            )
        )
        if not Path(customized["installed_path"]).is_relative_to(fork):
            raise RuntimeError("customized probe did not load the editable fork")
        if customized["hash"] == baseline["hash"]:
            raise RuntimeError("the customized interpolation did not change output pixels")
        if customized["fingerprint"] == baseline["fingerprint"]:
            raise RuntimeError("algorithm modification reused the original fingerprint")

        artifacts = root / "dist"
        command(
            "构建定制 wheel 与 sdist",
            "uv",
            "build",
            "--out-dir",
            str(artifacts),
            "--python",
            python,
            cwd=fork,
            env=dev_env,
        )
        wheels, sdists = sorted(artifacts.glob("*.whl")), sorted(artifacts.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError("expected exactly one customized wheel and sdist")
        assert_valid([error for item in (*wheels, *sdists) for error in archive_errors(item)])

        installation = root / "installation-env"
        installed_env = environment_for(installation)
        command(
            "建立独立发行物环境",
            sys.executable,
            "-m",
            "venv",
            str(installation),
            cwd=outside,
            env=installed_env,
        )
        installed_python = str(installation / "bin/python")
        command(
            "非 editable 安装定制 wheel",
            "uv",
            "pip",
            "install",
            "--python",
            installed_python,
            str(wheels[0]),
            cwd=outside,
            env=installed_env,
        )
        command(
            "检查发行物依赖", installed_python, "-m", "pip", "check", cwd=outside, env=installed_env
        )
        installed = json.loads(
            command(
                "验证已安装版本、行为、配方与随包示例",
                installed_python,
                "-I",
                str(script),
                str(outside / "wheel.webp"),
                "wheel",
                FORK_DISTRIBUTION,
                FORK_VERSION,
                FORK_ALGORITHM,
                cwd=outside,
                env=installed_env,
            )
        )
        for field in ("hash", "fingerprint", "version", "algorithm"):
            if installed[field] != customized[field]:
                raise RuntimeError(f"installed fork differs from validated source: {field}")
        print(
            json.dumps(
                {
                    "wheel": wheels[0].name,
                    "baseline": baseline,
                    "customized_installation": installed,
                    "checks": [
                        "pytest",
                        "mypy",
                        "ruff",
                        "format",
                        "wheel/sdist resources",
                        "non-editable installation",
                        "public/metadata versions",
                        "changed output and fingerprint",
                        "installed lifecycle and conversion",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
