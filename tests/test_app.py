"""Local interface and invented records only. External test requests are blocked."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import desktop
import file_app as app
import native_edge
from http_ui import LocalServer


class PlatformTests(unittest.TestCase):
    def test_mac_browser_locations_and_default(self):
        with patch.object(native_edge.sys, "platform", "darwin"), patch.object(Path, "is_file", return_value=True):
            self.assertEqual(native_edge.NativeEdge("invented-profile").channel, "chrome")
            self.assertEqual(native_edge.browser_executable("chrome"), Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
            self.assertEqual(native_edge.browser_executable("msedge"), Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))

    def test_mac_native_dialog_cancel_and_finder_use_argument_arrays(self):
        with patch.object(desktop.sys, "platform", "darwin"), patch.object(desktop.subprocess, "run") as run:
            run.return_value.stdout = ""
            self.assertEqual(desktop.browse_folder(), "")
            self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/osascript", "-e"])
            folder = Path("/Synthetic folder/O'Brien")
            desktop.open_path(folder)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", str(folder.resolve())])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_user_applications_browser_install_is_supported(self):
        def installed(path):
            return str(path).startswith(str(Path.home() / "Applications"))
        with patch.object(native_edge.sys, "platform", "darwin"), patch.object(Path, "is_file", installed):
            self.assertEqual(native_edge.browser_executable("chrome"), Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class InterfaceTests(unittest.TestCase):
    @property
    def collector(self):
        return self.app

    def interface_url(self):
        return self.server.url

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = app.LocalApp(self.root / "app-data")
        self.server = LocalServer(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.shutdown()
        if self.app.worker:
            self.app.worker.join(30)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def auth(self):
        return {"Authorization": "Bearer " + self.server.token, "Origin": self.server.origin,
                "Content-Type": "application/json"}

    def test_auth_origin_host_and_local_only_boundary(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        for headers in (dict(self.auth(), Origin="https://foreign.invalid"),
                        dict(self.auth(), Host="foreign.invalid"),
                        dict(self.auth(), Authorization="Bearer wrong"),
                        {k:v for k,v in self.auth().items() if k != "Origin"}):
            self.assertEqual(self.request("POST", "/api/action", '{"action":"stop"}', headers)[0], 403)
        status, headers, body = self.request("GET", "/api/state", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_only_interface_assets_are_served(self):
        for path in ("/manifest.json", "/../README.md", "/local_app.py", "/api/state?token=secret"):
            self.assertEqual(self.request("GET", path, headers=self.auth())[0], 404)
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Canvas Download Tools", body)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_invalid_and_stale_commands_cannot_start_a_browser(self):
        for url in ("http://canvas.invalid", "https://canvas.invalid/?secret=x", "https://user:password@canvas.invalid"):
            with self.assertRaises(app.UIError):
                self.app.command("launch", {"url": url, "output": str(self.root)})
        self.app.update(phase="checking", run_id="current")
        for payload in ({"run_id":"old", "reviewed":True}, {"run_id":"current", "reviewed":False}):
            with self.assertRaises(app.UIError):
                self.app.command("checked", payload)
        self.assertIsNone(self.app.worker)

    def test_stop_wakes_manual_wait_without_downloads(self):
        errors = []
        def waiting():
            try:
                self.app.wait_for_action("navigate")
            except app.DownloadError as error:
                errors.append(str(error))
        worker = threading.Thread(target=waiting)
        worker.start()
        deadline = time.monotonic() + 2
        while self.app.snapshot()["phase"] != "navigate" and time.monotonic() < deadline:
            time.sleep(.01)
        self.app.command("stop", {})
        worker.join(2)
        self.assertEqual(errors, ["stopped_by_user"])
        self.assertFalse(worker.is_alive())


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional invented browser workflow")
class BrowserInterfaceTests(InterfaceTests):
    def test_buttons_drive_the_real_collector_and_keep_private_data_off_the_interface(self):
        from playwright.sync_api import sync_playwright
        from test_learned_download import OUTER
        origin = "https://canvas-fixture.invalid"
        output = self.root / "downloads"
        output.mkdir()
        downloads = []
        class InventedBrowser:
            def __init__(self, *args, **kwargs):
                self.browser = None
            def start(self, url):
                self.url = url
            def connect(self, driver, artifacts):
                self.browser = driver.chromium.launch(headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))
                context = self.browser.new_context(accept_downloads=True)
                def route(request):
                    from urllib.parse import urlsplit, parse_qs
                    parsed = urlsplit(request.request.url)
                    if not request.request.url.startswith(origin + "/"):
                        request.abort()
                    elif parsed.path.endswith("/speed_grader"):
                        body = OUTER.replace('<input id="name" role="combobox" value="Invented Same Name" readonly>', '<select id="students_selectmenu"><option>Invented Same Name</option></select><input id="name" role="combobox" value="Invented Same Name" readonly>')
                        body += "<script>setInterval(()=>document.querySelector('#students_selectmenu option').textContent=document.querySelector('#name').value,10)</script>"
                        request.fulfill(content_type="text/html", body=body)
                    elif parsed.path == "/submission":
                        student = parse_qs(parsed.query)["id"][0]
                        body = f'<div id="upload" onclick="location.href=\'/file?id={student}\'"><span>Invented file.pdf</span></div>'
                        if student == "101":
                            body += "<script>const timer=setInterval(()=>{if(Object.keys(window).some(k=>k.startsWith('__local_download_training_'))){clearInterval(timer);document.querySelector('#upload span').click()}},50)</script>"
                        request.fulfill(content_type="text/html", body=body)
                    elif parsed.path == "/file":
                        student = parse_qs(parsed.query)["id"][0]
                        downloads.append(student)
                        request.fulfill(headers={"Content-Type":"application/octet-stream", "Content-Disposition":'attachment; filename="Invented file.pdf"'}, body=("Invented file " + student).encode())
                    else:
                        request.abort()
                context.route("**/*", route)
                context.new_page().goto(origin + "/courses/1/gradebook/speed_grader?assignment_id=2&student_id=101")
                return context
            def close(self):
                if self.browser:
                    self.browser.close()
                    self.browser = None
        with patch.object(app, "NativeEdge", InventedBrowser), contextlib.redirect_stdout(io.StringIO()):
            with sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))
                context = browser.new_context(viewport={"width":1280,"height":1000})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.server.origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.interface_url())
                page.locator("#canvas-url").fill(origin)
                page.locator("#output").fill(str(output))
                page.locator("#launch").click()
                page.locator("#ready").wait_for(state="visible")
                page.locator("#ready").click()
                page.locator("#checking").wait_for(state="visible", timeout=30000)
                self.assertEqual(downloads, ["101"])
                self.assertTrue(page.locator("#checked").is_disabled())
                page.locator("#reviewed").check()
                page.locator("#checked").click()
                page.get_by_role("heading", name="Collection finished", exact=True).wait_for(timeout=30000)
                self.assertEqual(downloads, ["101", "101", "102", "103"])
                self.assertEqual(self.collector.snapshot()["saved"], 3)
                self.assertNotIn("Invented Same Name", json.dumps(self.collector.snapshot()))
                self.assertEqual(len(list(Path(self.collector.snapshot()["output"]).iterdir())), 3)
                self.assertFalse(errors)
                browser.close()
