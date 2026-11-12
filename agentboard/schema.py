"""Public JSON Schema contracts and the small, explicit validator they use."""

from copy import deepcopy
from decimal import Decimal
import math
import re

DIALECT = "https://json-schema.org/draft/2020-12/schema"


class BusinessError(Exception):
    def __init__(self, code, message, path=None, details=None, recovery=None):
        super().__init__(message)
        self.error = {
            "code": code, "message": message,
            "field_errors": ([{"path": path, "code": code, "message": message}] if path else []),
            "retryable": code in ("SERVICE_UNAVAILABLE", "OPERATION_PENDING"),
            "recovery": recovery or "请核对参数或重新读取当前状态后，使用新命令提交。",
            "details": details or {},
        }


def fail(message, path=None, code="VALIDATION_FAILED", **details):
    raise BusinessError(code, message, path, details)


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def string(maximum=2000, minimum=0):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


NUMBER = {"type": "number"}
BOOL = {"type": "boolean"}
NULLABLE_NUMBER = {"type": ["number", "null"]}
ID = string(255, 1)
VERSION = {"type": "integer", "minimum": 1, "maximum": 9007199254740991}
REVISION = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
COMMAND = {"type": "string", "pattern": r"^[\x21-\x7e]{1,128}$", "maxLength": 128}
LAYOUT = obj({"space": enum("world"),
              "x": {"type": "number", "minimum": -10000000, "maximum": 10000000},
              "y": {"type": "number", "minimum": -10000000, "maximum": 10000000},
              "w": {"type": "number", "minimum": 180, "maximum": 10000},
              "h": {"type": "number", "minimum": 100, "maximum": 20000},
              "z": {"type": "integer", "minimum": -9007199254740991, "maximum": 9007199254740991},
              "height_mode": enum("auto", "fixed", "expanded")}, ("space", "x", "y", "w", "h"))

TYPES = {
    "text": {"label": "文本", "description": "说明、资料与阶段结论，支持安全 Markdown。",
             "props_schema": obj({"format": enum("plain", "markdown"),
                                  "font_size": {"type": "integer", "minimum": 12, "maximum": 48}}),
             "state_schema": obj({"text": string(100000)}, ("text",)),
             "default_props": {"format": "markdown", "font_size": 16}, "default_state": {},
             "default_layout": {"w": 432, "h": 336}},
    "image": {"label": "图片", "description": "图片资源与说明；资源字节单独读取。",
              "props_schema": obj({"fit": enum("contain", "cover"), "alt": string(500)}),
              "state_schema": obj({"asset_id": ID, "caption": string()}, ("asset_id",)),
              "default_props": {"fit": "contain", "alt": ""},
              "default_state": {"caption": ""}, "default_layout": {"w": 432, "h": 336}},
    "file": {"label": "文件", "description": "导入副本或用户授权的本地文件引用。",
             "props_schema": obj({"preview": enum("auto", "none")}),
             "state_schema": obj({"asset_id": ID, "note": string()}, ("asset_id",)),
             "default_props": {"preview": "auto"}, "default_state": {"note": ""},
             "default_layout": {"w": 432, "h": 200}},
    "slider": {"label": "滑条", "description": "有限数值范围，按步长选择精确共享值。",
               "props_schema": obj({"min": NUMBER, "max": NUMBER, "step": NUMBER,
                                    "unit": string(24),
                                    "precision": {"type": "integer", "minimum": 0, "maximum": 6}},
                                   ("min", "max", "step")),
               "state_schema": obj({"value": NUMBER}, ("value",)),
               "default_props": {"unit": "", "precision": 2}, "default_state": {},
               "default_layout": {"w": 432, "h": 200}},
    "checkbox": {"label": "勾选框", "description": "共享一个明确的布尔状态。",
                 "props_schema": obj({"label": string(200, 1)}, ("label",)),
                 "state_schema": obj({"checked": BOOL}, ("checked",)),
                 "default_props": {}, "default_state": {}, "default_layout": {"w": 320, "h": 180}},
    "input": {"label": "输入框", "description": "文本或数值输入；空值为 null，模式不可更改。",
              "props_schema": {"oneOf": [
                  obj({"mode": enum("text"), "placeholder": string(200), "required": BOOL,
                       "max_length": {"type": "integer", "minimum": 1, "maximum": 2000}},
                      ("mode",)),
                  obj({"mode": enum("number"), "placeholder": string(200), "required": BOOL,
                       "min": NULLABLE_NUMBER, "max": NULLABLE_NUMBER, "unit": string(24),
                       "precision": {"type": "integer", "minimum": 0, "maximum": 6}}, ("mode",))]},
              "state_schema": obj({"value": {"type": ["string", "number", "null"]}}, ("value",)),
              "default_props": {"placeholder": "", "required": False}, "default_state": {},
              "default_layout": {"w": 432, "h": 200}},
}


def validate(value, schema, path="", code="VALIDATION_FAILED"):
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                validate(value, option, path, code)
                matches += 1
            except BusinessError:
                pass
        if matches != 1:
            fail("输入字段与当前类型或模式不匹配。", path, code)
        return
    if "type" in schema:
        allowed = schema["type"]
        allowed = allowed if isinstance(allowed, list) else [allowed]
        types = {"object": type(value) is dict, "array": type(value) is list,
                 "string": type(value) is str, "integer": type(value) is int,
                 "number": type(value) in (int, float), "boolean": type(value) is bool,
                 "null": value is None}
        if not any(types[t] for t in allowed):
            fail("字段类型错误，应为 " + " / ".join(allowed) + "。", path, code)
    if "enum" in schema and value not in schema["enum"]:
        fail("不支持此选项。", path, code)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                fail("缺少必填字段。", f"{path}.{key}".strip("."), code)
        for key, item in value.items():
            item_path = f"{path}.{key}".strip(".")
            if key not in schema.get("properties", {}):
                if schema.get("additionalProperties") is False:
                    fail("不允许写入未知或只读字段。", item_path, code)
            else:
                validate(item, schema["properties"][key], item_path, code)
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf):
            fail("文字长度超出允许范围。", path, code)
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            fail("字段格式无效。", path, code)
    elif type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            fail("数值必须有限。", path, code)
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            fail("数值超出允许范围。", path, code)


def decimal6(value, path):
    number = Decimal(str(value))
    digits = number.as_tuple()
    fractional = -digits.exponent
    trailing = 0
    for digit in reversed(digits.digits):
        if digit != 0:
            break
        trailing += 1
    if fractional - trailing > 6:
        fail("数值最多保留六位小数。", path)
    return number


def normalize_card(card, creating=False):
    """Merge defaults on creation, then validate the complete resulting card."""
    kind = card.get("type")
    if kind not in TYPES or card.get("type_version") != "1.0":
        fail("不支持此卡片类型或类型版本。", "type", "UNSUPPORTED_TYPE")
    card = deepcopy(card)
    if not isinstance(card["title"], str):
        fail("标题必须为文本。", "title")
    card["title"] = card["title"].strip()
    validate(card["title"], string(120, 1), "title")
    validate(card.get("description", ""), string(), "description")
    definition = TYPES[kind]
    if creating:
        card.setdefault("description", "")
        card["props"] = definition["default_props"] | card["props"]
        card["state"] = definition["default_state"] | card["state"]
        if kind == "input":
            extra = ({"max_length": 500} if card["props"].get("mode") == "text" else
                     {"min": None, "max": None, "unit": "", "precision": 2})
            card["props"] = extra | card["props"]
    p, s = card["props"], card["state"]
    validate(p, definition["props_schema"], "props")
    validate(s, definition["state_schema"], "state")
    if kind == "slider":
        decimal_values = [decimal6(v, k) for v, k in
                                ((p["min"], "props.min"), (p["max"], "props.max"),
                                 (p["step"], "props.step"), (s["value"], "state.value"))]
        # Integer micro-units avoid binary floating point and Decimal context rounding.
        def micro(value):
            sign, digits, exponent = value.as_tuple()
            coefficient = int("".join(map(str, digits))) * (-1 if sign else 1)
            return (coefficient * 10 ** (exponent + 6) if exponent >= -6 else
                    coefficient // 10 ** (-exponent - 6))
        low, high, step, val = map(micro, decimal_values)
        if low >= high or step <= 0:
            fail("滑条要求 min < max，且 step > 0。", "props")
        if val < low or val > high or (val - low) % step != 0:
            fail("数值须在范围内，且落在 min + n × step 的步长点上。", "state.value")
    if kind == "input":
        v = s["value"]
        if p["mode"] == "text":
            if v is not None and not isinstance(v, str):
                fail("文本模式只接受字符串或 null。", "state.value")
            if v == "":
                s["value"] = v = None
            if v is not None and len(v) > p["max_length"]:
                fail("输入文字超过 max_length。", "state.value")
            if p["required"] and (v is None or not v.strip()):
                fail("此输入框必须填写非空白文字。", "state.value")
        else:
            for key in ("min", "max"):
                if p[key] is not None:
                    decimal6(p[key], "props." + key)
            if p["min"] is not None and p["max"] is not None and p["min"] > p["max"]:
                fail("最小值不能大于最大值。", "props.min")
            if v is not None:
                validate(v, NUMBER, "state.value")
                decimal6(v, "state.value")
                if ((p["min"] is not None and v < p["min"]) or
                        (p["max"] is not None and v > p["max"])):
                    fail("数值不在输入框允许的范围内。", "state.value")
            elif p["required"]:
                fail("此输入框必须填写数值，0 是有效值。", "state.value")
    if "layout" in card:
        validate(card["layout"], LAYOUT, "layout")
        card["layout"].setdefault("z", 0)
        card["layout"].setdefault("height_mode", "auto" if kind == "text" else "fixed")
    return card


def card_types():
    result = []
    for kind, definition in TYPES.items():
        item = deepcopy(definition)
        item.pop("default_state")
        item.update(type=kind, type_version="1.0",
                    min_layout={"w": 180, "h": 100},
                    constraints=["完整卡片在局部合并后统一校验，未指定字段保留原值。",
                                 "layout.space=world，单位为100%时CSS像素；允许负坐标与重叠。"])
        if kind == "text":
            item["constraints"] += ["font_size 为共享整数12～48，默认16；修改正文时保留未指定字号。",
                                    "height_mode: auto限高720内部滚动，expanded完整展开，fixed固定高度。"]
        if kind == "slider":
            item["constraints"] += ["min < max; step > 0; value = min + n × step，n 为非负整数。",
                                    "min、max、step、value 最多六位小数；precision 只影响显示。"]
        if kind == "input":
            item["constraints"] += ["mode 不可更改；required 与当前值、上下界一起校验。",
                                    "文本模式默认 max_length=500；空字符串归一为 null，保留非空文字空白。",
                                    "数值模式默认 min=null,max=null,unit='',precision=2；数值最多六位小数。"]
        item["props_schema"]["$schema"] = DIALECT
        item["state_schema"]["$schema"] = DIALECT
        result.append(item)
    return result


CREATE_CARD = obj({"type": enum(*TYPES), "type_version": enum("1.0"),
                   "title": string(120, 1), "description": string(),
                   "props": {"type": "object"}, "state": {"type": "object"}, "layout": LAYOUT},
                  ("type", "type_version", "title", "props", "state"))
CHANGES = obj({"title": string(120, 1), "description": string(), "props": {"type": "object"},
               "state": {"type": "object"}, "layout": obj(LAYOUT["properties"])})
SOURCE = {"oneOf": [
    obj({"kind": enum("inline"), "mime_type": string(255, 1), "data_base64": string(1400000)},
        ("kind", "mime_type", "data_base64")),
    obj({"kind": enum("approved_file"), "root_id": ID, "relative_path": string(4096, 1)},
        ("kind", "root_id", "relative_path"))]}
PAGE = {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": ID}
BOARD = {"board_id": ID}
WRITE = BOARD | {"command_id": COMMAND}
TARGET = WRITE | {"card_id": ID, "expected_version": VERSION}
EDIT_TARGET = TARGET | {"expected_content_version": VERSION, "expected_layout_version": VERSION}
PERMISSIONS = obj({k: BOOL for k in ("read", "create", "edit", "delete", "restore", "import_asset")})

INPUTS = {
    "list_boards": obj(PAGE),
    "get_board_info": obj(BOARD, BOARD),
    "get_card_types": obj(BOARD | {"type": string(40, 1)}, BOARD),
    "get_card_list": obj(BOARD | PAGE | {"lifecycle": enum("active", "deleted"),
                                         "type": string(40, 1), "title_query": string(120)}, BOARD),
    "read_card": obj(BOARD | {"card_id": ID, "include_deleted": BOOL}, ("board_id", "card_id")),
    "get_board_snapshot": obj(BOARD | PAGE | {"view": enum("summary", "full")}, BOARD),
    "get_changes": obj(BOARD | PAGE | {"since_revision": REVISION}, ("board_id", "since_revision")),
    "push_card": obj(WRITE | {"card": CREATE_CARD, "anchor_card_id": ID}, ("board_id", "command_id", "card")),
    "edit_card": obj(EDIT_TARGET | {"changes": CHANGES}, ("board_id", "command_id", "card_id", "changes")),
    "delete_card": obj(TARGET, TARGET),
    "restore_card": obj(TARGET, TARGET),
    "get_asset_sources": obj(BOARD, BOARD),
    "import_asset": obj(WRITE | {"name": string(255, 1), "mode": enum("imported", "reference"),
                                "source": SOURCE}, tuple(WRITE) + ("name", "mode", "source")),
    "get_asset_info": obj(BOARD | {"asset_id": ID}, ("board_id", "asset_id")),
    "read_asset": obj(BOARD | {"asset_id": ID, "format": enum("text", "bytes"),
                               "resource_version": ID, "cursor": ID},
                      ("board_id", "asset_id", "format")),
    "get_operation_status": obj(BOARD | {"command_id": COMMAND}, ("board_id", "command_id")),
}
USER_INPUTS = {
    "upload_asset": obj(WRITE | {"name": string(255, 1), "data_base64": string(27962028)},
                        tuple(WRITE) + ("name", "data_base64")),
    "set_card_lock": obj(TARGET | {"locked": BOOL}, tuple(TARGET) + ("locked",)),
    "update_board": obj(WRITE | {"name": string(80, 1), "description": string(),
                                  "agent_permissions": PERMISSIONS}, WRITE),
    "grant_root": obj(WRITE | {"path": string(4096, 1), "label": string(120, 1)},
                      tuple(WRITE) + ("path", "label")),
    "revoke_root": obj(WRITE | {"root_id": ID}, tuple(WRITE) + ("root_id",)),
    "list_assets": obj(BOARD | PAGE, BOARD),
}
WRITES = {"push_card", "edit_card", "delete_card", "restore_card", "import_asset",
          "set_card_lock", "update_board", "grant_root", "revoke_root", "upload_asset"}
DESCRIPTIONS = {
    "list_boards": "列出当前身份可访问的看板。首版只有一块看板。",
    "get_board_info": "读取看板标识、权限、类型与服务限制。",
    "get_card_types": "发现六类卡片的 JSON Schema、默认值及交叉字段约束。",
    "get_card_list": "按位置读取固定时点的卡片摘要；可筛选回收站。",
    "read_card": "读取完整卡片。资源正文使用 read_asset 单独读取。",
    "get_board_snapshot": "固定 revision 的快照；收齐分页后以此 revision 继续读取变化。",
    "get_changes": "读取固定截至版本的完整变更事件。完整消费后继续下一轮。",
    "push_card": "创建卡片；text.props.font_size 支持12～48，省略 layout 自动放置，可用 anchor_card_id 指定邻近卡片。world 布局允许重叠。相同命令重试不重复创建。",
    "edit_card": "局部编辑；可用 expected_content_version / expected_layout_version 分别检查内容与布局，或 expected_version 整卡检查。world 布局需 space。",
    "delete_card": "基于 expected_version 软删除，可恢复；不会删除资源或原文件。",
    "restore_card": "恢复原卡片；原位置被占用时自动安排并告知。",
    "get_asset_sources": "列出用户授权目录的逻辑标识；不能自行扩大授权范围。",
    "import_asset": "从 inline 或已授权目录导入副本或受控引用；不抓取远程 URL。",
    "get_asset_info": "重新核对资源状态与内容版本，不改变卡片版本。",
    "read_asset": "分片读取 UTF-8 文字或 Base64 字节；引用改变时中止，禁止混拼。",
    "get_operation_status": "查询当前身份命令的持久回执；unknown 不代表在途请求必然失败。",
}


def tool_catalog():
    envelope = {"$schema": DIALECT, "type": "object", "required": ["ok", "meta"],
                "properties": {"ok": BOOL, "data": {"type": "object"},
                               "error": {"type": "object"}, "meta": {"type": "object"}},
                "additionalProperties": False,
                "oneOf": [{"required": ["data"], "properties": {"ok": {"const": True}},
                           "not": {"required": ["error"]}},
                          {"required": ["error"], "properties": {"ok": {"const": False}},
                           "not": {"required": ["data"]}}]}
    return [{"name": name, "description": DESCRIPTIONS[name],
             "inputSchema": {"$schema": DIALECT} | schema, "outputSchema": envelope,
             "annotations": {"readOnlyHint": name not in WRITES,
                             "destructiveHint": name == "delete_card",
                             "idempotentHint": True, "openWorldHint": False}}
            for name, schema in INPUTS.items()]
