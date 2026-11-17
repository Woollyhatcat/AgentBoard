"""Portable, bounded ZIP data files. Never extract files or execute imported code."""

from contextlib import closing
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import re
import zipfile
import zlib

from . import __version__
from .schema import BusinessError, normalize_card, validate, string, PERMISSIONS
from .service import dump, now, uid

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_CARDS = 10000
MAX_ASSETS = 1000
CARD_FIELDS = ("id","type","type_version","title","description","props","state","layout","locked","lifecycle")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def invalid(message):
    raise BusinessError("INVALID_ARCHIVE",message)


def _identifier(value, prefix=None):
    if not isinstance(value,str) or not IDENTIFIER.fullmatch(value) or (prefix and not value.startswith(prefix+"_")):
        invalid("数据包包含无效标识。")
    return value


def export_board(service, board_id, actor):
    with closing(service.connect()) as db:
        db.execute("BEGIN")
        board = service._board(db,board_id)
        service._authorize(board,actor,"get_board_info",{"board_id":board_id})
        cards = service._cards(db,board_id)
        rows = db.execute("SELECT * FROM assets WHERE json_extract(body,'$.board_id')=? ORDER BY id",(board_id,)).fetchall()
        if len(cards) > MAX_CARDS or len(rows) > MAX_ASSETS:
            raise BusinessError("LIMIT_EXCEEDED","黑板超过单文件上限：10000张卡片、1000项资源。")
        manifest = {"format":"agentboard.board","format_version":1,"app_version":__version__,"exported_at":now(),
                    "board":{key:board[key] for key in ("id","name","description","locked","agent_permissions")},
                    "cards":[{key:deepcopy(card[key]) for key in CARD_FIELDS} for card in cards],"assets":[]}
        payloads, total = [], 0
        for row in rows:
            asset = json.loads(row["body"])
            try:
                content = service._file_bytes(db,asset["source"]) if asset["mode"] == "reference" else row["content"]
                if content is None:
                    raise BusinessError("ASSET_UNAVAILABLE","资源内容缺失。")
            except BusinessError as error:
                raise BusinessError("ASSET_UNAVAILABLE","无法打包资源「"+asset["name"]+"」："+error.error["message"],
                    recovery="恢复文件／目录授权，或移除不再需要的资源后重新导出。")
            total += len(content)
            if len(content) > MAX_ASSET_BYTES or total > MAX_ARCHIVE_BYTES:
                raise BusinessError("LIMIT_EXCEEDED","资源超过导出上限：单项20 MiB、总计256 MiB。")
            entry = "assets/"+_identifier(asset["id"])+".bin"
            manifest["assets"].append({"id":asset["id"],"name":asset["name"],"entry":entry,
                "size_bytes":len(content),"sha256":hashlib.sha256(content).hexdigest()})
            payloads.append((entry,content))
        metadata = dump(manifest).encode("utf-8")
        if len(metadata) > MAX_MANIFEST_BYTES or total+len(metadata) > MAX_ARCHIVE_BYTES:
            raise BusinessError("LIMIT_EXCEEDED","黑板内容超过数据包大小上限。")
        output = BytesIO()
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json",metadata)
            for entry,content in payloads: archive.writestr(entry,content)
        raw = output.getvalue()
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise BusinessError("LIMIT_EXCEEDED","数据包超过256 MiB。")
        return raw, board["name"]


def read_archive(raw):
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        invalid("数据文件为空或超过256 MiB。")
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_ASSETS+1 or len(set(names)) != len(names) or "manifest.json" not in names:
                invalid("数据包目录无效或包含重复文件。")
            if any(entry.flag_bits & 1 for entry in entries) or sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                invalid("不支持加密文件或解压后超过256 MiB的数据包。")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                invalid("黑板清单超过16 MiB。")
            def unique_object(pairs):
                result = {}
                for key,value in pairs:
                    if key in result: invalid("清单包含重复字段。")
                    result[key] = value
                return result
            manifest = json.loads(archive.read("manifest.json"),object_pairs_hook=unique_object,
                parse_constant=lambda _:invalid("清单包含非有限数值。"))
            dump(manifest).encode("utf-8")
            if not isinstance(manifest,dict) or manifest.get("format") != "agentboard.board" or type(manifest.get("format_version")) is not int or manifest["format_version"] != 1:
                invalid("不是受支持的AgentBoard数据文件（需要格式版本1）。")
            board,cards,assets = manifest.get("board"),manifest.get("cards"),manifest.get("assets")
            if not isinstance(board,dict) or not isinstance(cards,list) or not isinstance(assets,list):
                invalid("数据包缺少黑板、卡片或资源清单。")
            if len(cards) > MAX_CARDS or len(assets) > MAX_ASSETS: invalid("卡片或资源数量超过限制。")
            _identifier(board.get("id"),"board")
            validate(board.get("name"),string(80,1))
            validate(board.get("description"),string())
            if not board["name"].strip() or type(board.get("locked")) is not bool: invalid("黑板名称或删除保护无效。")
            validate(board.get("agent_permissions"),PERMISSIONS)
            if set(board["agent_permissions"]) != set(PERMISSIONS["properties"]): invalid("黑板权限字段不完整。")
            content, asset_ids, expected = {}, set(), {"manifest.json"}
            for asset in assets:
                if not isinstance(asset,dict): invalid("资源条目无效。")
                id = _identifier(asset.get("id"),"asset")
                validate(asset.get("name"),string(255,1))
                entry = "assets/"+id+".bin"
                if id in asset_ids or asset.get("entry") != entry or entry not in names:
                    invalid("资源标识重复或文件缺失。")
                if archive.getinfo(entry).file_size > MAX_ASSET_BYTES: invalid("单项资源超过20 MiB。")
                data = archive.read(entry)
                if type(asset.get("size_bytes")) is not int or len(data) != asset["size_bytes"] or hashlib.sha256(data).hexdigest() != asset.get("sha256"):
                    invalid("资源完整性校验失败。")
                content[id] = data; asset_ids.add(id); expected.add(entry)
            if set(names) != expected: invalid("数据包包含清单外文件。")
            card_ids, normalized = set(), []
            for card in cards:
                if not isinstance(card,dict) or set(card) != set(CARD_FIELDS): invalid("卡片字段不完整或包含未知字段。")
                id = _identifier(card.get("id"),"card")
                if id in card_ids: invalid("卡片标识重复。")
                if type(card["locked"]) is not bool or card["lifecycle"] not in ("active","deleted"):
                    invalid("卡片锁定或回收站状态无效。")
                normalized.append(normalize_card(card)); card_ids.add(id)
                if card["type"] in ("image","file") and card["state"]["asset_id"] not in asset_ids:
                    invalid("卡片引用了缺失的资源。")
            if card_ids & asset_ids or board["id"] in card_ids | asset_ids: invalid("数据包标识冲突。")
            # Missing/deleted inputs stay visible as input errors after import;
            # cycles are never admitted, even if hidden in trash cards.
            graph = {card["id"]:[b["card_id"] for b in card["props"].get("bindings",{}).values()] if card["type"] == "interactive" else [] for card in normalized}
            done = set()
            for id in graph:
                stack, visiting = [(id,False)], set()
                while stack:
                    node,leaving = stack.pop()
                    if leaving: visiting.remove(node); done.add(node); continue
                    if node in done: continue
                    if node in visiting: invalid("交互绑定包含循环依赖。")
                    if node not in graph: invalid("交互绑定引用了数据包外的卡片。")
                    visiting.add(node); stack.append((node,True))
                    stack.extend((target,False) for target in graph[node])
            return {"board":board,"cards":normalized,"assets":assets,"content":content}
    except BusinessError as error:
        if error.error["code"] == "INVALID_ARCHIVE": raise
        invalid("数据包字段无效："+error.error["message"])
    except (zipfile.BadZipFile,zlib.error,ValueError,KeyError,TypeError,RecursionError,RuntimeError,NotImplementedError,OSError):
        invalid("数据文件损坏、格式不受支持或无法读取。")


def install_archive(service, db, package, actor):
    if package is None: invalid("请使用管理黑板中的文件导入入口。")
    source = package["board"]
    board = service._create_board(db,source["name"],source["description"],actor)
    board.update(locked=source["locked"],agent_permissions=deepcopy(source["agent_permissions"]))
    ids = {card["id"]:uid("card") for card in package["cards"]}
    ids.update({asset["id"]:uid("asset") for asset in package["assets"]})
    ids[source["id"]] = board["id"]
    pattern = re.compile("|".join(re.escape(id) for id in sorted(ids,key=len,reverse=True)))
    def remap(value):
        if isinstance(value,dict): return {k:remap(v) for k,v in value.items()}
        if isinstance(value,list): return [remap(v) for v in value]
        return pattern.sub(lambda match:ids[match[0]],value) if isinstance(value,str) else value
    for item in package["assets"]:
        data = package["content"][item["id"]]
        mime,preview,readable = service._detect(data)
        asset = {"id":ids[item["id"]],"board_id":board["id"],"name":item["name"],"mode":"imported",
                 "source":{"kind":"board_archive"},"mime_type":mime,"preview_kind":preview,"readable_as":readable,
                 "size_bytes":len(data),"resource_version":hashlib.sha256(data).hexdigest(),"status":"available",
                 "checked_at":now(),"created_at":now(),"created_by":actor}
        db.execute("INSERT INTO assets VALUES (?,?,?)",(asset["id"],dump(asset),data))
    for original in package["cards"]:
        card = remap(original)
        service._check_asset_link(db,board,card)
        card.update(board_id=board["id"],version=0,content_version=0,layout_version=0,created_at=now(),created_by=actor,
                    deleted_at=now() if card["lifecycle"] == "deleted" else None,
                    deleted_by=actor if card["lifecycle"] == "deleted" else None)
        service._commit_card(db,board,actor,card,"created",True,True)
    service._save_board(db,board)
    return {"board":board,"card_count":len(package["cards"]),"asset_count":len(package["assets"])}
