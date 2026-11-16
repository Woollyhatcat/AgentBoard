"""Multi-board persistence, authorization and copying across real SQLite transactions."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from agentboard.service import AGENT, USER, BoardService, dump
from agentboard.schema import BusinessError, WRITES


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(self.temp.name)

    def call(self, tool, board="board_local", actor=AGENT, **args):
        request = {**({"board_id":board} if board is not None else {}),**args}
        if tool in ("create_board","rename_board","duplicate_board","delete_board","set_board_lock",
                    "push_card","edit_card","delete_card","import_asset","grant_root","revoke_root","update_board"):
            request.setdefault("command_id",str(uuid4()))
        result = self.service.call(tool,request,actor)
        self.assertTrue(result["ok"],result)
        return result["data"]

    def create(self,name="另一块黑板"):
        return self.call("create_board",None,name=name)["board"]

    def push(self,board="board_local",kind="text",state=None,props=None):
        return self.call("push_card",board,card={"type":kind,"type_version":"1.0","title":"测试卡片",
            "props":props or {},"state":state or {"text":"原始内容"}})["card"]

    def error(self,tool,board,code,actor=AGENT,**args):
        result = self.service.call(tool,{"board_id":board,**({"command_id":str(uuid4())} if tool in WRITES else {}),**args},actor)
        self.assertFalse(result["ok"],result)
        self.assertEqual(result["error"]["code"],code,result)

    def test_create_receipts_and_restart_auto_save(self):
        request = {"name":"数学讨论","command_id":"create-once"}
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _:self.service.call("create_board",request),range(2)))
        self.assertTrue(all(result["ok"] for result in results),results)
        self.assertEqual(results[0]["data"]["board"]["id"],results[1]["data"]["board"]["id"])
        board = results[0]["data"]["board"]
        status = self.call("get_operation_status",None,command_id="create-once")
        self.assertEqual(status["status"],"succeeded")
        card = self.push(board["id"])
        self.service = BoardService(self.temp.name)
        self.assertEqual(self.call("read_card",board["id"],card_id=card["id"])["card"],card)
        self.assertEqual(len(self.call("list_boards",None)["items"]),2)

    def test_cards_assets_events_and_cursors_stay_in_their_board(self):
        other = self.create()["id"]
        a,b = self.push(),self.push(other)
        self.assertEqual((a["last_revision"],b["last_revision"]),(1,1))
        self.error("read_card",other,"NOT_FOUND",card_id=a["id"])
        self.error("edit_card",other,"NOT_FOUND",card_id=a["id"],expected_version=a["version"],changes={"title":"不应修改"})
        self.error("push_card",other,"NOT_FOUND",anchor_card_id=a["id"],card={"type":"text","type_version":"1.0","title":"错误","props":{},"state":{"text":""}})
        self.push()
        page = self.call("get_board_snapshot",view="full",limit=1)
        result = self.service.call("get_board_snapshot",{"board_id":other,"view":"full","limit":1,"cursor":page["next_cursor"]})
        self.assertEqual(result["error"]["code"],"CURSOR_INVALID")
        events = self.call("get_changes",other,since_revision=0)["items"]
        self.assertEqual([e["card_id"] for e in events],[b["id"]])
        asset = self.call("import_asset",name="example.txt",mode="imported",source={"kind":"inline","mime_type":"text/plain","data_base64":base64.b64encode(b"hello").decode()})["asset"]
        self.error("get_asset_info",other,"NOT_FOUND",asset_id=asset["id"])
        self.error("read_asset",other,"NOT_FOUND",asset_id=asset["id"],format="text")
        with self.assertRaises(BusinessError): self.service.asset_bytes_for_actor(asset["id"],AGENT,other)
        self.error("push_card",other,"VALIDATION_FAILED",card={"type":"interactive","type_version":"1.0","title":"绑定越界",
            "props":{"source":"<p>test</p>","bindings":{"x":{"card_id":a["id"],"path":"/state/text"}}},"state":{"values":{}}})

    def test_board_lock_protects_deletion_but_allows_edits_and_checks_versions(self):
        board = self.create()
        board = self.call("set_board_lock",board["id"],USER,expected_version=board["version"],locked=True)["board"]
        self.error("delete_board",board["id"],"BOARD_LOCKED",expected_version=board["version"])
        self.error("delete_board",board["id"],"BOARD_LOCKED",USER,expected_version=board["version"])
        self.error("set_board_lock",board["id"],"FORBIDDEN",expected_version=board["version"],locked=False)
        self.call("rename_board",board["id"],expected_version=board["version"],name="已重命名")
        self.push(board["id"])
        current = self.call("get_board_info",board["id"])
        self.error("set_board_lock",board["id"],"VERSION_CONFLICT",USER,expected_version=board["version"],locked=False)
        unlocked = self.call("set_board_lock",board["id"],USER,expected_version=current["version"],locked=False)["board"]
        self.push(board["id"])
        self.error("delete_board",board["id"],"VERSION_CONFLICT",expected_version=unlocked["version"])
        current = self.call("get_board_info",board["id"])
        request = {"board_id":board["id"],"command_id":"delete-once","expected_version":current["version"]}
        deleted = self.service.call("delete_board",request)
        self.assertTrue(deleted["ok"],deleted)
        self.assertTrue(self.service.call("delete_board",request)["meta"]["replayed"])
        self.assertEqual(self.call("get_operation_status",board["id"],command_id="delete-once")["status"],"succeeded")
        result = self.service.call("get_board_info",{"board_id":board["id"]})
        self.assertEqual(result["error"]["code"],"BOARD_DELETED")
        self.assertNotIn(board["id"],[b["id"] for b in self.call("list_boards",None)["items"]])

    def test_duplicate_remaps_bindings_resources_and_remains_independent(self):
        slider = self.push(kind="slider",props={"min":0,"max":10,"step":1},state={"value":2})
        asset = self.call("import_asset",name="note.txt",mode="imported",source={"kind":"inline","mime_type":"text/plain","data_base64":"aGVsbG8="})["asset"]
        text = self.push(state={"text":"[资料](/api/assets/"+asset["id"]+")"})
        chart = self.push(kind="interactive",props={"source":"<p>chart</p>","bindings":{"x":{"card_id":slider["id"],"path":"/state/value"}}},state={"values":{"n":3}})
        trash = self.push()
        self.call("delete_card",card_id=trash["id"],expected_version=trash["version"])
        copied = self.call("duplicate_board",name="副本")
        board,ids = copied["board"]["id"],copied["card_id_map"]
        chart_copy = self.call("read_card",board,card_id=ids[chart["id"]])["card"]
        self.assertEqual(chart_copy["props"]["bindings"]["x"]["card_id"],ids[slider["id"]])
        self.assertEqual(chart_copy["state"],chart["state"])
        new_asset = copied["asset_id_map"][asset["id"]]
        self.assertIn(new_asset,self.call("read_card",board,card_id=ids[text["id"]])["card"]["state"]["text"])
        self.assertEqual(self.call("read_asset",board,asset_id=new_asset,format="text")["content"],"hello")
        self.assertEqual(self.call("get_card_list",board,lifecycle="deleted")["items"][0]["id"],ids[trash["id"]])
        self.call("edit_card",card_id=slider["id"],expected_version=slider["version"],changes={"state":{"value":8}})
        self.assertEqual(self.call("read_card",board,card_id=ids[slider["id"]])["card"]["state"]["value"],2)

    def test_workspace_authorization_is_global_revocable_and_not_recreated(self):
        user_sources = self.call("get_asset_sources",actor=USER)
        default = user_sources["roots"][0]
        self.assertEqual(Path(default["path"]).name,"workspace")
        agent_sources = self.call("get_asset_sources")
        self.assertNotIn("path",agent_sources["roots"][0])
        self.assertNotIn("default_workspace",agent_sources)
        other = self.create()["id"]
        self.assertEqual(self.call("get_asset_sources",other)["roots"][0]["root_id"],default["root_id"])
        docs = Path(self.temp.name)/"docs"; docs.mkdir(); (docs/"note.txt").write_text("shared",encoding="utf-8")
        root = self.call("grant_root",actor=USER,path=str(docs),label="资料")
        self.assertEqual(self.call("grant_root",actor=USER,path=str(docs),label="重复")["root_id"],root["root_id"])
        reference = self.call("import_asset",other,name="引用",mode="reference",source={"kind":"approved_file","root_id":root["root_id"],"relative_path":"note.txt"})["asset"]
        copied = self.call("duplicate_board",other,name="引用副本")
        copied_asset = copied["asset_id_map"][reference["id"]]
        self.assertEqual(self.call("read_asset",copied["board"]["id"],asset_id=copied_asset,format="text")["content"],"shared")
        self.call("revoke_root",actor=USER,root_id=root["root_id"])
        self.call("revoke_root",actor=USER,root_id=default["root_id"])
        self.service = BoardService(self.temp.name)
        self.assertEqual(self.call("get_asset_sources",other)["roots"],[])
        result = self.service.call("read_asset",{"board_id":other,"asset_id":reference["id"],"format":"text"})
        self.assertEqual(result["error"]["code"],"ASSET_UNAVAILABLE")
        result = self.service.call("read_asset",{"board_id":copied["board"]["id"],"asset_id":copied_asset,"format":"text"})
        self.assertEqual(result["error"]["code"],"ASSET_UNAVAILABLE")

    def test_hidden_board_does_not_block_other_boards_or_leak_from_frozen_list(self):
        other = self.create()["id"]
        frozen = self.call("list_boards",None,limit=1)
        self.call("update_board",other,USER,agent_permissions={"read":False})
        result = self.service.call("list_boards",{"limit":1,"cursor":frozen["next_cursor"]})
        self.assertEqual(result["error"]["code"],"FORBIDDEN")
        self.call("update_board",actor=USER,agent_permissions={"read":False})
        self.assertEqual(self.call("list_boards",None)["items"],[])
        new = self.create()
        self.assertEqual(self.call("list_boards",None)["items"][0]["id"],new["id"])

    def test_revoked_created_board_cannot_be_disclosed_by_global_receipt(self):
        request = {"command_id":"global-create","name":"私有讨论"}
        created = self.service.call("create_board",request)["data"]["board"]
        self.call("update_board",created["id"],USER,agent_permissions={"read":False})
        replay = self.service.call("create_board",request)
        self.assertEqual(replay["error"]["code"],"FORBIDDEN")
        status = self.service.call("get_operation_status",{"command_id":"global-create"})
        self.assertEqual(status["error"]["code"],"FORBIDDEN")

    def test_single_board_migration_preserves_cards_events_and_receipts(self):
        card = self.push()
        before = self.call("get_board_snapshot",view="full")
        with closing(self.service.connect()) as db, db:
            db.execute("ALTER TABLE events RENAME TO multi_events")
            db.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("INSERT INTO events SELECT revision,body FROM multi_events")
            db.execute("DROP TABLE multi_events")
            original = json.loads(db.execute("SELECT body FROM board").fetchone()[0])
            for key in ("version","locked","lifecycle","deleted_at"): original.pop(key,None)
            db.execute("UPDATE board SET body=?",(dump(original),))
        self.service = BoardService(self.temp.name)
        after = self.call("get_board_snapshot",view="full")
        self.assertEqual(after["items"],before["items"])
        self.assertEqual(after["revision"],before["revision"])
        self.assertEqual(self.call("get_changes",since_revision=0)["items"][0]["card_after"],card)
        self.assertTrue(list((Path(self.temp.name)/"backups").glob("before-multiboard-*.sqlite3")))
        self.push(self.create()["id"])
        with closing(self.service.connect()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")


if __name__ == "__main__": unittest.main()
