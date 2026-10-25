# 运维

所有示例使用同一份 TOML；CLI 的 `--config` 必须放在子命令前。成功输出 JSON、退出 0；预期错误输出 stderr JSON，含 error.type/message，退出 2；中断退出 130。`docs --path` 输出本地路径。

```bash
media-kit --config media.toml init
media-kit --config media.toml status
media-kit --config media.toml status <asset_id>
media-kit --config media.toml status --prefix articles/ --page 1 --page-size 30
media-kit --config media.toml check
```

status/check 使用只读入口，不初始化、不迁移、不推进恢复，不因查询失败创建目录。check 报告登记缺失、hash 不匹配和未知文件，默认不自动登记、覆盖或删除。修复前保留报告和备份，不要直接改 SQLite。

## Worker 与租约

```bash
media-kit --config media.toml worker --once
media-kit --config media.toml worker --once --asset-id <asset_id>
media-kit --config media.toml worker --poll-interval 1
```

--once 排空当前可领取任务后正常退出，空队列返回 processed=0；不会长期空闲等待，也不等待其他 worker 的有效租约。processed 表示本次执行过的任务数，转换失败也会持久记录，仍需用 status 确认各档 ready/failed，不能只按计数判断缩略图成功。持续模式独立于 Web 服务运行，Ctrl-C 停止。每进程串行处理图片，多个本机 worker 可竞争同一个 SQLite 队列。

领取为条件事务，claim token 每次独立，默认租约 300 秒、每 30 秒心跳。写入通过本地 POSIX fcntl 跨进程锁与 SQLite 短事务串行；耗时图片转换在锁外执行。worker 意外退出后任务仍为 running 直到租约到期，后续 worker 可接管；旧 token 即使完成转换也无法发布。不要因看到 running 就立即重复入队。`status <asset_id>` 查看 task ID、revision、profile、指纹、尝试次数与持久错误；错误不会只保留在日志。

```bash
# 进程中断后：等待租约到期，显式恢复文件操作，再继续 worker
media-kit --config media.toml status <asset_id>
media-kit --config media.toml recover
media-kit --config media.toml worker --once
media-kit --config media.toml status <asset_id>
```

这套命令通过公开入口接管到期任务，无需编辑数据库或假造 token。process(timeout=...) 共用相同租约，达到等待期限仍可返回；后台本地执行有并发上限，重复超时不会创建无限执行器。`close(wait=True)` 会等待在途线程，若要把等待责任交还后续进程，使用 close(wait=False) 并按持久状态判断结果。

## 重试、重建、恢复与删除

```bash
media-kit --config media.toml retry <asset_id>
media-kit --config media.toml rebuild <asset_id> --profile 320
media-kit --config media.toml worker --once
media-kit --config media.toml recover
media-kit --config media.toml cleanup
```

retry 保持当前失败请求原配方，保留有效成功档位；不会激活已被替换的历史请求。rebuild 使用当前配置，--profile 可重复，未选档位保持原请求。原图替换、删除或同版本配方切换后，过期转换会被拒绝，不能成为当前结果。

recover 推进已记录的原图/变体安装、删除及历史回收，可重复执行。返回 recovered 完成数；未恢复的文件操作另计 failed。删除文件错误保留 deleting；排查文件权限/磁盘/越界链接后再 recover 或重复 delete。只有全部受管历史公开文件移出后才报告 deleted；有冲突待办的资产暂停 replace/rebuild/retry/delete，其他资产仍可访问。

cleanup 会回收 active 资产已被新 revision 或新配方取代的登记文件，再独立清理私有 quarantine，返回 complete/failed 操作计数。历史版本不是回滚备份；未知文件保留并由 check 报告。回收文件缺失、移动或销毁失败会保留可重试待办。cleanup 不推断外部业务引用或删除 active 资产。check 发现差异、recover/cleanup 有 failed 时 CLI 输出 JSON 报告并退出 2；成功退出 0。完整写操作后置条件见 [API](API.md)。

## 一致备份与恢复

1. 停止上传/替换/删除入口和全部 worker、本地 process，等待在途操作结束并关闭 store。
2. 如发现待恢复操作，先以匹配版本 recover，复查状态，再停止写入。也可使用经过验证、覆盖文件与状态目录的同一一致快照机制。
3. 同一时点备份整个 root 与整个 state_dir，包括标记、SQLite 及其附属状态；不能只复制原图或单独抓一个活动数据库文件。
4. 恢复到成套的新位置，用同版本与正确配置执行 readonly/check，核对 store ID、布局/schema、缺失与 hash，再开放写入和 worker。

禁止用旧数据库直接覆盖仍在写入的新媒体目录；它会丢失后来提交的身份和操作决策。恢复的配置应保持实例布局，必要的主机路径搬迁要验证标记与配置兼容，不能只修改目录名冒充升级。保留原备份，恢复失败先停止写入并保留现场。
