# 源码开发

源码由 src/media_kit、tests、examples、docs 与 tools 组成；docs 是唯一文档正文，构建时复制到发行包资源。发行 metadata 支持 Python 3.11+、Pillow 11 到 13（Pillow>=11.0,<14）和本地 POSIX/Linux；CI 覆盖 Python 3.11 与最新稳定版 3.15。当前机器验证记录为 Python 3.11.14 + Pillow 13.0.0。跨进程写锁使用 fcntl，未声明 Windows 支持；运行机制为本地文件系统和 SQLite，不保证网络文件系统或多主机并发。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests examples tools
.venv/bin/python -m mypy
.venv/bin/python -m build
openspec validate add-standalone-media-package --strict
```

测试都应使用临时 root/state_dir，不读写调用方真实媒体。导入检查应在源码目录外、禁用 bytecode 写入的独立进程运行，断言模块导入没有创建目录、SQLite 或后台进程。类型检查针对公开导入与完整示例，不能依赖内部 SQL。

| 模块 | 职责与修改边界 |
| --- | --- |
| api.py | 公开生命周期、查询、同步等待与恢复编排 |
| config.py / models.py / errors.py | 配置、稳定结果与错误类型 |
| storage/ | 受管边界、文件复制/安装/隔离、URL 坐标 |
| persistence/ | schema、短事务和操作记录 |
| tasks/ | 持久领取、租约、心跳与发布校验 |
| transforms/ | 不依赖 store 的实际图片校验、转换和配方身份 |
| cli.py / documentation.py | 公共 API 命令与离线资源定位 |

新增行为要覆盖成功后置条件、输入失败无副作用、并发/崩溃边界、只读语义及发行资源。测试领取权丢失、同版本重建、替换和删除期间旧任务不可发布；仅测正常缩略图输出不够。

发布验收需要从 wheel 和由 sdist 构建的安装环境运行示例、CLI、类型检查、文档链接及资源完整性检查，并把工作目录切到源码树外。测试源码本地可读不能证明 wheel 带齐文档。打包后比较 docs、examples、py.typed、许可和来源声明；不要复制开发机绝对路径进资源。

本项目自身代码以 MIT 提供；重新使用第三方代码前核实权属和许可，把来源、版本、复用范围与署名要求写入 NOTICE.md。参考项目的行为调查不构成对其未明确许可源码的再许可授权。依赖的第三方许可保持各自适用条款。

修改输出行为的版本责任和可执行步骤见[customization](customization.md)。维护 agent 也应读取源码根 AGENTS.md；接入项目的 agent 从[导航](agent-guide.md)开始。

当前仓库具备 wheel/sdist 安装验收；两个验收脚本都用 `uv build` 构建发行物，需先安装 `uv`（CI 通过 `astral-sh/setup-uv` 提供）：

```bash
python tools/verify_distribution.py --python python3.11
```

脚本从源码构建两个发行物，在源码树外新建两个无用户 site-packages 的独立环境：一个直接安装 wheel，另一个从 sdist 再建 wheel。依赖由安装流程正常解析，离线资源与发行清单随后检查；并不把关闭网络下载当成离线文档验收。两种安装都会执行文档与哈希检查、完整生命周期和独立转换示例、公开 API 的 mypy 检查、CLI 帮助和错误码、只读命令不写入存储、静态 HTTP 范围及被中断 worker 的租约恢复。详细可复查报告写到 `dist/acceptance.json`。

```bash
python tools/verify_customization.py
```

此例在临时源码副本内把缩放规则从 LANCZOS 改为 BICUBIC，更新算法版本、distribution 名及源码/metadata 的本地版本后缀，同步临时版本测试；安装开发依赖并执行全套 pytest、mypy、Ruff 和格式检查后构建 wheel/sdist。脚本再用独立环境非 editable 安装定制 wheel，在源码树外核验行为、配方指纹、公开/metadata 版本及随包生命周期和转换示例；对照原版源码确认行为和身份变化。子命令均有 300 秒超时，不改当前 checkout，也不创建提交或上传发行物。

CI 在 Python 3.11 和 3.15 上运行全套测试和发行验收；本机执行 `python -V` 和 `python -c 'import PIL; print(PIL.__version__)'` 报告实际验证的解释器及 Pillow 版本。
