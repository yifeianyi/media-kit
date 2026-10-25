# Python API

正常接入从 `media_kit` 导入 `MediaStore`、`StoreConfig`、`URLMapping`、`Profile`、结果类型和异常；无需读取内部 SQLite。离线文档工具位于 `media_kit.documentation`。

## 实例与读取

`MediaStore.init(config)` 只初始化空的新实例，同身份同布局可重复；遇到未知非空目录报错且保留原内容。`MediaStore.open(config)` 打开并恢复已记录中断操作，不创建缺失实例或隐式迁移。`MediaStore.readonly(config)` 打开只读实例，供查询与核对；不恢复、不初始化。

三者返回可用作 context manager 的 MediaStore。`close(wait=True)` 停止接受新的本地处理，默认等待已开始执行；`close(wait=False)` 停止等待，在途线程可完成，进程退出后未完成工作可由租约恢复。`with` 结束默认等待，不能把它的等待时间当作 `process(timeout=...)` 的超时范围。

| 方法 | 输入与返回 |
| --- | --- |
| `get(asset_id)` | 返回当前 AssetView，不存在抛 AssetNotFoundError |
| `resolve(asset_id, profiles=None)` | 当前资产与所选已请求 profile；未知 profile 报错 |
| `resolve_many(asset_ids, profiles=None)` | 输入顺序的 tuple；缺失项为 None，其他项仍可用 |
| `list(prefix=None, page=1, page_size=30)` | AssetPage：items、total、page、page_size；page 从 1 开始 |
| `tasks(asset_id=None)` | 持久 TaskView tuple，可查询历史 revision/请求和失败信息 |
| `local_path(coordinate)` | 将已知公开存储坐标解析为现有本地路径，拒绝私有/越界路径 |
| `check()` | CheckReport：checked_assets、missing_files、content_mismatches、unknown_files |

分页按创建顺序稳定排列，page_size 为 1–1000，列表保留 inactive 资产的状态记录；无并发修改时跨页顺序稳定，不要把 offset 分页当作并发写入期间的快照。删除中的与已删除资产可查询状态，但不返回可用媒体地址。URL 为 None 表示调用方未配置访问映射，不表示文件不存在。

AssetView 包含 asset_id、key、revision、status、original_path/URL、原图 ImageInfo、variants 与 processing_status。幂等重放时 revision 仍表达资产当前版本，operation_revision 表达原成功操作产生的版本，避免把旧成功操作误认成新变更。ImageInfo 包含实际格式、MIME、尺寸、字节数和 SHA-256。VariantView 包含 profile、状态、recipe_fingerprint、可用结果坐标/URL 和错误。TaskView 暴露 task_id、asset_id、revision、profile、配方身份/快照、attempts、lease_until、状态和错误，足以定位后续重试。CheckReport.ok 表示未发现三类文件差异。

## 写入与重试契约

| 方法 | 成功后的状态 | 冲突或失败、重试与恢复 |
| --- | --- | --- |
| `save(source, key=None, idempotency_key=None)` | 返回 revision 1 AssetView；原图字节完整，资产和任务已提交，输入不移动/修改 | 重复 active/deleting key 冲突；无效输入不创建资产。响应丢失用同幂等键和内容重放；中断由 open/recover 处理 |
| `replace(id, source, expected_revision=..., idempotency_key=None)` | 保留 ID/key，建立不可变新 revision，切换当前版本并登记任务 | 版本冲突/无效输入不改变当前版本。成功请求的同键重放先于版本检查；不要盲目改为新 expected_revision 重试 |
| `rebuild(id, profiles=None)` | 为当前 revision 的选中 profile 显式提交当前配置配方；相同身份补齐缺失结果 | 未选 profile 保留；过期任务不能发布。重复相同请求复用身份；当前配置校验失败不登记任务 |
| `retry(id)` | 仅安排当前 revision 当前请求的失败/缺失/损坏档位；有效成功档位保留 | 使用原快照，不采用新默认配置，不重新激活 superseded 历史请求。重复调用不创建竞争身份 |
| `process(id, timeout=None)` | 返回 ProcessResult(asset, timed_out)，沿用持久队列/租约执行 | 超时不回滚原图、不撤销任务；单档阻塞也能到期返回。转换可在后台继续；查询或后续 worker 可继续同一请求 |
| `delete(id, expected_revision=..., idempotency_key=None)` | 返回 DeleteResult；仅在全部公开版本退出后 status=deleted | 版本冲突发生在删除决策前。文件移除失败保持 deleting，报告 errors，重复 delete/recover 继续。已删除重复调用幂等，不复活 |
| `recover()` | 返回 recovered 完成数；存在未恢复文件操作时另有 failed 计数 | 可重复，保留失败待办，不接管未知外部文件；不得覆盖后续版本。该资产的 replace/rebuild/retry/delete 在冲突待办恢复前抛 StorageError，其他资产可继续使用 |
| `cleanup()` | 回收 active 资产已被取代的历史 revision/配方登记文件，再销毁私有隔离文件，返回 complete/failed 操作计数 | 历史文件清理不可用作业务回滚；只处理尚未回收的登记文件。缺失或文件移动失败计入 failed 并保留待办，重复调用可重试；不根据外部业务引用删除 active 资产 |

`source` 接受本地路径（`str` 或 `Path`）或二进制流；保存复制输入。key 是规范相对 POSIX 逻辑路径，扩展名与实际内容冲突会被拒绝。二进制流从当前支持的输入策略读取；不要在请求并发期间复用同一个可变流对象。原图成功后任何缩略图失败都不等于 save 没有发生。

幂等键按同一 store 的操作类型隔离。请求摘要包括目标、expected_revision、源内容 hash、逻辑 key 和配方快照；同键不同请求抛 IdempotencyConflictError。同一成功 save 重放在资产后来被删除时仍返回原身份与可辨别当前状态，不创建新资产。业务数据库事务不参与包内提交，见[集成](integration.md)。

## 派生与执行状态

每档主要状态 pending/running/ready/failed，被新请求取代的历史任务为 superseded。TaskView 还可显示 succeeded、cancelled。聚合 processing_status 的 ready 表示所选当前档位全就绪，pending/processing 表示仍有工作，partial 表示已终止且部分成功，failed 表示全失败。资产 lifecycle status 与处理状态是两条独立维度。

默认 longest-edge 320/640/1280、WebP 80/4、EXIF 定向、首帧、不放大和透明保留。配方指纹包含输出参数和 algorithm_version；资源限制作为执行校验，不替代配方身份。不同名字即使同指纹也保留各自 profile 结果；结果路径包含 revision/完整指纹/profile。

`worker_once(asset_id=None)` 最多处理一档，返回 0 或处理数。`worker(once=False, poll_interval=1, stop_event=None)` 使用同一个持久执行器，持续模式等待后续任务，可用 stop_event 请求停止。CLI `worker --once` 持续调用 worker_once 到当前无可领取任务后退出，不等候别的 worker 的有效租约。

独立转换入口 `convert_thumbnail(source, output_path, profile, config=None)`、`inspect_image(source, max_bytes=..., max_pixels=...)` 不初始化 store，不产生数据库/任务；输出不得覆盖输入。`TransformConfig` 管理 profile 与资源限额，`Profile`/`TransformRecipe` 管理输出参数。inspect_image 根据实际内容验证格式并完整解码以拒绝损坏，不只检查文件头；max_pixels 同时限制单帧像素和动画全部帧累计像素，随后缩略图转换仍只选首帧。转换先写完整临时文件，再原子发布；失败保留原图与既有成功结果。

`format_capabilities()` 返回包声明支持的输入格式与当前 Pillow 的实际能力：键为 `JPEG/PNG/GIF/WEBP/AVIF`，值为含 `decode`、`encode` 布尔项的结构；本包只输出 WebP，因此 `encode` 仅对 WebP 可能为真。它不初始化存储，可在确定输入策略前用于显式能力检查。`convert_thumbnail` 返回 `TransformResult`，字段为 `path`（输出路径）、`size`（配方最长边）、`width`/`height`（实际像素）、`file_size`、`content_hash`（输出 SHA-256）与 `mime_type`。

## 错误类型

所有预期错误派生自 MediaKitError：ConfigurationError（配置）、StorageCompatibilityError（实例/版本不兼容）、InputValidationError（图片/路径输入）、AssetConflictError（逻辑 key 等冲突）、RevisionConflictError、IdempotencyConflictError、AssetNotFoundError、StorageError（文件/边界）、ProcessingError。调用方按类型处理，不解析错误自然语言；I/O 失败先查询/recover 决定是否已提交，再用原幂等身份重试。
