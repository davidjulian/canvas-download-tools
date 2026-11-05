"""Native launch and delayed local attachment, using invented pages only."""
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

from native_edge import NativeEdge, NativeEdgeError, parse_endpoint
from speedgrader_download import FileBatch


class EndpointTests(unittest.TestCase):
    def test_endpoint_is_loopback_only(self):
        self.assertEqual(parse_endpoint("12345\n/devtools/browser/test-123\n"),
                         "ws://127.0.0.1:12345/devtools/browser/test-123")
        for text in ("0\n/devtools/browser/a", "65536\n/devtools/browser/a",
                     "123\nhttps://example.invalid/", "123\n/devtools/browser/a?token=b",
                     "123\n/devtools/browser/a\nextra"):
            with self.assertRaises(NativeEdgeError):
                parse_endpoint(text)


@unittest.skipUnless(sys.platform in ("win32", "darwin") and os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1",
                     "optional Windows or Mac native browser test")
class NativeBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.native = NativeEdge(self.root / "profile", headless=True)
        self.playwright = None

    def tearDown(self):
        self.native.close()
        if self.playwright:
            self.playwright.stop()
        # Native Edge can finish profile database writes after CDP disconnects.
        deadline = time.monotonic() + 15
        while True:
            try:
                self.temp.cleanup()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)

    def connect(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        return self.native.connect(self.playwright, self.root / "temporary-downloads")

    def test_connect_preserves_page_opened_before_activation(self):
        self.native.start("data:text/html,<title>Invented page before activation</title><p>Ready</p>")
        context = self.connect()
        page = context.pages[0]
        self.assertEqual(page.title(), "Invented page before activation")
        self.assertTrue(page.url.startswith("data:text/html,"))

    def test_native_attachment_download_and_local_mapping(self):
        self.native.start("about:blank")
        context = self.connect()
        page = context.pages[0]
        origin = "http://native-edge-fixture.invalid"
        url = origin + "/courses/1/gradebook/speed_grader?assignment_id=2"
        def route(request):
            path = request.request.url
            if path == url:
                request.fulfill(content_type="text/html", body=(
                    '<select id="students_selectmenu"><option value="101">Invented student</option></select>'
                    '<iframe src="/submission" style="width:900px;height:600px"></iframe>'))
            elif path == origin + "/submission":
                request.fulfill(content_type="text/html", body='<a href="/file">invented.model</a>')
            elif path == origin + "/file":
                request.fulfill(headers={"Content-Type": "application/octet-stream",
                                         "Content-Disposition": 'attachment; filename="invented.model"'},
                                body=b"Invented native download")
            else:
                request.abort()
        context.route("**/*", route)
        page.goto(url)
        page.frame_locator("iframe").get_by_role("link").wait_for()
        responses = iter(("ready", "download", "checked"))
        with contextlib.redirect_stdout(io.StringIO()):
            batch = FileBatch(page, self.root / "out", quiet=0,
                              prompt=lambda _: next(responses))
            batch.run()
        self.assertEqual(batch.state["status"], "pilot_complete")
        self.assertTrue(batch.state["first_file_locally_checked"])
        self.assertEqual((batch.private / batch.records[0]["file"]).read_bytes(), b"Invented native download")
        self.assertIn("Invented student", (batch.private / "name_key.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual(list((self.root / "temporary-downloads").iterdir()), [])

    def test_profile_already_open_is_rejected_without_closing_owner(self):
        self.native.start("about:blank")
        second = NativeEdge(self.native.profile, headless=True)
        try:
            with self.assertRaisesRegex(NativeEdgeError, "close_comparison_edge_window_then_retry"):
                second.start("about:blank", timeout=10)
        finally:
            second.close()
        context = self.connect()
        self.assertTrue(context.pages)

    def test_cancel_before_activation_closes_local_endpoint(self):
        from urllib.parse import urlsplit
        self.native.start("about:blank")
        port = urlsplit(self.native.endpoint).port
        self.native.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=.2)
                connection.close()
            except OSError:
                return
            time.sleep(.1)
        self.fail("The dedicated browser endpoint remained open after cancellation")


if __name__ == "__main__":
    unittest.main()
