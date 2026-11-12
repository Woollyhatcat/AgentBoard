"""One atomic, backed-up upgrade from grid cards to the world-space contract."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

LAYOUT_POLICY = {"space": "world", "unit": "css_px_at_100_percent", "collision": "allow",
                 "coordinate_limit": 10000000, "auto_height_limit": 720}


def upgrade_embedded(value):
    if isinstance(value, list):
        return [upgrade_embedded(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: upgrade_embedded(item) for key, item in value.items()}
    # Full cards, summaries, events and frozen read pages all carry id + type + layout.
    if "layout" in result and "type" in result and "id" in result:
        layout = result["layout"]
        if layout.get("space") != "world":
            result["layout"] = {"space": "world", "x": layout["x"] * 112,
                                "y": layout["y"] * 88, "w": layout["w"] * 112 - 16,
                                "h": layout["h"] * 88 - 16, "z": 0,
                                "height_mode": "auto" if result["type"] == "text" else "fixed"}
        result.setdefault("content_version", result.get("version", 1))
        result.setdefault("layout_version", result.get("version", 1))
        if result["type"] == "text" and "props" in result:
            result["props"].setdefault("font_size", 16)
    return result


def migrate(db, database):
    row = db.execute("SELECT id,body FROM board").fetchone()
    board = json.loads(row["body"])
    if board["schema_version"] == "2.0":
        return
    if board["schema_version"] != "1.0":
        raise RuntimeError("不支持此数据库版本。")
    backups = Path(database).parent / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / f"before-world-{stamp}.sqlite3"
    # The caller holds BEGIN IMMEDIATE: other writers are excluded, a separate
    # source connection can still back up the committed WAL snapshot safely.
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    for table, key in (("cards", "id"), ("events", "revision"), ("cursors", "id")):
        for item in db.execute(f"SELECT {key},body FROM {table}").fetchall():
            changed = upgrade_embedded(json.loads(item["body"]))
            db.execute(f"UPDATE {table} SET body=? WHERE {key}=?",
                       (json.dumps(changed, ensure_ascii=False), item[key]))
    for item in db.execute("SELECT rowid,body FROM receipts").fetchall():
        changed = upgrade_embedded(json.loads(item["body"]))
        db.execute("UPDATE receipts SET body=? WHERE rowid=?",
                   (json.dumps(changed, ensure_ascii=False), item["rowid"]))
    board.update(schema_version="2.0", layout_policy=LAYOUT_POLICY,
                 revision=board["revision"] + 1, migration_backup=backup.name)
    board["migration_revision"] = board["revision"]
    db.execute("UPDATE board SET body=? WHERE id=?", (json.dumps(board, ensure_ascii=False), row["id"]))


def install_write_guard(db):
    # Reject writes from already-running v1 processes, which don't advance either
    # partition version and don't understand world coordinates. No data is removed.
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS world_card_insert BEFORE INSERT ON cards BEGIN
          SELECT CASE WHEN json_extract(NEW.body,'$.layout.space') IS NOT 'world'
            OR json_extract(NEW.body,'$.content_version') IS NULL
            OR json_extract(NEW.body,'$.layout_version') IS NULL
            THEN RAISE(ABORT,'AgentBoard schema upgraded; reconnect MCP') END;
          SELECT CASE WHEN EXISTS(SELECT 1 FROM cards WHERE id=NEW.id
            AND json_extract(NEW.body,'$.version')>json_extract(body,'$.version')
            AND json_extract(NEW.body,'$.content_version')<=json_extract(body,'$.content_version')
            AND json_extract(NEW.body,'$.layout_version')<=json_extract(body,'$.layout_version'))
            THEN RAISE(ABORT,'AgentBoard versions upgraded; reconnect MCP') END;
        END;
    """)
