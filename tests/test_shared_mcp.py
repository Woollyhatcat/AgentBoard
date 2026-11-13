import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest

from agentboard.http import create_server
from agentboard.service import BoardService


class SharedMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service=BoardService(self.temp.name)
        self.server=create_server(self.service,port=0)
        self.port=self.server.server_address[1]
        thread=Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close(),thread.join(3)))
        self.token=(Path(self.temp.name)/"mcp-token").read_text()

    def rpc(self,message,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=5)
        try:
            connection.request("POST","/mcp",json.dumps(message),{
                "Content-Type":"application/json","Authorization":"Bearer "+self.token,**(headers or {})})
            response=connection.getresponse()
            body=response.read()
            return response.status,dict(response.getheaders()),json.loads(body) if body else None
        finally: connection.close()

    def init(self):
        return self.rpc({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}})

    def test_shared_legacy_sessions_are_isolated_and_agent_only(self):
        _,headers,result=self.init()
        self.assertIn("result",result)
        session={"Mcp-Session-Id":headers["Mcp-Session-Id"]}
        _,second,_=self.init()
        self.assertNotEqual(second["Mcp-Session-Id"],headers["Mcp-Session-Id"])
        self.assertEqual(self.rpc({"jsonrpc":"2.0","method":"notifications/initialized"},session)[0],202)
        message={"jsonrpc":"2.0","id":3,"method":"tools/list"}
        self.assertEqual(len(self.rpc(message,session)[2]["result"]["tools"]),17)
        self.assertIn("error",self.rpc(message,{"Mcp-Session-Id":second["Mcp-Session-Id"]})[2])
        versioned={**message,"params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2025-11-25"}}}
        self.assertEqual(len(self.rpc(versioned,{**session,"MCP-Protocol-Version":"2025-11-25"})[2]["result"]["tools"]),17)
        call={"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"set_card_lock","arguments":{}}}
        self.assertEqual(self.rpc(call,session)[2]["error"]["code"],-32602)

    def test_bearer_origin_and_modern_version_checks(self):
        message={"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{
            "io.modelcontextprotocol/protocolVersion":"2026-07-28",
            "io.modelcontextprotocol/clientInfo":{"name":"test","version":"1"},
            "io.modelcontextprotocol/clientCapabilities":{}}}}
        self.assertEqual(self.rpc(message,{"Authorization":"Bearer wrong"})[0],401)
        self.assertEqual(self.rpc(message,{"Origin":"https://evil.example"})[0],401)
        self.assertEqual(self.rpc(message)[0],400)
        status,_,result=self.rpc(message,{"MCP-Protocol-Version":"2026-07-28"})
        self.assertEqual(status,200)
        self.assertEqual(result["result"]["resultType"],"complete")
        self.assertIn("2026-07-28",result["result"]["supportedVersions"])

    def test_two_stdio_clients_execute_in_same_backend(self):
        entry=Path(__file__).resolve().parent.parent/"scripts/mcp_server.py"
        def client(agent,title):
            requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
                "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":agent,"version":"1"}}},
                {"jsonrpc":"2.0","method":"notifications/initialized"},
                {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"push_card","arguments":{
                    "board_id":"board_local","command_id":"shared-"+agent,"card":{
                        "type":"text","type_version":"1.0","title":title,"props":{},"state":{"text":agent}}}}}]
            process=subprocess.run([sys.executable,str(entry),"--data-dir",self.temp.name,"--port",str(self.port),
                "--agent-id",agent,"--agent-name",title],input="\n".join(json.dumps(r) for r in requests)+"\n",
                capture_output=True,encoding="utf-8",timeout=15)
            self.assertEqual(process.returncode,0,process.stderr)
            replies=[json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(replies),2)
            return replies[-1]["result"]["structuredContent"]["data"]["card"]
        observed=[]
        original=self.service.call
        def track(tool,*args,**kwargs):
            if tool=="push_card":observed.append(os.getpid())
            return original(tool,*args,**kwargs)
        self.service.call=track
        a=client("codex","Local Codex")
        b=client("tunnel","Web ChatGPT")
        self.assertEqual(observed,[os.getpid(),os.getpid()])
        self.assertEqual(a["created_by"]["id"],"codex")
        self.assertEqual(b["created_by"]["id"],"tunnel")
        self.assertEqual(a["created_by"]["kind"],"agent")
        self.assertEqual(len(self.service.call("get_card_list",{"board_id":"board_local"})["data"]["items"]),2)


if __name__=="__main__":unittest.main()
