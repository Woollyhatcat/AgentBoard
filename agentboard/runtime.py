"""Start or reuse the local web process independently of an MCP conversation."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

_children = []


def storage_id(data_dir):
    database = Path(data_dir).resolve() / "agentboard.sqlite3"
    return hashlib.sha256(os.path.normcase(str(database)).encode("utf-8")).hexdigest()


def _request(url, data=None, headers=None, timeout=1):
    # Local service checks must not be routed through a user's HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url, data=data, headers=headers or {}), timeout=timeout) as response:
        return json.load(response)


def _matches_web(data_dir, url):
    try:
        health = _request(url + "/api/health", timeout=0.5)
        return (isinstance(health, dict) and health.get("service") == "agentboard" and
                health.get("storage_id") == storage_id(data_dir) and health.get("status") == "ready")
    except (OSError, URLError, ValueError):
        return False


def web_status(data_dir, port=8765):
    url = f"http://127.0.0.1:{port}"
    if _matches_web(data_dir, url):
        return {"status": "running", "url": url}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            pass
    except OSError:
        return {"status": "stopped", "url": url}
    # Another chat can bind the port between the first health and TCP probes.
    if _matches_web(data_dir, url):
        return {"status": "running", "url": url}
    return {"status": "unavailable", "url": url, "code": "PORT_IN_USE",
            "message": "该端口由其他服务或不同数据目录的看板占用，未接管或停止该服务。"}


def _reap_children():
    _children[:] = [child for child in _children if child.poll() is None]


def ensure_web(data_dir, port=8765, timeout=5):
    """Idempotently ensure the same board is reachable; never replace another listener."""
    _reap_children()
    current = web_status(data_dir, port)
    if current["status"] != "stopped":
        return current
    data_dir = Path(data_dir).resolve()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        kwargs = {"stdin": subprocess.DEVNULL, "cwd": str(Path(__file__).resolve().parent.parent),
                  "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with (data_dir / "web.log").open("ab") as log:
            child = subprocess.Popen(
                [sys.executable, "-u", "-m", "agentboard", "serve", "--data-dir", str(data_dir),
                 "--port", str(port)], stdout=log, stderr=log, **kwargs)
        _children.append(child)
    except OSError:
        return {"status": "unavailable", "url": current["url"], "code": "WEB_START_FAILED",
                "message": "无法启动本地网页服务，请检查数据目录权限和 Python 运行环境。"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = web_status(data_dir, port)
        if current["status"] == "running":
            return current
        # A socket can become bound between the HTTP probe and the TCP probe.
        # Wait for its health response rather than misreporting our own startup as a conflict.
        time.sleep(0.05)
    if current["status"] == "unavailable":
        return current
    return {"status": "unavailable", "url": current["url"], "code": "WEB_START_FAILED",
            "message": "网页服务未在规定时间内就绪，请查看数据目录中的 web.log。"}


def stop_web(data_dir, port=8765):
    current = web_status(data_dir, port)
    if current["status"] != "running":
        return current
    try:
        session = _request(current["url"] + "/api/session")
        _request(current["url"] + "/api/shutdown", data=b"{}", headers={
            "Content-Type": "application/json", "X-AgentBoard-Session": session["session"]})
    except (OSError, URLError, ValueError, KeyError):
        return {"status": "unavailable", "url": current["url"], "code": "WEB_STOP_FAILED",
                "message": "网页服务停止结果未确认，请重新核对服务状态。"}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = web_status(data_dir, port)
        if current["status"] == "stopped":
            _reap_children()
            return current
        time.sleep(0.05)
    return {"status": "unavailable", "url": current["url"], "code": "WEB_STOP_FAILED",
            "message": "网页服务尚未停止，请稍后重试。"}
