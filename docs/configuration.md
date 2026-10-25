# 配置

Python 入口为 `StoreConfig`、`Profile`、`URLMapping`；TOML 入口为 `StoreConfig.from_toml(path)`，已有解析后字典时用 `StoreConfig.from_mapping(mapping)`（section 结构与 TOML 相同）。构造配置仅做校验，不初始化存储、不写入目录。

```toml
[storage]
root = "/srv/example/media"
state_dir = "/var/lib/example/media"
original_dir = "src"
variant_dir = "thu"

[thumbnails]
sizes = [320, 640, 1280]
format = "webp"
quality = 80
method = 4
max_pixels = 40000000
max_bytes = 20971520
algorithm_version = "webp-v1"

[urls]
original_base = "/media/src/"
variant_base = "/media/thu/"
```

CLI 中 `--config` 放在子命令前：`media-kit --config media.toml init`。相对的配置文件路径按当前工作目录寻找；配置中的 root/state_dir 必须为绝对路径。

root 和 state_dir 必须互不相同、互不包含；状态目录不能放在媒体根下面。original_dir/variant_dir 是 root 内的非空 POSIX 相对目录，不能相同、重叠或互相包含，不能使用绝对路径、`.`、`..`、空段、反斜杠或 NUL。受管路径不能经符号链接越界。正式文件与 root 内私有 staging/quarantine 需要在同一本地文件系统。

```python
from pathlib import Path
from media_kit import Profile, StoreConfig, URLMapping

config = StoreConfig(
    root=Path('/srv/example/media'), state_dir=Path('/var/lib/example/media'),
    original_dir='originals', variant_dir='previews',
    profiles=(Profile('card', 480, quality=85), Profile('large', 1600)),
    urls=URLMapping('/assets/originals/', 'https://cdn.example.test/previews/'),
)
```

具名档位还可使用 TOML array-of-tables；此形式替代 sizes：

```toml
[[thumbnails.profiles]]
name = "card"
size = 480
quality = 85
method = 4

[[thumbnails.profiles]]
name = "large"
size = 1600
```

profile 名唯一，是安全的单路径段，允许字母、数字、点、下划线和连字符；最长 64 字符。size 必须为正，WebP quality 为 0–100，method 为 0–6；首版输出仅 WebP。字节/像素限制必须为正，默认最大 20 MiB、4000 万像素。max_pixels 同时约束单帧像素与动画所有帧的累计像素；动画最终只生成首帧缩略图，输入校验仍完整解码全部帧以识别损坏和控制资源。JPEG、PNG、GIF、WebP 为默认输入；AVIF 取决于当前 Pillow 的实际解码能力，能力不足必须报输入错误。

磁盘布局在初始化时记录。打开现有实例时修改 root 对应布局、state_dir 对应身份、original_dir 或 variant_dir 会被拒绝，不搬移文件。重新部署时须成套恢复 root 与 state_dir，并用只读 check 校验；不要用修改配置代替迁移。

URL 前缀可随部署调整，不改变 asset_id、revision 或磁盘文件。前缀须以 `/` 结尾，不能包含查询/片段、反斜杠或 NUL；路径坐标逐段 URL 编码。省略 URLMapping 时 URL 为 None，存储坐标仍可供 `local_path` 使用。只把原图与派生目录映射到静态服务，绝不公开整个 root、state_dir 或 `.media-kit-private`。

修改 profiles 不会变更历史请求。旧任务继续用持久快照；`retry` 保持旧配方；`rebuild(asset_id, profiles=['card'])` 才显式应用当前配置。见[配方身份](customization.md)。
