from contextlib import closing
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest

from agentboard.http import create_server
from agentboard.mcp import MCPConnection
from agentboard.service import BoardService, AGENT


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(self.temp.name)

    def init_mcp(self):
        mcp = MCPConnection(self.service)
        result = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(result["result"]["protocolVersion"], "2025-11-25")
        mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return mcp

    def test_mcp_lifecycle_catalog_and_error_channels(self):
        mcp = MCPConnection(self.service)
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["error"]["code"], -32600)
        mcp = self.init_mcp()
        catalog = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(catalog), 16)
        self.assertTrue(all("inputSchema" in tool and "outputSchema" in tool for tool in catalog))
        self.assertNotIn("set_card_lock", {tool["name"] for tool in catalog})
        unknown = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "set_card_lock"}})
        self.assertEqual(unknown["error"]["code"], -32602)
        failed = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "read_card", "arguments": {"board_id": "board_local", "card_id": "missing"}}})["result"]
        self.assertTrue(failed["isError"])
        self.assertEqual(json.loads(failed["content"][0]["text"]), failed["structuredContent"])
        self.assertEqual(failed["structuredContent"]["error"]["code"], "NOT_FOUND")

    def test_stdio_real_process_utf8_and_persistent_shared_state(self):
        script = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "push_card", "arguments": {"board_id": "board_local", "command_id": "stdio-test", "card": {
                    "type": "text", "type_version": "1.0", "title": "真实 MCP 进程", "props": {}, "state": {"text": "中文🙂"}}}}},
        ]
        entry = Path(__file__).resolve().parent.parent / "scripts" / "mcp_server.py"
        result = subprocess.run([sys.executable, str(entry), "--data-dir", self.temp.name],
            input="\n".join(json.dumps(item, ensure_ascii=False) for item in script) + "\n", encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, cwd=self.temp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(lines), 2)
        card = lines[1]["result"]["structuredContent"]["data"]["card"]
        self.assertEqual(card["state"]["text"], "中文🙂")
        read = self.service.call("read_card", {"board_id": "board_local", "card_id": card["id"]})
        self.assertEqual(read["data"]["card"], card)

    def start_http(self):
        server = create_server(self.service, port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join(timeout=3)
        self.addCleanup(stop)
        return server.server_address[1]

    def request(self, port, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_http_session_origin_host_and_user_identity(self):
        port = self.start_http()
        status, headers, body = self.request(port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(b'lang="zh-CN"', body)
        session = json.loads(self.request(port, "GET", "/api/session")[2])["session"]
        args = {"board_id": "board_local", "command_id": "browser", "card": {
            "type": "text", "type_version": "1.0", "title": "网页创建", "props": {}, "state": {"text": ""}}}
        headers = {"Content-Type": "application/json", "X-AgentBoard-Session": session}
        status, _, body = self.request(port, "POST", "/api/tools/push_card", json.dumps(args), headers)
        self.assertEqual(status, 200)
        card = json.loads(body)["data"]["card"]
        self.assertEqual(card["created_by"]["kind"], "user")
        status, _, _ = self.request(port, "POST", "/api/tools/push_card", json.dumps(args), headers | {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request(port, "GET", "/api/session", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request(port, "POST", "/api/tools/push_card", json.dumps(args), {"Content-Type": "application/json"})[0], 403)

    def test_http_asset_is_attachment_and_static_traversal_denied(self):
        port = self.start_http()
        session = json.loads(self.request(port, "GET", "/api/session")[2])["session"]
        args = {"board_id": "board_local", "command_id": "upload", "name": "untrusted.html", "data_base64": "PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="}
        headers = {"Content-Type": "application/json", "X-AgentBoard-Session": session}
        _, _, body = self.request(port, "POST", "/api/tools/upload_asset", json.dumps(args), headers)
        asset = json.loads(body)["data"]["asset"]
        status, headers, content = self.request(port, "GET", "/api/assets/" + asset["id"])
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request(port, "GET", "/../agentboard.sqlite3")[0], 404)


if __name__ == "__main__":
    unittest.main()
