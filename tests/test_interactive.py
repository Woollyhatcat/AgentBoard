import base64
from contextlib import closing
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from threading import Thread
import unittest
from uuid import uuid4

from agentboard.http import create_server
from agentboard.service import BoardService, AGENT, USER
from agentboard.schema import WRITES
from agentboard.screenshot import _prepare


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(self.temp.name)

    def call(self, tool, **args):
        request={"board_id":"board_local",**args}
        if tool in WRITES:request["command_id"]=str(uuid4())
        result = self.service.call(tool,request,USER)
        self.assertTrue(result["ok"],result)
        return result["data"]

    def create(self, **patch):
        return self.call("push_card",card={"type":"interactive","type_version":"1.0","title":"任意数学实验",
            "props":{"source":"<p>独立演示</p>","initial_state":{"x":2,"matrix":[[1,0],[0,1]]}},"state":{},**patch})["card"]

    def edit(self, card, changes):
        return self.call("edit_card",card_id=card["id"],expected_content_version=card["content_version"],changes=changes)["card"]

    def test_roundtrip_reset_baseline_and_independent_layout(self):
        card=self.create()
        self.assertEqual(card["state"]["values"],card["props"]["initial_state"])
        edited=self.edit(card,{"state":{"values":{"x":3}}})
        self.assertEqual(edited["props"]["source"],card["props"]["source"])
        self.assertEqual(edited["props"]["initial_state"]["x"],2)
        moved=self.call("edit_card",card_id=card["id"],expected_layout_version=1,
            changes={"layout":{"space":"world","x":-200}})["card"]
        self.assertEqual(moved["state"]["values"],{"x":3})
        self.assertEqual(moved["content_version"],2)
        snapshot=self.call("get_board_snapshot",view="summary")
        self.assertEqual(snapshot["items"][0]["preview"]["values"],{"x":3})
        restarted=BoardService(self.temp.name).call("read_card",{"board_id":"board_local","card_id":card["id"]},AGENT)
        self.assertEqual(restarted["data"]["card"],moved)

    def test_code_edit_preserves_parameters_and_lock_blocks_program_writes(self):
        card=self.create(state={"values":{"x":7}})
        edited=self.edit(card,{"props":{"source":"<canvas id='plot'></canvas>"}})
        self.assertEqual(edited["state"]["values"],{"x":7})
        locked=self.call("set_card_lock",card_id=card["id"],expected_version=edited["version"],locked=True)["card"]
        result=self.service.call("edit_card",{"board_id":"board_local","command_id":str(uuid4()),
            "card_id":card["id"],"expected_content_version":locked["content_version"],
            "changes":{"state":{"values":{"x":8}}}},AGENT)
        self.assertEqual(result["error"]["code"],"CARD_LOCKED")

    def test_real_mcp_creates_a_generated_program_with_json_state(self):
        requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"interactive-test","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"push_card","arguments":{
                "board_id":"board_local","command_id":"interactive-stdio","card":{"type":"interactive",
                    "type_version":"1.0","title":"独立生成的向量程序","props":{"source":"<svg></svg>",
                        "initial_state":{"a":[1,2],"playing":False}},"state":{}}}}}]
        script=Path(__file__).resolve().parent.parent/"scripts/mcp_server.py"
        process=subprocess.run([sys.executable,str(script),"--no-web","--data-dir",self.temp.name],
            input="\n".join(json.dumps(r,ensure_ascii=False) for r in requests)+"\n",encoding="utf-8",
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,cwd=self.temp.name)
        self.assertEqual(process.returncode,0,process.stderr)
        result=json.loads(process.stdout.splitlines()[-1])["result"]
        self.assertFalse(result["isError"],result)
        self.assertEqual(result["structuredContent"]["data"]["card"]["state"]["values"],{"a":[1,2],"playing":False})

    def test_json_limits_sources_and_bindings_validation(self):
        for props,state,layout in (({"source":""},{},None),
            ({"source":"x"},{"values":{"x":float("nan")}},None),
            ({"source":"x"},{"values":{"__proto__":{}}},None),
            ({"source":"x"},{"values":{"x":"大"*30000}},None),
            ({"source":"x","bindings":{"bad-name":{"card_id":"x","path":"/state/value"}}},{},None),
            ({"source":"x","bindings":{"x":{"card_id":"x","path":"/agent_permissions"}}},{},None),
            ({"source":"x","bindings":{"x":{"card_id":"missing","path":"/state/value"}}},{},None),
            ({"source":"x"},{},{"space":"world","x":0,"y":0,"w":500,"h":400,"height_mode":"auto"})):
            card={"type":"interactive","type_version":"1.0","title":"无效","props":props,"state":state}
            if layout:card["layout"]=layout
            result=self.service.call("push_card",{"board_id":"board_local","command_id":str(uuid4()),"card":card},AGENT)
            self.assertFalse(result["ok"],result)
            self.assertIn(result["error"]["code"],("VALIDATION_FAILED","INVALID_ARGUMENT"))

    def test_missing_source_and_cycle_do_not_break_unrelated_edits(self):
        first=self.create()
        second=self.create(props={"source":"<p>B</p>","bindings":{"x":{"card_id":first["id"],"path":"/state/values/x"}}})
        for target in (first,second):
            result=self.service.call("edit_card",{"board_id":"board_local","command_id":str(uuid4()),
                "card_id":first["id"],"expected_content_version":1,
                "changes":{"props":{"bindings":{"x":{"card_id":target["id"],"path":"/state/values"}}}}},AGENT)
            self.assertEqual(result["error"]["code"],"VALIDATION_FAILED")
        self.call("delete_card",card_id=first["id"],expected_version=first["version"])
        updated=self.edit(second,{"state":{"values":{"n":5}}})
        self.assertEqual(updated["state"]["values"],{"n":5})
        self.call("restore_card",card_id=first["id"],expected_version=2)

    def test_frame_endpoint_has_separate_csp_and_no_user_session(self):
        card=self.create()
        server=create_server(self.service,port=0)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        def request(path,headers=None):
            with closing(http.client.HTTPConnection("127.0.0.1",server.server_address[1],timeout=5)) as conn:
                conn.request("GET",path,headers=headers or {})
                result=conn.getresponse();return result.status,dict(result.getheaders()),result.read().decode("utf-8")
        status,headers,html=request("/api/interactive/"+card["id"]+"?key=frame-test&v=1")
        self.assertEqual(status,200)
        csp=headers["Content-Security-Policy"]
        self.assertIn("sandbox allow-scripts",csp)
        self.assertNotIn("allow-same-origin",csp)
        self.assertIn("connect-src 'none'",csp)
        self.assertIn("frame-ancestors 'self'",csp)
        self.assertIn("Object.defineProperty(window,\"Board\"",html)
        self.assertNotIn("__BOARD_FRAME_CONFIG__",html)
        self.assertIn("data:font/woff2;base64,",html)
        self.assertNotIn("X-AgentBoard-Session",html)
        self.assertEqual(request("/api/session",{"Origin":"null"})[0],403)
        self.assertEqual(request("/api/interactive/"+card["id"]+"?key=frame-test&v=999")[0],409)
        self.assertEqual(request("/api/interactive/"+card["id"]+"?key=invalid%3C")[0],400)
        self.assertIn("frame-ancestors 'none'",request("/")[1]["Content-Security-Policy"])

    def test_card_screenshot_keeps_hidden_source_snapshot_and_updates_plot(self):
        slider=self.call("push_card",card={"type":"slider","type_version":"1.0","title":"输入x",
            "props":{"min":0,"max":10,"step":1},"state":{"value":2}})["card"]
        source='''<svg viewBox="0 0 400 250" id="plot"></svg><script>
Board.deferReady(); Board.onChange(async ({inputs})=>{await new Promise(r=>setTimeout(r,150));
document.getElementById('plot').innerHTML=`<rect x="0" y="0" width="${inputs.x*30}" height="200" fill="teal"/>`;
Board.rendered();});</script>'''
        card=self.create(props={"source":source,"bindings":{"x":{"card_id":slider["id"],"path":"/state/value"}}},
            layout={"space":"world","x":-8000,"y":5000,"w":720,"h":700,"height_mode":"fixed"})
        payload=_prepare(self.service,{"board_id":"board_local","view":"card","card_id":card["id"]},AGENT)
        self.assertEqual([c["id"] for c in payload["cards"]],[card["id"]])
        self.assertEqual([c["id"] for c in payload["source_cards"]],[slider["id"]])
        if not importlib.util.find_spec("playwright"):self.skipTest("Screenshot renderer optional")
        def capture():
            result=self.service.call("get_board_screenshot",{"board_id":"board_local","view":"card","card_id":card["id"],"width":900,"height":700},AGENT)
            self.assertTrue(result["ok"],result)
            self.assertTrue(result["data"]["interactive"][0]["ready"])
            self.assertEqual(result["data"]["warnings"],[])
            return base64.b64decode(result["data"]["image_base64"])
        before=capture()
        self.edit(slider,{"state":{"value":8}})
        after=capture()
        self.assertNotEqual(before,after)
        current=self.call("read_card",card_id=card["id"])["card"]
        self.assertEqual(current["version"],card["version"])
        self.call("delete_card",card_id=slider["id"],expected_version=2)
        missing=self.service.call("get_board_screenshot",{"board_id":"board_local","view":"card","card_id":card["id"]},AGENT)
        self.assertTrue(missing["ok"],missing)
        self.assertIn("x",missing["data"]["interactive"][0]["input_errors"])
        self.assertTrue(missing["data"]["warnings"])

    def test_script_errors_and_deleted_inputs_are_visible_in_screenshot(self):
        if not importlib.util.find_spec("playwright"):self.skipTest("Screenshot renderer optional")
        card=self.create(props={"source":"<script>throw new Error('演示错误示例')</script>"})
        result=self.service.call("get_board_screenshot",{"board_id":"board_local","view":"card","card_id":card["id"]},AGENT)
        self.assertTrue(result["ok"],result)
        self.assertIn("演示错误示例",result["data"]["interactive"][0]["error"])
        self.assertTrue(result["data"]["warnings"])


if __name__ == "__main__":unittest.main()
