# 文档索引

这是与当前发行版本一起交付的离线文档。`media-kit docs --path` 定位安装副本；Python 可用 `from media_kit.documentation import docs_path, read_document, examples_path`。源码 `docs/` 是唯一权威正文，构建时复制进包资源。

| 任务 | 阅读入口 |
| --- | --- |
| 第一次接入、运行完整示例 | [quickstart](quickstart.md) |
| Python/TOML、目录布局、URL | [configuration](configuration.md) |
| 方法、返回值、错误与重试 | [API](API.md) |
| 资产/版本、文件状态与删除 | [storage-and-lifecycle](storage-and-lifecycle.md) |
| 业务数据库、授权、静态服务 | [integration](integration.md) |
| worker、核对、恢复、清理和备份 | [operations](operations.md) |
| 安装升级与磁盘/schema 兼容 | [upgrading](upgrading.md) |
| 修改源码、检查和构建 | [development](development.md) |
| 自定义档位、算法、fork | [customization](customization.md) |
| 给接入方与维护 agent 的导航 | [agent-guide](agent-guide.md) |

示例随发行物安装，可由 `examples_path()` 找到：`lifecycle.py`、`convert.py`、`static_server.py`。这些示例使用自己创建的临时目录和测试图片，不读取外部项目。
