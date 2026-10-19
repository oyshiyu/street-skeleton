import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser

from .engine import run
from .viewer import WEB, payload


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, workspace):
        super().__init__(("127.0.0.1", port), Handler)
        self.workspace = Path(workspace)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.state = {"busy": False, "message": "选择城市 PBF 文件", "ready": False, "error": False}
        self.result_json = None
        self.result_dir = None

    def progress(self, message):
        with self.lock:
            self.state["message"] = message

    def process(self, path, output, tolerance):
        try:
            result = run(path, output, tolerance, self.progress)
            data = gzip.compress(json.dumps(payload(result), ensure_ascii=False).encode())
            with self.lock:
                self.result_json, self.result_dir = data, output
                self.state.update(busy=False, ready=True, message="已完成", error=False)
        except Exception as error:
            with self.lock:
                self.state.update(busy=False, ready=False, error=True, message=str(error))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body=b"", content_type="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host", "") in {
            f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}",
        }

    def authorized(self):
        supplied = self.headers.get("X-Street-Token", "")
        return secrets.compare_digest(supplied, self.server.token)

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403)
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            html = (WEB / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", self.server.token)
            return self.respond(200, html.encode(), "text/html; charset=utf-8")
        static = {"/style.css": "text/css", "/app.js": "application/javascript"}
        if parsed.path in static:
            return self.respond(200, (WEB / parsed.path[1:]).read_bytes(), static[parsed.path] + "; charset=utf-8")
        if not self.authorized():
            return self.respond(403)
        if parsed.path == "/api/state":
            with self.server.lock:
                data = json.dumps(self.server.state, ensure_ascii=False).encode()
            return self.respond(200, data)
        if parsed.path == "/api/result" and self.server.result_json:
            return self.respond(200, self.server.result_json, headers={"Content-Encoding": "gzip"})
        if parsed.path == "/api/download" and self.server.result_dir:
            allowed = {"street-skeleton.zip", "street-skeleton.gpkg", "map.html"}
            name = parse_qs(parsed.query).get("file", [""])[0]
            if name in allowed:
                return self.respond(200, (self.server.result_dir / name).read_bytes(), "application/octet-stream",
                                    {"Content-Disposition": f'attachment; filename="{name}"'})
        return self.respond(404)

    def do_POST(self):
        if not self.valid_host() or not self.authorized():
            return self.respond(403)
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            return self.respond(404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            tolerance = float(parse_qs(parsed.query).get("tolerance", [8])[0])
            if not 0 < length <= 1024 ** 3:
                raise ValueError("请使用小于 1 GB 的城市 PBF 文件。")
            if not 1 <= tolerance <= 20:
                raise ValueError("交叉点合并距离应在 1–20 米之间。")
        except ValueError as error:
            return self.respond(400, json.dumps({"error": str(error)}, ensure_ascii=False).encode())
        with self.server.lock:
            if self.server.state["busy"]:
                return self.respond(409)
            self.server.state.update(busy=True, ready=False, error=False, message="正在读取文件")
        directory = self.server.workspace / secrets.token_hex(8)
        directory.mkdir()
        path, output = directory / "city.osm.pbf", directory / "results"
        try:
            remaining = length
            with path.open("wb") as destination:
                while remaining:
                    chunk = self.rfile.read(min(1024 ** 2, remaining))
                    if not chunk:
                        raise ValueError("文件传输中断。")
                    destination.write(chunk)
                    remaining -= len(chunk)
        except Exception as error:
            with self.server.lock:
                self.server.state.update(busy=False, error=True, message=str(error))
            return self.respond(400)
        threading.Thread(target=self.server.process, args=(path, output, tolerance), daemon=True).start()
        self.respond(202, b"{}")


def serve(port=8768, browser=True):
    with tempfile.TemporaryDirectory(prefix="street-skeleton-") as workspace:
        server = Server(port, workspace)
        url = f"http://127.0.0.1:{server.server_port}"
        print(url, flush=True)
        if browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
