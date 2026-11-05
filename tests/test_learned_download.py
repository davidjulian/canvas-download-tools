"""End-to-end download/Next loops with invented pages and blocked requests."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import learned_download as app
import speedgrader_download as pilot


ORIGIN = "http://learned-fixture.invalid"
URL = ORIGIN + "/courses/1/gradebook/speed_grader?assignment_id=2&student_id=101"
OUTER = '''<!doctype html><header>
<button aria-label="Previous student" disabled>Previous</button>
<input id="name" role="combobox" value="Invented Same Name" readonly>
<input role="combobox" value="Submitted" readonly>
<button id="next" aria-label="Next student" onclick="advance()">Next</button>
</header><iframe style="width:900px;height:600px" src="/submission?id=101"></iframe>
<script>
window.index=0;window.delay=0;window.wrap=false;window.loop=false;window.noAdvance=false;
function advance(){
 if(window.noAdvance)return;
 index=window.loop && index===2?1:(index+1)%3;
 history.replaceState({},'', '?assignment_id=2&student_id='+(101+index));
 document.querySelector('#name').value=index===2?'Invented Third Name':'Invented Same Name';
 document.querySelector('#next').disabled=index===2 && !window.wrap && !window.loop;
 const id=101+index;
 setTimeout(()=>document.querySelector('iframe').src='/submission?id='+id,window.delay);
}
</script>'''


class StartupTests(unittest.TestCase):
    def test_file_type_choices_are_not_tied_to_one_assignment(self):
        for value in ("", "all", "*", "ANY"):
            self.assertIsNone(app.file_type(value))
        self.assertEqual(app.file_type("PDF"), ".pdf")
        self.assertEqual(app.file_type(".docx"), ".docx")
        with self.assertRaisesRegex(pilot.DownloadError, "invalid_file_extension"):
            app.file_type("../private")

    def test_default_launcher_opens_edge_before_waiting_for_navigation(self):
        with tempfile.TemporaryDirectory() as temporary:
            native = MagicMock()
            def cancel(_):
                native.start.assert_called_once_with("https://ufl.instructure.com/")
                native.connect.assert_not_called()
                return "stop"
            with patch.object(app, "local_root", return_value=Path(temporary)), \
                    patch.object(app, "NativeEdge", return_value=native), \
                    patch.object(sys, "argv", ["learned_download.py"]), \
                    patch("builtins.input", side_effect=cancel), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(app.main(), 2)
            native.close.assert_called()

    def test_default_launcher_needs_no_previous_browser_endpoint_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            native, browser, context, page = (MagicMock() for _ in range(4))
            native.browser = browser
            native.connect.return_value = context
            browser.contexts = [context]
            page.locator.return_value.count.return_value = 1
            with patch.object(app, "local_root", return_value=Path(temporary)), \
                    patch.object(app, "NativeEdge", return_value=native), \
                    patch.object(app, "speedgrader_pages", return_value=[page]), \
                    patch.object(app, "LearnedBatch") as batch, \
                    patch.object(app, "choose_download_directory", return_value=Path(temporary) / "downloads"), \
                    patch("playwright.sync_api.sync_playwright"), \
                    patch.object(sys, "argv", ["learned_download.py"]), \
                    patch("builtins.input", side_effect=("start", "")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
            native.start.assert_called_once()
            native.connect.assert_called_once()
            batch.return_value.run.assert_called_once_with(limit=5000)
            self.assertIsNone(batch.call_args.kwargs["extension"])
            self.assertEqual(batch.call_args.kwargs["files_dir"], Path(temporary) / "downloads")
            self.assertFalse((Path(temporary) / "BrowserCheckProfile/DevToolsActivePort").exists())

    def test_startup_error_reports_stage_and_category_without_private_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            native = MagicMock()
            native.start.side_effect = PermissionError("InventedPrivateName https://example.invalid/private-session")
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(app, "local_root", return_value=Path(temporary)), \
                    patch.object(app, "NativeEdge", return_value=native), \
                    patch.object(sys, "argv", ["learned_download.py"]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(app.main(), 2)
            self.assertIn("open_normal_edge (local_access_denied)", errors.getvalue())
            self.assertNotIn("InventedPrivateName", output.getvalue() + errors.getvalue())
            self.assertNotIn("private-session", errors.getvalue())

    def test_connection_error_is_classified_without_printing_endpoint(self):
        reason = app.safe_failure(RuntimeError("connect ECONNREFUSED ws://127.0.0.1:12345/devtools/browser/private"))
        self.assertEqual(reason, "browser_connection_unavailable")


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional local browser test")
class LearnedLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.driver = sync_playwright().start()
        cls.browser = cls.driver.chromium.launch(headless=True, channel=os.environ.get(
            "CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = self.browser.new_context(accept_downloads=True)
        self.context.route("**/*", self.route)
        self.page = self.context.new_page()
        self.downloads = []
        self.missing = False
        self.absence_text = "No submission"
        self.mismatch = False
        self.bad_type = False
        self.filenames = {}
        self.empty_last = False
        self.page.goto(URL)
        self.page.frame_locator("iframe").locator("#upload").wait_for()
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        pilot.confirm_student_picker(self.page, lambda _: "student")

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.context.close()
        self.temp.cleanup()

    def route(self, route):
        from urllib.parse import parse_qs, urlsplit
        parsed = urlsplit(route.request.url)
        if not route.request.url.startswith(ORIGIN + "/"):
            route.abort()
        elif parsed.path == "/courses/1/gradebook/speed_grader":
            route.fulfill(content_type="text/html", body=OUTER)
        elif parsed.path == "/submission":
            student = parse_qs(parsed.query)["id"][0]
            body = (f'<p>{self.absence_text}</p>' if self.missing and student == "102" else
                    f'<div id="upload" onclick="location.href=\'/file?id={student}\'"><span>{self.filenames.get(student, "wrong_filename.model")}</span></div>')
            if self.empty_last and student == "103":
                body = "<p></p>"
            route.fulfill(content_type="text/html", body=body)
        elif parsed.path == "/file":
            student = parse_qs(parsed.query)["id"][0]
            self.downloads.append(student)
            body = ("Invented model " + student).encode()
            if self.mismatch and len(self.downloads) == 2:
                body = b"Different automatic click result"
            filename = "wrong.html" if self.bad_type else self.filenames.get(student, "wrong_filename.model")
            route.fulfill(headers={"Content-Type": "application/octet-stream",
                                   "Content-Disposition": f'attachment; filename="{filename}"'}, body=body)
        elif parsed.path in ("/name_frame", "/unrelated_name_frame"):
            route.fulfill(content_type="text/html", body=(
                '<button id="student" aria-haspopup="listbox"><span>Invented Same Name</span></button>'))
        else:
            route.abort()

    def run_batch(self, *, answer="run", limit=5000, timeout=3000, extension=".model", files_dir=None):
        batch = app.LearnedBatch(self.page, Path(self.temp.name) / "out", quiet=.05,
                                 extension=extension, files_dir=files_dir, timeout=timeout, prompt=lambda _: answer,
                                 status_path=Path(self.temp.name) / "progress.json")
        actual = app.download_event
        def simulate_first_manual_click(page, action=None, **kwargs):
            if action is None:
                action = lambda: page.frame_locator("iframe:not(#names)").locator("#upload span").click()
            return actual(page, action=action, **kwargs)
        with patch.object(app, "download_event", side_effect=simulate_first_manual_click), patch.object(app.os, "startfile", create=True):
            batch.run(limit=limit)
        return batch

    def test_learn_click_verify_automatic_click_and_download_all_three(self):
        batch = self.run_batch()
        self.assertEqual(self.downloads, ["101", "101", "102", "103"])
        self.assertEqual(batch.state["status"], "current_student_list_complete")
        self.assertTrue(batch.state["automatic_click_verified"])
        self.assertEqual(len(batch.records), 3)
        contents = [(batch.private / record["file"]).read_bytes() for record in batch.records]
        self.assertEqual(contents, [b"Invented model 101", b"Invented model 102", b"Invented model 103"])
        filenames = [record["file"] for record in batch.records]
        self.assertEqual(len(set(filenames)), 3)
        self.assertEqual(filenames[0], "files/Name_Invented_Same_wrong_filename.model")
        self.assertEqual(filenames[1], "files/Name_Invented_Same_wrong_filename (2).model")
        self.assertEqual(filenames[2], "files/Name_Invented_Third_wrong_filename.model")
        self.assertNotIn("Invented Same Name", self.output.getvalue())
        import json
        progress = (Path(self.temp.name) / "progress.json").read_text()
        self.assertEqual(json.loads(progress)["saved_files"], 3)
        self.assertNotIn("Invented", progress)

    def test_all_file_types_preserves_different_extensions_and_no_extension(self):
        self.filenames = {"101": "Invented report.PDF", "102": "Invented spreadsheet.xlsx", "103": "README"}
        self.page.frame_locator("iframe").locator("#upload span").evaluate("e => e.textContent='Invented report.PDF'")
        batch = self.run_batch(extension=None)
        self.assertEqual(self.downloads, ["101", "101", "102", "103"])
        self.assertEqual([r["file"] for r in batch.records], [
            "files/Name_Invented_Same_Invented report.PDF",
            "files/Name_Invented_Same_Invented spreadsheet.xlsx",
            "files/Name_Invented_Third_README"])
        self.assertEqual([(batch.private / r["file"]).read_bytes() for r in batch.records],
                         [b"Invented model 101", b"Invented model 102", b"Invented model 103"])

    def test_chosen_extension_rejects_a_different_download_type(self):
        self.bad_type = True
        with self.assertRaisesRegex(pilot.DownloadError, "unexpected_download_type"):
            self.run_batch()
        self.assertEqual(self.downloads, ["101"])

    def test_remembered_picker_works_on_a_later_assignment_without_student_data_in_cache(self):
        target = Path(self.temp.name) / "picker.json"
        app.remember_picker(self.page, target)
        cached = target.read_text()
        for private in ("Invented", "student_id", "assignment_id", ORIGIN, "value"):
            self.assertNotIn(private, cached)
        delattr(self.page, "_pilot_confirmed_student_picker")
        self.page.evaluate("history.replaceState({},'', '?assignment_id=900&student_id=103'); document.querySelector('#name').value='New Invented Person'")
        self.assertTrue(app.restore_picker(self.page, target))
        self.assertEqual(app.student_reader(self.page), ("103", "New Invented Person"))

    def test_changed_picker_structure_returns_to_setup(self):
        target = Path(self.temp.name) / "picker.json"
        app.remember_picker(self.page, target)
        delattr(self.page, "_pilot_confirmed_student_picker")
        self.page.locator("#name").evaluate("e => e.outerHTML='<button>Other control</button>'")
        self.assertFalse(app.restore_picker(self.page, target))
        self.assertIsNone(self.page._pilot_confirmed_student_picker)

    def test_status_value_cannot_replace_a_remembered_name(self):
        target = Path(self.temp.name) / "picker.json"
        app.remember_picker(self.page, target)
        delattr(self.page, "_pilot_confirmed_student_picker")
        self.page.locator("#name").evaluate("e => e.value='Submitted'")
        self.assertFalse(app.restore_picker(self.page, target))
        self.assertIsNone(self.page._pilot_confirmed_student_picker)

    def test_nested_picker_cache_rejects_an_unrelated_frame_in_the_same_slot(self):
        self.page.locator("body").evaluate("""e => {
          const frame=document.createElement('iframe');frame.id='names';frame.src='/name_frame';
          frame.style.cssText='width:900px;height:150px';e.append(frame);
        }""")
        item = self.page.frame_locator("#names").locator("#student")
        item.wait_for()
        frame = self.page.locator("#names").element_handle().content_frame()
        pilot.bind_name_control(self.page, frame, frame.locator("#student"), pilot.assignment_key(self.page))
        target = Path(self.temp.name) / "picker.json"
        app.remember_picker(self.page, target)
        delattr(self.page, "_pilot_confirmed_student_picker")
        self.assertTrue(app.restore_picker(self.page, target))
        delattr(self.page, "_pilot_confirmed_student_picker")
        frame.goto(ORIGIN + "/unrelated_name_frame")
        self.assertFalse(app.restore_picker(self.page, target))
        self.assertIsNone(self.page._pilot_confirmed_student_picker)

    def test_delayed_iframe_does_not_capture_previous_students_file(self):
        self.page.evaluate("window.delay=400")
        batch = self.run_batch()
        self.assertEqual(len(batch.records), 3)
        self.assertEqual(self.downloads, ["101", "101", "102", "103"])

    def test_automatic_loop_reads_name_from_a_separate_frame(self):
        self.page.locator("#name").evaluate("e => {e.hidden=true;e.removeAttribute('role')}")
        self.page.locator("header").evaluate("e => e.insertAdjacentHTML('beforeend','<input role=combobox value=Attempt readonly>')")
        self.page.locator("body").evaluate("""e => {
          const frame=document.createElement('iframe');frame.id='names';frame.src='/name_frame';
          frame.style.cssText='width:900px;height:150px';e.append(frame);
        }""")
        self.page.frame_locator("#names").locator("#student").wait_for()
        self.page.evaluate("""() => {
          const original=window.advance;
          window.advance=()=>{original();document.querySelector('#names').contentDocument.querySelector('#student').textContent=document.querySelector('#name').value;};
        }""")
        delattr(self.page, "_pilot_confirmed_student_picker")
        answers = iter(("n", " ", "y"))
        pilot.confirm_student_picker(self.page, lambda _: next(answers))
        batch = self.run_batch()
        self.assertEqual(self.downloads, ["101", "101", "102", "103"])
        self.assertEqual(len(batch.records), 3)
        self.assertIn("Name_Invented_Third_", batch.records[2]["file"])

    def test_returns_to_first_student_without_downloading_duplicates(self):
        self.page.evaluate("window.wrap=true")
        batch = self.run_batch()
        self.assertEqual(batch.state["end_reason"], "returned_to_first")
        self.assertEqual(len(batch.records), 3)

    def test_files_go_directly_in_chosen_folder_and_private_state_stays_separate(self):
        destination = Path(self.temp.name) / "chosen downloads" / "assignment"
        batch = self.run_batch(files_dir=destination)
        self.assertEqual(len(list(destination.iterdir())), 3)
        self.assertFalse((batch.private / "files").exists())
        self.assertTrue((batch.private / "manifest.json").exists())
        self.assertFalse((destination / "manifest.json").exists())
        self.assertEqual([Path(record["file"]).parent.resolve() for record in batch.records], [destination.resolve()] * 3)
        self.assertEqual([Path(r["file"]).read_bytes() for r in batch.records],
                         [b"Invented model 101", b"Invented model 102", b"Invented model 103"])

    def set_last_test_student(self, wrap=False):
        self.empty_last = True
        self.page.evaluate("""wrap => {
          window.wrap=wrap;
          const original=window.advance;
          window.advance=()=>{original();if(window.index===2)document.querySelector('#name').value='Test Student';};
        }""", wrap)

    def test_empty_test_student_at_end_finishes_normally(self):
        self.set_last_test_student()
        batch = self.run_batch()
        self.assertEqual(batch.state["status"], "current_student_list_complete")
        self.assertEqual(batch.state["end_reason"], "next_disabled")
        self.assertEqual(batch.rows[-1]["status"], "test_student_skipped")
        self.assertEqual(self.downloads, ["101", "101", "102"])

    def test_empty_test_student_then_wrap_finishes_without_retrying_first(self):
        self.set_last_test_student(wrap=True)
        batch = self.run_batch()
        self.assertEqual(batch.state["end_reason"], "returned_to_first")
        self.assertEqual(self.downloads, ["101", "101", "102"])

    def test_unrecognized_empty_real_submission_still_stops(self):
        self.empty_last = True
        with self.assertRaisesRegex(pilot.DownloadError, "did_not_become_ready"):
            self.run_batch(timeout=500)
        self.assertEqual(self.downloads, ["101", "101", "102"])

    def test_no_submission_is_recorded_and_remaining_students_continue(self):
        self.missing = True
        batch = self.run_batch()
        self.assertEqual(len(batch.rows), 3)
        self.assertEqual(len(batch.records), 2)
        self.assertEqual(batch.rows[1]["status"], "no_upload_displayed")
        self.assertEqual(self.downloads, ["101", "101", "103"])

    def test_unanswered_upload_is_recorded_and_remaining_students_continue(self):
        self.missing = True
        self.absence_text = "No answer"
        batch = self.run_batch()
        self.assertEqual(batch.rows[1]["status"], "no_upload_displayed")
        self.assertEqual(self.downloads, ["101", "101", "103"])

    def test_two_student_limit_uses_no_per_student_prompts(self):
        batch = self.run_batch(limit=2)
        self.assertEqual(len(batch.records), 2)
        self.assertEqual(batch.state["end_reason"], "limit")

    def test_first_file_rejection_preserves_file_and_never_advances(self):
        with self.assertRaisesRegex(pilot.DownloadError, "stopped_by_user"):
            self.run_batch(answer="stop")
        self.assertEqual(self.downloads, ["101"])
        self.assertEqual(pilot.student_url_identity(self.page), "101")
        self.assertEqual(len(list((Path(self.temp.name) / "out/private/files").iterdir())), 1)

    def test_wrong_automatic_file_stops_before_next_student(self):
        self.mismatch = True
        with self.assertRaisesRegex(pilot.DownloadError, "did_not_match_first_file"):
            self.run_batch()
        self.assertEqual(pilot.student_url_identity(self.page), "101")
        self.assertEqual(self.downloads, ["101", "101"])

    def test_unresponsive_next_stops_without_duplicate_capture(self):
        self.page.evaluate("window.noAdvance=true")
        with self.assertRaisesRegex(pilot.DownloadError, "did_not_become_ready"):
            self.run_batch(timeout=500)
        self.assertEqual(self.downloads, ["101", "101"])

    def test_repeated_student_other_than_first_stops_navigation_loop(self):
        self.page.evaluate("window.loop=true")
        with self.assertRaisesRegex(pilot.DownloadError, "student_navigation_loop"):
            self.run_batch()
        self.assertEqual(self.downloads, ["101", "101", "102", "103"])


if __name__ == "__main__":
    unittest.main()
