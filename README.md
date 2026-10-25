<div align="center">

# media-kit

**框架无关的本地图片托管与缩略图处理 Python 包**

保留原图 · 不可变版本 · 持久任务 · 崩溃可恢复

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pillow](https://img.shields.io/badge/Pillow-11%E2%80%9313-yellow)](https://python-pillow.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Typed](https://img.shields.io/badge/Typing-py.typed-blue)](https://peps.python.org/pep-0561/)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey)](#系统要求)

[简体中文](README.md) · [English](README.en.md)

</div>

`media-kit` 把「保存一张图片、生成多档缩略图，之后仍然能替换、删除和恢复」这套媒体侧逻辑收敛成一个独立 Python 包。它只使用本地文件系统与标准库 SQLite，不依赖任何 Web 框架，也不接管接入项目的业务表和授权逻辑。

原图字节完整保留；缩略图是独立的派生结果，可以异步生成；同步等待与独立 worker 共用同一套持久任务与租约。普通模块导入没有文件、数据库或后台进程副作用，`status` / `check` 等只读命令也不会写入存储。

> 当前版本 `0.1.0`，处于 Alpha 阶段；发行版本、磁盘布局版本、SQLite schema 版本与转换算法版本各自独立演进。

## 特性

| 能力 | 说明 |
| --- | --- |
| 原图不可变 | `save` 成功即原图完整安装并提交；`replace` 创建不可变的新 revision，历史文件不会被原地覆盖。 |
| 稳定的资产身份 | `asset_id` 是稳定且不复用的 UUID；逻辑 `key` 只用于业务标识，不决定物理文件名。 |
| 多档缩略图 | 默认最长边 320/640/1280 的 WebP（quality 80、method 4）；支持具名 profile，EXIF 定向、透明保留、动图取首帧、小图不放大。 |
| 可识别的派生身份 | 输出参数与算法版本参与配方指纹，指纹进入结果路径；改变输出行为必须更新算法版本，不会复用旧身份冒充新结果。 |
| 持久任务与租约 | 同步 `process()` 与独立 worker 共用同一队列；领取带 claim token、租约和心跳，进程崩溃后由后续 worker 接管，过期任务不会发布。 |
| 幂等重试 | `save` / `replace` / `delete` 支持幂等键与请求摘要，网络重试可以安全重放，不把旧成功操作误认成新变更。 |
| 安全边界 | `root`、`state_dir`、公开原图/派生目录与私有 staging/quarantine 分离；拒绝符号链接越界，不公开整个 root 或状态目录。 |
| 可恢复删除 | 删除按 `active → deleting → deleted` 推进；全部受管历史版本移出公开目录后才报告 `deleted`，私有隔离文件另有独立回收状态。 |
| 只读核对 | `check()` 报告缺失文件、内容 hash 不匹配与未知文件；`recover()` 只推进包已经记录的中断操作，不接管未知文件。 |
| 发行友好 | wheel/sdist 随附离线文档、可运行示例、`py.typed` 类型标记、CLI 与许可/来源声明；安装后 `media-kit docs --path` 可直接定位手册。 |

## 适用场景

- 单个 Python 项目在本机 POSIX/Linux 文件系统上托管自己的媒体目录。
- 需要稳定的资产身份、不可变 revision，以及可恢复的替换/删除语义。
- 希望缩略图在后台生成、进程重启后仍能继续；或先用同步接口，之后再拆分独立 worker。
- 不准备为媒体引入额外服务、云 SDK 或另一套业务数据库。

首版**不包含**：接管已有媒体目录、视频转码、对象存储/CDN 集成、多主机共享存储、Windows 支持、管理 UI，以及业务授权与引用检查（由宿主负责）。边界细节见[接入项目](docs/integration.md)与[升级与兼容性](docs/upgrading.md)。

## 系统要求

- Python 3.11+（CI 覆盖 3.11 与 3.15）
- Pillow 11–13
- 支持 SQLite、`fcntl` 文件锁和原子重命名的本地 POSIX/Linux 文件系统

## 安装

项目尚未发布到 PyPI，请从源码或本地构建产物安装：

```bash
python -m pip install .

# 或先构建 wheel 再安装
python -m pip install build
python -m build --wheel
python -m pip install dist/media_kit-0.1.0-py3-none-any.whl
```

安装后可通过 CLI 自检：

```bash
media-kit --help
media-kit docs --path
```

## 快速开始

下面的片段会创建临时目录和测试图片，完整走通「初始化 → 保存 → 生成缩略图 → 读取地址」，不会触碰已有媒体目录：

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_kit import MediaStore, StoreConfig, URLMapping

with TemporaryDirectory() as temporary:
    base = Path(temporary)
    source = base / "cover.png"
    Image.new("RGB", (800, 600), "steelblue").save(source)

    config = StoreConfig(
        root=base / "media",
        state_dir=base / "state",
        urls=URLMapping("/media/src/", "/media/thu/"),
    )
    with MediaStore.init(config) as store:
        asset = store.save(source, key="articles/42/cover.png")
        result = store.process(asset.asset_id, timeout=10)

        print(result.asset.processing_status)  # ready
        print(result.asset.original_url)       # /media/src/<asset_id>/1/cover.png
        for variant in result.asset.variants:
            print(variant.profile, variant.width, variant.height, variant.url)
```

`save` 成功后原图已经完整安装、资产与任务已经提交；缩略图可能仍是 pending。`process` 超时只结束调用方等待，原图和任务保留，本地执行上下文或后续 worker 继续处理。原图是否可用与缩略图是否就绪分开表达，不要把等待缩略图成功作为识别原图提交的唯一依据。

### 生产配置

```toml
# media.toml
[storage]
root = "/srv/example/media"
state_dir = "/var/lib/example/media"
original_dir = "src"
variant_dir = "thu"

[thumbnails]
sizes = [320, 640, 1280]
format = "webp"
quality = 80
method = 4
max_pixels = 40000000
max_bytes = 20971520
algorithm_version = "webp-v1"

[urls]
original_base = "/media/src/"
variant_base = "/media/thu/"
```

```bash
media-kit --config media.toml init       # 显式初始化全新空目录
media-kit --config media.toml status     # 只读查询
media-kit --config media.toml worker --once
```

`root` 与 `state_dir` 必须为绝对路径且互不相同、互不包含。生产部署建议使用 TOML 配置与独立 worker，详见[配置](docs/configuration.md)与[运维](docs/operations.md)。

## 目录布局

```text
<root>/
  .media-kit.json
  src/<asset_id>/<revision>/<filename>
  thu/<asset_id>/<revision>/<recipe_fingerprint>/<profile>.webp
  .media-kit-private/
    staging/<operation_id>/
    quarantine/<operation_id>/
<state_dir>/
  store.json
  media.sqlite3
```

静态服务只应映射原图目录与派生目录，绝不公开整个 `root`、`state_dir` 或 `.media-kit-private`。包不承诺撤回浏览器或 CDN 的既有缓存，缓存失效策略由宿主决定。

## 核心概念

| 概念 | 含义 |
| --- | --- |
| `asset_id` | 稳定且不复用的资产 UUID，是查询与业务关联的唯一标识。 |
| `revision` | 从 1 开始递增的不可变原图版本；替换即使字节相同也会递增。 |
| `key` | 可选的业务逻辑路径，仅用于冲突判定与列表筛选，不是物理路径。 |
| `profile` | 具名缩略图档位：最长边、格式、quality、method、算法版本。 |
| 配方指纹 | 影响输出的参数与 `algorithm_version` 的 SHA-256，是结果路径的一部分。 |
| 状态维度 | 资产生命周期 `active/deleting/deleted` 与处理状态 `pending/processing/ready/partial/failed` 相互独立。 |
| 任务与租约 | 持久任务记录 revision、profile、配方快照、尝试次数和错误；worker 按租约领取，发布前复核 token、资产状态与当前请求。 |
| 版本身份 | `VERSION`、`STORAGE_LAYOUT_VERSION`、`SCHEMA_VERSION`、`ALGORITHM_VERSION` 各自独立，升级时分别评估兼容性。 |

## 命令行

| 命令 | 作用 |
| --- | --- |
| `media-kit --config <toml> init` | 显式初始化全新媒体目录 |
| `media-kit --config <toml> status [asset_id]` | 只读查询资产与持久任务，可按 `--prefix` 筛选、`--page` 分页 |
| `media-kit --config <toml> check` | 只读核对登记文件、hash 与未知文件 |
| `media-kit --config <toml> worker --once` | 排空当前可领取任务后退出，可配合 `--asset-id` |
| `media-kit --config <toml> worker --poll-interval 1` | 持续 worker 模式 |
| `media-kit --config <toml> retry <asset_id>` | 沿用当前请求快照重试失败或缺失档位 |
| `media-kit --config <toml> rebuild <asset_id> --profile <name>` | 按当前配置显式请求新配方，`--profile` 可重复 |
| `media-kit --config <toml> recover` | 显式推进包已记录的中断操作 |
| `media-kit --config <toml> cleanup` | 回收历史版本与旧配方文件，并清理私有隔离区 |
| `media-kit docs --path` | 定位随包安装的离线文档 |

CLI 成功输出 JSON 并退出 0；预期错误向 stderr 输出含 `error.type` / `error.message` 的 JSON 并退出 2；中断退出 130。`--config` 必须放在子命令之前。

## 文档

完整离线文档随 wheel 与 sdist 安装，不需要网络或源码目录。

| 主题 | 入口 |
| --- | --- |
| 文档索引 | [docs/index.md](docs/index.md) |
| 第一次接入、完整示例 | [快速入门](docs/quickstart.md) |
| Python/TOML、目录布局与 URL | [配置](docs/configuration.md) |
| 方法、返回值、错误与重试 | [Python API](docs/API.md) |
| 版本、文件状态与删除 | [存储与生命周期](docs/storage-and-lifecycle.md) |
| 业务数据库、授权、静态服务 | [接入项目](docs/integration.md) |
| worker、核对、恢复、备份 | [运维](docs/operations.md) |
| 安装升级与磁盘/schema 兼容 | [升级与兼容性](docs/upgrading.md) |
| 修改源码、测试与构建 | [源码开发](docs/development.md) |
| 自定义档位、算法与 fork | [定制与重新打包](docs/customization.md) |
| 给接入方与维护 agent 的导航 | [agent 导航](docs/agent-guide.md) |

安装后可运行随包示例：

```bash
python -c "from media_kit.documentation import examples_path; print(examples_path())"
# 用打印出的目录替换 <examples>
python <examples>/lifecycle.py
python <examples>/convert.py
python <examples>/static_server.py --help
```

## 开发与测试

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests examples tools
.venv/bin/python -m mypy
.venv/bin/python -m build

# 在源码树外校验 wheel 与由 sdist 重建的安装
.venv/bin/python tools/verify_distribution.py --python python3.11
```

源码组织：`src/media_kit/`（API、配置、存储、持久化、任务与转换）、`tests/`、`examples/`、`docs/`（离线文档唯一正文来源）、`tools/`（发行与定制验收）。修改源码前请阅读 [AGENTS.md](AGENTS.md) 与[源码开发](docs/development.md)；测试必须使用独立临时 `root`/`state_dir`，不读写真实媒体。

## 版本与兼容性

- `VERSION`：发行版本，当前 `0.1.0`。
- `STORAGE_LAYOUT_VERSION` / `SCHEMA_VERSION`：磁盘布局与 SQLite schema 版本，当前均为 `1`，初始化后固定。
- `ALGORITHM_VERSION`：默认转换算法 `webp-v1`；改变输出行为必须使用新版本号。

`open` / `readonly` 会校验实例标记与数据库版本；较新且不兼容的存储会抛出 `StorageCompatibilityError` 并停止写入。普通打开不会隐式建表或迁移 schema；首版从全新实例开始，不提供自动迁移命令。升级、备份与恢复步骤见[运维](docs/operations.md)和[升级与兼容性](docs/upgrading.md)。

## 贡献

欢迎 issue、讨论与 pull request。提交前请：

1. 阅读 [AGENTS.md](AGENTS.md) 与[源码开发](docs/development.md)，保留既有公开接口契约。
2. 为行为变更补充契约测试；修改转换行为时同步更新 `algorithm_version` 与测试。
3. 保证 `pytest`、`ruff`、`mypy` 通过，并在需要时运行发行验收脚本。
4. 文档正文只维护 `docs/`；新增或复制第三方材料前先核实权属与许可并更新 `NOTICE.md`。

## 许可证与来源

本项目自身代码以 [MIT License](LICENSE) 发布。第三方依赖保留各自许可；参考材料仅用于行为研究，未直接复制源码，详见[来源与许可记录](NOTICE.md)。

分发名为 `media-kit`、导入名为 `media_kit`、命令名为 `media-kit`；对外发布到包注册表前请核验名称占用。fork 与重新打包见[定制与重新打包](docs/customization.md)。
