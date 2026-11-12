import base64
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zlib

from agentboard.mcp import MCPConnection
from agentboard.schema import BusinessError
from agentboard.service import BoardService, AGENT, USER


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(self.temp.name)

    def call(self, tool, **args):
        result = self.service.call(tool, {"board_id":"board_local","command_id":str(uuid4()),**args},USER)
        self.assertTrue(result["ok"],result)
        return result["data"]

    def card(self, **overrides):
        return self.call("push_card",card={"type":"text","type_version":"1.0","title":"公式与表格",
            "props":{"font_size":24},"state":{"text":"# 标题\n\n$G(s)=1/s$\n\n| 参数 | 值 |\n| --- | --- |\n| K | 2 |"},
            "layout":{"space":"world","x":-300,"y":-100,"w":400,"h":220},**overrides})["card"]

    def shot(self, **args):
        result = self.service.call("get_board_screenshot",{"board_id":"board_local",**args},AGENT)
        self.assertTrue(result["ok"],result)
        data = result["data"]
        picture = base64.b64decode(data["image_base64"])
        self.assertEqual(picture[:8],b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II",picture[16:24]),(data["width"],data["height"]))
        self.assertEqual(Path(data["path"]).read_bytes(),picture)
        self.assertNotIn("image_base64",json.loads(Path(data["path"]).with_suffix(".json").read_text(encoding="utf-8")))
        return data

    def require_renderer(self):
        if not importlib.util.find_spec("playwright"):
            self.skipTest("Install requirements-screenshot.txt and Edge/Chrome/Chromium for renderer integration tests")

    def test_validation_and_revoked_read_do_not_launch_browser(self):
        with patch("agentboard.screenshot._render",side_effect=AssertionError("must not launch")):
            for args in ({"view":"card"},{"view":"region"},{"view":"all","card_id":"x"},
                         {"width":5000},{"region":{"x":0,"y":0,"w":0,"h":10},"view":"region"},
                         {"width":True},{"region":{"x":float("nan"),"y":0,"w":10,"h":10},"view":"region"},
                         {"path":"C:/elsewhere.png"}):
                result=self.service.call("get_board_screenshot",{"board_id":"board_local",**args},AGENT)
                self.assertEqual(result["error"]["code"],"INVALID_ARGUMENT",result)
            self.call("update_board",agent_permissions={"read":False})
            result=self.service.call("get_board_screenshot",{"board_id":"board_local"},AGENT)
            self.assertEqual(result["error"]["code"],"FORBIDDEN")

    def test_revocation_during_render_prevents_image_and_file_disclosure(self):
        def revoke(*args):
            self.call("update_board",agent_permissions={"read":False})
            return b"image",{}
        with patch("agentboard.screenshot._render",side_effect=revoke):
            result=self.service.call("get_board_screenshot",{"board_id":"board_local"},AGENT)
        self.assertEqual(result["error"]["code"],"FORBIDDEN")
        self.assertFalse((Path(self.temp.name)/"screenshots").exists())

    def test_dependency_error_is_a_structured_business_failure(self):
        with patch("agentboard.screenshot._render",side_effect=BusinessError("SCREENSHOT_DEPENDENCY_MISSING","安装依赖")):
            result=self.service.call("get_board_screenshot",{"board_id":"board_local"},AGENT)
        self.assertEqual(result["error"]["code"],"SCREENSHOT_DEPENDENCY_MISSING")

    def test_real_overview_single_card_and_region_mapping_are_read_only(self):
        self.require_renderer()
        first=self.card()
        second=self.card(title="远处卡片",layout={"space":"world","x":50000,"y":-1200,"w":400,"h":220})
        before=self.service.call("get_board_snapshot",{"board_id":"board_local","view":"full"},AGENT)["data"]
        overview=self.shot(width=1200,height=800)
        self.assertEqual(len(overview["cards"]),2)
        self.assertLess(overview["camera"]["zoom"],.1)
        self.assertTrue(all(c["visible"] for c in overview["cards"]))
        self.assertEqual(overview["math_errors"],0)
        close=self.shot(view="card",card_id=first["id"],theme="dark",width=900,height=600)
        self.assertEqual([c["id"] for c in close["cards"]],[first["id"]])
        self.assertEqual(close["theme"],"dark")
        region=self.shot(view="region",region={"x":-400,"y":-200,"w":1200,"h":800},width=1200,height=800)
        card=next(c for c in region["cards"] if c["id"]==first["id"])
        self.assertEqual(card["pixels"]["x"],100)
        self.assertEqual(card["pixels"]["y"],100)
        self.assertFalse(next(c for c in region["cards"] if c["id"]==second["id"])["visible"])
        after=self.service.call("get_board_snapshot",{"board_id":"board_local","view":"full"},AGENT)["data"]
        self.assertEqual(before["revision"],after["revision"])
        self.assertEqual(before["items"],after["items"])

    def test_real_mcp_returns_native_png_and_image_assets(self):
        self.require_renderer()
        def chunk(kind,data):
            return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
        png=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",16,16,8,2,0,0,0))+chunk(
            b"IDAT",zlib.compress((b"\0"+b"\xff\0\0"*16)*16))+chunk(b"IEND",b"")
        asset=self.call("import_asset",name="红色.png",mode="imported",source={"kind":"inline",
            "mime_type":"image/png","data_base64":base64.b64encode(png).decode()})["asset"]
        self.card(type="image",title="图片卡",props={"fit":"contain","alt":"红色"},state={"asset_id":asset["id"]})
        mcp=MCPConnection(self.service)
        mcp.initialized=mcp.ready=True
        result=mcp.handle({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
            "name":"get_board_screenshot","arguments":{"board_id":"board_local","width":800,"height":600}}})["result"]
        self.assertFalse(result["isError"],result)
        self.assertEqual([c["type"] for c in result["content"]],["text","image"])
        self.assertEqual(result["content"][1]["mimeType"],"image/png")
        self.assertNotIn("image_base64",result["structuredContent"]["data"])
        self.assertEqual(result["structuredContent"]["data"]["warnings"],[])
        self.assertEqual(result["structuredContent"]["data"]["assets"][0]["id"],asset["id"])
        self.assertEqual(json.loads(result["content"][0]["text"]),result["structuredContent"])


if __name__ == "__main__":
    unittest.main()
