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
from .cable import load_planner
from .routing import RouteError

PLANNER_WEB = Path(__file__).parent / 'planner_web'


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, workspace, result_directory=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.workspace = Path(workspace)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.state = {"busy": False, "message": "选择城市 PBF 文件", "ready": False, "error": False}
        self.result_json = None
        self.result_dir = None
        self.cable_network = None
        self.cable_json = None
        if result_directory:
            self.cable_network, self.cable_json = load_planner(result_directory)
            self.state['message'] = '已载入选线路网，可继续导入其他城市'

    def progress(self, message):
        with self.lock:
            self.state["message"] = message

    def process(self, path, output, tolerance):
        try:
            result = run(path, output, tolerance, self.progress)
            data = gzip.compress(json.dumps(payload(result), ensure_ascii=False).encode())
            network, cable_json = load_planner(output)
            with self.lock:
                self.result_json, self.result_dir = data, output
                self.cable_network, self.cable_json = network, cable_json
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
        supplied = self.headers.get("X-Street-Token", self.headers.get('X-Cable-Token', ''))
        return secrets.compare_digest(supplied, self.server.token)

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403)
        parsed = urlparse(self.path)
        if parsed.path in {'/planner', '/planner/'}:
            html = (PLANNER_WEB / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', self.server.token)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        planner_static = {'/planner/app.js': 'application/javascript', '/planner/style.css': 'text/css'}
        if parsed.path in planner_static:
            return self.respond(200, (PLANNER_WEB / Path(parsed.path).name).read_bytes(), planner_static[parsed.path] + '; charset=utf-8')
        if parsed.path in {"/", "/index.html"}:
            html = (WEB / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", self.server.token)
            return self.respond(200, html.encode(), "text/html; charset=utf-8")
        static = {"/style.css": "text/css", "/app.js": "application/javascript"}
        if parsed.path in static:
            return self.respond(200, (WEB / parsed.path[1:]).read_bytes(), static[parsed.path] + "; charset=utf-8")
        if not self.authorized():
            return self.respond(403)
        if parsed.path == '/planner/data.json.gz':
            with self.server.lock:
                data = self.server.cable_json
            if data is None:
                return self.respond(409, json.dumps({'error': '请先返回首页导入城市数据。'}, ensure_ascii=False).encode())
            return self.respond(200, data, 'application/octet-stream')
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
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
            return self.respond(403)
        if parsed.path in {'/planner/api/snap', '/planner/api/route'}:
            with self.server.lock:
                network = self.server.cable_network
            if network is None:
                return self.respond(409, json.dumps({'error': '请先返回首页导入城市数据。'}, ensure_ascii=False).encode())
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16384:
                    raise RouteError('请求内容无效。')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise RouteError('请求内容无效。')
                result = (network.snap(data.get('point')) if parsed.path.endswith('/snap')
                          else network.route(data.get('start'), data.get('end')))
                return self.respond(200, json.dumps(result, ensure_ascii=False).encode())
            except RouteError as error:
                return self.respond(422, json.dumps({'error': str(error), 'code': error.code}, ensure_ascii=False).encode())
            except (ValueError, TypeError, KeyError):
                return self.respond(400, json.dumps({'error': '请求内容无效。'}, ensure_ascii=False).encode())
            except Exception:
                return self.respond(500, json.dumps({'error': '本次计算未完成，请重新选点。'}, ensure_ascii=False).encode())
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


def serve(port=8768, browser=True, result_directory=None):
    with tempfile.TemporaryDirectory(prefix="street-skeleton-") as workspace:
        server = Server(port, workspace, result_directory)
        url = f"http://127.0.0.1:{server.server_port}"
        if result_directory:
            url += '/planner/'
        print(url, flush=True)
        if browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
