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
