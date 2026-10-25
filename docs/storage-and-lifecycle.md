# 存储与生命周期

本包托管全新目录，初始化不会接管、登记、搬移或清理未知已有图片目录。相同实例与布局的重复初始化幂等；普通导入无文件或数据库副作用。

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

`asset_id` 是稳定 UUID；`key` 是可选业务逻辑路径，不决定物理文件名。key 在 active 和 deleting 资产间唯一；资产完成 deleted 后可复用 key，新资产必须获得不同 ID。`revision` 从 1 开始，每次显式替换递增，即使字节相同也递增。历史版本文件不被原地覆盖；cleanup() 可将不再当前的历史版本移入私有隔离区并清理，因此版本路径不能视作业务回滚备份接口。

保存和替换先在私有 staging 复制、验证并 hash 输入，再记录操作意图、原子安装完整文件；资产当前指向、revision、派生请求、任务和幂等结果在同一个 SQLite 事务中提交。文件系统与数据库不能组成一个事务，恢复记录负责跨边界中断。失败时正式 URL 不指向半文件；尚未提交的安装产物由恢复完成或隔离，不能手动把未知文件登记为资产。

`MediaStore.open` 校验实例及格式后推进已记录操作的恢复；`MediaStore.readonly` 不初始化、不迁移、不恢复写入。`check` 只报告缺失、内容不匹配及未知文件；`recover` 只推进包已经记录的操作，不接管未知文件。恢复可重复，不重复增加版本，也不能覆盖后续已提交版本。

每个 profile 当前请求绑定原图 revision、配方指纹与参数快照。转换失败不改变原图成功事实。解析只返回当前 revision、该 profile 当前请求的 ready 结果，不回退旧图。局部 rebuild 只更新选中 profile，其余请求保留。任务历史可查，retry 只作用于当前请求。

删除状态为 `active -> deleting -> deleted`。提交 deleting 后立即停止地址解析、取消后续任务发布；文件尚未移出前，已知静态 URL 仍可能读到字节。包按数据库登记且尚未回收的原图、派生文件逐个移入私有 quarantine；即使目录含有未知文件，也只移动登记路径，空的受管父目录会在保留非空未知内容的前提下清理。源文件和本次 quarantine 目标同时存在、文件不是普通文件，或两处都不存在时会保留待办并报告错误；恢复识别已移入 quarantine 的文件后可继续。所有受管历史版本移出公开目录后才记录 deleted。清理 quarantine 的 pending/complete/failed 独立于资产删除结果。

`cleanup()` 回收已被新 revision 或新配方取代的登记文件，不是回滚备份；历史文件清理只处理尚未回收的记录，重复调用不会创建空回收操作。未知文件保留在原目录，并由只读 `check()` 报告。

delete 成功仅保证受管源站文件退出公开目录，不承诺撤回浏览器/CDN 缓存。宿主在调用前检查业务引用与授权；包不根据缺少业务引用删除资产。cleanup 不依据外部业务状态猜测哪些 active 资产“无人使用”。

每个公开写操作的具体成功条件、冲突副作用和安全重试见 [API](API.md)；一致备份和恢复见[运维](operations.md)。
