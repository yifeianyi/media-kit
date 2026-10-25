# 接入项目

包负责自己的媒体、版本、配方、任务和 SQLite；宿主负责用户授权、业务引用、上传入口以及删除前的引用检查。可以接在脚本、任务进程、Django、Flask、FastAPI 或其他 Python 程序里；核心不要求这些框架依赖。

宿主数据库和媒体状态没有共同事务。推荐先保存资产、再提交业务引用，并为重试使用稳定的业务请求幂等键：

```python
asset = store.save(upload, key='articles/42/cover.png', idempotency_key='request-42-upload')
try:
    business_repository.attach_cover(article_id=42, asset_id=asset.asset_id)
except Exception:
    # 资产已保存，记下 asset_id 后可重新关联；不要假定媒体共同回滚。
    pending_associations.record(article_id=42, asset_id=asset.asset_id)
    raise
```

关联失败时可查询并重试关联；业务明确不再需要该资产时，用当前 revision 显式 delete 补偿。包不检测外部引用、不自动删除未关联资产。若媒体响应丢失，重放原 save 幂等键与同内容获得原 asset_id；替换重放保持原 expected_revision，不把它误当作新的更新。

不要把等待缩略图成功作为识别原图提交的唯一依据。超时或部分失败后照常保存 asset_id，再提供状态占位、当前原图或稍后查询。rebuild 是显式新配方请求，retry 保持当前请求的原快照。

## 静态服务

只映射原图目录和派生目录。以下 Nginx 示例须把路径替换为实际配置，并保证 URLMapping 与之匹配：

```nginx
location /media/src/ {
    alias /srv/example/media/src/;
    autoindex off;
}
location /media/thu/ {
    alias /srv/example/media/thu/;
    autoindex off;
}
```

不能把整个 root、state_dir 或 `.media-kit-private` 暴露为静态目录。包的 local_path 只解析公开坐标，不能拿它生成数据库、staging、quarantine 的 URL。发行示例 `static_server.py` 演示只路由 src/thu；它是本地验收服务，正式部署应使用宿主选择的静态服务与访问控制。

已发布文件不依赖 API 进程存活。不可变 revision/指纹 URL 可配置长期缓存，但涉及敏感媒体时须自行设计鉴权和缓存策略。deleting 立即停止新解析，已知 URL 在移动前可能继续可读；deleted 只保证受管源站移除，不能撤回浏览器/CDN 既有缓存。外部缓存失效由宿主处理。

为每个独立接入项目初始化自己的 root/state_dir，锁定包版本，并用临时实例先验证完整生命周期。本版不提供跨项目共享资产协议，也不支持直接接管已有历史媒体目录。
