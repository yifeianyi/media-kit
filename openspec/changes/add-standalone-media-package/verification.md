# 验证记录

验证日期：2026-10-26。对象：`add-standalone-media-package`，schema 为 `spec-driven`。本次在独立包的集成 worktree 验收，参考项目不参与安装或写入。

| 维度 | 结果 |
| --- | --- |
| 完整性 | 39/39 项任务完成；6 个能力、49 条新增要求均有实现或文档证据 |
| 正确性 | 80 个规格场景已按下表映射检查；全套 188 项测试通过，wheel/sdist 和定制安装验收通过 |
| 一致性 | 目录职责、显式初始化、不可变版本、快照指纹、租约发布、逐文件恢复、文档同源打包与设计一致 |

## 需求与证据映射

| 能力 | 实现/文档 | 验证入口 |
| --- | --- | --- |
| managed-media-storage（8 条/13 场景） | `config.py`、`storage/`、`MediaStore.init/open/readonly/check`；配置与存储手册 | `tests/test_config.py`、`test_paths.py`、`test_store.py`；安装探针静态服务检查 |
| media-asset-lifecycle（9 条/19 场景） | `api.py`、SQLite operations/idempotency；API、集成与生命周期手册 | `tests/test_store.py`、`test_recovery_boundaries.py`；`examples/lifecycle.py` |
| image-variants（8 条/13 场景） | `transforms/`、`models.py`、配方快照与指纹；定制手册 | `tests/test_transforms.py`、`test_runtime.py`、`test_recovery_boundaries.py` |
| media-task-runtime（8 条/16 场景） | `tasks/runtime.py`、`process/worker/retry/rebuild/tasks`；运维手册 | `tests/test_runtime.py`；`tools/installed_probe.py` 真实 worker 进程终止和租约恢复 |
| media-package-distribution（8 条/10 场景） | 公开入口/类型、CLI、构建、MIT/NOTICE、版本说明 | `test_import.py`、`test_cli.py`、`test_distribution.py`；两个发行验收工具 |
| media-package-documentation（8 条/9 场景） | README、11 篇离线手册、3 个示例、AGENTS 与资源清单 | `test_documentation.py`；wheel 与 sdist 安装环境中的资源 hash、相对链接、例子、CLI 和类型检查 |

缓存撤回限制、宿主授权与备份操作规程按明确文档契约检查；不把单机测试解释为浏览器/CDN 撤回保证或跨库事务保证。源码和安装包均无需参考仓库及聊天上下文。

## 已执行检查

- `.venv/bin/pytest -q`：188 passed。
- `.venv/bin/ruff check src tests tools examples` 与 `ruff format --check`：通过。
- `.venv/bin/mypy src/media_kit`：17 个源文件通过。
- `openspec validate add-standalone-media-package --strict` 与 `git diff --check`：通过。
- `uv run --python .venv/bin/python tools/verify_distribution.py --artifacts /tmp/media-kit-delivery --report /tmp/media-kit-delivery/acceptance.json`：wheel 与由 sdist 重建的 wheel 分别在干净环境安装；源码树外验证 19 项离线资源、公开类型、导入副作用、完整生命周期、独立转换、静态访问、worker 重启、CLI 及文档。
- `uv run --python .venv/bin/python tools/verify_customization.py`：临时 fork 将 LANCZOS 改为 BICUBIC，并更新算法与公开发行版本；188 项 fork 测试、mypy、Ruff、格式检查、wheel/sdist 构建均通过。另建环境非 editable 安装 wheel，验证行为/指纹、源码与 metadata 版本一致以及随包生命周期/转换示例。

审查发现的登记丢失、旧回收与当前重建冲突、已回收路径误接管、标准文件流输入及定制验收缺口已修复，并在最终版本复验。没有遗留阻断项或已知规格偏差；本记录不执行归档或远程发布。

## 实测范围

本机实测 Python 3.11.14、Pillow 13.0.0、Linux 本地文件系统。声明 Python 3.11+、Pillow >=11,<14；配置的 CI 矩阵为 3.11/3.15，本次未执行远程 CI，不声称已实测全部声明组合。fcntl 与 SQLite 构成单机协调；Windows、网络文件系统、多主机共享及已有媒体目录接管不在首版保证内。
