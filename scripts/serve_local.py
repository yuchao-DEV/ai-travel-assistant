#!/usr/bin/env python3
"""本地一键运行：托管构建好的前端 + 反向代理 /api 到后端。

## 为什么需要这个

在别人电脑上"部署"这个项目，最省事的路径有两种：

- **开发模式**：后端 8080 + `npm run dev`（Vite 自带 /api 代理），改代码自动热更新
- **接近生产**：`npm run build` 出静态文件，再由某个服务器托管 dist 并把 /api 转发给后端

第二种在服务器上通常用 nginx，但让同学为了跑一个作业去装 nginx 太重了。
这个脚本用 Python 标准库把这件小事做掉——**零依赖、跨平台**，
只要装了 Python 就能跑：

    python scripts/serve_local.py
    → http://127.0.0.1:5173        移动端
    → http://127.0.0.1:5173/pc/    桌面端

## 用法

    python scripts/serve_local.py                     # 默认 5173 → 127.0.0.1:8080
    python scripts/serve_local.py --port 8080         # 换端口
    python scripts/serve_local.py --backend 127.0.0.1:9000
    python scripts/serve_local.py --dist fronted/travel/dist

## 注意

- 必须先 `npm run build`（脚本会检查 dist 是否存在并提示）
- 这不是给公网用的：单线程开发服务器，没有 HTTPS / 限流 / 压缩
"""
from __future__ import annotations

import argparse
import http.server
import mimetypes
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = ROOT / "fronted" / "travel" / "dist"
DEFAULT_BACKEND = "127.0.0.1:8080"
DEFAULT_PORT = 5173

# 代理转发时不能带过去的头（会破坏目标请求）
HOP_BY_HOP = {
    "host", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding",
    "upgrade", "content-length",
}


def build_handler(dist: Path, backend: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        # ---------- 工具 ----------
        def _send_bytes(self, status: int, body: bytes, ctype: str,
                        extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # 本地开发：禁止缓存，避免重新构建后浏览器还在跑旧 bundle
            self.send_header("Cache-Control", "no-store, must-revalidate")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _proxy(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            url = f"http://{backend}{self.path}"
            req = urllib.request.Request(url, data=body, method=self.command)
            for k, v in self.headers.items():
                if k.lower() in HOP_BY_HOP:
                    continue
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    data = resp.read()
                    extra = {}
                    ctype = resp.headers.get("Content-Type", "application/json")
                    if "text/event-stream" in ctype:
                        extra["X-Accel-Buffering"] = "no"
                    self._send_bytes(resp.status, data, ctype, extra)
            except urllib.error.HTTPError as e:
                data = e.read()
                self._send_bytes(e.code, data,
                                 e.headers.get("Content-Type", "application/json"))
            except Exception as exc:                      # noqa: BLE001
                msg = (f"无法连接后端 {backend}：{type(exc).__name__}: {exc}\n"
                       f"请确认后端已启动：\n"
                       f"  cd backed && python -m uvicorn app.main:app "
                       f"--host 127.0.0.1 --port 8080\n").encode()
                self._send_bytes(502, msg, "text/plain; charset=utf-8")

        # ---------- 路由 ----------
        def do_GET(self):                     # noqa: N802
            if self.path.startswith("/api/"):
                return self._proxy()
            rel = self.path.split("?")[0].lstrip("/")
            target = dist / rel
            # SPA fallback：/pc/xxx 与 /xxx 都要回到对应入口
            if not rel or target.is_dir() or not target.exists():
                if rel.startswith("pc") or rel == "pc":
                    self.path = "/pc/index.html"
                else:
                    self.path = "/index.html"
            return super().do_GET()

        def do_HEAD(self):                    # noqa: N802
            return self.do_GET()

        def do_POST(self):                    # noqa: N802
            if self.path.startswith("/api/"):
                return self._proxy()
            self.send_error(405)

        def do_PUT(self):                     # noqa: N802
            if self.path.startswith("/api/"):
                return self._proxy()
            self.send_error(405)

        def do_PATCH(self):                   # noqa: N802
            if self.path.startswith("/api/"):
                return self._proxy()
            self.send_error(405)

        def do_DELETE(self):                  # noqa: N802
            if self.path.startswith("/api/"):
                return self._proxy()
            self.send_error(405)

        def log_message(self, fmt, *args):    # 静音访问日志，只看报错
            pass

        def log_error(self, fmt, *args):
            sys.stderr.write("[serve] " + (fmt % args) + "\n")

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="托管前端 dist 并代理 /api 到后端")
    ap.add_argument("--dist", default=str(DEFAULT_DIST), help="前端构建产物目录")
    ap.add_argument("--backend", default=DEFAULT_BACKEND,
                    help="后端地址 host:port（默认 127.0.0.1:8080）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    if not (dist / "index.html").exists():
        sys.stderr.write(
            f"\n[错误] 没有找到前端构建产物：{dist}\\index.html\n"
            f"请先构建：\n"
            f"  cd fronted/travel\n"
            f"  npm install\n"
            f"  npm run build\n\n")
        return 1

    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("image/svg+xml", ".svg")

    handler = build_handler(dist, args.backend)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.host, args.port), handler) as httpd:
        print(f"  前端目录 : {dist}")
        print(f"  /api 转发: http://{args.backend}")
        print(f"  移动端   : http://{args.host}:{args.port}/")
        print(f"  桌面端   : http://{args.host}:{args.port}/pc/")
        print("  Ctrl+C 停止\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
