# Media Kit AI 协作约定

用户当前任务的明确要求优先。保留用户和其他任务的修改及暂存状态，仅交付本任务范围；本地验证通过后按任务约定提交，不自行推送、发布或改写历史。

## 目录与权威资料

采用 `src/media_kit/` 布局，公开入口与类型以包入口、`models.py` 和 `errors.py` 为准。消费方正常接入只使用已记录的公开接口，不读取内部 SQL。

- `api.py`：组合托管存储与生命周期操作；`config.py`：Python/TOML 配置校验。
- `storage/`：受管路径、原图安装、隔离、文件核对与访问坐标。
- `transforms/`：独立图片校验及转换，不依赖 store、数据库或 worker。
- `persistence/`：本包 SQLite schema、事务、操作及幂等记录；`tasks/`：租约、任务执行与发布复核。
- `cli.py`：CLI 与公开 API 的适配；`tests/`、`examples/`、`tools/`：契约验证、可运行接入及发行检查。
- `docs/`：人类和 agent 离线文档唯一正文来源；构建时同步打包，不维护第二份正文。

修改前阅读当前 OpenSpec change 的 proposal、design、specs 和 tasks；已稳定能力以 `openspec/specs/` 为准。按需查阅 `docs/API.md`、`docs/configuration.md`、`docs/storage-and-lifecycle.md`、`docs/operations.md`、`docs/upgrading.md`、`docs/customization.md`、`docs/development.md` 与 `docs/agent-guide.md`。尚未交付的文件或命令不得描述为已经存在。

## 必须保持的不变量

- 普通模块导入无文件、数据库和后台进程副作用；初始化与恢复必须显式进入对应 API。
- 包只托管显式初始化的新媒体目录。root、root 外 state_dir、公开原图/派生目录和私有区保持安全边界；不得通过符号链接越界，也不公开整个 root 或 state_dir。
- `store_id`、布局版本和 schema 版本绑定实例。布局初始化后固定；不兼容版本停止写入，普通打开不得隐式迁移。
- `asset_id` 稳定且不复用；revision 递增、原图不可变。逻辑 key 不作为物理路径，不得绕过包直接编辑受管文件或数据库。
- 配方保存不可变快照；所有影响输出的参数和算法版本参与指纹。改变转换行为必须更新算法版本及相关测试，不能复用旧身份冒充新输出。
- 原图提交与转换状态分开。包内资产/任务事务不与宿主业务事务共同提交；不自动删除未关联业务记录的资产。
- 同步处理与 worker 共用持久任务和租约。发布前复核领取 token、资产状态、当前 revision 及当前 profile 请求；过期任务不得发布。
- 删除按 `active → deleting → deleted` 推进；所有受管历史版本离开公开目录后才能报告 deleted。私有清理另有状态，源站删除不保证撤回浏览器或 CDN 缓存。
- 发行版本、磁盘布局版本、schema 版本和算法版本各有职责，变更时分别评估兼容性，不能用包版本代替其他版本。

## 修改、验证与交付

Python 最低版本为 3.11；实际支持范围与依赖声明以 `pyproject.toml` 为准。测试、lint、类型检查和构建命令以 `pyproject.toml`、`docs/development.md` 和 `tools/` 中实际提供的入口为准；命令尚未建立时先补齐相应任务，不猜测不存在的脚本。

测试使用独立临时 root/state_dir，不访问参考项目真实媒体、业务数据库或开发目录。修改存储、并发或恢复逻辑时补充相应故障注入与契约测试。发行验证须在源码树外分别安装 wheel 和由 sdist 构建的包，核对完整流程、CLI、类型标记、离线文档、示例及许可资源，避免源码目录掩盖遗漏。

并行任务先明确文件归属与公共接口；共享文件由指定负责人修改。OpenSpec tasks 仅由集成负责人在实际集成与验收后勾选，不以子 agent 的完成报告替代检查。

本项目自身代码采用 MIT。新增或复制第三方材料前先核实权属及许可，并更新 `NOTICE.md`；未确认许可的参考源码只用于行为研究，按本项目契约独立实现。
