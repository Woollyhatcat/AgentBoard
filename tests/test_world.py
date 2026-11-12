from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentboard.service import BoardService, USER, AGENT, dump


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(self.temp.name)
        self.serial = 0

    def call(self, tool, **args):
        self.serial += 1
        result = self.service.call(tool, {"board_id": "board_local", "command_id": str(self.serial), **args}, USER)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def create(self, **patch):
        return self.call("push_card", card={"type": "text", "type_version": "1.0", "title": "正文",
            "props": {"font_size": 24}, "state": {"text": "# 标题\n\n$G(s)=1/s$"}, **patch})["card"]

    def test_agent_can_create_font_size_and_partial_edit_preserves_it(self):
        card = self.create()
        result = self.service.call("edit_card", {"board_id": "board_local", "command_id": "agent",
            "card_id": card["id"], "expected_content_version": 1, "changes": {"state": {"text": "更新正文"}}}, AGENT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["card"]["props"]["font_size"], 24)
        self.assertEqual(result["data"]["card"]["layout_version"], 1)

    def test_content_and_layout_writes_on_same_base_both_survive(self):
        card = self.create()
        moved = self.call("edit_card", card_id=card["id"], expected_layout_version=1,
            changes={"layout": {"space": "world", "x": -320.5, "y": 175, "z": 8}})["card"]
        edited = self.call("edit_card", card_id=card["id"], expected_content_version=1,
            changes={"props": {"font_size": 32}, "state": {"text": "新内容"}})["card"]
        self.assertEqual(edited["layout"], moved["layout"])
        self.assertEqual(edited["content_version"], 2)
        self.assertEqual(edited["layout_version"], 2)
        self.assertEqual(edited["version"], 3)
        for key, changes in (("content", {"title": "过时"}), ("layout", {"layout": {"space": "world", "x": 0}})):
            result = self.service.call("edit_card", {"board_id": "board_local", "command_id": "stale-" + key,
                "card_id": card["id"], "expected_" + key + "_version": 1, "changes": changes})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "VERSION_CONFLICT")

    def test_protection_change_invalidates_both_partitions(self):
        card = self.create()
        self.call("set_card_lock", card_id=card["id"], expected_version=1, locked=True)
        unlocked = self.call("set_card_lock", card_id=card["id"], expected_version=2, locked=False)["card"]
        self.assertEqual((unlocked["content_version"], unlocked["layout_version"]), (3, 3))

    def test_world_layout_and_font_validation(self):
        for font in (11, 49, "24", True):
            result = self.service.call("push_card", {"board_id": "board_local", "command_id": "font-" + str(font),
                "card": {"type": "text", "type_version": "1.0", "title": "bad", "props": {"font_size": font}, "state": {"text": ""}}})
            self.assertFalse(result["ok"])
        card = self.create()
        for layout in ({"x": 0, "y": 0, "w": 4, "h": 4}, {"space": "world", "x": 10000001}):
            result = self.service.call("edit_card", {"board_id": "board_local", "command_id": "layout-" + str(layout),
                "card_id": card["id"], "expected_layout_version": 1, "changes": {"layout": layout}})
            self.assertFalse(result["ok"])

    def test_anchor_placement_and_height_modes(self):
        anchor = self.create(layout={"space": "world", "x": -100, "y": -50, "w": 500, "h": 400})
        other = self.call("push_card", anchor_card_id=anchor["id"], card={"type": "text", "type_version": "1.0",
            "title": "讨论延续", "props": {}, "state": {"text": ""}})["card"]
        self.assertEqual(other["layout"]["x"], 424)
        self.assertEqual(other["layout"]["y"], -50)
        self.assertEqual(other["layout"]["height_mode"], "auto")
        fixed = self.call("edit_card", card_id=other["id"], expected_layout_version=1,
            changes={"layout": {"space": "world", "height_mode": "fixed", "h": 600}})["card"]
        self.assertEqual(fixed["layout"]["height_mode"], "fixed")

    def test_migration_backs_up_and_preserves_cards_events_receipts_and_cursors(self):
        card = self.create()
        original_id = card["id"]
        snapshot = self.service.call("get_board_snapshot", {"board_id": "board_local", "view": "full", "limit": 1})
        # Construct the persisted v1 state at the storage boundary, including nested historical cards.
        old = deepcopy(card)
        old["layout"] = {"x": 4, "y": 2, "w": 4, "h": 4}
        old["props"].pop("font_size")
        old.pop("content_version"); old.pop("layout_version")
        with closing(self.service.connect()) as db, db:
            board = json.loads(db.execute("SELECT body FROM board").fetchone()[0])
            board.update(schema_version="1.0", layout_policy={"columns": 12, "collision": "reject"})
            db.execute("UPDATE board SET body=?", (dump(board),))
            db.execute("UPDATE cards SET body=?", (dump(old),))
            event = json.loads(db.execute("SELECT body FROM events").fetchone()[0]); event["card_after"] = old
            db.execute("UPDATE events SET body=?", (dump(event),))
            receipt = json.loads(db.execute("SELECT body FROM receipts").fetchone()[0]); receipt["data"]["card"] = old
            db.execute("UPDATE receipts SET body=?", (dump(receipt),))
            db.execute("INSERT INTO cursors VALUES (?,?,?,?,?,?)", ("frozen", "user:local_user", "get_board_snapshot", "{}", 9999999999,
                       dump({"items": [old], "extra": {}, "start": 0})))
        self.service = BoardService(self.temp.name)
        with closing(self.service.connect()) as db:
            board = json.loads(db.execute("SELECT body FROM board").fetchone()[0])
            upgraded = json.loads(db.execute("SELECT body FROM cards").fetchone()[0])
            self.assertEqual(board["schema_version"], "2.0")
            self.assertEqual(upgraded["id"], original_id)
            self.assertEqual(upgraded["props"]["font_size"], 16)
            self.assertEqual(upgraded["layout"], {"space": "world", "x": 448, "y": 176, "w": 432, "h": 336, "z": 0, "height_mode": "auto"})
            self.assertEqual(upgraded["state"], card["state"])
            self.assertEqual(json.loads(db.execute("SELECT body FROM events").fetchone()[0])["card_after"], upgraded)
            self.assertEqual(json.loads(db.execute("SELECT body FROM receipts").fetchone()[0])["data"]["card"], upgraded)
            self.assertEqual(json.loads(db.execute("SELECT body FROM cursors WHERE id='frozen'").fetchone()[0])["items"][0], upgraded)
        backups = list((Path(self.temp.name) / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(json.loads(db.execute("SELECT body FROM board").fetchone()[0])["schema_version"], "1.0")
        changed = self.service.call("get_changes", {"board_id": "board_local", "since_revision": 1})
        self.assertEqual(changed["error"]["code"], "SNAPSHOT_REQUIRED")
        BoardService(self.temp.name)
        self.assertEqual(len(list((Path(self.temp.name) / "backups").glob("*.sqlite3"))), 1)

    def test_old_process_cannot_write_grid_cards_or_unversioned_partitions(self):
        card = self.create()
        with closing(self.service.connect()) as db:
            old = deepcopy(card); old["layout"] = {"x": 0, "y": 0, "w": 4, "h": 4}
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT OR REPLACE INTO cards VALUES (?,?)", (old["id"], dump(old)))
            old = deepcopy(card); old["version"] += 1; old["title"] = "旧进程覆盖"
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT OR REPLACE INTO cards VALUES (?,?)", (old["id"], dump(old)))


if __name__ == "__main__":
    unittest.main()
