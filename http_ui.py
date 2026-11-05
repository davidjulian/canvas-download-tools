"""Loopback interface adapted from the quiz log app; no request logging."""
import hmac
import json
import secrets
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from canvas_helpers import UIError
ROOT = Path(__file__).resolve().parent

class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, app, port=0):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.host = f"127.0.0.1:{self.server_port}"

    @property
    def url(self):
        return self.origin + "/#" + self.token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log paths, names, tokens, request bodies or page text.

    def send_error(self, code, message=None, explain=None):
        self.reply(code, b"Request could not be completed.", "text/plain; charset=utf-8")

    def reply(self, code, data, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def json_reply(self, value, status=200):
        self.reply(status, json.dumps(value, ensure_ascii=True).encode("utf-8"))

    def discard_small_post_body(self):
        # Consume a small rejected POST body so closing the connection does not
        # reset it before Windows clients receive the error response. Never parse
        # or log rejected data; limit both size and waiting time.
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 65536:
                    previous = self.connection.gettimeout()
                    try:
                        self.connection.settimeout(1)
                        self.rfile.read(length)
                    finally:
                        self.connection.settimeout(previous)
            except (OSError, ValueError):
                pass
    def reject(self):
        self.discard_small_post_body()
        self.send_error(403)
        return False

    def allowed(self, api=False):
        if self.headers.get("Host") != self.server.host:
            return self.reject()
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            return self.reject()
        if api:
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + self.server.token
            if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                return self.reject()
            if self.command == "POST" and origin != self.server.origin:
                return self.reject()
        return True

    def do_GET(self):
        mode, route = self.api_route()
        if not self.allowed(api=route.startswith("/api/")):
            return
        combined = getattr(self.server.app, "combined", False)
        assets = {"/common.js": ("common.js", "text/javascript; charset=utf-8")}
        if combined:
            assets["/"] = ("index.html", "text/html; charset=utf-8")
            assets["/style.css"] = ("style.css", "text/css; charset=utf-8")
            for tool in ("logs", "files"):
                assets["/" + tool + "/"] = (tool + "/index.html", "text/html; charset=utf-8")
                assets["/" + tool + "/app.js"] = (tool + "/app.js", "text/javascript; charset=utf-8")
                assets["/" + tool + "/style.css"] = (tool + "/style.css", "text/css; charset=utf-8")
        else:
            assets.update({"/": ("files/index.html", "text/html; charset=utf-8"),
                           "/app.js": ("files/app.js", "text/javascript; charset=utf-8"),
                           "/style.css": ("files/style.css", "text/css; charset=utf-8")})
        if self.path in assets:
            name, mime = assets[self.path]
            self.reply(200, (ROOT / "web" / name).read_bytes(), mime)
        elif route == "/api/state":
            app = self.server.app
            self.json_reply(app.snapshot_for(mode) if combined and mode else app.snapshot())
        else:
            self.send_error(404)

    def api_route(self):
        for mode in ("logs", "files"):
            prefix = "/" + mode
            if self.path.startswith(prefix + "/api/"):
                return mode, self.path[len(prefix):]
        return None, self.path

    def do_POST(self):
        if not self.allowed(api=True):
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.discard_small_post_body()
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return
        try:
            app = self.server.app
            combined = getattr(app, "combined", False)
            mode, route = self.api_route()
            if route == "/api/action":
                result = app.command_for(mode, payload.get("action"), payload) if combined else app.command(payload.get("action"), payload)
            elif combined and self.path == "/api/select":
                result = app.select_mode(payload.get("mode"))
            elif combined and self.path == "/api/settings":
                result = app.set_preferences(payload)
            elif route == "/api/browse":
                result = app.browse()
            elif route in ("/api/open-folder", "/api/open-first", "/api/open-key", "/api/open-summary"):
                options = {"first": True} if route.endswith("open-first") else {"key": True} if route.endswith("open-key") else {"summary": True} if route.endswith("open-summary") else {}
                result = app.open_local_for(mode, **options) if combined else app.open_local(**options)
            elif route == "/api/quit":
                app.shutdown()
                result = {}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_error(404)
                return
            self.json_reply(result)
        except UIError as error:
            self.json_reply({"error": str(error)}, 400)
        except Exception:
            self.json_reply({"error": "That action could not finish. Check your local folder and browser."}, 500)
