"""在独立临时目录运行完整生命周期；仅使用公开 media_kit 入口。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_kit import MediaStore, StoreConfig, URLMapping


def main() -> None:
    with TemporaryDirectory(prefix="media-kit-example-") as temporary:
        base = Path(temporary)
        source = base / "original.png"
        replacement = base / "replacement.png"
        Image.new("RGBA", (800, 450), (255, 0, 0, 128)).save(source)
        Image.new("RGB", (500, 900), "blue").save(replacement)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        config = StoreConfig(
            root=base / "media",
            state_dir=base / "state",
            urls=URLMapping("/media/src/", "/media/thu/"),
        )
        with MediaStore.init(config) as store:
            asset = store.save(source, key="articles/42/cover.png", idempotency_key="upload-42")
            replay = store.save(source, key="articles/42/cover.png", idempotency_key="upload-42")
            assert replay.asset_id == asset.asset_id
            result = store.process(asset.asset_id, timeout=10)
            assert not result.timed_out and result.asset.processing_status == "ready"
            assert result.asset.original_path is not None
            assert store.local_path(result.asset.original_path).read_bytes() == source.read_bytes()
            assert len(store.resolve_many([asset.asset_id, "missing"])) == 2
            assert store.resolve_many([asset.asset_id, "missing"])[1] is None
            assert store.list(prefix="articles/", page=1, page_size=10).total == 1
            store.retry(asset.asset_id)
            assert store.tasks(asset.asset_id)
            updated = store.replace(
                asset.asset_id,
                replacement,
                expected_revision=asset.revision,
                idempotency_key="replace-42",
            )
            assert updated.asset_id == asset.asset_id and updated.revision == 2
            # 独立 worker 路径：逐档处理，空队列即退出。
            while store.worker_once(asset_id=asset.asset_id):
                pass
            assert store.resolve(asset.asset_id).processing_status == "ready"
            deleted = store.delete(
                asset.asset_id, expected_revision=updated.revision, idempotency_key="delete-42"
            )
            assert deleted.status == "deleted"
            assert store.get(asset.asset_id).original_path is None
            store.cleanup()
        with MediaStore.readonly(config) as store:
            assert not store.check().missing_files
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        print("生命周期完成：原图保留、缩略图生成、批量/分页、替换、重试、删除与清理均通过")


if __name__ == "__main__":
    main()
