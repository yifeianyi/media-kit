"""Exercise an installed artifact while the current directory is outside its source tree."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.resources
import json
import selectors
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from distribution_checks import assert_valid, resource_errors


def run(*arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, text=True, capture_output=True, check=False, timeout=60)
    if result.returncode != expected:
        raise RuntimeError(
            f"command failed ({result.returncode}): {arguments}\n{result.stdout}{result.stderr}"
        )
    return result


def snapshot(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def cli(config: Path, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    return run(
        str(Path(sys.executable).parent / "media-kit"),
        "--config",
        str(config),
        *arguments,
        expected=expected,
    )


def verify_static_and_worker(package: Path, base: Path) -> None:
    from PIL import Image

    from media_kit import MediaStore, StoreConfig, URLMapping

    config_file = base / "media.toml"
    root, state = base / "public", base / "state"
    config_file.write_text(
        f'[storage]\nroot = "{root}"\nstate_dir = "{state}"\noriginal_dir = "originals"\n'
        'variant_dir = "thumbnails"\n[[thumbnails.profiles]]\nname = "card"\nsize = 48\n'
        '[urls]\noriginal_base = "/origin/"\nvariant_base = "/thumb/"\n',
        encoding="utf-8",
    )
    source = base / "source image.png"
    Image.new("RGBA", (96, 64), (255, 0, 0, 100)).save(source)
    config = StoreConfig(
        root=root,
        state_dir=state,
        original_dir="originals",
        variant_dir="thumbnails",
        profiles=StoreConfig.from_toml(config_file).profiles,
        urls=URLMapping("/origin/", "/thumb/"),
    )
    with MediaStore.init(config) as store:
        asset = store.save(source)
        assert store.tasks(asset.asset_id)[0].status == "pending"
    # Simulate a terminated worker using a short lease, without editing its database.
    interrupted = base / "interrupted_worker.py"
    interrupted.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "from media_kit import MediaStore,StoreConfig\nimport media_kit.tasks.runtime as runtime\n"
        "runtime.LEASE_SECONDS=0.2\n"
        'def blocked(*args, **kwargs):\n print("CLAIMED",flush=True)\n time.sleep(60)\n'
        "runtime.convert_thumbnail=blocked\n"
        "with MediaStore.open(StoreConfig.from_toml(Path(sys.argv[1]))) as store:\n"
        " store.worker_once(asset_id=sys.argv[2])\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, "-I", str(interrupted), str(config_file), asset.asset_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10) or child.stdout.readline().strip() != "CLAIMED":
                raise RuntimeError("interrupted worker did not acquire its task")
    finally:
        child.terminate()
        child.wait(timeout=5)
    time.sleep(0.25)
    cli(config_file, "recover")
    cli(config_file, "worker", "--once")
    with MediaStore.readonly(config) as store:
        resolved = store.resolve(asset.asset_id)
        assert resolved.processing_status == "ready"
        assert store.tasks(asset.asset_id)[0].attempts >= 2
        assert resolved.original_url and resolved.variants[0].url
    before = snapshot(root), snapshot(state)
    cli(config_file, "status", asset.asset_id)
    checked = json.loads(cli(config_file, "check").stdout)
    assert not checked["missing_files"] and not checked["content_mismatches"]
    assert before == (snapshot(root), snapshot(state)), "readonly CLI changed the store"
    # The API process has closed the store. Only installed example static routes serve it.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(package / "examples/static_server.py"),
            "--config",
            str(config_file),
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(
                    base_url + resolved.original_url, timeout=1
                ) as response:
                    assert response.read() == source.read_bytes()
                break
            except (OSError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "installed static example did not serve the original"
                    ) from None
                time.sleep(0.05)
        with urllib.request.urlopen(base_url + resolved.variants[0].url, timeout=2) as response:
            data = response.read()
            assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        for private_path in (
            "/",
            "/.media-kit.json",
            "/origin/",
            "/origin/../.media-kit.json",
            "/origin/%2e%2e/.media-kit.json",
            "/.media-kit-private/staging/",
        ):
            try:
                urllib.request.urlopen(base_url + private_path, timeout=2)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError(f"private path exposed: {private_path}")
        with MediaStore.open(config) as store:
            assert (
                store.delete(asset.asset_id, expected_revision=asset.revision).status == "deleted"
            )
            store.cleanup()
        try:
            urllib.request.urlopen(base_url + resolved.original_url, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("deleted original remains publicly available")
    finally:
        server.terminate()
        server.wait(timeout=5)
    missing = base / "missing.toml"
    missing.write_text(
        f'[storage]\nroot = "{base / "missing-root"}"\nstate_dir = "{base / "missing-state"}"\n'
    )
    failure = json.loads(cli(missing, "status", expected=2).stderr)
    assert failure["error"]["type"] == "StorageCompatibilityError"
    assert not (base / "missing-root").exists() and not (base / "missing-state").exists()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forbidden-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    cwd = Path.cwd()
    before = set(cwd.rglob("*")), threading.active_count()
    from PIL import __version__ as pillow_version

    import media_kit
    from media_kit.documentation import docs_path, examples_path, read_document

    assert before == (set(cwd.rglob("*")), threading.active_count()), "import has side effects"
    installed = Path(media_kit.__file__).resolve()
    assert not installed.is_relative_to(args.forbidden_root.resolve()), installed
    package = Path(str(importlib.resources.files("media_kit")))
    assert_valid(resource_errors(package))
    manifest = json.loads((package / "offline-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == importlib.metadata.version("media-kit") == media_kit.VERSION
    assert docs_path() == package / "docs" and examples_path() == package / "examples"
    assert "API" in read_document("index.md")
    executable = str(Path(sys.executable).parent / "media-kit")
    assert "worker" in run(executable, "--help").stdout
    assert Path(run(executable, "docs", "--path").stdout.strip()) == docs_path()
    with tempfile.TemporaryDirectory(prefix="media-kit-typecheck-") as typecheck_temp:
        sample = Path(typecheck_temp) / "consumer.py"
        sample.write_text(
            "from pathlib import Path\n"
            "from media_kit import (AssetPage, AssetView, ConfigurationError, ImageInfo, MediaStore,\n"
            "    Profile, StoreConfig, convert_thumbnail, fingerprint, inspect_image, recipe_for)\n"
            "config = StoreConfig(root=Path('/tmp/media'), state_dir=Path('/tmp/media-state'),\n"
            "    profiles=(Profile('card', 480),))\n"
            "with MediaStore.init(config) as store:\n"
            "    asset: AssetView = store.save(Path('input.png'))\n"
            "    page: AssetPage = store.list(page=1, page_size=10)\n"
            "    image: ImageInfo | None = asset.original\n"
            "    store.get(asset.asset_id)\n"
            "    store.resolve_many([asset.asset_id])\n"
            "    store.replace(asset.asset_id, Path('next.png'), expected_revision=asset.revision)\n"
            "    store.delete(asset.asset_id, expected_revision=asset.revision)\n"
            "    store.tasks(asset.asset_id)\n"
            "    store.retry(asset.asset_id)\n"
            "recipe = recipe_for(Profile('card', 480))\n"
            "fingerprint(recipe)\n"
            "inspect_image(Path('input.png'))\n"
            "convert_thumbnail(Path('input.png'), Path('output.webp'), Profile('card', 480))\n"
            "try:\n    raise ConfigurationError('sample')\n"
            "except ConfigurationError:\n    pass\n",
            encoding="utf-8",
        )
        run(sys.executable, "-m", "mypy", "--strict", "--no-incremental", str(sample))
    for example in ("lifecycle.py", "convert.py"):
        print(run(sys.executable, "-I", str(examples_path() / example)).stdout.strip(), flush=True)
    run(sys.executable, "-I", str(examples_path() / "static_server.py"), "--help")
    with tempfile.TemporaryDirectory(prefix="managed-media-") as temporary:
        verify_static_and_worker(package, Path(temporary))
    report = {
        "python": sys.version,
        "pillow": pillow_version,
        "package": media_kit.VERSION,
        "installed_path": str(installed),
        "resources": len(manifest["files"]),
        "checks": [
            "runtime-only dependency check",
            "installed API type check",
            "import side effects",
            "resource hashes",
            "offline links",
            "lifecycle example",
            "standalone example",
            "static HTTP example",
            "worker restart",
            "readonly CLI",
            "CLI error exits",
            "offline docs",
        ],
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
