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

    def modern_request(self, method, **params):
        return {"jsonrpc":"2.0","id":101,"method":method,"params":{
            "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                     "io.modelcontextprotocol/clientInfo":{"name":"web-test","version":"1"},
                     "io.modelcontextprotocol/clientCapabilities":{}},**params}}

    def test_modern_discovery_and_tools_without_initialize(self):
        discovered=MCPConnection(self.service).handle(self.modern_request("server/discover"))
        self.assertIn("result",discovered)
        self.assertIn("2026-07-28",discovered["result"]["supportedVersions"])
        self.assertEqual(discovered["result"]["resultType"],"complete")
        self.assertEqual(discovered["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],"agentboard")
        # Requests on fresh connections must work independently of discovery.
        tools=MCPConnection(self.service).handle(self.modern_request("tools/list"))["result"]
        self.assertEqual(len(tools["tools"]),17)
        result=MCPConnection(self.service).handle(self.modern_request("tools/call",name="list_boards",arguments={}))
        self.assertTrue(result["result"]["structuredContent"]["ok"])
        self.assertEqual(result["result"]["resultType"],"complete")

    def test_modern_metadata_validation_does_not_unlock_legacy(self):
        mcp=MCPConnection(self.service)
        request=self.modern_request("tools/list")
        request["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"]="2099-01-01"
        error=mcp.handle(request)["error"]
        self.assertEqual(error["code"],-32022)
        self.assertIn("2026-07-28",error["data"]["supported"])
        request=self.modern_request("tools/list")
        del request["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        self.assertEqual(mcp.handle(request)["error"]["code"],-32602)
        mcp.handle(self.modern_request("server/discover"))
        self.assertEqual(mcp.handle({"jsonrpc":"2.0","id":3,"method":"tools/list"})["error"]["code"],-32600)

    def test_modern_stdio_process_and_authorization(self):
        requests=[self.modern_request("server/discover"),self.modern_request("tools/list"),
                  self.modern_request("tools/call",name="list_boards",arguments={})]
        for i,request in enumerate(requests): request["id"]=i+1
        entry=Path(__file__).resolve().parent.parent/"scripts/mcp_server.py"
        process=subprocess.run([sys.executable,str(entry),"--no-web","--data-dir",self.temp.name],
            input="\n".join(json.dumps(r) for r in requests)+"\n",encoding="utf-8",
            capture_output=True,timeout=15)
        self.assertEqual(process.returncode,0,process.stderr)
        replies=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(replies),3)
        self.assertTrue(all(r["result"]["resultType"]=="complete" for r in replies))
        self.assertEqual(len(replies[1]["result"]["tools"]),17)
        self.assertTrue(replies[2]["result"]["structuredContent"]["ok"])
        # Modern client metadata conveys protocol context, never authorization.
        from agentboard.service import USER
        changed=self.service.call("update_board",{"board_id":"board_local","command_id":"deny-modern",
            "agent_permissions":{"read":False}},USER)
        self.assertTrue(changed["ok"],changed)
        request=self.modern_request("tools/call",name="get_board_info",arguments={"board_id":"board_local"})
        request["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["kind"]="user"
        denied=MCPConnection(self.service).handle(request)["result"]
        self.assertTrue(denied["isError"])
        self.assertEqual(denied["structuredContent"]["error"]["code"],"FORBIDDEN")

    def test_mcp_lifecycle_catalog_and_error_channels(self):
        mcp = MCPConnection(self.service)
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["error"]["code"], -32600)
        mcp = self.init_mcp()
        catalog = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(catalog), 17)
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
                    "type": "text", "type_version": "1.0", "title": "真实 MCP 进程", "props": {"font_size": 24}, "state": {"text": "中文🙂"}}}}},
        ]
        entry = Path(__file__).resolve().parent.parent / "scripts" / "mcp_server.py"
        result = subprocess.run([sys.executable, str(entry), "--no-web", "--data-dir", self.temp.name],
            input="\n".join(json.dumps(item, ensure_ascii=False) for item in script) + "\n", encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, cwd=self.temp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(lines), 2)
        card = lines[1]["result"]["structuredContent"]["data"]["card"]
        self.assertEqual(card["state"]["text"], "中文🙂")
        self.assertEqual(card["props"]["font_size"], 24)
        self.assertEqual(card["layout"]["space"], "world")
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

    def test_offline_renderer_assets_and_vendor_boundary(self):
        port = self.start_http()
        for path, mime in (("/canvas.js", "javascript"), ("/render.js", "javascript"),
                           ("/vendor/katex/dist/katex.min.js", "javascript"),
                           ("/vendor/katex/dist/katex.min.css", "text/css"),
                           ("/vendor/katex/dist/fonts/KaTeX_Main-Regular.woff2", "font/woff2")):
            status, headers, body = self.request(port, "GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(mime, headers["Content-Type"])
            self.assertTrue(body)
        for path in ("/vendor/../../service.py", "/vendor/%2e%2e/%2e%2e/service.py",
                     "/vendor/manifest.json", "/vendor/katex/LICENSE"):
            self.assertEqual(self.request(port, "GET", path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
