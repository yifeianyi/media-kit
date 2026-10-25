from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image
from pytest import CaptureFixture

from media_kit.api import MediaStore
from media_kit.cli import main
from media_kit.config import StoreConfig


def _config_file(tmp_path: Path) -> Path:
    config = tmp_path / "media.toml"
    config.write_text(
        f"[storage]\nroot = {json.dumps(str(tmp_path / 'media'))}\n"
        f"state_dir = {json.dumps(str(tmp_path / 'state'))}\n"
        "[thumbnails]\nsizes = [32, 64]\n",
        encoding="utf-8",
    )
    return config


def test_help_and_docs_need_no_store(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    assert main(["docs", "--path"]) == 0
    assert Path(capsys.readouterr().out.strip()).joinpath("index.md").is_file()
    result = subprocess.run(
        [sys.executable, "-m", "media_kit.cli", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "worker" in result.stdout and "status" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_cli_failure_is_structured_and_does_not_initialize(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config = _config_file(tmp_path)
    assert main(["--config", str(config), "status"]) == 2
    result = json.loads(capsys.readouterr().err)
    assert result["error"]["type"] == "StorageCompatibilityError"
    assert not (tmp_path / "media").exists() and not (tmp_path / "state").exists()
    assert main(["check"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["type"] == "ConfigurationError"


def test_cli_init_queries_and_worker(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_file = _config_file(tmp_path)
    prefix = ["--config", str(config_file)]
    assert main([*prefix, "init"]) == 0
    assert json.loads(capsys.readouterr().out)["initialized"] is True
    source = tmp_path / "image.png"
    Image.new("RGB", (100, 60), "red").save(source)
    with MediaStore.open(StoreConfig.from_toml(config_file)) as store:
        asset = store.save(source)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert main([*prefix, "status", asset.asset_id]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["asset"]["asset_id"] == asset.asset_id
    assert len(result["tasks"]) == 2
    assert main([*prefix, "check"]) == 0
    assert json.loads(capsys.readouterr().out)["missing_files"] == []
    after = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert main([*prefix, "worker", "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 2
    assert main([*prefix, "worker", "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 0
    assert main([*prefix, "retry", asset.asset_id]) == 0
    assert json.loads(capsys.readouterr().out)["processing_status"] == "ready"
    assert main([*prefix, "rebuild", asset.asset_id, "--profile", "32"]) == 0
    assert json.loads(capsys.readouterr().out)["asset_id"] == asset.asset_id
    assert main([*prefix, "recover"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)
    assert main([*prefix, "cleanup"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_cli_rejects_invalid_worker_options(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_file = _config_file(tmp_path)
    prefix = ["--config", str(config_file)]
    assert main([*prefix, "init"]) == 0
    capsys.readouterr()
    assert main([*prefix, "worker", "--poll-interval", "0"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["type"] == "ConfigurationError"


def test_cli_reports_file_check_and_recovery_failures(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    config_file = _config_file(tmp_path)
    config = StoreConfig.from_toml(config_file)
    prefix = ["--config", str(config_file)]
    source = tmp_path / "image.png"
    Image.new("RGB", (80, 50), "green").save(source)
    with MediaStore.init(config) as store:
        first = store.save(source)
        old_path = store.local_path(first.original_path)
        store.replace(first.asset_id, source, expected_revision=1)
        old_path.unlink()

    assert main([*prefix, "check"]) == 2
    assert json.loads(capsys.readouterr().out)["missing_files"] == [first.original_path]
    assert main([*prefix, "cleanup"]) == 2
    assert json.loads(capsys.readouterr().out)["failed"] == 1
    assert main([*prefix, "recover"]) == 2
    assert json.loads(capsys.readouterr().out)["failed"] == 1

    old_path.write_bytes(source.read_bytes())
    assert main([*prefix, "cleanup"]) == 0
    assert json.loads(capsys.readouterr().out)["failed"] == 0
