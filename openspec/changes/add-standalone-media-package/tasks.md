# Tasks

## 1. 项目骨架与公开约束

- [x] 1.1 建立 `src/media_kit/`、`tests/`、`examples/`、`docs/` 和 `tools/` 目录，添加 Python 3.11+ 的 `pyproject.toml`、src 布局、类型检查配置和 `py.typed`；验证纯模块导入不会创建文件、数据库或后台进程。
- [x] 1.2 定义公开模型、异常层级和存储/磁盘/schema/算法版本常量，导出稳定的包入口；验证接入示例无需导入内部模块即可完成类型检查。
- [x] 1.3 添加 MIT 许可、版权/来源记录模板和根 `AGENTS.md`，核实参考代码与依赖的许可边界；验证源码树包含许可文本且构建清单能定位来源声明。

## 2. 配置、实例初始化与安全存储

- [x] 2.1 实现 TOML 与 Python 配置模型，校验绝对 root/state_dir、受管目录相对路径、目录不重叠/不嵌套、缩略图 profile 和 URL 前缀；验证非法配置在写入前返回可区分错误。
- [x] 2.2 实现显式 `MediaStore.init/open`、实例标识、布局/schema 元数据和重复初始化幂等；验证空目录能初始化、未知已有图片目录被拒绝、布局漂移被拒绝且不搬移文件。
- [x] 2.3 实现 key、文件名和符号链接边界检查，以及原图/变体/私有 staging、quarantine 的受管路径解析；验证路径穿越、空段、反斜杠、NUL 和越界链接均不读写边界外。
- [x] 2.4 实现私有 staging 的流式复制、hash/格式检查、同文件系统原子安装和临时文件清理；验证中断或异常不会发布半文件，源文件保持不变。
- [x] 2.5 实现 `check` 的只读核对、未知文件/缺失文件/hash 不匹配报告和 URL 坐标生成；验证核对不登记、覆盖、删除文件，URL 前缀改变不改变资产和物理文件。
- [x] 2.6 为配置和存储边界编写 `docs/configuration.md`、`docs/storage-and-lifecycle.md` 的对应章节与测试夹具；验证文档示例在临时 root/state_dir 中可执行且不暴露私有目录。

## 3. 资产、版本与持久操作

- [x] 3.1 设计并实现 SQLite schema/migration runner，覆盖 assets、revisions、variant_requests、variants、tasks、operations、idempotency_records 及索引；验证 schema 版本不兼容时停止写入且不隐式迁移。
- [x] 3.2 实现 `save` 的内容登记、逻辑 key 唯一性、revision 1、资产/操作/任务同包事务和输入复制；验证有效原图字节一致、重复 key 冲突、无 worker 时任务仍可查询。
- [x] 3.3 实现幂等摘要与 `save/replace/delete` 重放，按“同键同请求复用、同键不同请求冲突、重放优先于版本检查”验证响应丢失场景不产生重复资产。
- [x] 3.4 实现 `replace` 的 expected_revision 并发保护、不可变新 revision、原子当前指向切换和新配方任务登记；验证并发替换至多成功一次，输入失败或版本冲突不改变旧版本。
- [x] 3.5 实现 operations 恢复记录、启动恢复入口和重复恢复保护；通过故障注入验证安装、SQLite 提交边界中断后可完成或隔离，且不会覆盖后来提交的版本。
- [x] 3.6 编写生命周期契约测试及 `docs/API.md`、`docs/integration.md` 的保存/替换/幂等/事务边界章节；验证宿主业务事务失败时资产保留，包不自动清理未关联资产。

## 4. 图片转换与派生结果

- [x] 4.1 实现无 store/数据库依赖的独立 Pillow 转换 API、输入能力检查和资源限制；验证伪装、损坏、格式/字节/像素超限输入被拒绝且输出目标无半文件。
- [x] 4.2 实现默认 320/640/1280 WebP 规则：EXIF 定向、等比不放大、透明保留、动画首帧及原图字节保护；移植方向、透明、小图、动画和多档输出测试并验证原图未变。
- [x] 4.3 实现具名 profile 校验、规范化配方快照和 transform fingerprint；验证参数或算法版本变化产生新指纹，profile 路径安全且非法配置不创建任务。
- [x] 4.4 实现按 asset/revision/fingerprint/profile 的变体路径、元数据登记、完整文件原子发布和当前 revision/profile 严格解析；验证旧 revision、旧指纹和部分失败档位不会被误报为当前成功结果。
- [x] 4.5 实现 `rebuild` 的新配方请求与当前请求 `retry` 的原快照复用，保留有效档位；验证修改默认配置后重试仍使用旧快照，局部重建只更新所选档位，不重新激活历史请求。
- [x] 4.6 编写 `docs/API.md` 的变体状态/配方章节和 `docs/customization.md` 的算法版本修改示例；验证示例更新算法版本后测试能识别缓存身份变化。

## 5. 持久任务与 worker

- [x] 5.1 实现含 profile 名称的任务去重、SQLite 条件领取、claim token、尝试次数、300 秒租约和 30 秒心跳；验证同参数不同名称的档位均生成结果、两个 worker 竞争时只有一个有效领取者、过期租约能被接管。
- [x] 5.2 实现按档转换、发布前复核资产 active/current revision/当前 profile 请求指纹/token、失效结果隔离及状态持久化；故障注入验证替换/删除/同版本重建期间旧任务不能发布。
- [x] 5.3 实现独立 `worker --once` 与持续运行模式、空队列退出、停止恢复和任务查询；验证 worker 不依赖 Web 应用生命周期，重启后已完成档位保留、未完成任务可继续。
- [x] 5.4 实现 `process(timeout=...)` 与 worker 共用的执行路径及有界本地执行上下文；用阻塞单档测试验证等待调用能在转换完成前超时返回，原图和任务保留，重复调用不无限创建执行器，并发遵守同一租约规则。
- [x] 5.5 实现任务/档位状态、错误信息、重试和缺失变体补齐的查询 API；验证 worker 退出后仍能读取失败原因、源 revision、配方指纹和可重试身份。
- [x] 5.6 编写并发、租约、部分失败、配置变更、进程中断故障注入测试及 `docs/operations.md`；验证文档命令可恢复一个到期任务且不会要求直接编辑 SQLite。

## 6. 生命周期删除、查询与 CLI

- [x] 6.1 实现 `get/list/resolve/resolve_many`、稳定分页排序和批量逐项结果；验证返回输入顺序、删除中/已删除不可用、仅返回当前 revision 与请求 profile 的派生物。
- [x] 6.2 实现 `delete` 的 active→deleting→deleted 状态、预期版本/幂等检查、任务撤销和公开文件移入私有隔离区；验证所有历史版本移出公开目录后才报告 deleted。
- [x] 6.3 实现隔离区 cleanup 的独立 pending/complete/failed 状态、重试和启动恢复；验证删除文件错误保持 deleting、重复删除幂等、源站删除语义不承诺撤回 CDN 缓存。
- [x] 6.4 实现统一 CLI：`init`、`worker`、`status`、`check`、`recover`、`retry`、`rebuild`、`cleanup`、`docs --path`，并统一错误和退出码；验证只读命令不隐式写入，`--help` 可在安装环境运行。
- [x] 6.5 编写纯 Python 完整示例，覆盖初始化、保存、同步/worker、批量解析、分页、替换、重试和删除；验证示例在源码树外使用独立临时目录可完整运行。
- [x] 6.6 编写 `docs/quickstart.md`、`docs/API.md`、`docs/storage-and-lifecycle.md` 和 `docs/operations.md` 的最终操作后置条件；验证每个公开写操作都说明冲突副作用、重试和崩溃恢复结果。

## 7. 文档、发行物与集成验收

- [x] 7.1 完成 `README.md`、`docs/agent-guide.md`、`docs/development.md`、`docs/upgrading.md`、`docs/integration.md`、`docs/customization.md` 与文档索引；验证 agent 能按导航定位模块、关键不变量、测试和修改入口。
- [x] 7.2 配置 wheel/sdist 资源打包，将文档、示例、类型标记、许可和来源声明纳入发行物；验证源码、wheel、sdist 均包含必需离线资源且不依赖开发机绝对路径。
- [x] 7.3 编写文档链接、示例执行、模块导入无副作用、类型检查和发行资源完整性检查；验证检查从构建产物而非源码目录读取文档和示例。
- [x] 7.4 在源码树外的干净 Python 环境分别安装 wheel 和由 sdist 构建的包，执行完整生命周期、worker 恢复和静态坐标示例；验证两种发行物的功能和离线文档一致。
- [x] 7.5 按 `docs/customization.md` 修改一个转换规则并更新算法版本，执行测试、类型检查和构建；验证修改后的发行物能体现新行为且旧配方身份未被错误复用。
- [x] 7.6 执行全套测试、lint、类型检查、构建和 OpenSpec 严格校验，记录支持的 Python/Pillow 范围与已知单机限制；验证所有需求场景有对应测试或可重复验收命令。

## Workflow follow-up

- 评审全部规划产物后，使用 `$openspec-apply-change (Codex) or /openspec-apply-change (other agents)` 开始实现。
- 实施完成后，运行 `$openspec-verify-change (Codex) or /openspec-verify-change (other agents)`，再按发布要求创建本地提交；本 change 不包含远程发布或推送。
