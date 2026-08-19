# 静态文件服务器。
# Windows 上 SimpleHTTPRequestHandler 会去读注册表里的 MIME 映射，
# .js 常被解析成 text/plain，浏览器就会拒绝加载 ES module —— 这里写死。
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8177


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".svg": "image/svg+xml",
        "": "application/octet-stream",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        # POST /save?name=xxx  body = "data:image/png;base64,...."
        # 只写到 _shots/ 下、只接受纯文件名，方便无头环境里把渲染结果落盘查看。
        if not self.path.startswith("/save"):
            self.send_error(404)
            return
        import base64
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        name = Path(q.get("name", ["shot"])[0]).name or "shot"
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("ascii")
        head, _, b64 = body.partition(",")
        ext = ".jpg" if "jpeg" in head else ".png"
        out = ROOT / "_shots" / (name + ext)
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(base64.b64decode(b64))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(str(out))))
        self.end_headers()
        self.wfile.write(str(out).encode())

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"base3d → http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT),
                        partial(Handler, directory=str(ROOT))).serve_forever()
