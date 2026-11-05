"""Invented submissions only. Browser requests cannot leave the fixture route."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import speedgrader_download as app


ORIGIN = "http://speedgrader-fixture.invalid"
URL = ORIGIN + "/courses/123/gradebook/speed_grader?assignment_id=456"
OUTER = '''<!doctype html><select id="students_selectmenu" onchange="load()">
<option value="101">Invented student</option><option value="102">Invented student</option>
<option value="103">=Invented formula</option></select>
<a href="/outside.model">sidebar.model</a><iframe style="width:900px;height:600px"></iframe>
<script>function load(){document.querySelector('iframe').src='/submission?id='+
document.querySelector('select').value;}load();</script>'''


class LocationTests(unittest.TestCase):
    def test_unicode_filename_fits_windows_and_mac_component_limits(self):
        name = app.download_filename('报告' * 160 + '.PDF', '学生' * 50, '', 1, None)
        self.assertTrue(name.endswith('.PDF'))
        self.assertLessEqual(len(name.encode('utf-8')), 240)
        self.assertLessEqual(len(name.encode('utf-16-le')) // 2, 240)

    def keyboard(self, keys):
        pending = deque(keys)
        return SimpleNamespace(kbhit=lambda: bool(pending), getwch=lambda: pending.popleft())

    def test_single_key_choice_shortcuts_do_not_need_enter(self):
        for key, expected in (("n", "next"), (" ", "next"), ("y", "student"), ("q", "stop"), ("p", "point")):
            with patch.object(app.os, "name", "nt"), \
                    patch.dict(sys.modules, {"msvcrt": self.keyboard(key)}), \
                    patch.object(app, "_choice_tail", ""), patch.object(app, "_choice_enter", False), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.live_choice(object(), "Choice: "), expected)

    def test_full_next_word_does_not_contaminate_following_choice(self):
        with patch.object(app.os, "name", "nt"), \
                patch.dict(sys.modules, {"msvcrt": self.keyboard("next\rstudent\r")}), \
                patch.object(app, "_choice_tail", ""), patch.object(app, "_choice_enter", False), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.live_choice(object(), "Choice: "), "next")
            self.assertEqual(app.live_choice(object(), "Choice: "), "student")

    def test_yes_shortcut_word_does_not_contaminate_later_run_prompt(self):
        with patch.object(app.os, "name", "nt"), \
                patch.dict(sys.modules, {"msvcrt": self.keyboard("yes\rrun\r")}), \
                patch.object(app, "_choice_tail", ""), patch.object(app, "_choice_enter", False), \
                patch.object(app.sys, "stdin", SimpleNamespace(isatty=lambda: True)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.live_choice(object(), "Choice: "), "student")
            self.assertEqual(app.ConsoleInput().poll(), "run")

    def test_filename_starts_with_surname_and_keeps_original_without_ids(self):
        name = app.download_filename('../../wrongly_named.model', 'Invented Student',
                                     'R-0123456789abcdef', 2, '.model')
        self.assertEqual(name, 'Student_Invented_wrongly_named.model')
        self.assertEqual(Path(name).name, name)
        second = app.download_filename('wrongly_named.model', 'Invented Student',
                                       'R-fedcba9876543210', 2, '.model')
        self.assertEqual(name, second)
        self.assertEqual(app.download_filename('Original file with spaces.MODEL',
                         'De Silva, Morgan Lee', 'unused', 1, '.model'),
                         'De_Silva_Morgan_Lee_Original file with spaces.MODEL')
        original = 'a' * 90 + '.model'
        self.assertEqual(app.download_filename(original, 'Jordan Lee', '', 1, '.model'),
                         'Lee_Jordan_' + original)
        unsafe = app.download_filename('..\\CON<>:file.model', 'A/B:*? Student',
                                       'R-0123456789abcdef', 1, '.model')
        self.assertFalse(any(character in unsafe for character in '<>:"/\\|?*'))

    def test_retry_discovery_keeps_browser_session(self):
        prompts = []
        with patch.object(app, "choose_page", side_effect=[
                app.DownloadError("speedgrader_student_selector_not_recognized"), "ready-page"]):
            with patch.object(app, "discovery_counts", return_value={"tabs": []}), \
                    patch.object(app, "speedgrader_pages", return_value=[]):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = app.await_speedgrader(object(), ORIGIN, lambda message: prompts.append(message))
        self.assertEqual(result, "ready-page")
        self.assertEqual(len(prompts), 2)

    def test_rejects_existing_source_profile_and_synced_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(app.DownloadError, "output_already_exists"):
                app.validate_locations(root, root / "profile")
            with self.assertRaisesRegex(app.DownloadError, "separate"):
                app.validate_locations(root / "new", root)
            with self.assertRaisesRegex(app.DownloadError, "separate"):
                app.validate_locations(Path(app.__file__).parent / "new", root / "profile")
            with self.assertRaisesRegex(app.DownloadError, "onedrive"):
                app.validate_locations(root / "OneDrive - Invented" / "new", root / "profile")
            out, profile = app.validate_locations(root / "out", root / "profile")
            self.assertFalse(out.exists())
            self.assertFalse(profile.exists())

    def test_bounded_student_count(self):
        self.assertEqual(app.student_count("all"), "all")
        self.assertEqual(app.student_count("2"), 2)
        for value in ("0", "5001", "secret"):
            with self.assertRaises(Exception):
                app.student_count(value)


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional local browser test")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()
        self.requests = []
        self.bad_type = False
        self.empty = False
        self.fail_download = False
        self.context.route("**/*", self.route)
        self.page.goto(URL)
        self.page.frame_locator("iframe").get_by_role("link", name="first.model").wait_for()
        self.output = io.StringIO()
        self.console = contextlib.redirect_stdout(self.output)
        self.console.__enter__()

    def tearDown(self):
        self.console.__exit__(None, None, None)
        self.context.close()
        self.temp.cleanup()

    def route(self, route):
        from urllib.parse import parse_qs, urlsplit
        url = route.request.url
        self.requests.append(url)
        parsed = urlsplit(url)
        if not (url.startswith(ORIGIN + "/") or url.startswith("http://controls-fixture.invalid/")):
            route.abort()
        elif parsed.path == "/courses/123/gradebook/speed_grader":
            route.fulfill(content_type="text/html", body=OUTER)
        elif parsed.path == "/submission":
            student = parse_qs(parsed.query)["id"][0]
            route.fulfill(content_type="text/html", body=(
                f'<a href="/download?id={student}&file=1">first.model</a>'
                f'<a href="/download?id={student}&file=2">second.model</a>'
                '<a href="/ignored">unrelated.txt</a>'))
        elif parsed.path == "/download":
            if self.fail_download:
                route.abort()
            else:
                filename = "wrong.html" if self.bad_type else "../../Invented_student.model"
                route.fulfill(headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                       "Content-Type": "application/octet-stream"},
                              body=b"" if self.empty else ("Invented model " + parsed.query).encode())
        elif parsed.path == "/courses/123/quizzes/456":
            route.fulfill(content_type="text/html", body='<h1>Invented quiz</h1>')
        elif parsed.path == "/student_controls":
            route.fulfill(content_type="text/html", body=(
                '<button id="student" aria-haspopup="listbox"><span>Invented Frame Student</span></button>'
                '<button aria-label="Previous student" disabled>Previous</button>'
                '<button aria-label="Next student">Next</button>'))
        elif parsed.path == "/nested_controls":
            route.fulfill(content_type="text/html", body=(
                '<iframe src="http://controls-fixture.invalid/student_controls" style="width:800px;height:180px"></iframe>'))
        else:
            route.abort()

    def collector(self, responses=None, prompt=None, **kwargs):
        answers = iter(responses or ["ready", "download", "checked"])
        def answer(message):
            if "Type ready" in message:
                # Simulates the instructor checking the newly loaded iframe locally.
                self.page.wait_for_timeout(150)
            return next(answers)
        return app.FileBatch(self.page, Path(self.temp.name) / "out", prompt=prompt or answer,
                             quiet=0, timeout=1500, **kwargs)

    def manifest(self, collector):
        return json.loads((collector.private / "manifest.json").read_text())

    def use_modern_student_picker(self, *, named=True, name="Invented Modern Student"):
        label = 'aria-label="Select student"' if named else ''
        self.page.set_content(
            '<div><input role="combobox" aria-label="Submission status" value="Submitted" readonly></div>'
            '<nav aria-label="Student navigation"><button aria-label="Previous student" disabled>Previous</button>'
            f'<input role="combobox" {label} value="{name}" readonly>'
            '<button aria-label="Next student">Next</button></nav>'
            '<iframe src="/submission?id=101" style="width:900px;height:600px"></iframe>')
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456&student_id=101&anonymous_id=InventedAnon')")
        self.page.frame_locator("iframe").get_by_role("link", name="first.model").wait_for()

    def use_framed_picker(self, nested=False):
        target = ORIGIN + "/nested_controls" if nested else "http://controls-fixture.invalid/student_controls"
        self.page.set_content(
            '<input role="combobox" value="Submitted" readonly><input role="combobox" value="Attempt 1" readonly>'
            f'<iframe id="controls" src="{target}" style="width:900px;height:300px"></iframe>')
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456&student_id=101')")
        if nested:
            self.page.frame_locator("#controls").frame_locator("iframe").locator("#student").wait_for()
        else:
            self.page.frame_locator("#controls").locator("#student").wait_for()

    def test_selector_search_includes_menu_buttons_in_other_frames(self):
        self.use_framed_picker()
        answers = iter(("n", " ", "y"))
        app.confirm_student_picker(self.page, lambda _: next(answers))
        self.assertEqual(app.modern_student_record(self.page), ("101", "Invented Frame Student"))
        self.assertEqual(len(self.page._pilot_confirmed_student_picker["frame_path"]), 1)
        self.assertEqual(app.navigation_button(self.page, "next").count(), 1)

    def test_nested_cross_origin_name_frame_is_bound_and_read(self):
        self.use_framed_picker(nested=True)
        answers = iter(("next", "next", "student"))
        app.confirm_student_picker(self.page, lambda _: next(answers))
        self.assertEqual(len(self.page._pilot_confirmed_student_picker["frame_path"]), 2)
        self.assertEqual(app.modern_student_record(self.page)[1], "Invented Frame Student")

    def test_different_document_in_the_same_frame_slot_is_rejected(self):
        self.use_framed_picker()
        answers = iter(("n", "n", "y"))
        app.confirm_student_picker(self.page, lambda _: next(answers))
        self.page.locator("#controls").evaluate("e => e.src='/submission?id=101'")
        self.page.frame_locator("#controls").get_by_role("link", name="first.model").wait_for()
        with self.assertRaisesRegex(app.DownloadError, "confirmed_student_picker_changed"):
            app.modern_student_record(self.page)

    def test_point_directly_binds_clicked_name_in_other_frame(self):
        self.use_framed_picker()
        target = self.page.frame_locator("#controls").locator("#student span")
        with patch.object(self.page, "bring_to_front", side_effect=lambda: target.click()):
            app.point_student_picker(self.page, timeout=2000)
        self.assertEqual(app.modern_student_record(self.page)[1], "Invented Frame Student")

    def test_rejecting_all_candidates_returns_to_setup_and_keeps_page_open(self):
        self.use_framed_picker()
        answers = iter(("n", "n", "n", "r", "n", "n", "y"))
        app.confirm_student_picker(self.page, lambda _: next(answers))
        self.assertFalse(self.page.is_closed())
        self.assertEqual(app.modern_student_record(self.page)[1], "Invented Frame Student")

    def test_modern_picker_one_file_name_mapping_and_download(self):
        self.use_modern_student_picker()
        self.assertEqual(app.choose_page(self.context, ORIGIN), self.page)
        self.assertEqual(app.read_roster(self.page), (("101", "Invented Modern Student"),))
        collector = self.collector()
        collector.run()
        self.assertTrue(collector.modern_picker)
        self.assertEqual(collector.state["status"], "pilot_complete")
        self.assertEqual(len(collector.records), 1)
        target = collector.private / collector.records[0]["file"]
        self.assertTrue(target.name.startswith("Student_Invented_Modern_"))
        self.assertEqual(target.read_bytes(), b"Invented model id=101&file=1")
        self.assertFalse(collector.state["all_students_verified"])

    def test_modern_unlabelled_picker_is_scoped_by_navigation(self):
        self.use_modern_student_picker(named=False)
        self.assertEqual(self.page.get_by_role("combobox").count(), 2)
        self.assertEqual(app.modern_student_combo(self.page).input_value(), "Invented Modern Student")
        self.assertEqual(app.choose_page(self.context, ORIGIN), self.page)

    def test_visual_student_picker_is_confirmed_and_outline_restored(self):
        self.use_modern_student_picker(named=False)
        answers = iter(("next", "student"))
        def answer(message):
            self.assertTrue(any(self.page.get_by_role("combobox").nth(i).evaluate(
                "e => e.style.outline.includes('4px')") for i in range(2)))
            return next(answers)
        app.confirm_student_picker(self.page, answer)
        self.assertEqual(app.modern_student_record(self.page), ("101", "Invented Modern Student"))
        self.assertEqual(self.page.get_by_role("combobox").evaluate_all("items => items.map(e=>e.style.outline)"), ["", ""])

    def test_visual_picker_survives_header_rerender_without_using_a_stale_handle(self):
        self.use_modern_student_picker(named=False)
        answers = iter(("next", "student"))
        app.confirm_student_picker(self.page, lambda _: next(answers))
        self.page.get_by_role("combobox").nth(1).evaluate("e => {e.outerHTML=e.outerHTML}")
        self.assertEqual(app.modern_student_record(self.page)[1], "Invented Modern Student")

    def test_modern_ambiguous_student_pickers_are_rejected(self):
        self.use_modern_student_picker()
        self.page.locator("body").evaluate("e => e.insertAdjacentHTML('beforeend','<input role=combobox aria-label=Student value=Other>')")
        with self.assertRaisesRegex(app.DownloadError, "selector_ambiguous"):
            app.choose_page(self.context, ORIGIN)

    def test_modern_student_url_change_stops_before_download(self):
        self.use_modern_student_picker()
        def answer(message):
            if "Type download" in message:
                self.page.evaluate("history.replaceState({},'', '?assignment_id=456&student_id=102')")
                return "download"
            return "ready"
        collector = self.collector(prompt=answer)
        with self.assertRaisesRegex(app.DownloadError, "selected_student_changed"):
            collector.run()
        self.assertEqual(collector.records, [])

    def test_modern_duplicate_names_keep_distinct_student_identities(self):
        self.use_modern_student_picker()
        first = app.read_roster(self.page)[0]
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456&student_id=102')")
        second = app.read_roster(self.page)[0]
        self.assertEqual(first[1], second[1])
        self.assertNotEqual(first[0], second[0])

    def test_modern_missing_name_or_conflicting_url_identity_is_rejected(self):
        self.use_modern_student_picker(name="")
        with self.assertRaisesRegex(app.DownloadError, "current_student_name_not_available"):
            app.choose_page(self.context, ORIGIN)
        self.page.get_by_role("combobox", name="Select student").evaluate("e => e.value='Invented student'")
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456&student_id=101#student_id=102')")
        with self.assertRaisesRegex(app.DownloadError, "student_url_identity_ambiguous"):
            app.choose_page(self.context, ORIGIN)

    def test_modern_pilot_does_not_claim_to_process_a_full_roster(self):
        self.use_modern_student_picker()
        collector = self.collector()
        with self.assertRaisesRegex(app.DownloadError, "one_student_pilot_first"):
            collector.run(students="all", all_files=True)
        self.assertEqual(collector.records, [])
        self.assertEqual(collector.state["status"], "stopped")

    def test_modern_discovery_counts_do_not_include_names_or_identities(self):
        self.use_modern_student_picker()
        report = app.discovery_counts(self.context, ORIGIN)
        main = report["tabs"][0]["frames"][0]
        self.assertEqual(main["legacy_student_selects"], 0)
        self.assertEqual(main["comboboxes"], 2)
        self.assertEqual(main["student_named_comboboxes"], 1)
        self.assertEqual(main["combobox_values_present"], [True, True])
        for private in ("Invented Modern Student", "InventedAnon", "student_id=101"):
            self.assertNotIn(private, json.dumps(report))

    def test_terminal_wait_services_new_tab_and_frame_loading(self):
        loaded = []
        self.context.on("page", lambda popup: popup.on("load", lambda: loaded.append(True)))
        self.page.evaluate("url => setTimeout(() => window.open(url), 50)", URL)
        deadline = time.monotonic() + 5
        def poll():
            if time.monotonic() > deadline:
                self.fail("Browser did not load a new tab while the terminal waited")
            return "ready" if loaded else None
        answer = app.live_prompt(self.context, "Synthetic prompt: ", poll=poll)
        self.assertEqual(answer, "ready")
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(len(app.file_controls(self.context.pages[1], ".model")), 2)
        self.assertEqual(self.output.getvalue().count("Synthetic prompt:"), 1)

    def test_pilot_one_file_uses_iframe_download_and_checksums(self):
        collector = self.collector()
        collector.run()
        manifest = self.manifest(collector)
        self.assertEqual(manifest["status"], "pilot_complete")
        self.assertTrue(manifest["first_file_locally_checked"])
        self.assertFalse(manifest["contents_deidentified"])
        self.assertFalse(manifest["all_students_verified"])
        records = manifest["records"]
        self.assertEqual(len(records), 1)
        target = collector.private / records[0]["file"]
        self.assertEqual(target.read_bytes(), b"Invented model id=101&file=1")
        self.assertEqual(records[0]["sha256"], app.hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertTrue(target.name.startswith("student_Invented_"))
        self.assertEqual(list((collector.private / "incoming").iterdir()), [])
        self.assertFalse(any("outside.model" in url for url in self.requests))
        self.assertNotIn("http", json.dumps(manifest))
        self.assertNotIn("Invented student", self.output.getvalue())

    def test_two_students_duplicate_names_remain_separate(self):
        collector = self.collector(["ready", "download", "checked", "ready", "download"])
        collector.run(students=2, all_files=True)
        manifest = self.manifest(collector)
        self.assertEqual(manifest["status"], "requested_scope_complete")
        self.assertEqual(len(manifest["records"]), 4)
        self.assertEqual(len({row["record_id"] for row in manifest["records"]}), 2)
        content = [(collector.private / row["file"]).read_bytes() for row in manifest["records"]]
        self.assertEqual(content, [f"Invented model id={student}&file={file}".encode()
                                   for student in (101, 102) for file in (1, 2)])

    def test_all_students_csv_formula_protection_and_private_only(self):
        collector = self.collector(["ready", "download", "checked", "ready", "download", "ready", "download"])
        collector.run(students="all", all_files=True)
        self.assertEqual(len(collector.records), 6)
        self.assertIn("'=Invented formula", (collector.private / "name_key.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual([path.name for path in collector.out.iterdir()], ["private"])
        self.assertEqual(list(collector.out.glob("*.zip")), [])

    def test_stop_after_first_preserves_file_without_verification_claim(self):
        collector = self.collector(["ready", "download", "stop"])
        with self.assertRaisesRegex(app.DownloadError, "stopped_by_user"):
            collector.run(students=2, all_files=True)
        manifest = self.manifest(collector)
        self.assertEqual(manifest["status"], "stopped")
        self.assertFalse(manifest["first_file_locally_checked"])
        self.assertEqual(len(manifest["records"]), 1)
        self.assertTrue((collector.private / manifest["records"][0]["file"]).exists())

    def test_user_changes_student_before_click_stops_without_download(self):
        def answer(message):
            if "Type download" in message:
                self.page.locator(app.STUDENTS).select_option("102")
                return "download"
            return "ready"
        collector = self.collector(prompt=answer)
        with self.assertRaisesRegex(app.DownloadError, "selected_student_changed"):
            collector.run()
        self.assertEqual(collector.records, [])

    def test_assignment_change_and_roster_reorder_are_rejected(self):
        collector = self.collector()
        self.page.evaluate("history.replaceState({},'', '?assignment_id=999')")
        with self.assertRaisesRegex(app.DownloadError, "assignment_changed"):
            collector.run()
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456')")
        self.page.evaluate("let s=document.querySelector('select');s.append(s.firstElementChild)")
        with self.assertRaisesRegex(app.DownloadError, "student_list_changed"):
            collector.verify_student("101")

    def test_no_controls_is_unverified_and_stops(self):
        collector = self.collector(extension=".absent")
        with self.assertRaisesRegex(app.DownloadError, "no_matching_controls_unverified"):
            collector.run()
        self.assertEqual(self.manifest(collector)["students"][0]["status"], "no_matching_controls_unverified")

    def test_changed_file_control_is_rejected(self):
        def answer(message):
            if "Type download" in message:
                self.page.frames[1].locator("a").first.evaluate("e => e.textContent='changed.model'")
                return "download"
            return "ready"
        collector = self.collector(prompt=answer)
        with self.assertRaisesRegex(app.DownloadError, "file_controls_changed"):
            collector.run()

    def test_wrong_type_does_not_publish_file(self):
        self.bad_type = True
        collector = self.collector()
        with self.assertRaisesRegex(app.DownloadError, "unexpected_download_type"):
            collector.run()
        self.assertEqual(collector.records[0]["status"], "failed")
        self.assertEqual(list((collector.private / "files").iterdir()), [])
        self.assertEqual(list((collector.private / "incoming").iterdir()), [])

    def test_empty_download_is_failed(self):
        self.empty = True
        collector = self.collector()
        with self.assertRaisesRegex(app.DownloadError, "empty_download"):
            collector.run()
        self.assertEqual(collector.records[0]["status"], "failed")

    def test_download_failure_does_not_persist_exception_text(self):
        self.fail_download = True
        collector = self.collector()
        with self.assertRaises(Exception):
            collector.run()
        manifest = self.manifest(collector)
        self.assertEqual(manifest["error"], "download_or_browser_error")
        self.assertNotIn(ORIGIN, json.dumps(manifest))
        self.assertEqual(manifest["records"][0]["status"], "failed")

    def test_nested_controls_rejected_and_cross_frame_matches_supported(self):
        self.page.frames[1].set_content('<a href="/download?id=101&file=1"><span>first.model</span></a>')
        with self.assertRaisesRegex(app.DownloadError, "nested_file_controls"):
            app.file_controls(self.page, ".model", "a,span")
        self.page.locator("body").evaluate("e => {let f=document.createElement('iframe');f.src='/submission?id=102';e.append(f)}")
        self.page.frame_locator("iframe").nth(1).get_by_role("link", name="first.model").wait_for()
        self.assertEqual(len(app.file_controls(self.page, ".model")), 3)

    def test_choose_page_rejects_multiple_tabs(self):
        self.assertEqual(app.choose_page(self.context, ORIGIN), self.page)
        second = self.context.new_page()
        second.goto(URL)
        with self.assertRaisesRegex(app.DownloadError, "multiple_speedgrader"):
            app.choose_page(self.context, ORIGIN)

    def test_quiz_tab_plus_speedgrader_tab_is_accepted(self):
        quiz = self.context.new_page()
        quiz.goto(ORIGIN + "/courses/123/quizzes/456")
        self.assertEqual(app.choose_page(self.context, ORIGIN), self.page)
        counts = app.discovery_counts(self.context, ORIGIN)
        self.assertEqual(len(counts["tabs"]), 2)
        self.assertTrue(counts["tabs"][0]["expected_speedgrader_route"])
        self.assertFalse(counts["tabs"][1]["expected_speedgrader_route"])

    def test_student_and_anonymous_query_parameters_are_accepted(self):
        self.page.evaluate("history.replaceState({},'', '?assignment_id=456&anonymous_id=InventedAnon&student_id=101')")
        self.assertEqual(app.choose_page(self.context, ORIGIN), self.page)
        self.assertEqual(app.assignment_key(self.page), ("http", "speedgrader-fixture.invalid",
                                                        "/courses/123/gradebook/speed_grader", "456"))
        counts = json.dumps(app.discovery_counts(self.context, ORIGIN))
        self.assertNotIn("InventedAnon", counts)
        self.assertNotIn("student_id=101", counts)

    def test_loaded_page_without_legacy_student_selector_is_distinguished(self):
        self.page.locator(app.STUDENTS).evaluate("e => e.remove()")
        self.page.locator("body").evaluate("e => e.insertAdjacentHTML('beforeend','<input role=combobox aria-label=Students>')")
        with self.assertRaisesRegex(app.DownloadError, "student_selector_not_recognized"):
            app.choose_page(self.context, ORIGIN)
        counts = app.discovery_counts(self.context, ORIGIN)
        main = counts["tabs"][0]["frames"][0]
        self.assertEqual(main["legacy_student_selects"], 0)
        self.assertEqual(main["comboboxes"], 1)
        self.assertTrue(main["document_complete"])

    def test_unrecognized_url_and_counts_never_expose_private_labels(self):
        self.page.evaluate("history.replaceState({},'', '/courses/123/alternate_speedgrader?assignment_id=456&private_label=InventedSecret#student_id=789')")
        with self.assertRaisesRegex(app.DownloadError, "no_speedgrader_tab"):
            app.choose_page(self.context, ORIGIN)
        counts = app.discovery_counts(self.context, ORIGIN)
        self.assertTrue(counts["tabs"][0]["speedgrader_route_variant"])
        serialized = json.dumps(counts)
        for private in ("InventedSecret", "Invented student", "789", ORIGIN, "assignment_id=456"):
            self.assertNotIn(private, serialized)


if __name__ == "__main__":
    unittest.main()
