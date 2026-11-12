"""Loopback-only web adapter. Browser administration never uses agent identity."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import socket
from threading import Thread
from urllib.parse import urlsplit, unquote, parse_qs
import re

from .mcp import reject_constant
from .service import USER, LIMITS, dump
from .schema import BusinessError
from .runtime import storage_id
from .interactive import FRAME_CSP, frame_document

STATIC = Path(__file__).parent / "web"


class LoopbackServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR can allow two live servers to bind the same port.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(service, host="127.0.0.1", port=8765):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("首版仅允许绑定本机回环地址。")
    session = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentBoard/0.1"

        def _allowed(self):
            actual_port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
            host_header = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host_header in hosts and (not origin or origin == "http://" + host_header)

        def _send(self, status, content, content_type="application/json; charset=utf-8", headers=None):
            if not isinstance(content, bytes):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            extra = dict(headers or {})
            self.send_header("Content-Security-Policy", extra.pop("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self'; style-src-attr 'unsafe-inline'; font-src 'self'; "
                             "img-src 'self' blob:; connect-src 'self'; "
                             "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"))
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status, value):
            self._send(status, dump(value))

        def do_GET(self):
            if not self._allowed():
                self._json(403, {"error": "访问来源不允许。"})
                return
            path = urlsplit(self.path).path
            if path == "/api/session":
                self._json(200, {"session": session, "board_id": "board_local"})
            elif path == "/api/health":
                self._json(200, {"service": "agentboard", "status": "ready", "board_id": "board_local",
                                 "storage_id": storage_id(service.data_dir)})
            elif path.startswith("/api/interactive/"):
                query = parse_qs(urlsplit(self.path).query)
                key = query.get("key",[""])[0]
                card_id = unquote(path.removeprefix("/api/interactive/"))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",key):
                    self._json(400,{"error":"演示会话标识无效。"}); return
                result = service.call("read_card",{"board_id":"board_local","card_id":card_id},USER)
                if not result["ok"]:
                    self._json(404,result); return
                card = result["data"]["card"]
                if card["type"] != "interactive":
                    self._json(404,{"error":"不是交互卡片。"}); return
                if query.get("v",[str(card["content_version"])])[0] != str(card["content_version"]):
                    self._json(409,{"error":"演示已更新，请加载最新版本。"}); return
                self._send(200,frame_document(card,key),"text/html; charset=utf-8",{
                    "Content-Security-Policy":FRAME_CSP,"Permissions-Policy":"camera=(), microphone=(), geolocation=()"})
            elif path.startswith("/api/assets/"):
                parts = path.strip("/").split("/")
                if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "download"):
                    self._json(404, {"error": "地址不存在。"})
                    return
                try:
                    asset, content = service.asset_bytes_for_user(unquote(parts[2]))
                    preview = len(parts) == 3 and asset["preview_kind"] == "image"
                    from urllib.parse import quote
                    disposition = ("inline" if preview else "attachment") + "; filename*=UTF-8''" + quote(asset["name"])
                    self._send(200, content, asset["mime_type"], {"Content-Disposition": disposition})
                except BusinessError as error:
                    self._json(404, {"ok": False, "error": error.error})
            else:
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/canvas.js": ("canvas.js", "text/javascript; charset=utf-8"),
                         "/render.js": ("render.js", "text/javascript; charset=utf-8"),
                         "/interactive.js": ("interactive.js", "text/javascript; charset=utf-8"),
                         "/examples/complex-angle.html": ("examples/complex-angle.html", "text/plain; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path.startswith("/vendor/"):
                    local = (STATIC / unquote(path).lstrip("/")).resolve()
                    if local.is_relative_to((STATIC / "vendor").resolve()) and local.is_file():
                        mime = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                                ".woff2": "font/woff2"}.get(local.suffix)
                        if mime:
                            self._send(200, local.read_bytes(), mime)
                            return
                if path not in files:
                    self._json(404, {"error": "地址不存在。"})
                    return
                filename, mime = files[path]
                self._send(200, (STATIC / filename).read_bytes(), mime)

        def do_POST(self):
            if not self._allowed() or not secrets.compare_digest(
                    self.headers.get("X-AgentBoard-Session", ""), session):
                self._json(403, {"error": "会话或来源无效，请重新连接。"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(415, {"error": "请求必须为 JSON。"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= LIMITS["request_max_bytes"]:
                    self._json(413, {"error": "请求为空或超过大小限额。"})
                    return
                self.connection.settimeout(30)
                args = json.loads(self.rfile.read(length), parse_constant=reject_constant)
                path = urlsplit(self.path).path
                if path == "/api/shutdown":
                    self._json(200, {"ok": True})
                    Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if not path.startswith("/api/tools/"):
                    self._json(404, {"error": "操作地址不存在。"})
                    return
                tool = path.removeprefix("/api/tools/")
                response = service.call(tool, args, USER)
                self._json(200, response)
            except (ValueError, UnicodeDecodeError, RecursionError):
                self._json(400, {"error": "请求不是有效 JSON。"})

        def log_message(self, format, *args):
            # Keep terminal output concise; do not log contents or authentication tokens.
            if args and str(args[1] if len(args) > 1 else "").startswith("5"):
                super().log_message(format, *args)

    server = LoopbackServer((host, port), Handler)
    server.daemon_threads = True
    return server
