"""Authenticated loopback MCP endpoint and a stdio forwarding client."""

import json
import secrets
import sys
from pathlib import Path
from threading import Lock
import time
from urllib.request import ProxyHandler, Request, build_opener

from .mcp import MCPConnection, PROTOCOL_VERSIONS, reject_constant
from .service import AGENT, LIMITS, dump
from .runtime import ensure_web


class SharedMCP:
    def __init__(self, service, port):
        self.service, self.port = service, port
        self.token = secrets.token_urlsafe(32)
        self.sessions, self.lock = {}, Lock()

    def dispatch(self, message, session_id=None, actor=None):
        actor = actor or AGENT
        params = message.get("params", {}) if isinstance(message, dict) else {}
        method = message.get("method") if isinstance(message, dict) else None
        metadata = params.get("_meta", {}) if isinstance(params, dict) else {}
        version = metadata.get("io.modelcontextprotocol/protocolVersion") if isinstance(metadata, dict) else None
        modern = version is not None and version not in PROTOCOL_VERSIONS
        headers = {}
        if method == "initialize":
            with self.lock:
                now = time.monotonic()
                self.sessions = {k:v for k,v in self.sessions.items() if now-v[2] < 1800}
                if len(self.sessions) >= 256:
                    return 503, {"error":"MCP session limit reached"}, headers
                session_id = secrets.token_urlsafe(24)
                entry = [MCPConnection(self.service,actor,web_port=self.port),Lock(),now]
                self.sessions[session_id] = entry
                headers["Mcp-Session-Id"] = session_id
        elif session_id and not modern:
            with self.lock:
                entry = self.sessions.get(session_id)
                if not entry or time.monotonic()-entry[2] >= 1800:
                    return 404, {"error":"MCP session expired; initialize again"}, headers
                if entry[0].actor != actor:
                    return 403, {"error":"MCP session identity mismatch"}, headers
                entry[2] = time.monotonic()
        else:
            entry = [MCPConnection(self.service,actor,web_port=self.port),Lock(),time.monotonic()]
        with entry[1]:
            response = entry[0].handle(message)
        return (202 if response is None else 200), response, headers


def serve_shared_stdio(data_dir, port=8765, actor=None):
    """Relay only: all MCP protocol handling and tools run in the web process."""
    status = ensure_web(data_dir,port)
    if status["status"] != "running":
        raise RuntimeError("Shared AgentBoard service is unavailable")
    opener = build_opener(ProxyHandler({}))
    token_path = Path(data_dir).resolve()/"mcp-token"
    if not token_path.is_file():
        raise RuntimeError("Restart AgentBoard web service to enable shared MCP")
    session_id = None
    actor = actor or AGENT
    from urllib.parse import quote
    if hasattr(sys.stdin,"reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8",newline="\n")
    while True:
        line = sys.stdin.buffer.readline(LIMITS["request_max_bytes"]+1)
        if not line:
            return
        message = None
        try:
            if len(line) > LIMITS["request_max_bytes"]:
                raise ValueError("Request too large")
            message = json.loads(line,parse_constant=reject_constant)
            headers = {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
                       "Authorization":"Bearer "+token_path.read_text(encoding="ascii").strip(),
                       "X-AgentBoard-Agent-Id":quote(actor["id"],safe=""),
                       "X-AgentBoard-Agent-Name":quote(actor["display_name"],safe="")}
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            params = message.get("params",{}) if isinstance(message,dict) else {}
            meta = params.get("_meta",{}) if isinstance(params,dict) else {}
            if isinstance(meta,dict) and meta.get("io.modelcontextprotocol/protocolVersion"):
                headers["MCP-Protocol-Version"] = str(meta["io.modelcontextprotocol/protocolVersion"])
            with opener.open(Request(status["url"]+"/mcp",data=dump(message).encode("utf-8"),headers=headers),timeout=120) as result:
                if result.headers.get("Mcp-Session-Id"):
                    session_id = result.headers["Mcp-Session-Id"]
                body = result.read()
                response = json.loads(body) if body else None
        except (OSError,ValueError,RecursionError):
            if isinstance(message,dict) and "id" not in message:
                continue
            response = MCPConnection.error(message.get("id") if isinstance(message,dict) else None,
                -32603,"共享MCP服务请求失败，请检查后台服务与连接状态。")
        if response is not None:
            print(dump(response),flush=True)
