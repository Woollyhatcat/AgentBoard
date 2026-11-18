"""Browser acceptance of switching, drafts, management and shared directory settings."""

import importlib.util
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from uuid import uuid4

from agentboard.http import create_server
from agentboard.service import BoardService, USER


@unittest.skipUnless(importlib.util.find_spec("playwright"),"Browser acceptance requires Playwright")
class BoardUITests(unittest.TestCase):
    def test_card_manager_filters_and_manages_cards_outside_canvas_search(self):
        from playwright.sync_api import sync_playwright, expect, Error as BrowserError

        with tempfile.TemporaryDirectory() as directory:
            service = BoardService(directory)

            def push_card(title, card_type, props, state):
                result = service.call("push_card", {
                    "board_id": "board_local", "command_id": str(uuid4()),
                    "card": {"type": card_type, "type_version": "1.0", "title": title,
                             "props": props, "state": state},
                }, USER)
                self.assertTrue(result["ok"], result)
                return result["data"]["card"]["id"]

            card_id = push_card("项目说明", "text", {"format": "markdown"}, {"text": "正文"})
            push_card("验收进度", "checkbox", {"label": "已完成"}, {"checked": False})
            server = create_server(service, port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    for channel in ("msedge", "chrome", None):
                        try:
                            browser = playwright.chromium.launch(channel=channel, headless=True)
                            break
                        except BrowserError:
                            continue
                    else:
                        self.skipTest("Browser acceptance requires Edge/Chrome or Playwright Chromium")

                    try:
                        page = browser.new_page(viewport={"width": 960, "height": 800})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
                        page.wait_for_function("() => app.connected && app.loaded")
                        page.locator("#search").fill("验收")
                        expect(page.locator("#canvas article")).to_have_count(1)
                        page.locator("#manage-cards-btn").click()
                        manager = page.get_by_role("dialog", name="卡片管理")
                        rows = manager.get_by_role("listitem")
                        search = manager.get_by_role("searchbox")
                        expect(rows).to_have_count(2)
                        push_card("更新说明", "text", {}, {"text": "新内容"})
                        expect(rows).to_have_count(3)
                        expect(search).to_be_focused()
                        manager.get_by_label("筛选卡片类型").select_option("text")
                        expect(rows).to_have_count(2)
                        search.fill(card_id)
                        expect(rows).to_have_count(1)

                        more = manager.get_by_role("button", name="更多操作「项目说明」")
                        more.click()
                        page.get_by_role("button", name="锁定卡片", exact=True).click()
                        expect(rows).to_have_count(1)
                        expect(search).to_have_value(card_id)
                        expect(manager.get_by_role("button", name="编辑「项目说明」")).to_be_disabled()
                        more.click()
                        page.get_by_role("button", name="显式解锁卡片", exact=True).click()
                        expect(manager.get_by_role("button", name="编辑「项目说明」")).to_be_enabled()
                        manager.get_by_role("button", name="编辑「项目说明」").click()
                        expect(page.locator('#inspector [name="title"]')).to_have_value("项目说明")
                        expect(page.locator("#search")).to_have_value("验收")

                        page.locator("#manage-cards-btn").click()
                        manager.get_by_role("button", name="定位「项目说明」").click()
                        expect(page.locator("#search")).to_have_value("")
                        expect(page.locator("#canvas").get_by_text("项目说明", exact=True)).to_be_visible()
                        page.locator("#manage-cards-btn").click()
                        more.click()
                        page.get_by_role("button", name="删除（可从回收站恢复）").click()
                        page.get_by_role("button", name="移至回收站").click()
                        expect(rows).to_have_count(2)
                        expect(manager.get_by_text("项目说明", exact=True)).to_have_count(0)
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_archive_buttons_file_picker_cancel_and_lost_import_reply(self):
        from playwright.sync_api import sync_playwright, expect, Error as BrowserError
        with tempfile.TemporaryDirectory() as directory:
            service = BoardService(directory)
            result = service.call("push_card",{"board_id":"board_local","command_id":"archive-example","card":{
                "type":"text","type_version":"1.0","title":"便携板书","props":{"font_size":24},"state":{"text":"$$x^2+y^2=1$$"}}},USER)
            self.assertTrue(result["ok"],result)
            server = create_server(service,port=0)
            thread = Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = None
                    for channel in ("msedge","chrome",None):
                        try: browser = playwright.chromium.launch(channel=channel,headless=True); break
                        except BrowserError: continue
                    if browser is None: self.skipTest("Browser acceptance requires Edge/Chrome or Playwright Chromium")
                    try:
                        page = browser.new_page(viewport={"width":1500,"height":1000})
                        errors = []; page.on("pageerror",lambda error:errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
                        page.wait_for_function("() => app.connected && app.loaded")
                        page.locator("#manage-boards-btn").click()
                        # OS dialog UI is outside headless automation. Substitute only
                        # the user-selected handles; use real HTTP and ZIP processing.
                        page.evaluate("""() => {
                          window.showSaveFilePicker = async options => {
                            if (!navigator.userActivation.isActive) throw Error('Missing user gesture');
                            window.suggestedArchiveName = options.suggestedName;
                            return {createWritable:async () => ({write:async blob => {window.savedArchive=blob},close:async () => {}})};
                          };
                        }""")
                        page.get_by_role("button",name="导出",exact=True).click()
                        page.wait_for_function("() => window.savedArchive instanceof Blob")
                        self.assertTrue(page.evaluate("() => window.suggestedArchiveName.endsWith('.agentboard')"))
                        expect(page.get_by_role("button",name="导入黑板",exact=True)).to_be_enabled()
                        page.evaluate("""() => {window.showOpenFilePicker=async () => {throw new DOMException('cancel','AbortError')}}""")
                        page.get_by_role("button",name="导入黑板",exact=True).click()
                        expect(page.get_by_role("button",name="导入黑板",exact=True)).to_be_enabled()
                        self.assertEqual(len(service.call("list_boards",{},USER)["data"]["items"]),1)
                        page.evaluate("""() => {window.showOpenFilePicker=async () => [{getFile:async () => new File(['bad archive'],'bad.agentboard')}]}""")
                        page.get_by_role("button",name="导入黑板",exact=True).click()
                        expect(page.locator("#modal .field-error")).not_to_have_text("")
                        expect(page.get_by_role("button",name="导入黑板",exact=True)).to_be_enabled()
                        self.assertEqual(len(service.call("list_boards",{},USER)["data"]["items"]),1)
                        page.evaluate("""() => {window.showOpenFilePicker=async () => [{getFile:async () => new File([window.savedArchive],'portable.agentboard')}]}""")
                        uploads = []
                        def lose_reply(route):
                            uploads.append(route.request.headers.get("x-agentboard-command"))
                            response = route.fetch()
                            self.assertEqual(response.status,200)
                            route.abort("failed")
                        page.route("**/api/boards/import",lose_reply)
                        page.get_by_role("button",name="导入黑板",exact=True).click()
                        page.wait_for_function("() => app.boardId !== 'board_local' && app.loaded && app.pending.size === 0",timeout=20000)
                        self.assertEqual(len(uploads),1)
                        self.assertEqual(len(service.call("list_boards",{},USER)["data"]["items"]),2)
                        expect(page.locator(".card-head h2")).to_have_text("便携板书")
                        page.locator("#manage-boards-btn").click()
                        expect(page.locator(".board-management-row")).to_have_count(2)
                        artifacts = Path(__file__).resolve().parent.parent/"test-results"
                        artifacts.mkdir(exist_ok=True)
                        page.screenshot(path=str(artifacts/"board-archive-management.png"))
                        self.assertEqual(errors,[])
                    finally: browser.close()
            finally: server.shutdown(); server.server_close(); thread.join(3)

    def test_management_switching_and_authorized_directories(self):
        from playwright.sync_api import sync_playwright, expect, Error as BrowserError
        with tempfile.TemporaryDirectory() as directory:
            service = BoardService(directory)
            def call(tool,**args):
                result = service.call(tool,{"board_id":"board_local","command_id":str(uuid4()),**args},USER)
                self.assertTrue(result["ok"],result)
                return result["data"]
            server = create_server(service,port=0)
            thread = Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = None
                    for channel in ("msedge","chrome",None):
                        try:
                            browser = playwright.chromium.launch(channel=channel,headless=True)
                            break
                        except BrowserError: continue
                    if browser is None: self.skipTest("Browser acceptance requires Edge/Chrome or Playwright Chromium")
                    try:
                        page = browser.new_page(viewport={"width":1500,"height":1000})
                        errors = []; page.on("pageerror",lambda error:errors.append(str(error)))
                        page.on("dialog",lambda dialog:dialog.accept())
                        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
                        page.wait_for_function("() => app.connected && app.loaded")
                        page.locator("#new-board-btn").click()
                        page.get_by_label("黑板名称",exact=True).fill("数学讨论")
                        page.locator("#modal").get_by_role("button",name="新建黑板",exact=True).click()
                        page.wait_for_function("() => app.info?.name === '数学讨论' && app.connected && app.loaded")
                        board = page.locator("#board-select").input_value()
                        card = call("push_card",board_id=board,card={"type":"text","type_version":"1.0","title":"公式卡片",
                            "props":{"font_size":22},"state":{"text":"## 数学讨论\n\n$$s=\\sigma+j\\omega$$"}})["card"]
                        call("push_card",board_id=board,card={"type":"interactive","type_version":"1.0","title":"交互图形",
                            "props":{"source":"<p>演示已渲染</p><script>Board.onChange(() => Board.rendered());</script>"},"state":{}})
                        expect(page.locator(".card")).to_have_count(2)
                        expect(page.frame_locator("iframe.interactive-frame").locator("p")).to_have_text("演示已渲染")
                        page.evaluate("() => {space.camera={x:125,y:90,zoom:.8};space.paint();space.store()}")
                        page.locator("#manage-boards-btn").click()
                        row = page.locator(".board-management-row").filter(has=page.locator("strong",has_text="数学讨论"))
                        row.get_by_role("button",name="重命名",exact=True).click()
                        page.get_by_label("黑板名称",exact=True).fill("数学空间")
                        page.locator("#modal").get_by_role("button",name="重命名黑板",exact=True).click()
                        expect(page.locator("#current-board-name")).to_have_text("数学空间")
                        page.locator("#manage-boards-btn").click()
                        row = page.locator(".board-management-row").filter(has=page.locator("strong",has_text="数学空间"))
                        row.get_by_role("button",name="锁定黑板",exact=True).click()
                        expect(row.get_by_role("button",name="解除删除保护")).to_be_enabled()
                        expect(row.get_by_role("button",name="删除",exact=True)).to_be_disabled()
                        artifacts = Path(__file__).resolve().parent.parent/"test-results"
                        artifacts.mkdir(exist_ok=True)
                        page.screenshot(path=str(artifacts/"boards-management.png"))
                        row.get_by_role("button",name="复制副本").click()
                        page.get_by_label("黑板名称",exact=True).fill("数学空间副本")
                        page.locator("#modal").get_by_role("button",name="复制黑板副本",exact=True).click()
                        page.wait_for_function("() => app.info?.name === '数学空间副本' && app.loaded")
                        copy = page.locator("#board-select").input_value()
                        self.assertNotEqual(board,copy)
                        expect(page.frame_locator("iframe.interactive-frame").locator("p")).to_have_text("演示已渲染")
                        page.evaluate("() => {space.camera={x:11,y:22,zoom:.65};space.paint();space.store()}")
                        page.locator("#board-select").select_option(board)
                        page.wait_for_function("() => app.info?.name === '数学空间' && app.loaded")
                        self.assertEqual(page.evaluate("() => space.camera"),{"x":125,"y":90,"zoom":.8})
                        page.locator("#"+card["id"]+" .card-head h2").click()
                        page.locator('#inspector [name="title"]').fill("尚未保存的标题")
                        page.locator("#board-select").select_option(copy)
                        expect(page.locator("#board-select")).to_have_value(board)
                        expect(page.locator('#inspector [name="title"]')).to_have_value("尚未保存的标题")
                        page.locator("#save-editor").click()
                        page.wait_for_function("() => !app.editor?.dirty && app.pending.size === 0")
                        page.locator("#board-select").select_option(copy)
                        page.wait_for_function("() => app.info?.name === '数学空间副本' && app.loaded")
                        self.assertEqual(page.evaluate("() => space.camera"),{"x":11,"y":22,"zoom":.65})
                        page.reload(); page.wait_for_function("() => app.info?.name === '数学空间副本' && app.loaded")
                        expect(page.locator(".card-head h2").filter(has_text="公式卡片")).to_have_count(1)
                        page.locator("#settings-btn").click()
                        expect(page.locator(".root-path")).to_contain_text("workspace")
                        page.locator("#modal").get_by_role("button",name="取消授权",exact=True).click()
                        expect(page.locator(".root-item")).to_have_count(0)
                        page.locator("#modal-close").click()
                        page.locator("#manage-boards-btn").click()
                        row = page.locator(".board-management-row").filter(has=page.locator("strong",has_text="数学空间副本"))
                        row.get_by_role("button",name="解除删除保护").click()
                        expect(row.get_by_role("button",name="锁定黑板",exact=True)).to_be_enabled()
                        row.get_by_role("button",name="删除",exact=True).click()
                        expect(page.locator(".board-management-row")).to_have_count(2)
                        page.locator("#modal-close").click()
                        page.wait_for_function("() => app.boardId === 'board_local' && app.loaded")
                        self.assertEqual(service.call("get_asset_sources",{"board_id":"board_local"},USER)["data"]["roots"],[])
                        # Deleting the final boards leaves a usable create action, not a reconnect loop.
                        for item in service.call("list_boards",{},USER)["data"]["items"]:
                            if item["locked"]:
                                item = call("set_board_lock",board_id=item["id"],expected_version=item["version"],locked=False)["board"]
                            call("delete_board",board_id=item["id"],expected_version=item["version"])
                        page.wait_for_function("() => app.boardId === null && app.connected")
                        expect(page.locator("#add-btn")).to_be_disabled()
                        page.locator("#new-board-btn").click()
                        page.get_by_label("黑板名称",exact=True).fill("重新开始")
                        page.locator("#modal").get_by_role("button",name="新建黑板",exact=True).click()
                        page.wait_for_function("() => app.info?.name === '重新开始' && app.loaded")
                        expect(page.locator("#add-btn")).to_be_enabled()
                        self.assertEqual(errors,[])
                    finally: browser.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(3)


if __name__ == "__main__": unittest.main()
