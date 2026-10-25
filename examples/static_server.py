"""本地静态验收：仅映射已配置的原图/派生目录，拒绝目录浏览与越界。"""

from __future__ import annotations

import argparse
import mimetypes
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from media_kit import MediaStore, StoreConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = StoreConfig.from_toml(args.config)
    # 校验现有实例与目录边界，静态服务不会初始化或恢复写入。
    with MediaStore.readonly(config):
        pass
    routes = {
        urlsplit(config.urls.original_base or "/media/src/").path: (
            config.normalized_root / config.original_dir
        ).resolve(),
        urlsplit(config.urls.variant_base or "/media/thu/").path: (
            config.normalized_root / config.variant_dir
        ).resolve(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = unquote(urlsplit(self.path).path)
            for prefix, directory in routes.items():
                if not path.startswith(prefix):
                    continue
                relative = path[len(prefix) :]
                if not relative or any(
                    segment in {"", ".", ".."} for segment in relative.split("/")
                ):
                    break
                target = (directory / relative).resolve()
                if not target.is_relative_to(directory) or not target.is_file():
                    break
                try:
                    with target.open("rb") as stream:
                        self.send_response(200)
                        self.send_header(
                            "Content-Type",
                            mimetypes.guess_type(target)[0] or "application/octet-stream",
                        )
                        self.send_header("Content-Length", str(target.stat().st_size))
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile)
                    return
                except OSError:
                    break
            self.send_error(404)

    print(f"仅服务原图与派生目录：http://{args.host}:{args.port}")
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
