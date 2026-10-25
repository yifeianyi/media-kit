# media-kit

面向 Python 项目的本地媒体托管包：复制并保留原图，生成缩略图，管理稳定资产身份、不可变内容版本、持久任务和可恢复删除。运行时为 Python 3.11+、Pillow 与标准库 SQLite；不要求使用任何 Web 框架。

接入项目初始化全新的媒体目录，随后通过本包管理其中的资产。原图目录、缩略图目录、独立状态目录与 URL 前缀均可配置。首版支持本地 POSIX/Linux；已有媒体目录接管、视频、对象存储和多主机任务不属于首版范围。

```bash
python -m pip install .
media-kit --help
media-kit docs --path
```

```python
from pathlib import Path
from media_kit import MediaStore, StoreConfig

config = StoreConfig(root=Path('/srv/example/media'), state_dir=Path('/var/lib/example/media'))
with MediaStore.init(config) as store:
    asset = store.save(Path('/tmp/photo.jpg'), key='articles/42/cover.jpg')
    result = store.process(asset.asset_id, timeout=10)
    print(result.asset.asset_id, result.asset.original_path, result.timed_out)
```

默认生成最长边 320、640、1280 的 WebP，quality=80、method=4，按 EXIF 定向、不放大小图、保留透明度、动画取首帧。原图保存成功与转换是否成功分开表达；没有 worker 时原图仍可用，任务保留。

从[文档索引](docs/index.md)开始，常用入口为[快速入门](docs/quickstart.md)、[配置](docs/configuration.md)、[API](docs/API.md)、[运维](docs/operations.md)和 [agent 导航](docs/agent-guide.md)。完整离线文档与示例随 wheel 和 sdist 安装，不依赖这个源码目录或网络。

源码采用 MIT 许可，欢迎 fork、修改与重新打包；见[二次开发](docs/customization.md)与[开发指南](docs/development.md)。许可与来源记录为 `LICENSE`、`NOTICE.md`。公开发布分发名称前应核验注册表占用；本项目的本地分发名为 `media-kit`，导入名为 `media_kit`。
