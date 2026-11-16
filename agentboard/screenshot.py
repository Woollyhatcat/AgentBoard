"""Read-only, frozen board screenshots rendered with the real web card renderer."""

import base64
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import BoundedSemaphore
from urllib.parse import unquote, urlsplit, parse_qs
from .interactive import FRAME_CSP, frame_document

from .schema import BusinessError, INPUTS, validate
from .service import dump, uid

STATIC = Path(__file__).parent / "web"
ORIGIN = "http://agentboard.invalid"
_render_slot = BoundedSemaphore(1)


def _checked(response):
    if not response["ok"]:
        error = BusinessError(response["error"]["code"], response["error"]["message"])
        error.error = response["error"]
        raise error
    return response["data"]


def _prepare(service, args, actor):
    # Check the same read permission as all other board tools, before disclosure.
    if not isinstance(args, dict):
        raise BusinessError("INVALID_ARGUMENT", "参数必须是对象。")
    info = _checked(service.call("get_board_info", {"board_id": args.get("board_id")}, actor))
    validate(args, INPUTS["get_board_screenshot"], code="INVALID_ARGUMENT")
    options = {"view":"all", "width":1600, "height":1000, "theme":"light", **args}
    view = options["view"]
    if ((view == "card") != ("card_id" in args) or
            (view == "region") != ("region" in args)):
        raise BusinessError("INVALID_ARGUMENT", "card 视图仅需 card_id；region 视图仅需 region；all 无需两者。")
    if view == "region" and (args["region"]["w"] <= 0 or args["region"]["h"] <= 0):
        raise BusinessError("INVALID_ARGUMENT", "截图区域宽高必须大于0。")
    cards, cursor, revision = [], None, None
    while True:
        page = _checked(service.call("get_board_snapshot", {"board_id":args["board_id"],
            "view":"full", "limit":100, **({"cursor":cursor} if cursor else {})}, actor))
        cards.extend(page["items"]); revision = page["revision"]; cursor = page["next_cursor"]
        if not cursor:
            break
    all_cards = {card["id"]:card for card in cards}
    if view == "card":
        cards = [card for card in cards if card["id"] == args["card_id"]]
        if not cards:
            raise BusinessError("NOT_FOUND", "目标卡片不存在或已删除。")
    asset_ids = set()
    source_ids = {binding["card_id"] for card in cards if card["type"] == "interactive"
                  for binding in card["props"]["bindings"].values()}
    rendered_ids = {card["id"] for card in cards}
    for card in cards:
        if card["type"] in ("image", "file"):
            asset_ids.add(card["state"]["asset_id"])
        if card["type"] == "text":
            asset_ids.update(re.findall(r"/api/assets/(asset_[a-f\d]+)", card["state"]["text"]))
    assets, warnings = [], []
    for asset_id in sorted(asset_ids):
        result = service.call("get_asset_info", {"board_id":args["board_id"],"asset_id":asset_id}, actor)
        if result["ok"]:
            assets.append(result["data"]["asset"])
        elif result["error"]["code"] == "FORBIDDEN":
            _checked(result)
        else:
            warnings.append("资源不可用：" + asset_id)
    return {"board":{k:info[k] for k in ("id","name","description")}, "revision":revision,
            "cards":cards,"source_cards":[all_cards[id] for id in sorted(source_ids-rendered_ids) if id in all_cards],
            "assets":assets,"options":options,"warnings":warnings}


def _render(service, payload, actor):
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError:
        raise BusinessError("SCREENSHOT_DEPENDENCY_MISSING", "截图需要 Playwright。",
            recovery="在 MCP 使用的 Python 中执行 python -m pip install -r requirements-screenshot.txt。")
    resources, warnings = {}, list(payload["warnings"])
    with sync_playwright() as playwright:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True, timeout=10000)
                break
            except BrowserError:
                continue
        if browser is None:
            raise BusinessError("SCREENSHOT_BROWSER_UNAVAILABLE", "无法启动独立截图浏览器。",
                recovery="安装本机 Edge/Chrome，或执行 python -m playwright install chromium。")
        try:
            page = browser.new_page(viewport={"width":payload["options"]["width"],
                "height":payload["options"]["height"]}, device_scale_factor=1, service_workers="block")
            page.set_default_timeout(20000)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def route_request(route):
                request = route.request
                url = urlsplit(request.url)
                if url.scheme != "http" or url.netloc != "agentboard.invalid" or request.method != "GET":
                    route.abort(); return
                path = unquote(url.path)
                if path == "/__snapshot__":
                    route.fulfill(status=200, content_type="application/json", body=dump(payload)); return
                if path == "/__capture__":
                    html = (STATIC / "index.html").read_text(encoding="utf-8").replace(
                        '<script src="/app.js" defer>', '<script src="/capture.js" defer></script>\n  <script src="/app.js" defer>')
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html,
                        headers={"Content-Security-Policy":"default-src 'self'; script-src 'self'; "
                            "style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self'; font-src 'self'; "
                            "object-src 'none'; base-uri 'none'"}); return
                if path.startswith("/api/interactive/"):
                    card_id = path.removeprefix("/api/interactive/")
                    card = next((card for card in payload["cards"] if card["id"] == card_id and card["type"] == "interactive"),None)
                    key = parse_qs(url.query).get("key",[""])[0]
                    if card and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",key):
                        route.fulfill(status=200,content_type="text/html; charset=utf-8",body=frame_document(card,key),
                                      headers={"Content-Security-Policy":FRAME_CSP}); return
                    route.fulfill(status=404,body="Not found"); return
                if re.fullmatch(r"/api/assets/asset_[a-f\d]+", path):
                    asset_id = path.rsplit("/",1)[1]
                    try:
                        if asset_id not in resources:
                            resources[asset_id] = service.asset_bytes_for_actor(asset_id,actor,payload["board"]["id"])
                        asset, content = resources[asset_id]
                        if asset["preview_kind"] == "image":
                            route.fulfill(status=200,content_type=asset["mime_type"],body=content); return
                    except BusinessError:
                        warnings.append("图片资源不可用：" + asset_id)
                    route.fulfill(status=404,body="Unavailable"); return
                local = (STATIC / path.lstrip("/")).resolve()
                allowed = (path in ("/app.js","/capture.js","/canvas.js","/render.js","/interactive.js","/style.css") or
                           path.startswith("/vendor/"))
                mime = {".js":"text/javascript", ".css":"text/css", ".woff2":"font/woff2"}.get(local.suffix)
                if allowed and local.is_relative_to(STATIC.resolve()) and local.is_file() and mime:
                    route.fulfill(status=200,content_type=mime,body=local.read_bytes()); return
                route.fulfill(status=404,body="Not found")

            page.route("**/*", route_request)
            page.goto(ORIGIN + "/__capture__", wait_until="load", timeout=20000)
            page.locator("body[data-capture-ready],body[data-capture-error]").wait_for(state="attached")
            error = page.get_attribute("body", "data-capture-error")
            if error or page_errors:
                raise BusinessError("SCREENSHOT_RENDER_FAILED", "截图页面渲染失败。",
                                    details={"errors":([error] if error else [])+page_errors})
            metadata = page.evaluate("window.boardCapture")
            picture = page.screenshot(type="png", animations="disabled", timeout=15000)
        except BrowserError as error:
            raise BusinessError("SCREENSHOT_RENDER_FAILED", "截图浏览器运行失败或超时，请重试。",
                                details={"reason":str(error)[:1200]})
        finally:
            browser.close()
    metadata["warnings"] += warnings
    metadata["assets"] = [{"id":asset["id"],"resource_version":asset["resource_version"]}
                          for asset, _ in resources.values()]
    return picture, metadata


def capture_board(service, args, actor):
    meta = {"request_id":uid("req")}
    acquired = False
    try:
        payload = _prepare(service,args,actor)
        meta["board_revision"] = payload["revision"]
        acquired = _render_slot.acquire(blocking=False)
        if not acquired:
            raise BusinessError("SCREENSHOT_BUSY", "另一张截图正在渲染，请稍后重试。")
        picture, data = _render(service,payload,actor)
        # Revocation during rendering must also prevent disclosure of the image.
        latest = _checked(service.call("get_board_info", {"board_id":args["board_id"]},actor))
        if len(picture) > 16*1024*1024:
            raise BusinessError("SCREENSHOT_TOO_LARGE", "截图超过16MiB，请降低像素尺寸或改用局部截图。")
        data.update(board_id=args["board_id"],revision=payload["revision"],current_revision=latest["revision"],
            stale=latest["revision"] != payload["revision"],view=payload["options"]["view"],
            theme=payload["options"]["theme"],mime_type="image/png",
            captured_at=datetime.now(timezone.utc).isoformat(),
            coordinate_mapping="pixel_x=camera.x+world_x*camera.zoom; pixel_y=camera.y+world_y*camera.zoom")
        directory = service.data_dir / "screenshots"
        directory.mkdir(exist_ok=True)
        path = directory / (uid("capture")+".png")
        data["path"] = str(path)
        path.write_bytes(picture)
        path.with_suffix(".json").write_text(dump(data),encoding="utf-8")
        return {"ok":True,"data":{**data,"image_base64":base64.b64encode(picture).decode("ascii")},"meta":meta}
    except BusinessError as error:
        return {"ok":False,"error":error.error,"meta":meta}
    except (OSError, sqlite3.Error):
        return {"ok":False,"error":BusinessError("SERVICE_UNAVAILABLE", "截图文件或本地资源不可用。").error,"meta":meta}
    finally:
        if acquired:
            _render_slot.release()
