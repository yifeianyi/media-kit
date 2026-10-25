"""Command-line access to the same public lifecycle used by Python callers."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from media_kit.config import StoreConfig
from media_kit.documentation import docs_path
from media_kit.errors import ConfigurationError, MediaKitError
from media_kit.models import CheckReport

if TYPE_CHECKING:
    from media_kit.api import MediaStore


def _store_type() -> type[MediaStore]:
    # Help and offline manuals do not need to open or even locate a media store.
    from media_kit.api import MediaStore

    return MediaStore


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit(value: object) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="media-kit", description="独立媒体存储与缩略图管理")
    parser.add_argument("--config", type=Path, help="TOML 配置路径，放在子命令前")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="显式初始化全新媒体目录")
    worker = commands.add_parser("worker", help="运行独立持久任务 worker")
    worker.add_argument("--once", action="store_true", help="处理当前可领取队列后退出")
    worker.add_argument("--poll-interval", type=float, default=1.0, help="持续模式空闲轮询秒数")
    worker.add_argument("--asset-id", help="仅在 --once 模式处理指定资产")
    status = commands.add_parser("status", help="只读查询资产与持久任务")
    status.add_argument("asset_id", nargs="?", help="省略时分页列出资产")
    status.add_argument("--prefix", help="按逻辑 key 前缀筛选")
    status.add_argument("--page", type=int, default=1)
    status.add_argument("--page-size", type=int, default=30)
    commands.add_parser("check", help="只读核对登记文件、hash 与未知文件")
    commands.add_parser("recover", help="显式推进已记录的中断操作")
    retry = commands.add_parser("retry", help="沿用当前请求快照重试失败或缺失档位")
    retry.add_argument("asset_id")
    rebuild = commands.add_parser("rebuild", help="按当前配置显式请求新的派生配方")
    rebuild.add_argument("asset_id")
    rebuild.add_argument("--profile", action="append", dest="profiles", help="可重复指定档位")
    commands.add_parser("cleanup", help="回收历史版本与旧配方文件，并清理私有隔离文件")
    docs = commands.add_parser("docs", help="定位随安装包提供的离线文档")
    docs.add_argument("--path", action="store_true", help="打印文档目录")
    return parser


def _run(args: argparse.Namespace) -> object:
    if args.command == "docs":
        return str(docs_path())
    if args.config is None:
        raise ConfigurationError("此命令需要 --config，放在子命令前")
    config = StoreConfig.from_toml(args.config)
    store_class = _store_type()
    if args.command == "init":
        with store_class.init(config):
            return {
                "initialized": True,
                "root": str(config.root),
                "state_dir": str(config.state_dir),
            }
    opener = store_class.readonly if args.command in {"status", "check"} else store_class.open
    with opener(config) as store:
        if args.command == "status":
            if args.asset_id:
                return {"asset": store.get(args.asset_id), "tasks": store.tasks(args.asset_id)}
            return {
                "assets": store.list(prefix=args.prefix, page=args.page, page_size=args.page_size),
                "tasks": store.tasks(),
            }
        if args.command == "check":
            return store.check()
        if args.command == "recover":
            return store.recover()
        if args.command == "cleanup":
            return store.cleanup()
        if args.command == "retry":
            return store.retry(args.asset_id)
        if args.command == "rebuild":
            return store.rebuild(args.asset_id, profiles=args.profiles)
        if args.command == "worker":
            if args.poll_interval <= 0:
                raise ConfigurationError("--poll-interval 必须大于零")
            if args.asset_id and not args.once:
                raise ConfigurationError("--asset-id 必须与 --once 一起使用")
            if args.once:
                processed = 0
                while True:
                    count = store.worker_once(asset_id=args.asset_id)
                    if count == 0:
                        break
                    processed += count
                return {"processed": processed, "once": True}
            return {"processed": store.worker(poll_interval=args.poll_interval), "once": False}
    raise ConfigurationError(f"未知命令: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI, producing JSON output and returning 0, 2, or 130."""
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
        if args.command == "docs":
            print(result)
        else:
            _emit(result)
        if isinstance(result, CheckReport) and not result.ok:
            return 2
        if isinstance(result, dict) and result.get("failed", 0):
            return 2
        return 0
    except (MediaKitError, OSError, ValueError) as error:
        print(
            json.dumps(
                {"error": {"type": type(error).__name__, "message": str(error)}},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"interrupted": True}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
