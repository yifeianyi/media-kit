# 快速入门

需要 Python 3.11 或更新版本、安装包声明范围内的 Pillow，以及支持 SQLite、fcntl 文件锁和原子重命名的本地 POSIX/Linux 文件系统。此版未声明 Windows、网络文件系统或多个主机共享存储支持。

```bash
python -m pip install ./media_kit-0.1.0-py3-none-any.whl
media-kit --help
media-kit docs --path
```

先用临时目录完成接入，再初始化自己的正式空目录。以下完整片段不依赖 Web 框架：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from media_kit import MediaStore, StoreConfig, URLMapping

with TemporaryDirectory() as directory:
    base = Path(directory)
    source = base / 'input.png'
    Image.new('RGBA', (800, 450), (255, 0, 0, 128)).save(source)
    config = StoreConfig(
        root=base / 'media', state_dir=base / 'state',
        urls=URLMapping('/media/src/', '/media/thu/'),
    )
    with MediaStore.init(config) as store:
        asset = store.save(source, key='articles/42/cover.png', idempotency_key='upload-42')
        result = store.process(asset.asset_id, timeout=10)
        assert not result.timed_out
        assert result.asset.processing_status == 'ready'
        assert source.read_bytes() == store.local_path(result.asset.original_path).read_bytes()
        print(store.resolve_many([asset.asset_id, 'missing']))
        print(store.list(page=1, page_size=10))
        updated = store.replace(asset.asset_id, source, expected_revision=asset.revision,
                                idempotency_key='replace-42')
        store.process(updated.asset_id, timeout=10)
        deletion = store.delete(updated.asset_id, expected_revision=updated.revision)
        assert deletion.status == 'deleted'
        store.cleanup()
```

`save` 成功后原图已经完整安装、资产与任务已经提交；缩略图可能仍为 pending。`process` 超时只结束调用方等待，原图和任务保留，本地执行上下文或后续 worker 继续处理。`with` 退出默认等待已开始的本地工作；需要快速退出时显式 `close(wait=False)`，让后续 worker 按租约接管。

安装后的完整示例：

```bash
python -c "from media_kit.documentation import examples_path; print(examples_path())"
# 把打印出的目录替换为下方 <examples>
python <examples>/lifecycle.py
python <examples>/convert.py
python <examples>/static_server.py --help
```

生产部署使用 [TOML 配置](configuration.md)与独立 [worker](operations.md)。业务关联、权限和浏览器/CDN 缓存策略见[接入边界](integration.md)。
