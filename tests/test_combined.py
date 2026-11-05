"""Combined routing, shared setup and both collectors with invented data."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import local_app as combined
from http_ui import LocalServer


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = combined.CombinedApp(self.root / "app")
        self.server = LocalServer(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.shutdown()
        self.app.join()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, payload=None, authorize=True, origin=None):
        headers = {}
        if authorize:
            headers = {"Authorization": "Bearer " + self.server.token,
                       "Origin": origin or self.server.origin, "Content-Type": "application/json"}
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_both_api_namespaces_require_authentication_and_matching_origin(self):
        for mode in ("logs", "files"):
            self.assertEqual(self.request("GET", f"/{mode}/api/state", authorize=False)[0], 403)
            self.assertEqual(self.request("GET", f"/{mode}/api/state")[0], 200)
            self.assertEqual(self.request("POST", f"/{mode}/api/action", {"action":"stop"}, origin="https://foreign.invalid")[0], 403)
        for path in ("/log_app.py", "/logs/../README.md", "/files/private/manifest.json", "/logs/api/state?secret=x"):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_shared_setup_carries_between_modes_and_restarts(self):
        values = {"canvas_url":"https://canvas-fixture.invalid/", "output_base":str(self.root), "browser":"chrome"}
        self.app.set_preferences(values)
        for mode in ("logs", "files"):
            state = self.app.snapshot_for(mode)
            self.assertEqual(state["canvas_url"], values["canvas_url"])
            self.assertEqual(state["output_base"], values["output_base"])
        restarted = combined.CombinedApp(self.root / "app")
        self.assertEqual(restarted.preferences, values)

    def test_active_collection_blocks_other_mode_launch_and_preference_changes(self):
        self.app.logs.update(phase="checking")
        for command in (lambda: self.app.select_mode("files"),
                        lambda: self.app.set_preferences({"browser":"chrome"}),
                        lambda: self.app.command_for("files", "launch", {})):
            with self.assertRaises(combined.UIError):
                command()
        self.assertEqual(self.app.files.snapshot()["phase"], "idle")
        self.app.select_mode("logs")

    def test_switch_closes_an_idle_retained_browser_and_preserves_result_state(self):
        self.app.logs.update(phase="complete", browser_open=True, saved=3, run_id="invented-run")
        worker = MagicMock()
        worker.is_alive.side_effect = [True, False]
        self.app.logs.worker = worker
        old = self.app.logs
        self.app.select_mode("files")
        self.assertTrue(old.quit_event.is_set())
        self.assertIsNot(self.app.logs, old)
        self.assertFalse(self.app.logs.quit_event.is_set())
        self.assertEqual(self.app.logs.snapshot()["saved"], 3)
        self.assertFalse(self.app.logs.snapshot()["browser_open"])

    def test_quit_stops_both_modes_and_rejects_future_launches(self):
        self.app.shutdown()
        self.assertTrue(self.app.logs.stop_event.is_set())
        self.assertTrue(self.app.files.stop_event.is_set())
        with self.assertRaises(combined.UIError):
            self.app.command_for("files", "launch", {})


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional combined browser tests")
class CombinedBrowserTests(CombinedTests):
    def test_main_about_modal_supports_keyboard_close_and_shows_privacy_and_contact(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))
            context = browser.new_context(viewport={"width":1280, "height":1000})
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.server.origin + "/") else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.server.url)
            button = page.get_by_role("button", name="About", exact=True)
            dialog = page.get_by_role("dialog", name="About Canvas Download Tools", exact=True)
            button.click()
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role("heading", name="Your files and information stay local")).to_be_visible()
            expect(dialog).to_contain_text("OpenAI Codex")
            expect(dialog).to_contain_text("0BSD")
            expect(dialog.get_by_role("link", name="djulian@ufl.edu")).to_have_attribute("href", "mailto:djulian@ufl.edu")
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            expect(button).to_be_focused()
            button.click()
            dialog.get_by_role("button", name="Close", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(button).to_be_focused()
            page.set_viewport_size({"width":390,"height":844})
            button.click()
            expect(dialog).to_be_visible()
            self.assertFalse(dialog.evaluate("element => element.scrollWidth > element.clientWidth"))
            self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > innerWidth"))
            self.assertFalse(errors)
            browser.close()

    def test_mode_navigation_shares_setup_without_exposing_the_picker_scan_ui(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))
            context = browser.new_context()
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.server.origin + "/") else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.server.url)
            page.get_by_role("link", name="Submitted files", exact=False).first.click()
            page.wait_for_url("**/files/")
            self.assertEqual(page.locator("#canvas-url").get_attribute("placeholder"), "https://elearning.ufl.edu/")
            page.locator("#canvas-url").fill("https://canvas-fixture.invalid/")
            page.locator("#output").fill(str(self.root))
            page.locator(".tool-switch a[data-tool='logs']").click()
            page.wait_for_url("**/logs/")
            expect(page.locator("#canvas-url")).to_have_value("https://canvas-fixture.invalid/")
            expect(page.locator("#output")).to_have_value(str(self.root))
            self.assertTrue(page.locator("#export-modes").is_visible())
            self.assertEqual(page.locator("#extension").count(), 0)
            page.locator(".tool-switch a[data-tool='files']").click()
            page.wait_for_url("**/files/")
            expect(page.locator("#canvas-url")).to_have_value("https://canvas-fixture.invalid/")
            self.assertEqual(page.locator("#export-modes").count(), 0)
            self.assertIn("click", page.locator("#pick-student").text_content().lower())
            self.assertNotIn("outlined in orange", page.content())
            self.assertFalse(errors)
            browser.close()

    def test_log_collection_through_combined_routes_then_switch_to_files(self):
        from playwright.sync_api import BrowserType, sync_playwright, expect
        from test_log_app import FIXTURE
        original = BrowserType.launch_persistent_context
        output = self.root / "downloads"
        output.mkdir()
        channel = os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome")
        def launch(browser_type, user_data_dir, **kwargs):
            context = original(browser_type, user_data_dir, channel=channel, headless=True, accept_downloads=False)
            context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=FIXTURE)
                          if route.request.url.split('#')[0] == "https://canvas-fixture.invalid/" else route.abort())
            return context
        with patch.object(BrowserType, "launch_persistent_context", launch), contextlib.redirect_stdout(io.StringIO()):
            with sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True, channel=channel)
                context = browser.new_context()
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.server.origin + "/") else route.abort())
                page = context.new_page()
                page.goto(self.server.origin + "/logs/#" + self.server.token)
                page.locator("#canvas-url").fill("https://canvas-fixture.invalid/")
                page.locator("#output").fill(str(output))
                page.locator("#mode-named").check()
                page.locator("#launch").click()
                page.locator("#moderate").wait_for(state="visible")
                page.locator(".tool-switch a[data-tool='files']").click(force=True)
                expect(page.locator("#shared-notice")).to_contain_text("Finish or stop")
                self.assertIn("/logs/", page.url)
                page.locator("#moderate").click()
                page.locator("#name-rows input").nth(2).wait_for()
                page.locator("#names-reviewed").check()
                page.locator("#start").click()
                page.locator("#open-first").wait_for(state="visible", timeout=30000)
                self.assertEqual(self.app.logs.snapshot()["saved"], 1)
                page.locator("#first-reviewed").check()
                page.locator("#checked").click()
                page.get_by_role("heading", name="Collection finished", exact=True).wait_for(timeout=45000)
                state = self.app.logs.snapshot()
                self.assertEqual(state["saved"], 3)
                self.assertTrue((Path(state["output"]) / "named-logs.zip").is_file())
                self.assertTrue(state["summary_ready"])
                page.locator(".tool-switch a[data-tool='files']").click()
                page.wait_for_url("**/files/")
                expect(page.locator("#canvas-url")).to_have_value("https://canvas-fixture.invalid/")
                self.assertTrue(page.locator("#extension").is_visible())
                browser.close()


import test_app as file_tests


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional combined file workflow")
class CombinedFileWorkflowTests(CombinedTests):
    @property
    def collector(self):
        return self.app.files

    def interface_url(self):
        return self.server.origin + "/files/#" + self.server.token

    test_real_file_collector_through_combined_interface = file_tests.BrowserInterfaceTests.test_buttons_drive_the_real_collector_and_keep_private_data_off_the_interface
