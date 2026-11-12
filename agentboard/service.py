"""Transactional board operations shared by the HTTP and MCP adapters.

SQLite serializes commands across processes. Card changes, event records and
idempotency receipts commit together. Cursors persist frozen read results.
"""

import base64
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import secrets
import sqlite3
import time

from .schema import (BusinessError, INPUTS, USER_INPUTS, WRITES, TYPES, card_types,
                     normalize_card, validate, COMMAND, fail)
from .migration import LAYOUT_POLICY, migrate, install_write_guard

USER = {"id": "local_user", "kind": "user", "display_name": "本地用户"}
AGENT = {"id": "local_agent", "kind": "agent", "display_name": "Agent"}
PERMISSIONS = {key: True for key in ("read", "create", "edit", "delete", "restore", "import_asset")}
LIMITS = {"text_max_codepoints": 100000, "asset_max_bytes": 20 * 1024 * 1024,
          "inline_max_bytes": 1024 * 1024, "page_default": 20, "page_max": 100,
          "cursor_ttl_seconds": 600, "changes_retention_days": 30,
          "text_chunk_codepoints": 32768, "bytes_chunk_size": 256 * 1024,
          "request_max_bytes": 29 * 1024 * 1024, "receipts_expire": False}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uid(prefix):
    return prefix + "_" + secrets.token_hex(12)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def overlaps(a, b):
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and
            a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


class BoardService:
    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.data_dir / "agentboard.sqlite3"
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS board (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (revision INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    actor TEXT NOT NULL, board TEXT NOT NULL, command TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, body TEXT NOT NULL, tool TEXT NOT NULL,
                    PRIMARY KEY(actor, board, command));
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, content BLOB);
                CREATE TABLE IF NOT EXISTS roots (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, path TEXT NOT NULL, active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, tool TEXT NOT NULL,
                    signature TEXT NOT NULL, expires REAL NOT NULL, body TEXT NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            board = {"id": "board_local", "name": "我的工作黑板", "description": "人与 Agent，一起接着做。",
                     "revision": 0, "schema_version": "2.0", "layout_policy": LAYOUT_POLICY,
                     "agent_permissions": PERMISSIONS}
            db.execute("INSERT OR IGNORE INTO board VALUES (?,?)", (board["id"], dump(board)))
            migrate(db, self.database)
            install_write_guard(db)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _board(db):
        return json.loads(db.execute("SELECT body FROM board").fetchone()[0])

    @staticmethod
    def _save_board(db, board):
        db.execute("UPDATE board SET body=? WHERE id=?", (dump(board), board["id"]))

    @staticmethod
    def _cards(db):
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM cards")]

    @staticmethod
    def permissions(board, actor):
        return (PERMISSIONS if actor["kind"] == "user" else board["agent_permissions"]) | {
            "manage_lock": actor["kind"] == "user"}

    def _authorize(self, board, actor, tool, args):
        if actor.get("kind") not in ("user", "agent") or not actor.get("id"):
            raise BusinessError("UNAUTHORIZED", "访问身份无效。")
        permissions = self.permissions(board, actor)
        if not permissions["read"]:
            raise BusinessError("FORBIDDEN", "当前身份没有看板访问权限。")
        if tool != "list_boards" and args.get("board_id") != board["id"]:
            raise BusinessError("NOT_FOUND", "找不到可访问的目标看板。")
        action = {"push_card": "create", "edit_card": "edit", "delete_card": "delete",
                  "restore_card": "restore", "import_asset": "import_asset",
                  "set_card_lock": "manage_lock"}.get(tool)
        if action and not permissions[action]:
            raise BusinessError("FORBIDDEN", "当前身份没有此操作权限。")
        if tool in USER_INPUTS and actor["kind"] != "user":
            raise BusinessError("FORBIDDEN", "此管理操作只能由用户执行。")

    def call(self, tool, args, actor=None):
        actor = deepcopy(actor or AGENT)
        identity = actor.get("kind", "") + ":" + actor.get("id", "")
        meta = {"request_id": uid("req")}
        db = self.connect()
        try:
            # The transaction also freezes the read point for multi-query reads.
            db.execute("BEGIN IMMEDIATE")
            board = self._board(db)
            if board["schema_version"] != "2.0":
                raise BusinessError("UNSUPPORTED_SCHEMA", "看板契约已改变，请重新连接 MCP 并刷新网页。")
            meta["board_revision"] = board["revision"]
            if tool not in INPUTS and tool not in USER_INPUTS:
                raise BusinessError("INVALID_ARGUMENT", "未知操作。")
            if not isinstance(args, dict):
                raise BusinessError("INVALID_ARGUMENT", "参数必须是对象。")
            # Authorization precedes receipts and any disclosure of past results.
            self._authorize(board, actor, tool, args)
            writing = tool in WRITES
            fingerprint = None
            if writing:
                validate(args.get("command_id"), COMMAND, "command_id", "INVALID_ARGUMENT")
                meta.update(command_id=args["command_id"], replayed=False)
                try:
                    fingerprint = hashlib.sha256(dump([tool, args]).encode()).hexdigest()
                except (ValueError, TypeError):
                    raise BusinessError("INVALID_ARGUMENT", "输入必须是有效 JSON，数值必须有限。")
                previous = db.execute(
                    "SELECT fingerprint, body FROM receipts WHERE actor=? AND board=? AND command=?",
                    (identity, board["id"], args["command_id"])).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise BusinessError("IDEMPOTENCY_KEY_REUSED", "此命令标识已用于不同请求。")
                    response = json.loads(previous["body"])
                    response["meta"].update(request_id=meta["request_id"], replayed=True)
                    db.commit()
                    return response
            db.execute("SAVEPOINT business")
            try:
                # Preserve specific unsupported-type feedback for creation.
                if tool == "push_card" and isinstance(args.get("card"), dict):
                    c = args["card"]
                    if c.get("type") not in TYPES or c.get("type_version") != "1.0":
                        raise BusinessError("UNSUPPORTED_TYPE", "不支持此卡片类型或版本。")
                validate(args, (INPUTS | USER_INPUTS)[tool], code="INVALID_ARGUMENT")
                data = self._execute(db, board, actor, tool, args)
                response = {"ok": True, "data": data, "meta": meta}
                meta["board_revision"] = data.get("revision", data.get("as_of_revision",
                                            data.get("to_revision", board["revision"])))
            except BusinessError as error:
                db.execute("ROLLBACK TO business")
                response = {"ok": False, "error": error.error, "meta": meta}
            if writing:
                db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?)",
                           (identity, board["id"], args["command_id"], fingerprint, dump(response), tool))
            db.commit()
            return response
        except BusinessError as error:
            db.rollback()
            return {"ok": False, "error": error.error, "meta": meta}
        except (sqlite3.Error, OSError):
            db.rollback()
            error = BusinessError("SERVICE_UNAVAILABLE", "服务暂时不可用，操作结果尚未确认。",
                                  recovery="请先查询原命令状态，或使用相同命令和输入重试。")
            return {"ok": False, "error": error.error, "meta": meta}
        finally:
            db.close()

    def _execute(self, db, board, actor, tool, a):
        if tool == "list_boards":
            item = {k: board[k] for k in ("id", "name", "description", "revision")}
            item["permissions"] = self.permissions(board, actor)
            return self._page(db, actor, tool, a, [item])
        if tool == "get_board_info":
            cards = self._cards(db)
            public = {k: v for k, v in board.items() if k != "agent_permissions"}
            result = public | {"permissions": self.permissions(board, actor),
                               "supported_types": list(TYPES), "limits": LIMITS,
                               "active_card_count": sum(c["lifecycle"] == "active" for c in cards),
                               "deleted_card_count": sum(c["lifecycle"] == "deleted" for c in cards)}
            if actor["kind"] == "user":
                result["agent_permissions"] = board["agent_permissions"]
            return result
        if tool == "get_card_types":
            if a.get("type") and a["type"] not in TYPES:
                raise BusinessError("UNSUPPORTED_TYPE", "此卡片类型未注册。")
            return {"types": [t for t in card_types() if not a.get("type") or t["type"] == a["type"]]}
        if tool in ("get_card_list", "get_board_snapshot"):
            if a.get("cursor"):
                return self._page(db, actor, tool, a)
            cards = sorted(self._cards(db), key=lambda c: (c["layout"]["y"], c["layout"]["x"], c["id"]))
            life = a.get("lifecycle", "active") if tool == "get_card_list" else "active"
            cards = [c for c in cards if c["lifecycle"] == life]
            if a.get("type"):
                if a["type"] not in TYPES:
                    raise BusinessError("UNSUPPORTED_TYPE", "此卡片类型未注册。")
                cards = [c for c in cards if c["type"] == a["type"]]
            if a.get("title_query"):
                cards = [c for c in cards if a["title_query"].lower() in c["title"].lower()]
            full = tool == "get_board_snapshot" and a.get("view", "summary") == "full"
            items = cards if full else [self._summary(db, c) for c in cards]
            extra = ({"snapshot_id": uid("snapshot"), "revision": board["revision"],
                      "view": a.get("view", "summary")} if tool == "get_board_snapshot" else
                     {"list_id": uid("list"), "as_of_revision": board["revision"]})
            return self._page(db, actor, tool, a, items, extra)
        if tool == "read_card":
            card = self._card(db, a["card_id"])
            if card["lifecycle"] == "deleted" and not a.get("include_deleted", False):
                raise BusinessError("CARD_DELETED", "卡片已删除，可从回收站读取或恢复。")
            return {"card": card}
        if tool == "get_changes":
            if a.get("cursor"):
                return self._page(db, actor, tool, a)
            if a["since_revision"] > board["revision"]:
                raise BusinessError("INVALID_ARGUMENT", "起始版本不能大于当前版本。")
            if a["since_revision"] < board.get("migration_revision", 0):
                raise BusinessError("SNAPSHOT_REQUIRED", "看板已升级为自由画布，请重新获取完整快照。",
                                    details={"earliest_available_revision": board["migration_revision"]})
            earliest = db.execute("SELECT MIN(revision) FROM events").fetchone()[0]
            if a["since_revision"] < (earliest - 1 if earliest else board["revision"]):
                raise BusinessError("SNAPSHOT_REQUIRED", "该区间的变化记录已不可用，请重新获取快照。",
                                    details={"earliest_available_revision": earliest})
            events = [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM events WHERE revision>? AND revision<=? ORDER BY revision",
                (a["since_revision"], board["revision"]))]
            return self._page(db, actor, tool, a, events,
                              {"from_revision": a["since_revision"], "to_revision": board["revision"]})
        if tool == "push_card":
            if "layout" in a["card"] and a["card"]["layout"].get("space") != "world":
                raise BusinessError("UNSUPPORTED_SCHEMA", "旧网格布局不可写入，请查询 world 坐标契约。")
            card = normalize_card(a["card"], creating=True)
            self._check_asset_link(db, board, card)
            if "layout" not in card:
                card["layout"] = self._place(db, TYPES[card["type"]]["default_layout"],
                                             a.get("anchor_card_id"), kind=card["type"])
            card.update(id=uid("card"), board_id=board["id"], locked=False, lifecycle="active",
                        version=0, content_version=0, layout_version=0,
                        created_at=now(), created_by=actor, deleted_at=None, deleted_by=None)
            self._commit_card(db, board, actor, card, "created", content_changed=True, layout_changed=True)
            return {"card": card, "changed": True,
                    "placement": "requested" if "layout" in a["card"] else "automatic"}
        if tool in ("edit_card", "delete_card", "restore_card", "set_card_lock"):
            card = self._card(db, a["card_id"])
            if tool in ("edit_card", "set_card_lock") and card["lifecycle"] == "deleted":
                raise BusinessError("CARD_DELETED", "此卡片已删除，请先恢复。")
            if card["locked"] and tool != "set_card_lock":
                raise BusinessError("CARD_LOCKED", "此卡片已锁定，请由用户显式解锁。")
            if tool == "edit_card":
                if "layout" in a["changes"] and a["changes"]["layout"].get("space") != "world":
                    raise BusinessError("UNSUPPORTED_SCHEMA", "布局修改须明确 space=world，请查询最新契约。")
                parts = []
                if set(a["changes"]) - {"layout"}:
                    parts.append("content")
                if "layout" in a["changes"]:
                    parts.append("layout")
                self._check_version(card, a, parts)
            else:
                self._check_version(card, a)
            original = deepcopy(card)
            adjusted = False
            if tool == "edit_card":
                if not a["changes"]:
                    fail("至少需要一个可编辑字段。", "changes")
                for key, value in a["changes"].items():
                    card[key] = card[key] | value if key in ("props", "state", "layout") else value
                if card["type"] == "input" and card["props"].get("mode") != original["props"]["mode"]:
                    fail("输入框模式不可更改，请创建新卡片。", "props.mode")
                card = normalize_card(card)
                if card["type"] in ("image", "file") and card["state"]["asset_id"] != original["state"]["asset_id"]:
                    self._check_asset_link(db, board, card)
                kind = "updated"
            elif tool == "delete_card":
                if card["lifecycle"] == "active":
                    card.update(lifecycle="deleted", deleted_at=now(), deleted_by=actor)
                kind = "deleted"
            elif tool == "restore_card":
                if card["lifecycle"] == "deleted":
                    if self._collisions(db, card["layout"], card["id"]):
                        card["layout"] = self._place(db, card["layout"], origin=card["layout"],
                                                     kind=card["type"], excluding=card["id"])
                        adjusted = True
                    card.update(lifecycle="active", deleted_at=None, deleted_by=None)
                kind = "restored"
            else:
                card["locked"] = a["locked"]
                kind = "locked" if a["locked"] else "unlocked"
            changed = card != original
            if changed:
                content_changed = any(card[k] != original[k] for k in
                                      ("title", "description", "props", "state", "locked", "lifecycle"))
                layout_changed = (card["layout"] != original["layout"] or
                                  card["locked"] != original["locked"] or
                                  card["lifecycle"] != original["lifecycle"])
                self._commit_card(db, board, actor, card, kind, content_changed, layout_changed)
            result = {"card": card, "changed": changed}
            if tool == "restore_card":
                result["layout_adjusted"] = adjusted
            return result
        if tool == "get_operation_status":
            receipt = db.execute("SELECT body,tool FROM receipts WHERE actor=? AND board=? AND command=?",
                                 (actor["kind"] + ":" + actor["id"], board["id"], a["command_id"])).fetchone()
            if receipt:
                self._authorize(board, actor, receipt["tool"], {"board_id": board["id"]})
            result = json.loads(receipt[0]) if receipt else None
            return {"status": ("succeeded" if result["ok"] else "failed") if result else "unknown",
                    "result": result}
        if tool == "update_board":
            for key in ("name", "description", "agent_permissions"):
                if key in a:
                    if key == "name" and not a[key].strip():
                        fail("看板名称不能为空。", "name")
                    board[key] = board[key] | a[key] if key == "agent_permissions" else a[key].strip()
            self._save_board(db, board)
            return {"board": board}
        if tool == "grant_root":
            try:
                root = Path(a["path"]).resolve(strict=True)
            except (OSError, ValueError):
                fail("目录不存在或不可访问。", "path")
            if not root.is_dir():
                fail("请选择一个目录。", "path")
            root_id = uid("root")
            db.execute("INSERT INTO roots VALUES (?,?,?,1)", (root_id, a["label"], str(root)))
            return {"root_id": root_id, "label": a["label"]}
        if tool == "revoke_root":
            db.execute("UPDATE roots SET active=0 WHERE id=?", (a["root_id"],))
            return {"revoked": True, "root_id": a["root_id"]}
        if tool == "get_asset_sources":
            allowed = self.permissions(board, actor)["import_asset"]
            roots = [{"root_id": r["id"], "label": r["label"], "can_import": allowed, "can_reference": allowed}
                     for r in db.execute("SELECT id,label FROM roots WHERE active=1 ORDER BY id")]
            return {"roots": roots, "inline_max_bytes": LIMITS["inline_max_bytes"],
                    "asset_max_bytes": LIMITS["asset_max_bytes"]}
        if tool == "upload_asset":
            request = {"name": a["name"], "mode": "imported", "source": {
                "kind": "inline", "data_base64": a["data_base64"]}}
            return {"asset": self._import(db, board, actor, request, upload=True)}
        if tool == "import_asset":
            return {"asset": self._import(db, board, actor, a)}
        if tool == "get_asset_info":
            asset, _ = self._asset_content(db, a["asset_id"], strict=False)
            return {"asset": asset}
        if tool == "read_asset":
            return self._read_asset(db, actor, a)
        if tool == "list_assets":
            items = [json.loads(r[0]) for r in db.execute("SELECT body FROM assets ORDER BY id")]
            return self._page(db, actor, tool, a, items)
        raise BusinessError("INVALID_ARGUMENT", "未知操作。")

    @staticmethod
    def _card(db, card_id):
        row = db.execute("SELECT body FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            raise BusinessError("NOT_FOUND", "找不到此卡片。")
        return json.loads(row[0])

    @staticmethod
    def _check_version(card, args, parts=None):
        if "expected_version" in args:
            checks = [("version", args["expected_version"])]
        elif parts:
            checks = []
            for part in parts:
                key = "expected_" + part + "_version"
                if key not in args:
                    fail("缺少所修改分区的版本；也可以传 expected_version。", key, "INVALID_ARGUMENT")
                checks.append((part + "_version", args[key]))
        else:
            fail("此操作需要 expected_version。", "expected_version", "INVALID_ARGUMENT")
        for key, expected in checks:
            if expected != card[key]:
                raise BusinessError("VERSION_CONFLICT", "目标内容或布局已更新，本次修改未保存。",
                                    details={"card_id": card["id"], "partition": key,
                                             "expected_version": expected, "current_version": card[key],
                                             "card_version": card["version"]},
                                    recovery="重新读取卡片并审阅发生冲突的内容或布局，使用新命令提交。")

    def _commit_card(self, db, board, actor, card, kind, content_changed=True, layout_changed=False):
        board["revision"] += 1
        card.update(version=card["version"] + 1, last_revision=board["revision"],
                    updated_at=now(), updated_by=actor)
        card["content_version"] += int(content_changed)
        card["layout_version"] += int(layout_changed)
        db.execute("INSERT OR REPLACE INTO cards VALUES (?,?)", (card["id"], dump(card)))
        event = {"revision": board["revision"], "event_id": uid("evt"), "kind": kind,
                 "card_id": card["id"], "card_version": card["version"], "actor": actor,
                 "occurred_at": card["updated_at"], "card_after": card}
        db.execute("INSERT INTO events VALUES (?,?)", (board["revision"], dump(event)))
        self._save_board(db, board)

    def _collisions(self, db, layout, excluding=None):
        return [c for c in self._cards(db) if c["lifecycle"] == "active" and
                c["id"] != excluding and overlaps(layout, c["layout"])]

    def _place(self, db, size, anchor_id=None, origin=None, kind=None, excluding=None):
        cards = [c for c in self._cards(db) if c["lifecycle"] == "active" and c["id"] != excluding]
        anchor = self._card(db, anchor_id)["layout"] if anchor_id else None
        origin = origin or ({"x": anchor["x"] + anchor["w"] + 24, "y": anchor["y"]} if anchor else
                            {"x": 0, "y": 0})
        rows = sorted({origin["y"]} | {c["layout"]["y"] + c["layout"]["h"] + 24 for c in cards
                                      if c["layout"]["y"] >= origin["y"]})
        columns = sorted({origin["x"]} | {c["layout"]["x"] + c["layout"]["w"] + 24 for c in cards
                                         if c["layout"]["x"] >= origin["x"]})
        for y in rows:
            for x in columns:
                layout = {"space": "world", "x": x, "y": y, "w": size["w"], "h": size["h"],
                          "z": size.get("z", 0),
                          "height_mode": size.get("height_mode", "auto" if kind == "text" else "fixed")}
                if x > 10000000 or y > 10000000:
                    continue
                if not any(overlaps(layout, c["layout"]) for c in cards):
                    return layout
        raise BusinessError("LAYOUT_CONFLICT", "无法自动放置，请指定布局。")

    def _summary(self, db, card):
        keys = ("id", "board_id", "type", "type_version", "title", "layout", "locked", "lifecycle",
                "version", "content_version", "layout_version", "last_revision", "updated_at", "updated_by")
        result = {key: card[key] for key in keys}
        p, s, kind = card["props"], card["state"], card["type"]
        if kind == "text":
            preview = {"text": s["text"][:160], "truncated": len(s["text"]) > 160}
            summary = preview["text"]
        elif kind == "slider":
            preview = {"value": s["value"], "unit": p["unit"]}
            summary = str(s["value"]) + " " + p["unit"]
        elif kind == "checkbox":
            preview = {"checked": s["checked"], "label": p["label"]}
            summary = p["label"] + ("：是" if s["checked"] else "：否")
        elif kind == "input":
            preview = {"mode": p["mode"], "value": s["value"], "unit": p.get("unit", "")}
            summary = str(s["value"]) if s["value"] is not None else "未填写"
        else:
            row = db.execute("SELECT body FROM assets WHERE id=?", (s["asset_id"],)).fetchone()
            name = json.loads(row[0])["name"] if row else None
            preview = {"asset_id": s["asset_id"], "name": name}
            summary = s.get("caption", s.get("note", "")) or name or "资源不可用"
        result.update(preview=preview, summary=summary[:160])
        return result

    def _cursor(self, db, actor, tool, args, body):
        token = uid("cursor")
        signature = dump({k: v for k, v in args.items() if k != "cursor"})
        db.execute("INSERT INTO cursors VALUES (?,?,?,?,?,?)",
                   (token, actor["kind"] + ":" + actor["id"], tool, signature, time.time() + 600, dump(body)))
        # Keep recently expired handles long enough to report CURSOR_EXPIRED distinctly.
        db.execute("DELETE FROM cursors WHERE expires<?", (time.time() - 86400,))
        return token

    def _load_cursor(self, db, actor, tool, args):
        row = db.execute("SELECT * FROM cursors WHERE id=?", (args["cursor"],)).fetchone()
        signature = dump({k: v for k, v in args.items() if k != "cursor"})
        if not row or row["actor"] != actor["kind"] + ":" + actor["id"] or row["tool"] != tool or row["signature"] != signature:
            raise BusinessError("CURSOR_INVALID", "续读标记无效或读取条件不一致。")
        if row["expires"] < time.time():
            raise BusinessError("CURSOR_EXPIRED", "续读标记已过期，请从头重新读取。")
        return json.loads(row["body"]), row["expires"]

    def _page(self, db, actor, tool, args, items=None, extra=None):
        expires = None
        if args.get("cursor"):
            frozen, expires = self._load_cursor(db, actor, tool, args)
            items, extra, start = frozen["items"], frozen["extra"], frozen["start"]
        else:
            start = 0
        extra = extra or {}
        limit = args.get("limit", 20)
        page = items[start:start + limit]
        end = start + len(page)
        token = None
        if end < len(items):
            token = self._cursor(db, actor, tool, args, {"items": items, "extra": extra, "start": end})
            if expires is not None:
                db.execute("UPDATE cursors SET expires=? WHERE id=?", (expires, token))
        result = extra | {"items": page, "next_cursor": token}
        if tool in ("get_board_snapshot", "get_changes"):
            result["complete"] = token is None
        if tool == "get_changes":
            result["next_since_revision"] = (extra["to_revision"] if token is None else
                                             page[-1]["revision"] if page else extra["from_revision"])
        return result

    @staticmethod
    def _resolve_source(db, source):
        root = db.execute("SELECT * FROM roots WHERE id=? AND active=1", (source["root_id"],)).fetchone()
        if not root:
            raise BusinessError("ASSET_UNAVAILABLE", "资源目录未授权或授权已撤销。",
                                details={"reason": "forbidden"})
        rel = source["relative_path"]
        # Reject Windows device/drive syntax and alternate data streams on all platforms.
        reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
            prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789¹²³"}
        device_path = any(part.rstrip(" .").split(".")[0].upper() in reserved
                          for part in PureWindowsPath(rel).parts)
        if (":" in rel or "\x00" in rel or Path(rel).is_absolute() or
                PureWindowsPath(rel).is_absolute() or device_path):
            raise BusinessError("ASSET_UNAVAILABLE", "资源路径不在授权目录内。",
                                details={"reason": "forbidden"})
        root_path = Path(root["path"]).resolve()
        path = (root_path / rel).resolve()
        if not path.is_relative_to(root_path):
            raise BusinessError("ASSET_UNAVAILABLE", "资源路径越过授权目录边界。",
                                details={"reason": "forbidden"})
        return path

    def _file_bytes(self, db, source):
        try:
            path = self._resolve_source(db, source)
            if not path.is_file():
                reason = "unreadable" if path.exists() else "missing"
                raise BusinessError("ASSET_UNAVAILABLE", "引用目标不是可读取的普通文件。",
                                    details={"reason": reason})
            # Resolve symlinks again on every read, including Windows junctions.
            with path.open("rb") as stream:
                if os.fstat(stream.fileno()).st_size > LIMITS["asset_max_bytes"]:
                    raise BusinessError("ASSET_UNAVAILABLE", "资源超过 20 MiB 限额。",
                                        details={"reason": "too_large"})
                content = stream.read(LIMITS["asset_max_bytes"] + 1)
            if len(content) > LIMITS["asset_max_bytes"]:
                raise BusinessError("ASSET_UNAVAILABLE", "资源超过大小限额。", details={"reason": "too_large"})
            return content
        except FileNotFoundError:
            raise BusinessError("ASSET_UNAVAILABLE", "引用的原文件已不存在。", details={"reason": "missing"})
        except (OSError, ValueError):
            raise BusinessError("ASSET_UNAVAILABLE", "引用的原文件不可读取。", details={"reason": "unreadable"})

    @staticmethod
    def _detect(content):
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "image", ["bytes"]
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "image", ["bytes"]
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif", "image", ["bytes"]
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp", "image", ["bytes"]
        if content.startswith(b"%PDF-"):
            return "application/pdf", "none", ["bytes"]
        try:
            content.decode("utf-8")
            if b"\x00" not in content:
                return "text/plain; charset=utf-8", "text", ["text", "bytes"]
        except UnicodeDecodeError:
            pass
        return "application/octet-stream", "none", ["bytes"]

    def _import(self, db, board, actor, a, upload=False):
        source = a["source"]
        if source["kind"] == "inline":
            if a["mode"] != "imported":
                fail("inline 只支持导入副本。", "mode")
            try:
                content = base64.b64decode(source["data_base64"], validate=True)
            except ValueError:
                fail("Base64 内容无效。", "source.data_base64")
            maximum = LIMITS["asset_max_bytes"] if upload else LIMITS["inline_max_bytes"]
            if len(content) > maximum:
                raise BusinessError("LIMIT_EXCEEDED", "导入资源超出此入口的大小限额。")
            public_source = {"kind": "upload" if upload else "inline"}
        else:
            content = self._file_bytes(db, source)
            public_source = ({"kind": "approved_file"} if a["mode"] == "imported" else
                             {"root_id": source["root_id"], "relative_path": source["relative_path"]})
        mime, preview, readable = self._detect(content)
        asset = {"id": uid("asset"), "board_id": board["id"], "mode": a["mode"],
                 "name": a["name"], "mime_type": mime, "size_bytes": len(content),
                 "source": public_source, "status": "available",
                 "resource_version": hashlib.sha256(content).hexdigest(), "checked_at": now(),
                 "created_at": now(), "created_by": actor, "preview_kind": preview, "readable_as": readable}
        db.execute("INSERT INTO assets VALUES (?,?,?)",
                   (asset["id"], dump(asset), content if a["mode"] == "imported" else None))
        return asset

    def _asset_content(self, db, asset_id, strict=True):
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise BusinessError("NOT_FOUND", "找不到此资源。")
        asset = json.loads(row["body"])
        content = row["content"]
        try:
            if asset["mode"] == "reference":
                content = self._file_bytes(db, asset["source"])
            if content is None:
                raise BusinessError("ASSET_UNAVAILABLE", "资源内容不可读取。", details={"reason": "missing"})
            mime, preview, readable = self._detect(content)
            asset.update(status="available", mime_type=mime, preview_kind=preview, readable_as=readable,
                         size_bytes=len(content), resource_version=hashlib.sha256(content).hexdigest())
        except BusinessError as error:
            asset.update(status=error.error["details"].get("reason", "unreadable"),
                         resource_version=None, size_bytes=None)
            if strict:
                raise
        asset["checked_at"] = now()
        db.execute("UPDATE assets SET body=? WHERE id=?", (dump(asset), asset_id))
        return asset, content

    def _check_asset_link(self, db, board, card):
        if card["type"] not in ("image", "file"):
            return
        asset, _ = self._asset_content(db, card["state"]["asset_id"])
        if asset["board_id"] != board["id"]:
            raise BusinessError("NOT_FOUND", "找不到此看板资源。")
        if card["type"] == "image" and asset["preview_kind"] != "image":
            fail("图片卡片需要 PNG、JPEG、WebP 或 GIF 图片资源。", "state.asset_id")

    def _read_asset(self, db, actor, args):
        asset, content = self._asset_content(db, args["asset_id"])
        version, offset, expires = args.get("resource_version"), 0, None
        if args.get("cursor"):
            frozen, expires = self._load_cursor(db, actor, "read_asset", args)
            version, offset = frozen["version"], frozen["offset"]
        if version is not None and version != asset["resource_version"]:
            raise BusinessError("ASSET_CHANGED", "资源内容已改变，请重新开始读取。")
        if args["format"] not in asset["readable_as"]:
            raise BusinessError("UNSUPPORTED_TYPE", "此资源不支持指定读取格式。")
        if args["format"] == "text":
            raw = content.decode("utf-8")
            piece = raw[offset:offset + LIMITS["text_chunk_codepoints"]]
            end = offset + len(piece)
        else:
            raw = content
            chunk = raw[offset:offset + LIMITS["bytes_chunk_size"]]
            piece = base64.b64encode(chunk).decode()
            end = offset + len(chunk)
        token = None
        if end < len(raw):
            token = self._cursor(db, actor, "read_asset", args,
                                 {"offset": end, "version": asset["resource_version"]})
            if expires is not None:
                db.execute("UPDATE cursors SET expires=? WHERE id=?", (expires, token))
        return {"asset_id": asset["id"], "resource_version": asset["resource_version"],
                "format": args["format"], "offset": offset, "content": piece,
                "next_cursor": token, "complete": token is None}

    def asset_bytes_for_user(self, asset_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return self._asset_content(db, asset_id)
