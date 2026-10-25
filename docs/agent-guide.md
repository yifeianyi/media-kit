# 给 agent 的导航

接入方无需原始聊天历史或参考仓库。先读[quickstart](quickstart.md)和[configuration](configuration.md)，再按任务查阅[API](API.md)。修改本包源码还必须读发行源码根 AGENTS.md 与[development](development.md)。

| 任务 | 代码入口 | 文档/验证 |
| --- | --- | --- |
| 保存与业务引用 | 公共 MediaStore.save/replace/delete | [integration](integration.md)，生命周期契约测试 |
| 目录、静态 URL 与边界 | config.py、storage/ | [configuration](configuration.md)，路径/符号链接/URL 测试 |
| 转换视觉规则、资源限制 | transforms/converter.py | [customization](customization.md)，方向/透明/动画/小图/损坏测试 |
| 配方变化与缓存 | transforms/fingerprint.py、Profile/TransformRecipe | 参数与算法版本身份测试、局部 rebuild 测试 |
| worker 卡住或失败 | 公开 tasks/retry/recover/worker | [operations](operations.md)，租约与故障注入测试 |
| 升级或备份恢复 | _version.py、实例标记/schema | [upgrading](upgrading.md)，兼容性/只读检查 |
| 找安装后的手册/例子 | documentation.docs_path/examples_path | [index](index.md)，发行物资源检查 |

关键不变量：原图字节保留；资产 ID 稳定而 revision 不可变；key 与物理地址分离；当前结果严格匹配 revision/profile/指纹；发布前校验 active、当前请求与 claim token；状态目录和私有文件不公开；deleted 仅在公开文件全部移出后成立；SQLite 与宿主业务库没有共同事务；未关联业务资产不会自动删除。

改输出算法必须更改 algorithm_version，并更新测试和文档。配置变化不触发查询写入；retry 不采用新默认配方，不复活 superseded 请求。不要直接编辑 SQLite、篡改版本号、接管未知目录或用缺失业务引用推断可删除。

验收从临时目录和公开 API 开始，随后执行测试、类型、lint、构建与 wheel/sdist 干净安装检查。查明现有接口和任务后再改代码；对共享文件指定唯一负责人，保留其他任务修改。完整命令见[development](development.md)。

维护本源码的 agent 应在提交前运行 `python tools/verify_distribution.py --python python3.11`；它会实际安装 wheel 和由 sdist 重建的包，在源码目录外检查随包文档、类型、CLI、示例、静态目录边界和 worker 恢复。算法修改另运行 `python tools/verify_customization.py`。本仓库的 CI 再于 Python 3.11 与 3.15 执行相同发行验收。
