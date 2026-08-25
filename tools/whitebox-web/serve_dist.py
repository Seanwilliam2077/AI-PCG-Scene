"""本地预览 dist（修正 Windows 注册表导致的 .js→text/plain MIME 问题）。"""
import http.server
import mimetypes
from pathlib import Path

mimetypes.init()  # 先触发惰性初始化，否则 add_type 会被 init 覆盖
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

DIST = str(Path(__file__).parent / "dist")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        ".html": "text/html", ".css": "text/css",
        ".js": "application/javascript", ".mjs": "application/javascript",
        ".wasm": "application/wasm", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
        "": "application/octet-stream",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


http.server.ThreadingHTTPServer(("127.0.0.1", 4173), Handler).serve_forever()
