"""Portable files tested between independent data directories, without path grants."""

import base64
from contextlib import closing
from copy import deepcopy
from io import BytesIO
import http.client
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from agentboard.board_archive import read_archive, MAX_ARCHIVE_BYTES
from agentboard.http import create_server
from agentboard.schema import BusinessError, WRITES
from agentboard.service import AGENT, USER, BoardService, dump


class BoardArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.source = BoardService(Path(self.temp.name)/"source")
        self.target = BoardService(Path(self.temp.name)/"target")

    def call(self, service, tool, **args):
        request = {"board_id":"board_local",**args}
        if tool in WRITES: request.setdefault("command_id",str(uuid4()))
        result = service.call(tool,request,USER)
        self.assertTrue(result["ok"],result)
        return result["data"]

    def push(self,kind,title,state,props=None):
        return self.call(self.source,"push_card",card={"type":kind,"type_version":"1.0","title":title,
            "props":props or {},"state":state})["card"]

    def sample(self):
        slider = self.push("slider","共享参数",{"value":3},{"min":0,"max":10,"step":1})
        self.push("checkbox","确认",{"checked":True},{"label":"已确认"})
        self.push("input","输入",{"value":"讨论中"},{"mode":"text"})
        image = self.call(self.source,"import_asset",name="image.png",mode="imported",source={"kind":"inline",
            "mime_type":"image/png","data_base64":"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB1sAAAAASUVORK5CYII="})["asset"]
        self.push("image","插图",{"asset_id":image["id"]})
        folder = Path(self.temp.name)/"external"; folder.mkdir()
        (folder/"note.txt").write_text("跨电脑保留的资料",encoding="utf-8")
        root = self.call(self.source,"grant_root",path=str(folder),label="原机文件")
        ref = self.call(self.source,"import_asset",name="引用资料",mode="reference",source={"kind":"approved_file",
            "root_id":root["root_id"],"relative_path":"note.txt"})["asset"]
        self.push("file","资料文件",{"asset_id":ref["id"],"note":"原机引用"})
        self.push("text","公式",{"text":"$$x^2$$\n\n[图片](/api/assets/"+image["id"]+")"},{"font_size":24})
        self.push("interactive","联动图",{"values":{"scale":2}},{"source":"<p>图形</p>","initial_state":{"scale":1},
            "bindings":{"x":{"card_id":slider["id"],"path":"/state/value"}}})
        trash = self.push("text","回收站",{"text":"已删除卡片"})
        self.call(self.source,"delete_card",card_id=trash["id"],expected_version=trash["version"])
        board = self.call(self.source,"get_board_info")
        self.call(self.source,"set_board_lock",expected_version=board["version"],locked=True)
        return root

    def raw(self): return self.source.export_board_archive("board_local",USER)[0]

    def rewrite(self,raw,change):
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            contents = {entry:archive.read(entry) for entry in archive.namelist()}
        manifest = json.loads(contents["manifest.json"])
        change(manifest,contents)
        contents["manifest.json"] = dump(manifest).encode()
        output = BytesIO()
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            for key,data in contents.items(): archive.writestr(key,data)
        return output.getvalue()

    def test_roundtrip_all_types_references_trash_locks_and_links(self):
        root = self.sample()
        before = self.call(self.source,"get_board_snapshot",view="full")
        raw = self.raw()
        self.assertEqual(self.call(self.source,"get_board_snapshot",view="full")["items"],before["items"])
        self.call(self.source,"revoke_root",root_id=root["root_id"])
        for default in self.call(self.target,"get_asset_sources")["roots"]:
            self.call(self.target,"revoke_root",root_id=default["root_id"])
        imported = self.target.import_board_archive(raw,"transfer-once",USER)
        self.assertTrue(imported["ok"],imported)
        board = imported["data"]["board"]
        self.assertNotEqual(board["id"],"board_local")
        self.assertTrue(board["locked"])
        snapshot = self.call(self.target,"get_board_snapshot",board_id=board["id"],view="full")["items"]
        by_title = {card["title"]:card for card in snapshot}
        self.assertEqual(len(snapshot),7)
        self.assertEqual(by_title["公式"]["props"]["font_size"],24)
        self.assertEqual(by_title["联动图"]["state"]["values"],{"scale":2})
        self.assertEqual(by_title["联动图"]["props"]["bindings"]["x"]["card_id"],by_title["共享参数"]["id"])
        self.assertIn(by_title["插图"]["state"]["asset_id"],by_title["公式"]["state"]["text"])
        info = self.call(self.target,"get_asset_info",board_id=board["id"],asset_id=by_title["资料文件"]["state"]["asset_id"])["asset"]
        self.assertEqual(info["mode"],"imported")
        self.assertNotIn("root_id",info["source"])
        self.assertEqual(self.call(self.target,"read_asset",board_id=board["id"],asset_id=info["id"],format="text")["content"],"跨电脑保留的资料")
        self.assertEqual(self.call(self.target,"get_asset_sources",board_id=board["id"])["roots"],[])
        self.assertEqual(len(self.call(self.target,"get_card_list",board_id=board["id"],lifecycle="deleted")["items"]),1)
        again = self.target.import_board_archive(raw,"transfer-once",USER)
        self.assertTrue(again["meta"]["replayed"])
        self.assertEqual(again["data"]["board"]["id"],board["id"])
        self.assertEqual(self.target.call("get_operation_status",{"command_id":"transfer-once"},USER)["data"]["status"],"succeeded")
        restarted = BoardService(self.target.data_dir)
        self.assertTrue(restarted.call("get_board_info",{"board_id":board["id"]},USER)["ok"])

    def test_invalid_files_and_integrity_checks_leave_no_partial_board(self):
        self.sample(); raw = self.raw()
        invalid_files = [b"not an archive",raw[:30],self.rewrite(raw,lambda m,c:m.update(format_version=2)),
            self.rewrite(raw,lambda m,c:c.update({m["assets"][0]["entry"]:b"tampered"})),
            self.rewrite(raw,lambda m,c:c.update({"../escape.txt":b"no extraction"})),
            self.rewrite(raw,lambda m,c:m["cards"].append(deepcopy(m["cards"][0]))),
            self.rewrite(raw,lambda m,c:m["cards"][0].update(title=""))]
        for bad in invalid_files:
            with self.subTest(length=len(bad)), self.assertRaises(BusinessError) as caught:
                self.target.import_board_archive(bad,str(uuid4()),USER)
            self.assertEqual(caught.exception.error["code"],"INVALID_ARCHIVE")
        self.assertEqual(len(self.target.call("list_boards",{},USER)["data"]["items"]),1)
        with closing(self.target.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cards").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assets").fetchone()[0],0)

    def test_transaction_rolls_back_if_install_fails_after_first_card(self):
        self.sample(); raw = self.raw(); original = self.target._commit_card; count = 0
        def interrupted(*args,**kwargs):
            nonlocal count
            count += 1
            if count == 2: raise BusinessError("SERVICE_UNAVAILABLE","模拟写入故障")
            return original(*args,**kwargs)
        with patch.object(self.target,"_commit_card",side_effect=interrupted):
            result = self.target.import_board_archive(raw,"failed-install",USER)
        self.assertFalse(result["ok"])
        with closing(self.target.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM board").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cards").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assets").fetchone()[0],0)

    def test_revoked_reference_fails_export_and_agents_cannot_import_or_export(self):
        root = self.sample()
        self.call(self.source,"revoke_root",root_id=root["root_id"])
        with self.assertRaises(BusinessError) as caught: self.raw()
        self.assertEqual(caught.exception.error["code"],"ASSET_UNAVAILABLE")
        with self.assertRaises(BusinessError): self.source.export_board_archive("board_local",AGENT)
        with self.assertRaises(BusinessError): self.target.import_board_archive(b"bad","command",AGENT)
        result = self.target.call("import_board",{"command_id":"bad-agent","archive_sha256":"0"*64},AGENT)
        self.assertEqual(result["error"]["code"],"FORBIDDEN")

    def test_authenticated_http_and_native_picker_cancel_and_atomic_save(self):
        self.sample(); server = create_server(self.source,port=0)
        thread = Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(path,args=None,headers=None):
            client = http.client.HTTPConnection("127.0.0.1",server.server_address[1],timeout=10)
            try:
                client.request("GET" if args is None else "POST",path,None if args is None else dump(args).encode("utf-8"),headers or {})
                response = client.getresponse(); return response.status,response.read()
            finally: client.close()
        try:
            status,body = request("/api/session"); session = json.loads(body)["session"]
            headers = {"Content-Type":"application/json","X-AgentBoard-Session":session}
            with patch("agentboard.native_files.choose_file",return_value=None) as picker:
                self.assertEqual(request("/api/board-files/export",{"board_id":"board_local"})[0],403)
                self.assertEqual(request("/api/board-files/open",{},headers | {"Origin":"https://evil.example"})[0],403)
                picker.assert_not_called()
                status,body = request("/api/board-files/export",{"board_id":"board_local"},headers)
                self.assertTrue(json.loads(body)["data"]["cancelled"])
                self.assertEqual(request("/api/board-files/open",{},headers)[0],204)
            selected = Path(self.temp.name)/"跨电脑.agentboard"; selected.write_bytes(b"old file")
            with patch("agentboard.native_files.choose_file",return_value=selected):
                status,body = request("/api/board-files/export",{"board_id":"board_local"},headers)
                self.assertEqual(status,200,body)
                self.assertEqual(len(read_archive(selected.read_bytes())["cards"]),8)
                self.assertEqual(request("/api/board-files/open",{},headers)[1],selected.read_bytes())
            self.assertEqual(request("/api/board-files/export",{"board_id":"board_local","path":str(selected)},headers)[0],400)
        finally: server.shutdown(); server.server_close(); thread.join(3)


if __name__ == "__main__": unittest.main()
