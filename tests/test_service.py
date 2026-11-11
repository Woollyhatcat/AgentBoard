import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from agentboard.service import BoardService, USER, AGENT, LIMITS

BOARD = {"board_id": "board_local"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nksAAAAASUVORK5CYII=")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BoardService(Path(self.temp.name) / "board")

    def call(self, tool, args=None, user=False, command=None):
        a = BOARD | (args or {})
        if tool in ("push_card", "edit_card", "delete_card", "restore_card", "set_card_lock",
                    "import_asset", "grant_root", "revoke_root", "update_board", "upload_asset"):
            a["command_id"] = command or str(uuid4())
        return self.service.call(tool, a, USER if user else AGENT)

    def ok(self, result):
        self.assertTrue(result["ok"], result)
        return result["data"]

    def error(self, result, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], code, result)
        return result

    def push(self, kind="text", **overrides):
        defaults = {
            "text": {"props": {}, "state": {"text": "目标：最大超调量不超过 20%。"}},
            "slider": {"props": {"min": 0, "max": 10, "step": 0.1}, "state": {"value": 2.5}},
            "checkbox": {"props": {"label": "已经验证"}, "state": {"checked": False}},
            "input": {"props": {"mode": "number"}, "state": {"value": 0}},
        }
        card = {"type": kind, "type_version": "1.0", "title": "测试 " + kind,
                **defaults.get(kind, {}), **overrides}
        return self.ok(self.call("push_card", {"card": card}))["card"]

    def edit(self, card, changes, **kwargs):
        return self.call("edit_card", {"card_id": card["id"], "expected_version": card["version"],
                                       "changes": changes}, **kwargs)

    def lifecycle(self, tool, card, **kwargs):
        return self.call(tool, {"card_id": card["id"], "expected_version": card["version"]}, **kwargs)

    def read(self, card):
        return self.ok(self.call("read_card", {"card_id": card["id"], "include_deleted": True}))["card"]

    def asset(self, content=b"hello", name="example.txt"):
        return self.ok(self.call("import_asset", {"name": name, "mode": "imported", "source": {
            "kind": "inline", "mime_type": "application/fake", "data_base64": base64.b64encode(content).decode()}}))["asset"]

    def grant(self):
        path = Path(self.temp.name) / "approved"
        path.mkdir(exist_ok=True)
        root = self.ok(self.call("grant_root", {"path": str(path), "label": "实验资料"}, user=True))
        return path, root["root_id"]

    def reference(self, root_id, relative_path):
        return self.ok(self.call("import_asset", {"name": relative_path, "mode": "reference",
            "source": {"kind": "approved_file", "root_id": root_id, "relative_path": relative_path}}))["asset"]

    def test_discovery_and_six_card_roundtrips(self):
        types = self.ok(self.call("get_card_types"))["types"]
        self.assertEqual(len(types), 6)
        self.assertTrue(all(t["props_schema"]["$schema"].endswith("2020-12/schema") for t in types))
        for kind in ("text", "slider", "checkbox", "input"):
            card = self.push(kind)
            self.assertEqual(self.read(card), card)
        asset = self.asset(PNG, "截图.png")
        for kind in ("image", "file"):
            card = self.push(kind, props={}, state={"asset_id": asset["id"]})
            self.assertEqual(self.read(card), card)
        info = self.ok(self.call("get_board_info"))
        self.assertEqual(info["active_card_count"], 6)
        self.assertFalse(info["permissions"]["manage_lock"])

    def test_user_agent_parameter_collaboration(self):
        card = self.push("slider")
        edited = self.ok(self.edit(card, {"state": {"value": 3}}, user=True))["card"]
        self.assertEqual(edited["updated_by"]["kind"], "user")
        read = self.read(card)
        self.assertEqual(read["state"]["value"], 3)
        self.assertEqual(read["version"], 2)

    def test_partial_merge_and_no_op(self):
        card = self.push("slider")
        new = self.ok(self.edit(card, {"title": "Kp", "props": {"max": 12}}))["card"]
        self.assertEqual(new["state"], card["state"])
        self.assertEqual(new["props"]["step"], 0.1)
        self.assertEqual(new["layout"], card["layout"])
        result = self.ok(self.edit(new, {"title": " Kp "}))
        self.assertFalse(result["changed"])
        self.assertEqual(result["card"], new)

    def test_invalid_merged_definition_rolls_back(self):
        card = self.push("slider")
        self.error(self.edit(card, {"props": {"max": 1}}), "VALIDATION_FAILED")
        self.assertEqual(self.read(card), card)
        good = self.ok(self.edit(card, {"props": {"max": 1}, "state": {"value": 0.5}}))["card"]
        self.assertEqual(good["state"]["value"], 0.5)
        self.assertEqual(good["last_revision"], 2)

    def test_decimal_step_points_and_unaligned_max(self):
        card = self.push("slider", props={"min": 0.1, "max": 0.95, "step": 0.1, "precision": 0}, state={"value": 0.3})
        self.assertEqual(card["state"]["value"], 0.3)
        self.error(self.edit(card, {"state": {"value": 0.95}}), "VALIDATION_FAILED")
        self.error(self.edit(card, {"state": {"value": 0.3000001}}), "VALIDATION_FAILED")
        self.ok(self.edit(card, {"state": {"value": 0.9}}))

    def test_number_null_zero_and_required(self):
        card = self.push("input")
        self.ok(self.edit(card, {"props": {"required": True}}))
        card = self.read(card)
        self.error(self.edit(card, {"state": {"value": None}}), "VALIDATION_FAILED")
        self.error(self.edit(card, {"state": {"value": "0"}}), "VALIDATION_FAILED")
        self.error(self.edit(card, {"props": {"mode": "text"}}), "VALIDATION_FAILED")

    def test_text_empty_normalization_and_whitespace(self):
        card = self.push("input", props={"mode": "text"}, state={"value": ""})
        self.assertIsNone(card["state"]["value"])
        card = self.ok(self.edit(card, {"state": {"value": "  x  "}}))["card"]
        self.assertEqual(card["state"]["value"], "  x  ")
        self.error(self.edit(card, {"props": {"required": True}, "state": {"value": "  "}}), "VALIDATION_FAILED")
        self.ok(self.edit(card, {"props": {"required": True}, "state": {"value": "0"}}))

    def test_read_only_unknown_and_wrong_types(self):
        card = self.push()
        self.error(self.edit(card, {"locked": True}), "INVALID_ARGUMENT")
        self.error(self.edit(card, {"state": {"unknown": "x"}}), "VALIDATION_FAILED")
        self.error(self.edit(card, {"layout": {"x": True}}), "INVALID_ARGUMENT")
        self.error(self.call("push_card", {"card": {"type": "html", "type_version": "1.0"}}), "UNSUPPORTED_TYPE")

    def test_stale_write_and_delete_are_rejected(self):
        card = self.push("slider")
        self.ok(self.edit(card, {"state": {"value": 3}}, user=True))
        result = self.error(self.edit(card, {"title": "旧版本标题"}), "VERSION_CONFLICT")
        self.assertEqual(result["error"]["details"]["current_version"], 2)
        self.error(self.lifecycle("delete_card", card), "VERSION_CONFLICT")
        self.assertEqual(self.read(card)["state"]["value"], 3)

    def test_idempotency_replay_and_parameter_reuse(self):
        args = {"card": {"type": "text", "type_version": "1.0", "title": "一次", "props": {}, "state": {"text": ""}}}
        first = self.call("push_card", args, command="same")
        again = self.call("push_card", args, command="same")
        self.assertTrue(again["meta"]["replayed"])
        self.assertEqual(first["data"], again["data"])
        args["card"]["title"] = "不同意图"
        self.error(self.call("push_card", args, command="same"), "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(self.ok(self.call("get_board_info"))["revision"], 1)

    def test_failure_receipt_is_durable_and_actor_scoped(self):
        card = self.push("slider")
        bad = self.edit(card, {"state": {"value": 11}}, command="failed")
        status = self.ok(self.call("get_operation_status", {"command_id": "failed"}))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["result"], bad)
        self.assertEqual(self.ok(self.call("get_operation_status", {"command_id": "failed"}, user=True))["status"], "unknown")
        self.service = BoardService(self.service.data_dir)
        again = self.edit(card, {"state": {"value": 11}}, command="failed")
        self.assertTrue(again["meta"]["replayed"])
        self.assertEqual(again["error"], bad["error"])

    def test_agent_cannot_lock_and_revocation_blocks_receipts(self):
        card = self.push()
        self.error(self.call("set_card_lock", {"card_id": card["id"], "expected_version": 1, "locked": True}), "FORBIDDEN")
        self.ok(self.edit(card, {"title": "已修改"}, command="old-edit"))
        self.ok(self.call("update_board", {"agent_permissions": {"edit": False}}, user=True))
        self.error(self.call("get_operation_status", {"command_id": "old-edit"}), "FORBIDDEN")
        self.ok(self.call("update_board", {"agent_permissions": {"read": False}}, user=True))
        self.error(self.call("read_card", {"card_id": card["id"]}), "FORBIDDEN")

    def test_lock_protects_content_layout_and_delete(self):
        card = self.push()
        card = self.ok(self.call("set_card_lock", {"card_id": card["id"], "expected_version": 1, "locked": True}, user=True))["card"]
        self.error(self.edit(card, {"title": "禁止"}), "CARD_LOCKED")
        self.error(self.edit(card, {"layout": {"y": 10}}, user=True), "CARD_LOCKED")
        self.error(self.lifecycle("delete_card", card, user=True), "CARD_LOCKED")
        self.assertEqual(self.read(card)["version"], 2)
        card = self.ok(self.call("set_card_lock", {"card_id": card["id"], "expected_version": 2, "locked": False}, user=True))["card"]
        self.ok(self.edit(card, {"title": "可编辑"}))

    def test_soft_delete_restore_and_relocation(self):
        card = self.push()
        deleted = self.ok(self.lifecycle("delete_card", card))["card"]
        self.assertFalse(self.ok(self.lifecycle("delete_card", deleted))["changed"])
        self.error(self.edit(deleted, {"title": "禁止"}), "CARD_DELETED")
        self.error(self.call("read_card", {"card_id": card["id"]}), "CARD_DELETED")
        replacement = self.push(layout=card["layout"])
        restored = self.ok(self.lifecycle("restore_card", deleted))
        self.assertTrue(restored["layout_adjusted"])
        self.assertNotEqual(restored["card"]["layout"], replacement["layout"])
        self.assertEqual(restored["card"]["id"], card["id"])
        self.assertFalse(self.ok(self.lifecycle("restore_card", restored["card"]))["changed"])

    def test_layout_conflict_does_not_move_other_card(self):
        one, two = self.push(), self.push()
        self.error(self.edit(two, {"layout": one["layout"]}), "LAYOUT_CONFLICT")
        self.assertEqual(self.read(one), one)
        self.assertEqual(self.read(two), two)
        self.error(self.edit(two, {"layout": {"x": 10}}), "VALIDATION_FAILED")

    def test_snapshot_is_frozen_while_writes_continue(self):
        first, second = self.push(), self.push()
        page1 = self.ok(self.call("get_board_snapshot", {"view": "full", "limit": 1}))
        self.ok(self.edit(second, {"title": "快照之后"}))
        page2 = self.ok(self.call("get_board_snapshot", {"view": "full", "limit": 1, "cursor": page1["next_cursor"]}))
        self.assertEqual(page1["revision"], page2["revision"])
        self.assertEqual(page2["items"][0]["title"], second["title"])
        self.assertTrue(page2["complete"])
        changes = self.ok(self.call("get_changes", {"since_revision": page2["revision"]}))
        self.assertEqual(changes["items"][0]["card_after"]["title"], "快照之后")

    def test_changes_bounded_by_first_page_and_tombstones(self):
        one, two = self.push(), self.push()
        page1 = self.ok(self.call("get_changes", {"since_revision": 0, "limit": 1}))
        self.ok(self.lifecycle("delete_card", one))
        page2 = self.ok(self.call("get_changes", {"since_revision": 0, "limit": 1, "cursor": page1["next_cursor"]}))
        self.assertEqual(page2["to_revision"], 2)
        self.assertEqual(page2["next_since_revision"], 2)
        next_round = self.ok(self.call("get_changes", {"since_revision": 2}))
        self.assertEqual(next_round["items"][0]["card_after"]["lifecycle"], "deleted")

    def test_cursor_condition_identity_and_expiration(self):
        self.push(); self.push()
        args = {"limit": 1, "view": "full"}
        page = self.ok(self.call("get_board_snapshot", args))
        token = page["next_cursor"]
        self.error(self.call("get_board_snapshot", args | {"limit": 2, "cursor": token}), "CURSOR_INVALID")
        self.error(self.call("get_board_snapshot", args | {"cursor": token}, user=True), "CURSOR_INVALID")
        with closing(self.service.connect()) as db, db:
            db.execute("UPDATE cursors SET expires=0")
        self.error(self.call("get_board_snapshot", args | {"cursor": token}), "CURSOR_EXPIRED")

    def test_missing_change_range_requires_snapshot(self):
        self.push(); self.push()
        with closing(self.service.connect()) as db, db:
            db.execute("DELETE FROM events WHERE revision=1")
        self.error(self.call("get_changes", {"since_revision": 0}), "SNAPSHOT_REQUIRED")
        self.error(self.call("get_changes", {"since_revision": 100}), "INVALID_ARGUMENT")

    def test_long_text_read_is_not_truncated(self):
        text = "中文🙂" * 30000
        card = self.push(state={"text": text})
        summary = self.ok(self.call("get_card_list"))["items"][0]
        self.assertEqual(len(summary["preview"]["text"]), 160)
        self.assertTrue(summary["preview"]["truncated"])
        self.assertEqual(self.read(card)["state"]["text"], text)

    def test_large_asset_unicode_and_byte_chunks(self):
        text = "中文🙂\n" * 40000
        asset = self.asset(text.encode())
        output, cursor, offsets = [], None, []
        while True:
            page = self.ok(self.call("read_asset", {"asset_id": asset["id"], "format": "text", **({"cursor": cursor} if cursor else {})}))
            offsets.append(page["offset"]); output.append(page["content"])
            cursor = page["next_cursor"]
            if not cursor: break
        self.assertEqual("".join(output), text)
        self.assertEqual(offsets[1], 32768)
        first = self.ok(self.call("read_asset", {"asset_id": asset["id"], "format": "bytes"}))
        self.assertEqual(len(base64.b64decode(first["content"])), 262144)

    def test_empty_asset_returns_complete_empty_chunk(self):
        asset = self.asset(b"")
        page = self.ok(self.call("read_asset", {"asset_id": asset["id"], "format": "bytes"}))
        self.assertTrue(page["complete"])
        self.assertEqual(page["content"], "")

    def test_reference_changes_in_middle_of_chunk_stream(self):
        path, root = self.grant()
        file = path / "result.txt"; file.write_text("a" * 70000, encoding="utf-8")
        asset = self.reference(root, file.name)
        first = self.ok(self.call("read_asset", {"asset_id": asset["id"], "format": "text"}))
        file.write_text("b" * 70000, encoding="utf-8")
        self.error(self.call("read_asset", {"asset_id": asset["id"], "format": "text", "cursor": first["next_cursor"]}), "ASSET_CHANGED")
        self.assertEqual(self.ok(self.call("get_board_info"))["revision"], 0)

    def test_reference_revoked_but_imported_copy_survives(self):
        path, root = self.grant()
        (path / "notes.txt").write_text("原内容", encoding="utf-8")
        reference = self.reference(root, "notes.txt")
        copy = self.ok(self.call("import_asset", {"name": "copy.txt", "mode": "imported", "source": {
            "kind": "approved_file", "root_id": root, "relative_path": "notes.txt"}}))["asset"]
        card = self.push("file", props={}, state={"asset_id": reference["id"], "note": "保留说明"})
        deleted = self.ok(self.lifecycle("delete_card", card))["card"]
        self.ok(self.call("revoke_root", {"root_id": root}, user=True))
        self.assertEqual(self.ok(self.call("get_asset_info", {"asset_id": reference["id"]}))["asset"]["status"], "forbidden")
        self.error(self.call("read_asset", {"asset_id": reference["id"], "format": "text"}), "ASSET_UNAVAILABLE")
        self.assertEqual(self.ok(self.call("read_asset", {"asset_id": copy["id"], "format": "text"}))["content"], "原内容")
        self.ok(self.lifecycle("restore_card", deleted))
        self.assertEqual(self.read(card)["state"]["note"], "保留说明")

    def test_missing_reference_and_resource_replacement(self):
        path, root = self.grant()
        file = path / "source.txt"; file.write_text("旧资料", encoding="utf-8")
        reference = self.reference(root, file.name)
        card = self.push("file", props={}, state={"asset_id": reference["id"], "note": "说明"})
        file.unlink()
        info = self.ok(self.call("get_asset_info", {"asset_id": reference["id"]}))["asset"]
        self.assertEqual(info["status"], "missing")
        self.ok(self.edit(card, {"title": "仍可编辑标题"}))
        card = self.read(card)
        self.error(self.edit(card, {"state": {"asset_id": "missing"}}), "NOT_FOUND")
        self.assertEqual(self.read(card)["state"]["asset_id"], reference["id"])

    def test_paths_cannot_escape_approved_root(self):
        path, root = self.grant()
        for relative in ("../secret.txt", "C:/Windows/win.ini", "result.txt:secret", "/etc/passwd", "NUL", "CON.txt"):
            result = self.call("import_asset", {"name": "bad", "mode": "reference", "source": {
                "kind": "approved_file", "root_id": root, "relative_path": relative}})
            self.error(result, "ASSET_UNAVAILABLE")
            self.assertEqual(result["error"]["details"]["reason"], "forbidden")

    def test_mime_detection_and_inline_limit(self):
        image = self.asset(PNG)
        self.assertEqual(image["mime_type"], "image/png")
        self.push("image", props={}, state={"asset_id": image["id"]})
        text = self.asset(b"<script>alert(1)</script>", "unsafe.html")
        self.assertTrue(text["mime_type"].startswith("text/plain"))
        self.error(self.call("push_card", {"card": {"type": "image", "type_version": "1.0", "title": "bad", "props": {}, "state": {"asset_id": text["id"]}}}), "VALIDATION_FAILED")
        content = base64.b64encode(b"x" * (1048576 + 1)).decode()
        self.error(self.call("import_asset", {"name": "too big", "mode": "imported", "source": {"kind": "inline", "mime_type": "text/plain", "data_base64": content}}), "LIMIT_EXCEEDED")
        self.ok(self.call("upload_asset", {"name": "user upload", "data_base64": content}, user=True))
        self.error(self.call("upload_asset", {"name": "not agent", "data_base64": ""}), "FORBIDDEN")

    def test_data_survives_service_restart(self):
        card = self.push("slider")
        card = self.ok(self.call("set_card_lock", {"card_id": card["id"], "expected_version": 1, "locked": True}, user=True))["card"]
        asset = self.asset(b"persistent")
        self.service = BoardService(self.service.data_dir)
        self.assertEqual(self.read(card), card)
        self.assertEqual(self.ok(self.call("read_asset", {"asset_id": asset["id"], "format": "text"}))["content"], "persistent")

    def test_concurrent_writers_only_one_wins(self):
        card = self.push("slider")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda value: self.edit(card, {"state": {"value": value}}), (3, 4)))
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.error(next(result for result in results if not result["ok"]), "VERSION_CONFLICT")
        self.assertEqual(self.read(card)["version"], 2)

    def test_concurrent_same_command_creates_once(self):
        args = {"card": {"type": "checkbox", "type_version": "1.0", "title": "一次", "props": {"label": "确认"}, "state": {"checked": False}}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.call("push_card", args, command="same-command"), range(2)))
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(results[0]["data"]["card"]["id"], results[1]["data"]["card"]["id"])
        self.assertEqual(self.ok(self.call("get_board_info"))["revision"], 1)


if __name__ == "__main__":
    unittest.main()
