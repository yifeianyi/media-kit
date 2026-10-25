# 升级与兼容性

发行版本、磁盘布局版本、SQLite schema 版本、转换算法版本是不同身份。`VERSION`、`STORAGE_LAYOUT_VERSION`、`SCHEMA_VERSION`、`ALGORITHM_VERSION` 可从 media_kit 导入；v0.1 初始布局/schema 为 1，默认算法 webp-v1。

安装新 Python 包只替换程序代码，并不代表任何旧目录都可安全写入。open/readonly 会校验实例标记与数据库版本；较新且不兼容的存储必须抛 StorageCompatibilityError 并停止写入。普通导入或打开不隐式建表/升级 schema。

升级步骤：先阅读目标发行说明与[来源记录](../NOTICE.md)，在临时副本安装目标版本并执行只读 check、完整生命周期和 worker 恢复检查；停写并按[备份流程](operations.md)做一致备份；再升级程序，校验格式和状态后逐步恢复写入。失败时停止写入，保留现场，用匹配版本与成套备份恢复。

首版从全新实例开始，不提供虚构的历史 schema/layout 自动迁移命令。未来引入新格式必须提供明确、可验证的显式迁移路径；在此之前保持原版本或建立新实例，不能手改 SQLite 版本号或媒体标记绕过检查。

只改变 URL 前缀无需改文件。修改 profile 默认值不更新历史请求，须显式 rebuild。修改输出算法必须更新 algorithm_version；相同资产同 revision 也应产生新的配方指纹和 URL。retry 保持已提交快照，不能用它完成配方升级。

fork 重新发行要协调项目版本/分发名称与内部兼容策略。若改变磁盘或 schema，不得沿用旧兼容版本谎称可写；若改变输出行为，不得沿用旧算法身份。见[定制指南](customization.md)。
