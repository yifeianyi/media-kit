# 验证记录

验证日期：2026-10-26。对象：`add-standalone-media-package`，schema 为 `spec-driven`。验证基线为 `21bdc50`；第 8 节验证后续修正与本次记录作为其后的本地提交（未推送）。参考项目不参与安装或写入，本记录不执行归档或远程发布。

| 维度 | 结果 |
| --- | --- |
| 完整性 | 46/46 项任务完成；6 个能力、49 条新增要求均有实现或文档证据；`media_kit.__all__` 的 34 个公开名均可在随包文档检索到 |
| 正确性 | 80 个规格场景已按下表映射检查；全套 189 项测试通过，wheel/sdist 和定制安装验收通过 |
| 一致性 | 目录职责、显式初始化、不可变版本、快照指纹、租约发布、逐文件恢复、文档同源打包与设计一致 |

## 需求与证据映射

| 能力 | 实现/文档 | 验证入口 |
| --- | --- | --- |
| managed-media-storage（8 条/13 场景） | `config.py`、`storage/`、`MediaStore.init/open/readonly/check`；配置与存储手册 | `tests/test_config.py`、`test_paths.py`、`test_store.py`；安装探针静态服务检查 |
| media-asset-lifecycle（9 条/19 场景） | `api.py`、SQLite operations/idempotency；API、集成与生命周期手册 | `tests/test_store.py`、`test_recovery_boundaries.py`；`examples/lifecycle.py` |
| image-variants（8 条/13 场景） | `transforms/`、`models.py`、配方快照与指纹；定制手册与能力查询说明 | `tests/test_transforms.py`、`test_runtime.py`、`test_recovery_boundaries.py` |
| media-task-runtime（8 条/16 场景） | `tasks/runtime.py`、`process/worker/retry/rebuild/tasks`；运维手册 | `tests/test_runtime.py`（含同步等待与独立 worker 同请求竞争）；`tools/installed_probe.py` 真实 worker 进程终止和租约恢复 |
| media-package-distribution（8 条/10 场景） | 公开入口/类型、CLI、构建、MIT/NOTICE、版本说明 | `test_import.py`、`test_cli.py`、`test_distribution.py`；两个发行验收工具 |
| media-package-documentation（8 条/9 场景） | README、11 篇离线手册、3 个示例、AGENTS 与资源清单 | `test_documentation.py`；wheel 与 sdist 安装环境中的资源 hash、相对链接、例子、CLI 和类型检查 |

缓存撤回限制、宿主授权与备份操作规程按明确文档契约检查；不把单机测试解释为浏览器/CDN 撤回保证或跨库事务保证。源码和安装包均无需参考仓库及聊天上下文。

## 第 8 节验证后续修正

针对 verify-change 发现的公开 API 文档覆盖与发行检查缺口完成修正并复验：

| 任务 | 修正内容 | 复验入口 |
| --- | --- | --- |
| 8.1 | `docs/API.md` 补 `format_capabilities` 能力查询与 `TransformResult` 字段；`docs/configuration.md` 补 `StoreConfig.from_mapping`；`docs/index.md` 补 `documentation.list_documents` | 公开导出文档覆盖检查（34/34 可检索）、安装环境 mypy 与离线链接检查 |
| 8.2 | WebP quality 范围改 0–100；`save`/`replace` 的 `source` 补 `str` 路径；`docs/upgrading.md` 来源记录链接改指 `NOTICE.md` | `test_documentation.py`、`test_distribution.py` 链接检查与本地链接全量检查 |
| 8.3 | 新增 `src/media_kit/storage/__init__.py`，子包形态与 `persistence`/`tasks`/`transforms` 一致 | ruff、mypy（18 个源文件）、wheel/sdist archive 检查均含 storage 模块 |
| 8.4 | `tools/distribution_checks.py` 的 sdist 期望资源加入 `README.en.md` | `tools/verify_distribution.py`：wheel 与 sdist 重建安装各含 20 项离线资源 |
| 8.5 | 新增 `test_process_and_independent_worker_contend_for_one_lease`，轮询已提交 claim 而非依赖时序事件，避免抖动 | `tests/test_runtime.py`；定制 fork 环境中同样通过 |
| 8.6 | README、README.en.md 与 `docs/development.md` 声明发行/定制验收脚本需要 `uv` | 文档链接检查与安装环境验收 |
| 8.7 | 刷新本验证记录 | 本文件 |

## 已执行检查

- `.venv/bin/pytest -q`：189 passed。
- `.venv/bin/ruff check src tests tools examples` 与 `ruff format --check`：通过。
- `.venv/bin/mypy src/media_kit`：18 个源文件通过。
- `openspec validate add-standalone-media-package --strict` 与 `git diff --check`：通过。
- `.venv/bin/python tools/verify_distribution.py --artifacts /tmp/media-kit-apply-delivery --report /tmp/media-kit-apply-delivery/acceptance.json --python .venv/bin/python`：wheel 与由 sdist 重建的 wheel 分别在源码树外干净环境安装；两种发行物均验证 20 项离线资源 hash、公开类型、导入副作用、完整生命周期、独立转换、静态访问、worker 重启、只读 CLI 及错误退出码。
- `.venv/bin/python tools/verify_customization.py`：临时 fork 将 LANCZOS 改为 BICUBIC 并更新算法与公开发行版本；fork 全套 189 项测试、mypy、Ruff、格式检查、wheel/sdist 构建均通过；非 editable 安装后验证行为/指纹、源码与 metadata 版本一致及随包示例。

## 实测范围

本机实测 Python 3.11.14、Pillow 13.0.0、Linux 本地文件系统。声明 Python 3.11+、Pillow >=11,<14；配置的 CI 矩阵为 3.11/3.15，本次未执行远程 CI，不声称已实测全部声明组合。fcntl 与 SQLite 构成单机协调；Windows、网络文件系统、多主机共享及已有媒体目录接管不在首版保证内。
