"""Exercise the real MCP launch path, including the user-facing web service."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from urllib.request import Request, urlopen

from agentboard.http import create_server
from agentboard.runtime import ensure_web, stop_web, web_status, storage_id
from agentboard.service import BoardService
from threading import Thread
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


class WebLifecycleTests(unittest.TestCase):
    def test_health_retries_when_another_chat_binds_during_probe(self):
        from urllib.error import URLError
        with tempfile.TemporaryDirectory() as folder:
            health = {"service": "agentboard", "status": "ready", "storage_id": storage_id(folder)}
            with patch("agentboard.runtime._request", side_effect=[URLError("starting"), health]), \
                    patch("agentboard.runtime.socket.create_connection"):
                self.assertEqual(web_status(folder)["status"], "running")

    def free_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_start_reuses_existing_service_and_can_restart_after_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            port = self.free_port()
            try:
                self.assertEqual(ensure_web(folder, port)["status"], "running")
                with patch("agentboard.runtime.subprocess.Popen", side_effect=AssertionError("duplicate process")):
                    self.assertEqual(ensure_web(folder, port)["status"], "running")
                self.assertEqual(stop_web(folder, port)["status"], "stopped")
                self.assertEqual(ensure_web(folder, port)["status"], "running")
            finally:
                self.assertEqual(stop_web(folder, port)["status"], "stopped")

    def test_concurrent_chats_share_one_web_listener(self):
        with tempfile.TemporaryDirectory() as folder:
            port = self.free_port()
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(lambda _: ensure_web(folder, port), range(3)))
                self.assertTrue(all(result["status"] == "running" for result in results), results)
                self.assertEqual(len({result["url"] for result in results}), 1)
            finally:
                self.assertEqual(stop_web(folder, port)["status"], "stopped")

    def test_different_data_directory_is_never_reused_or_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            server = create_server(BoardService(data / "first"), port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with patch("agentboard.runtime.subprocess.Popen", side_effect=AssertionError("wrong board")):
                    self.assertEqual(ensure_web(data / "second", port)["code"], "PORT_IN_USE")
                self.assertEqual(stop_web(data / "second", port)["code"], "PORT_IN_USE")
                self.assertEqual(web_status(data / "first", port)["status"], "running")
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_shutdown_requires_browser_session(self):
        with tempfile.TemporaryDirectory() as folder:
            port = self.free_port()
            try:
                self.assertEqual(ensure_web(folder, port)["status"], "running")
                from urllib.error import HTTPError
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
                                    headers={"Content-Type": "application/json"}), timeout=2)
                self.assertEqual(failure.exception.code, 403)
                self.assertEqual(web_status(folder, port)["status"], "running")
            finally:
                self.assertEqual(stop_web(folder, port)["status"], "stopped")

    def test_mcp_launch_makes_same_board_available_in_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "mcp_server.py"),
                 "--data-dir", folder, "--port", str(port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", cwd=folder,
            )
            try:
                initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "browser-startup-regression", "version": "1"}}}
                process.stdin.write(json.dumps(initialize) + "\n")
                process.stdin.flush()
                result = json.loads(process.stdout.readline())
                self.assertIn("result", result)
                # This is the reported symptom: MCP works while the web port refuses connections.
                with urlopen(base + "/api/health", timeout=2) as response:
                    health = json.load(response)
                self.assertEqual(health["service"], "agentboard")
                with urlopen(base + "/", timeout=2) as response:
                    self.assertIn("卡片画布", response.read().decode("utf-8"))
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "get_board_info", "arguments": {"board_id": "board_local"}}}) + "\n")
                process.stdin.flush()
                info = json.loads(process.stdout.readline())["result"]["structuredContent"]["data"]
                self.assertEqual(info["web"]["url"], base)
                self.assertEqual(info["web"]["status"], "running")
                # Ending a chat must not remove the board from the user's browser.
                process.communicate(timeout=5)
                with urlopen(base + "/api/health", timeout=2) as response:
                    self.assertEqual(json.load(response)["board_id"], info["id"])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                subprocess.run([sys.executable, "-m", "agentboard", "stop", "--data-dir", folder,
                                "--port", str(port)], cwd=ROOT, capture_output=True, timeout=10)
