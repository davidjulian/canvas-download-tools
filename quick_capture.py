"""Save text from each visible View Log control on one Canvas Moderate page.

Uses the instructor's local browser. No API tokens, external AI, telemetry,
screenshots, traces, DOM uploads or private Canvas service endpoints.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

VIEW_LOG = re.compile(r"view\s+log", re.I)
VIEW_LOG_PREFIX = re.compile(r"^view\s+log\b", re.I)
VERSION = "0.13.2"


class CaptureError(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_error_category(error):
    """Classify locally; never return the exception message or unknown text."""
    message = str(error).casefold()
    rules = (
        (("trustedscript", "trusted type", "content security policy", "unsafe-eval"), "page_script_security_restriction"),
        (("execution context was destroyed", "cannot find context", "navigation"), "page_navigated_during_inspection"),
        (("has been closed", "target closed", "page closed"), "browser_or_tab_closed"),
        (("frame was detached", "frame has been detached"), "frame_detached"),
        (("not an htmlelement",), "non_html_element"),
        (("syntaxerror",), "page_script_syntax_error"),
        (("typeerror",), "page_script_type_error"),
        (("timeout", "timed out"), "inspection_timeout"),
    )
    return next((category for terms, category in rules if any(term in message for term in terms)), "browser_inspection_error")


def element_text(item):
    try:
        return item.inner_text(timeout=3000)
    except Exception as error:
        if safe_error_category(error) != "non_html_element":
            raise
        # SVG navigation links do not implement HTMLElement.innerText.
        return item.text_content(timeout=3000) or ""


def normalize_name(value):
    value = " ".join(unicodedata.normalize("NFC", value).split())
    return "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()


def filename_name(value):
    value = re.sub(r'[<>:"/\\|?*]', "_", normalize_name(value))
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")[:60].rstrip(" ._")
    return value or "student"


def csv_name(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def log_from_began(text):
    """Remove navigation preceding the log, without guessing when absent."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^\s*Began\s+\S", line)]
    if len(starts) != 1:
        raise CaptureError("log_start_began_not_found_or_ambiguous")
    return "\n".join(line for line in lines[starts[0]:]
                     if not re.fullmatch(r"=== Visible document \d+ ===", line)).strip()


def proposed_student_name(item):
    """Propose locally from a student row/name field or accessible control label.

    The instructor reviews every required mapping in names.csv. No names are
    printed in the console or in the count-only diagnostic.
    """
    def candidate(value):
        name = normalize_name(value)
        if not name or len(name) > 200 or not any(c.isalpha() for c in name):
            return ""
        if re.match(r"^(?:view\s+log|attempt\b|submission\b|session\b|moderate$|score\b)", name, re.I):
            return ""
        return name

    def unique_field(container):
        fields = container.locator('[data-testid*="student-name"], [data-testid*="studentName"], '
                                   '[data-test*="student-name"], [class*="student-name"], [class*="studentName"]')
        values = {candidate(element_text(fields.nth(i))) for i in range(fields.count()) if fields.nth(i).is_visible()}
        values.discard("")
        return next(iter(values)) if len(values) == 1 else ""

    row = item.locator("xpath=ancestor::*[self::tr or @role='row' or self::li][1]")
    if row.count():
        name = unique_field(row)
        if name:
            return name, "student_name_field"
        # Direct cells avoid taking a name from an unrelated nested student row.
        cells = row.locator(':scope > td,:scope > th,:scope > [role="cell"],'
                            ':scope > [role="rowheader"],:scope > [role="gridcell"]')
        for i in range(cells.count()):
            cell = cells.nth(i)
            if not cell.is_visible():
                continue
            links = cell.get_by_role("link")
            values = {candidate(element_text(links.nth(j))) for j in range(links.count()) if links.nth(j).is_visible()}
            values.discard("")
            if len(values) == 1:
                return next(iter(values)), "student_row_link"
            lines = [candidate(line) for line in element_text(cell).splitlines() if line.strip()]
            if lines and lines[0]:
                return lines[0], "student_row_cell"

    # aria_snapshot exposes the computed accessible name, including labelledby.
    labels = [item.get_attribute("aria-label") or "", element_text(item)]
    first_line = item.aria_snapshot(timeout=3000).splitlines()[0]
    quoted = re.search(r'^\s*-\s+(?:button|link)\s+("(?:[^"\\]|\\.)*")', first_line)
    if quoted:
        try:
            labels.append(json.loads(quoted.group(1)))
        except ValueError:
            pass
    for label in labels:
        match = re.fullmatch(r"\s*view\s+log\s+(?:for\s+)?(.+?)\s*", label, re.I)
        if match:
            tail = re.sub(r"\s*[,;:\-]?\s*\(?attempt\s+\d+\)?\s*$", "", match.group(1), flags=re.I)
            name = candidate(tail.strip(' :\"'))
            if name:
                return name, "view_log_accessible_name"

    # Some New Quizzes layouts use cards rather than semantic table rows.
    parent = item.locator("xpath=..")
    for _ in range(7):
        if not parent.count():
            break
        name = unique_field(parent)
        if name:
            return name, "student_name_field"
        parent = parent.locator("xpath=..")
    return "", "manual_required"


def visible_frame(frame):
    current = frame
    while current.parent_frame is not None:
        if not current.frame_element().is_visible():
            return False
        current = current.parent_frame
    return True


def view_log_locator(frame):
    return frame.get_by_role("button", name=VIEW_LOG_PREFIX).or_(
        frame.get_by_role("link", name=VIEW_LOG_PREFIX)).or_(
        frame.locator('a,button,[role="button"],[role="link"]').filter(
            has_text=re.compile(r"^\s*view\s+log\s*$", re.I)))


def control_signature(item):
    text = " ".join(element_text(item).split())
    label = " ".join((item.get_attribute("aria-label") or "").split())
    row = item.locator("xpath=ancestor::*[self::tr or @role='row' or self::li][1]")
    if not row.count():
        row = item.locator("xpath=..")
    context = json.dumps({"href": item.get_attribute("href"), "text": text, "label": label,
                          "accessible_name": item.aria_snapshot(timeout=3000),
                          "context": element_text(row) if row.count() else text}, sort_keys=True)
    return digest(context)


def list_quiet_token(page):
    """Cheap local change detection; full identity checks remain authoritative."""
    observations = []
    for number, frame in enumerate(page.frames):
        try:
            if not visible_frame(frame):
                continue
            values = view_log_locator(frame).evaluate_all(r"""items => items.filter(e => {
              const r=e.getBoundingClientRect();
              return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'&&
                !e.disabled&&!e.closest('[aria-disabled="true"]');
            }).map(e => {
              const row=e.closest('tr,[role="row"],li')||e.parentElement||e;
              return [e.getAttribute('href'),e.getAttribute('aria-label'),e.getAttribute('aria-labelledby'),
                e.getAttribute('aria-disabled'),!!e.disabled,row.innerText||row.textContent||''];
            })""")
            observations.append((number, values))
        except Exception:
            observations.append((number, 'inspection_in_progress'))
    # Row text never leaves the local worker; only this temporary token is used.
    return digest(json.dumps(observations, sort_keys=True))


def controls_in_frame(frame):
    """Return local controls and identity hashes from one visible document."""
    if visible_frame(frame):
        controls, signatures = [], []
        # Match computed accessible names, including aria-labelledby and labels
        # such as 'View Log for <student>'. These were present in the instructor's
        # count-only diagnostic even when exact text/name matching found none.
        loc = view_log_locator(frame)
        for i in range(loc.count()):
            item = loc.nth(i)
            if not item.is_visible() or not item.is_enabled():
                continue
            # Generated DOM IDs can change after Back; identity comes from the
            # accessible name and row text instead. Names never leave the local
            # process except in the intentionally saved rendered log page text.
            controls.append(item)
            signatures.append(control_signature(item))
        return controls, tuple(signatures)
    return [], ()


def controls_on_page(page):
    """Match View Log accessible names, then hash local identity context."""
    matches = []
    for frame in page.frames:
        controls, signatures = controls_in_frame(frame)
        if controls:
            matches.append((controls, signatures))
    if not matches:
        raise CaptureError("no_view_log_controls_found")
    if len(matches) > 1:
        raise CaptureError("view_log_controls_in_multiple_visible_frames")
    return matches[0]


def locator_diagnostic(frame):
    """Fallback uses browser locator APIs, not a custom page script."""
    exact = re.compile(r"^view\s+log$", re.I)
    extended = re.compile(r"^view\s+log\b", re.I)
    def visible_count(locator):
        return sum(locator.nth(i).is_visible() for i in range(locator.count()))
    return {"body_present": frame.locator("body").count() > 0,
            "standard_controls_in_dom": frame.locator('a,button,[role="button"],[role="link"]').count(),
            "visible_exact_text_matches": visible_count(frame.get_by_text(exact)),
            "visible_exact_named_links": visible_count(frame.get_by_role("link", name=exact)),
            "visible_exact_named_buttons": visible_count(frame.get_by_role("button", name=exact)),
            "visible_extended_named_links": visible_count(frame.get_by_role("link", name=extended)),
            "visible_extended_named_buttons": visible_count(frame.get_by_role("button", name=extended))}


def diagnostic_report(context):
    """Only counts and static flags leave the page. Never return DOM text/URLs."""
    report = {"version": VERSION, "tabs": []}
    for tab_number, page in enumerate(context.pages, 1):
        entry = {"tab_number": tab_number, "frames": []}
        for frame_number, frame in enumerate(page.frames, 1):
            item = {"frame_number": frame_number}
            try:
                item["visible"] = visible_frame(frame)
                if item["visible"]:
                    item["inspection_method"] = "page_script"
                    item.update(frame.evaluate("""() => {
                        const normal=s=>(s||'').replace(/\\s+/g,' ').trim();
                        const exact=s=>/^view\\s+log$/i.test(normal(s));
                        const visible=e=>{const s=getComputedStyle(e);return !!e.getClientRects().length&&s.visibility!=='hidden'&&s.display!=='none'};
                        const all=[...document.querySelectorAll('a,button,[role="button"],[role="link"]')].filter(visible);
                        const enabled=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true';
                        const usable=all.filter(enabled);
                        const leaves=[...document.querySelectorAll('body *')].filter(e=>!e.children.length&&visible(e)&&exact(e.innerText));
                        return {
                            visible_standard_controls:all.length,
                            exact_visible_text:usable.filter(e=>exact(e.innerText)).length,
                            exact_aria_label:usable.filter(e=>exact(e.getAttribute('aria-label'))).length,
                            either_exact:usable.filter(e=>exact(e.innerText)||exact(e.getAttribute('aria-label'))).length,
                            extended_view_log_label_only:usable.filter(e=>!exact(e.innerText)&&!exact(e.getAttribute('aria-label'))&&/^view\\s+log\\b/i.test(normal(e.getAttribute('aria-label')))).length,
                            disabled_exact:all.filter(e=>!enabled(e)&&(exact(e.innerText)||exact(e.getAttribute('aria-label')))).length,
                            exact_text_outside_standard_control:leaves.filter(e=>!e.closest('a,button,[role="button"],[role="link"]')).length
                        };
                    }"""))
            except Exception as error:
                item["script_error_category"] = safe_error_category(error)
                try:
                    item["locator_probe"] = locator_diagnostic(frame)
                    item["inspection_method"] = "locator_fallback"
                except Exception as fallback_error:
                    item["inspection_failed"] = True
                    item["fallback_error_category"] = safe_error_category(fallback_error)
            entry["frames"].append(item)
        report["tabs"].append(entry)
    return report


def choose_moderate_page(context, prompt=input, timeout=45, stable_seconds=1, check_cancel=lambda: None,
                         candidate_fingerprint=None):
    # The first Enter can coincide with a navigation or React rendering. Require
    # a nonempty matching list to remain identical across repeated observations.
    candidates, previous, stable_since = [], None, None
    deadline = time.monotonic() + timeout
    print("Waiting for the Moderate log list to finish loading...")
    while time.monotonic() < deadline:
        check_cancel()
        observed, fingerprint = [], []
        for number, page in enumerate(context.pages, 1):
            try:
                if not page.locator("body").count() or visibly_busy(page):
                    continue
                if candidate_fingerprint:
                    count, signature = candidate_fingerprint(page)
                else:
                    controls, signature = controls_on_page(page)
                    count = len(controls)
                observed.append((number, page, count))
                fingerprint.append((number, signature))
            except Exception:
                continue
        now = time.monotonic()
        if observed and fingerprint == previous:
            if stable_since is not None and now - stable_since >= stable_seconds:
                candidates = observed
                break
        else:
            stable_since = now if observed else None
        previous = fingerprint
        pages = context.pages
        if not pages:
            break
        pages[0].wait_for_timeout(250)
    if not candidates:
        print("No single matching log list was found. This diagnostic contains counts only, no names, URLs or page text:")
        print(json.dumps(diagnostic_report(context), indent=2))
        raise CaptureError("no_unambiguous_moderate_tab_found")
    for number, _, count in candidates:
        print(f"Browser tab {number}: {count} matching View Log controls.")
    if len(candidates) == 1:
        page = candidates[0][1]
    else:
        answer = prompt("Multiple tabs have log lists. Enter the tab number to use: ").strip()
        chosen = [p for number, p, _ in candidates if str(number) == answer]
        if len(chosen) != 1:
            raise CaptureError("moderate_tab_not_selected")
        page = chosen[0]
    page.bring_to_front()
    return page


def page_text(page):
    """Capture rendered text only. Do not include frame URLs, HTML or storage."""
    parts = []
    for frame in page.frames:
        if visible_frame(frame) and frame.locator("body").count():
            text = frame.locator("body").inner_text().strip()
            if text:
                parts.append(f"=== Visible document {len(parts) + 1} ===\n{text}")
    return "\n\n".join(parts)


def visibly_busy(page):
    for frame in page.frames:
        if visible_frame(frame):
            busy = frame.locator('[aria-busy="true"], [role="progressbar"]')
            if any(busy.nth(i).is_visible() for i in range(busy.count())):
                return True
    return False


def wait_log(page, marker, previous_list_text, timeout=45, stable_seconds=3, check_cancel=lambda: None):
    deadline, stable_since, previous = time.monotonic() + timeout, None, None
    while time.monotonic() < deadline:
        check_cancel()
        try:
            text = page_text(page)
            ready = marker.casefold() in text.casefold() and text != previous_list_text and not visibly_busy(page)
        except Exception:
            ready, text = False, ""
        now = time.monotonic()
        if ready and text == previous:
            if stable_since is not None and now - stable_since >= stable_seconds:
                return text
        else:
            stable_since = now if ready else None
        previous = text
        page.wait_for_timeout(250)
    raise CaptureError("log_did_not_become_ready_and_stable")


def wait_list(page, expected, timeout=30, check_cancel=lambda: None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_cancel()
        try:
            controls, signature = controls_on_page(page)
            if signature == expected and not visibly_busy(page):
                return controls
        except Exception:
            pass
        page.wait_for_timeout(250)
    raise CaptureError("back_did_not_restore_the_same_moderate_page")


def back_to_list(page, expected, check_cancel=lambda: None):
    # history.back() covers joint iframe history too. Waiting for a main-document
    # navigation event can time out when only an iframe's route changes.
    try:
        page.evaluate("() => { window.history.back(); }")
    except Exception:
        # A document navigation can destroy the evaluating context. The required
        # check is the restored list below; never click another control blindly.
        pass
    return wait_list(page, expected, check_cancel=check_cancel)


class QuickCapture:
    def __init__(self, page, out, prompt=input, settle=3, check_cancel=lambda: None, trim_menu=False):
        self.page, self.out, self.prompt, self.settle = page, Path(out), prompt, settle
        self.check_cancel = check_cancel
        self.trim_menu = trim_menu
        self.out.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "format": "canvas_quick_capture_v1", "app_version": VERSION,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "View Log controls visible on one Moderate page only",
            "all_students_verified": False, "all_events_verified": False,
            "page_loop_completed": False, "first_capture_locally_checked": False,
            "detected_controls": None, "saved_logs": [], "error": None,
            "names_locally_reviewed": False,
            "limitations": "Rendered page text only; no expansion, scrolling, event pagination, student pagination or analysis."
        }
        self.names = {}
        self.checkpoint()

    def checkpoint(self):
        temp = self.out / "manifest.json.tmp"
        temp.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        temp.replace(self.out / "manifest.json")

    def prepare_names(self, controls, positions=None):
        self.proposed_names = {}
        with (self.out / "names.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("control_position", "student_name", "proposed_source"))
            writer.writeheader()
            for index, control in enumerate(controls):
                if positions is not None and index not in positions:
                    continue
                name, source = proposed_student_name(control)
                self.proposed_names[index] = (name, source)
                writer.writerow({"control_position": index + 1, "student_name": csv_name(name),
                                 "proposed_source": source})
        print("Open names.csv in the output folder locally. Check the proposed student names against Moderate.")
        print("Correct missing or wrong student_name cells there, save and close the file. Keep control_position unchanged.")
        print("Use CSV UTF-8 if your editor asks which format to save.")
        print("Names are proposed from Moderate, not verified from the log page. Do not share names.csv with this chat.")

    def load_reviewed_names(self, required_positions=None):
        required = set(self.proposed_names) if required_positions is None else set(required_positions)
        names = {}
        with (self.out / "names.csv").open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "control_position" not in reader.fieldnames or "student_name" not in reader.fieldnames:
                raise CaptureError("names_csv_columns_changed")
            for row in reader:
                try:
                    index = int(row["control_position"]) - 1
                except (TypeError, ValueError):
                    raise CaptureError("names_csv_position_invalid") from None
                if index not in self.proposed_names or index in names:
                    raise CaptureError("names_csv_positions_changed")
                original, source = self.proposed_names[index]
                value = row["student_name"] or ""
                # Undo our formula-safe CSV prefix only for an unchanged proposal.
                if value == csv_name(original):
                    value = original
                name = normalize_name(value)
                if index in required and (not name or len(name) > 200):
                    raise CaptureError("required_student_name_missing_or_too_long")
                names[index] = (name, source if name == original else "locally_edited")
        if set(names) != set(self.proposed_names):
            raise CaptureError("names_csv_positions_changed")
        self.names = names
        self.manifest["names_locally_reviewed"] = True
        self.manifest["reviewed_control_positions"] = sorted(i + 1 for i in required)
        self.checkpoint()

    def file_content(self, index, text, student):
        name = f"log_{index + 1:03d}_{filename_name(student)}.txt"
        content = (f"Student: {student}\nStudent name source: Moderate page, reviewed locally\n"
                   f"View Log position on Moderate: {index + 1}\n\n{text}")
        if self.trim_menu:
            first, _, rest = log_from_began(text).partition("\n")
            content = (f"{first}\nStudent: {student}\nStudent name source: Moderate page, reviewed locally\n"
                       f"View Log position on Moderate: {index + 1}\n\n{rest}")
        return name, content

    def save(self, index, text, signature):
        page_fingerprint = digest(text)
        repeated = [r for r in self.manifest["saved_logs"] if r["page_text_sha256"] == page_fingerprint]
        if any(r["list_control_sha256"] == signature[index] for r in repeated):
            raise CaptureError("duplicate_log_text_needs_local_review")
        student, source = self.names[index]
        name, content = self.file_content(index, text, student)
        (self.out / name).write_text(content, encoding="utf-8", newline="\n")
        self.manifest["saved_logs"].append({"file": name, "sha256": digest(content),
            "page_text_sha256": page_fingerprint, "student_name": student, "name_source": source,
            "same_page_text_as_another_control": bool(repeated),
            "list_control_sha256": signature[index], "control_position": index + 1})
        if repeated:
            print("Note: this page text matches another capture. Its Moderate control differs; review both files locally.")
        self.checkpoint()
        print(f"Saved {len(self.manifest['saved_logs'])} log(s) locally.")

    def open_control(self, index, signature, verified_controls=None):
        self.check_cancel()
        if verified_controls is None:
            controls, observed = controls_on_page(self.page)
            if observed != signature:
                raise CaptureError("moderate_list_changed")
        else:
            controls = verified_controls
            if control_signature(controls[index]) != signature[index]:
                raise CaptureError("moderate_list_changed")
        original_pages = set(self.page.context.pages)
        controls[index].click()
        self.page.wait_for_timeout(300)
        if set(self.page.context.pages) != original_pages:
            raise CaptureError("new_tab_navigation_not_supported")
        return original_pages

    def confirm_start(self):
        return self.prompt("Type start to open the first log, or Enter to stop: ").strip().lower() == "start"

    def get_readiness_phrase(self):
        return self.prompt("Type that generic readiness phrase here (no student name or answer): ").strip()

    def confirm_first_capture(self):
        return self.prompt("Type checked only if the first text capture is adequate; otherwise Enter to stop: ").strip().lower() == "checked"

    def run(self):
        try:
            controls, signature = controls_on_page(self.page)
            self.manifest["detected_controls"] = len(signature)
            self.checkpoint()
            print(f"Found {len(signature)} visible View Log controls on this page.")
            print("Check this count against the Moderate page, including its current filters and pagination.")
            self.prepare_names(controls)
            if not self.confirm_start():
                raise CaptureError("not_started")
            self.load_reviewed_names()
            baseline = page_text(self.page)
            original_pages = self.open_control(0, signature)
            print("In the browser, wait until the FIRST log and its event rows have fully loaded.")
            print("Choose a generic heading/label present on every loaded log but absent from Moderate.")
            marker = self.get_readiness_phrase()
            if not marker or marker.casefold() in baseline.casefold():
                raise CaptureError("readiness_phrase_is_empty_or_already_on_moderate")
            if set(self.page.context.pages) != original_pages:
                raise CaptureError("new_tab_navigation_not_supported")
            text = wait_log(self.page, marker, baseline, stable_seconds=self.settle, check_cancel=self.check_cancel)
            self.save(0, text, signature)
            print("Open the first named log file (log_001_...) locally. Check its Student header against names.csv.")
            print("Compare its event rows with the browser log.")
            print("Check the final event, all loaded rows, and whether the log itself needs paging/scroll-to-load.")
            print("If more events need paging/expansion/scroll-to-load, stop: this quick mode will miss those events.")
            if not self.confirm_first_capture():
                raise CaptureError("first_capture_not_confirmed")
            self.manifest["first_capture_locally_checked"] = True
            self.checkpoint()
            for index in range(1, len(signature)):
                back_to_list(self.page, signature, check_cancel=self.check_cancel)
                baseline = page_text(self.page)
                original_pages = self.open_control(index, signature)
                text = wait_log(self.page, marker, baseline, stable_seconds=self.settle, check_cancel=self.check_cancel)
                if set(self.page.context.pages) != original_pages:
                    raise CaptureError("new_tab_navigation_not_supported")
                self.save(index, text, signature)
            # Test return navigation even for a one-log page, and end on the list.
            back_to_list(self.page, signature, check_cancel=self.check_cancel)
            self.manifest["page_loop_completed"] = True
            self.checkpoint()
        except BaseException as error:
            self.manifest["error"] = str(error) if isinstance(error, CaptureError) else type(error).__name__
            self.checkpoint()
            raise

    def label_existing(self, source):
        """Make named copies of old captures; match stored control hashes, never row order."""
        try:
            source = Path(source).resolve()
            def source_file(name):
                path = (source / name).resolve()
                if not path.is_relative_to(source) or not path.is_file():
                    raise CaptureError("source_file_outside_export_or_missing")
                return path
            prior = json.loads(source_file("manifest.json").read_text(encoding="utf-8"))
            if prior.get("format") != "canvas_quick_capture_v1" or not prior.get("saved_logs"):
                raise CaptureError("source_manifest_invalid_or_no_saved_logs")
            if any(r.get("student_name") for r in prior["saved_logs"]):
                raise CaptureError("source_export_already_has_names")
            controls, signature = controls_on_page(self.page)
            lookup = {}
            for index, key in enumerate(signature):
                lookup.setdefault(key, []).append(index)
            copies, positions = [], set()
            for entry in prior["saved_logs"]:
                matches = lookup.get(entry.get("list_control_sha256"), [])
                if len(matches) != 1 or matches[0] in positions:
                    raise CaptureError("old_capture_not_uniquely_matched_to_moderate")
                text = source_file(entry["file"]).read_text(encoding="utf-8")
                # Versions <=0.4 hashed the normalized text before Windows newline
                # conversion. read_text restores that normalization for validation.
                if digest(text) != entry.get("sha256"):
                    raise CaptureError("old_capture_integrity_check_failed")
                positions.add(matches[0])
                copies.append((matches[0], text, entry["file"]))
            self.manifest["detected_controls"] = len(signature)
            self.manifest["mode"] = "named_copies_of_existing_exports"
            self.manifest["original_capture_at_utc"] = prior.get("captured_at_utc")
            self.manifest["labelled_at_utc"] = self.manifest["captured_at_utc"]
            self.prepare_names(controls, positions)
            print(f"Matched {len(copies)} existing file(s) by saved control hashes. No logs will be opened.")
            if self.prompt("After reviewing names.csv, type label to write named copies, or Enter to stop: ").strip().lower() != "label":
                raise CaptureError("not_started")
            self.load_reviewed_names(positions)
            _, current = controls_on_page(self.page)
            if current != signature:
                raise CaptureError("moderate_list_changed")
            for index, text, old_filename in copies:
                self.save(index, text, signature)
                self.manifest["saved_logs"][-1]["source_file"] = old_filename
            self.manifest["existing_files_labelled"] = True
            self.checkpoint()
        except BaseException as error:
            self.manifest["error"] = str(error) if isinstance(error, CaptureError) else type(error).__name__
            self.checkpoint()
            raise

    def bundle(self):
        # A ZIP contains only local text captures and manifest; no browser profile.
        with zipfile.ZipFile(self.out / "captured-logs.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(self.out.glob("log_*.txt")):
                bundle.write(path, path.name)
            if (self.out / "names.csv").exists():
                bundle.write(self.out / "names.csv", "names.csv")
            bundle.write(self.out / "manifest.json", "manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTPS Canvas home URL without query parameters")
    parser.add_argument("--profile", required=True, type=Path, help="Dedicated local browser profile")
    parser.add_argument("--out", type=Path, help="New output folder on approved storage")
    parser.add_argument("--diagnose", action="store_true", help="Print counts and static flags only; do not click or export logs")
    parser.add_argument("--label-existing", type=Path, help="Create named copies from an older export folder containing manifest.json")
    parser.add_argument("--channel", choices=("msedge", "chrome", "chromium"), default="msedge")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("Use an HTTPS Canvas home URL without credentials or query parameters.")
    if not args.diagnose and args.out is None:
        parser.error("Capture requires --out. Diagnostic mode does not.")
    if not args.diagnose and args.out.exists():
        parser.error("Output folder already exists. Choose a new folder to preserve existing exports.")
    if args.label_existing and args.diagnose:
        parser.error("Choose diagnostic mode or existing-file labelling, not both.")
    collector = None
    code = 0
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            options = {"headless": False, "accept_downloads": False}
            if args.channel != "chromium":
                options["channel"] = args.channel
            context = playwright.chromium.launch_persistent_context(str(args.profile.resolve()), **options)
            try:
                context.set_default_timeout(30000)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.url, wait_until="domcontentloaded")
                print(f"Quick Capture {VERSION}")
                print("Sign in yourself. Open the completed New Quiz > Moderate. Start on the desired student page.")
                if args.diagnose:
                    print("Diagnostic mode counts controls only. It does not click or export logs.")
                else:
                    print("Student names from Moderate are reviewed locally and included in filenames and file headers.")
                    print("This saves rendered log page text, including any visible names/answers, locally.")
                    print("It processes THIS student page only. It cannot see hidden attempts or unrendered events.")
                input("When the Moderate list is loaded, press Enter here: ")
                if args.diagnose:
                    print("DIAGNOSTIC: counts and static flags only; no logs were clicked or exported.")
                    print(json.dumps(diagnostic_report(context), indent=2))
                    return 0
                page = choose_moderate_page(context)
                collector = QuickCapture(page, args.out)
                if args.label_existing:
                    collector.label_existing(args.label_existing)
                else:
                    collector.run()
            finally:
                context.close()
    except (Exception, KeyboardInterrupt) as error:
        code = 2
        reason = str(error) if isinstance(error, CaptureError) else type(error).__name__
        detail = ("Completed files remain local; inspect the manifest before using them." if collector
                  else "No logs were exported in this run.")
        print(f"Stopped ({reason}). {detail}", file=sys.stderr)
        # Do not print browser exception text: it can contain student data or URLs.
    finally:
        if collector:
            try:
                collector.bundle()
            except OSError:
                code = 2
                print("Could not create ZIP. Any text captures remain in the output folder.", file=sys.stderr)
    if code == 0:
        if args.label_existing:
            print("Named copies written to the new output folder. Original export files were preserved.")
        else:
            print("Finished this page's View Log controls. Review every text capture for completeness.")
            print("For another Moderate page, run again with a new output folder and navigate there yourself.")
    return code


if __name__ == "__main__":
    sys.exit(main())
