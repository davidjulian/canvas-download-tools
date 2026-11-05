"""Learn one file click locally, then download and advance through students."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

from native_edge import NativeEdge, NativeEdgeError, parse_endpoint
from canvas_helpers import csv_name, visible_frame
from windows_paths import physical_directory
from download_destination import choose_download_directory
from student_picker_cache import remember_picker, restore_picker
from speedgrader_download import (DownloadError, FileBatch, MAX_STUDENTS, STUDENTS,
    assignment_key, bound_frame, frame_path, frame_signature, confirm_student_picker, live_choice, live_prompt, local_root, navigation_button,
    modern_student_record, normalize_name, speedgrader_pages, student_url_identity,
    validate_locations)


CLICK_SCRIPT = r"""key => {
  function path(element) {
    const steps=[];
    for(let node=element;node && node.nodeType===1;node=node.parentElement){
      let position=1;
      for(let sibling=node.previousElementSibling;sibling;sibling=sibling.previousElementSibling)
        if(sibling.localName===node.localName)position++;
      steps.unshift(node.localName+':nth-of-type('+position+')');
      if(node.localName==='body')break;
    }
    return steps.join(' > ');
  }
  window[key]=null;
  document.addEventListener('click', event => {
    const target=event.composedPath().find(node => node instanceof Element);
    if(target)window[key]={selector:path(target),time:Date.now()};
  },{capture:true});
}"""


def student_reader(page):
    if page.locator(STUDENTS).count() == 1:
        name = normalize_name(page.locator(STUDENTS).locator("option:checked").inner_text())
        return student_url_identity(page), name
    return modern_student_record(page)


def is_test_student(name):
    return normalize_name(name).casefold() in {"test student", "student, test"}


def download_event(page, action=None, timeout=120000, check_cancel=None):
    """Capture downloads on the submission page or a popup it opens."""
    received, watched = [], []
    def receive(download):
        if received:
            download.cancel()
        else:
            received.append(download)
    def watch(candidate):
        watched.append(candidate)
        candidate.on("download", receive)
    context = page.context
    context.on("page", watch)
    for candidate in context.pages:
        watch(candidate)
    deadline = time.monotonic() + timeout / 1000
    try:
        if check_cancel:
            check_cancel()
        if action:
            action()
        while not received:
            if check_cancel:
                check_cancel()
            if time.monotonic() >= deadline:
                raise DownloadError("file_download_not_received")
            page.wait_for_timeout(100)
        return received[0]
    finally:
        context.remove_listener("page", watch)
        for candidate in watched:
            candidate.remove_listener("download", receive)


def file_context(item, extension):
    return item.evaluate(r"""(element, extension) => {
      for(let node=element,depth=0;node && depth<6;depth++,node=node.parentElement){
        const text=(node.innerText||node.textContent||'').trim();
        if(text && text.length<600 && (!extension || text.toLowerCase().includes(extension.toLowerCase())))return true;
      }
      return false;
    }""", extension)


def file_type(value):
    value = value.strip().lower()
    if value in ("", "all", "any", "*"):
        return None
    value = "." + value.lstrip(".")
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", value):
        raise DownloadError("invalid_file_extension")
    return value


def write_collection_results(private, state):
    """Identified results stay beside the private name key, never in source."""
    records = {record["record_id"]: record for record in state.get("records", [])}
    target = Path(private) / "collection_results.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["student_name", "status", "saved_file", "record_id"])
        for row in state.get("students", []):
            record = records.get(row["record_id"], {})
            status = row.get("status", "unverified")
            if status == "started":
                status = record.get("status", "unverified")
            writer.writerow([csv_name(row["student_name"]), status,
                             csv_name(record.get("file", "")), row["record_id"]])
    return target


class LearnedBatch(FileBatch):
    def __init__(self, page, out, *, extension=None, prompt=input, timeout=60000, quiet=1.5, status_path=None, files_dir=None,
                 check_cancel=None, progress=None, open_folder=True):
        self.status_path = Path(status_path) if status_path else None
        self.check_cancel = check_cancel or (lambda: None)
        self.progress = progress
        self.open_folder = open_folder
        super().__init__(page, out, extension=extension, prompt=prompt, timeout=timeout, quiet=quiet,
                         student_reader=lambda: student_reader(page), files_dir=files_dir)
        self.active = self.roster[0]
        self.rows = []
        self.learned = None
        self.learned_frame = None
        self.learned_source = None
        self.pending = set()
        self.requests_seen = 0
        self.source_requests_seen = 0
        self.quiz_frame = None
        self.last_network = time.monotonic()
        self.page.on("request", self.request_started)
        self.page.on("requestfinished", self.request_done)
        self.page.on("requestfailed", self.request_done)
        self.state.update(workflow="learn_download_next", students=self.rows,
                          automatic_click_verified=False, all_students_verified=False)

    def checkpoint(self):
        super().checkpoint()
        if self.progress:
            self.progress(self.state)
        if self.status_path:
            write_status(self.status_path, self.state.get("status", "prepared"),
                         saved=sum(record.get("status") == "saved" for record in self.records),
                         error=self.state.get("error", ""))

    def request_started(self, request):
        if request.resource_type in ("document", "xhr", "fetch"):
            self.pending.add(request)
            self.requests_seen += 1
            try:
                if request.frame == self.quiz_frame:
                    self.source_requests_seen += 1
            except Exception:
                pass
            self.last_network = time.monotonic()

    def request_done(self, request):
        if request in self.pending:
            self.pending.discard(request)
            self.last_network = time.monotonic()

    def verify_student(self, expected):
        self.check_cancel()
        if assignment_key(self.page) != self.assignment:
            raise DownloadError("assignment_changed")
        current = student_reader(self.page)
        if current[0] != expected:
            raise DownloadError("selected_student_changed")
        if current != self.active:
            raise DownloadError("selected_student_name_changed")

    def add_student(self):
        self.active = student_reader(self.page)
        row = {"record_id": "R-" + secrets.token_hex(8), "student_id": self.active[0],
               "student_name": self.active[1], "status": "started"}
        self.rows.append(row)
        with (self.private / "name_key.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["record_id", "student_id", "student_name"])
            writer.writerows((r["record_id"], csv_name(r["student_id"]), csv_name(r["student_name"])) for r in self.rows)
        self.checkpoint()
        return row

    def learn_click(self):
        key = "__local_download_training_" + secrets.token_hex(8)
        frames = []
        for frame in self.page.frames:
            if visible_frame(frame):
                frame.evaluate(CLICK_SCRIPT, key)
                frames.append(frame)
        print("Click ONE submitted filename in SpeedGrader. The app is listening locally.", flush=True)
        self.page.bring_to_front()
        download = download_event(self.page, timeout=120000, check_cancel=self.check_cancel)
        observed = []
        for frame in frames:
            try:
                click = frame.evaluate("key => window[key]", key)
                if click and time.time() * 1000 - click["time"] < 30000:
                    item = frame.locator(click["selector"])
                    if item.count() == 1 and item.is_visible() and file_context(item, self.extension):
                        observed.append((click, frame))
            except Exception:
                pass
        if len(observed) != 1:
            download.cancel()
            raise DownloadError("file_click_not_learned")
        self.learned = observed[0][0]["selector"]
        self.quiz_frame = observed[0][1]
        self.learned_frame = frame_path(self.quiz_frame)
        self.learned_source = frame_signature(self.quiz_frame)
        self.state["learned_file_selector"] = self.learned
        return download

    def locate_file(self):
        matches = []
        frames = [frame for frame in self.page.frames if visible_frame(frame)]
        if self.extension is None:
            # All file types broadens filename matching, not the owning frame.
            owner = bound_frame(self.page, self.learned_frame)
            frames = [owner] if owner and visible_frame(owner) and frame_signature(owner) == self.learned_source else []
        for frame in frames:
            item = frame.locator(self.learned)
            if item.count() == 1 and item.is_visible() and file_context(item, self.extension):
                matches.append((frame, item))
        if len(matches) == 1:
            return matches[0]
        # Different students may add wrappers or extra answer elements. The
        # fallback still requires exactly one visible filename of this type.
        matches = []
        suffix = re.escape(self.extension) if self.extension else r"\.[^\s.]{1,16}"
        pattern = re.compile(r"^[^\r\n]{1,240}" + suffix + r"\s*$", re.I)
        for frame in frames:
            controls = frame.get_by_text(pattern)
            for index in range(controls.count()):
                item = controls.nth(index)
                if item.is_visible():
                    matches.append((frame, item))
        if len(matches) != 1:
            raise DownloadError("learned_file_control_missing" if not matches else "learned_file_control_ambiguous")
        return matches[0]

    def fingerprint(self):
        try:
            frame, item = self.locate_file()
        except DownloadError as error:
            if str(error) != "learned_file_control_missing" or not self.no_submission():
                raise
            frame, item = self.page.main_frame, self.page.locator("body")
        text = frame.locator("body").inner_text(timeout=3000)
        return hashlib.sha256(text.encode()).hexdigest(), item.element_handle(), frame

    def no_submission(self):
        if is_test_student(student_reader(self.page)[1]):
            return True
        pattern = re.compile(r"^\s*(?:no submissions?\b[^\r\n]*|no (?:file )?uploads?(?:ed)?[.!]?|"
                             r"no answer(?: provided)?[.!]?|this question was not answered[.!]?)\s*$", re.I)
        for frame in self.page.frames:
            if visible_frame(frame):
                labels = frame.get_by_text(pattern)
                if any(labels.nth(index).is_visible() for index in range(labels.count())):
                    return True
        return False

    def verify_automatic_click(self, row):
        self.verify_student(row["student_id"])
        _, target = self.locate_file()
        download = download_event(self.page, lambda: target.click(timeout=15000), timeout=self.timeout, check_cancel=self.check_cancel)
        temporary = self.private / "incoming" / (secrets.token_hex(8) + ".probe")
        try:
            download.save_as(temporary)
            self.verify_student(row["student_id"])
            expected = self.records[0]["sha256"]
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise DownloadError("automatic_click_download_did_not_match_first_file")
            self.state["automatic_click_verified"] = True
        finally:
            if temporary.exists():
                temporary.unlink()
            download.delete()

    def wait_next(self, previous, before, network_before):
        deadline = time.monotonic() + self.timeout / 1000
        last, since = None, time.monotonic()
        while time.monotonic() < deadline:
            self.check_cancel()
            try:
                current = student_reader(self.page)
                if current[0] != previous:
                    try:
                        frame, item = self.locate_file()
                    except DownloadError as error:
                        if str(error) != "learned_file_control_missing" or not self.no_submission():
                            raise
                        frame, item = self.page.main_frame, self.page.locator("body")
                    fingerprint = hashlib.sha256(frame.locator("body").inner_text(timeout=3000).encode()).hexdigest()
                    try:
                        detached = not before[1].evaluate("e => e.isConnected")
                    except Exception:
                        detached = True
                    transition = (fingerprint != before[0] or detached or frame != before[2]
                                  or self.source_requests_seen > network_before)
                    token = (current, fingerprint)
                    if token != last:
                        last, since = token, time.monotonic()
                    progress = frame.get_by_role("progressbar")
                    busy = any(progress.nth(index).is_visible() for index in range(progress.count()))
                    if (transition and not busy and not self.pending and time.monotonic() - since >= self.quiet
                            and time.monotonic() - self.last_network >= self.quiet):
                        self.quiz_frame = frame
                        return current
            except Exception:
                last, since = None, time.monotonic()
            self.page.wait_for_timeout(100)
        raise DownloadError("next_submission_did_not_become_ready")

    def run(self, limit=MAX_STUDENTS):
        try:
            row = self.add_student()
            if is_test_student(row["student_name"]):
                raise DownloadError("start_with_a_real_student")
            self.state["status"] = "learning_first_file"
            self.checkpoint()
            download = self.learn_click()
            self.save_received(row, 0, download)
            row["status"] = "saved"
            self.state["status"] = "waiting_first_file_approval"
            self.checkpoint()
            print("First file saved. Download folder: " + str(physical_directory(self.files_dir)), flush=True)
            if self.open_folder and os.name == "nt":
                try:
                    os.startfile(str(physical_directory(self.files_dir)))
                except OSError:
                    print("The saved file is intact, but its folder could not open automatically. "
                          "Use the output folder printed above.", flush=True)
            self.confirm("Check that first file and its name locally. Type run to approve automatic downloads "
                         "through the remaining students, or stop: ", "run")
            self.state["first_file_locally_checked"] = True
            self.verify_automatic_click(row)
            self.state["status"] = "running"
            self.checkpoint()
            visited = {row["student_id"]}
            first = row["student_id"]
            while len(visited) < limit:
                self.verify_student(row["student_id"])
                button = navigation_button(self.page, "next")
                if not button.is_enabled() or button.get_attribute("aria-disabled") == "true":
                    self.state.update(status="current_student_list_complete", end_reason="next_disabled")
                    return
                before = self.fingerprint()
                network_before = self.source_requests_seen
                button.click(timeout=15000)
                current = self.wait_next(row["student_id"], before, network_before)
                if current[0] in visited:
                    if current[0] != first:
                        raise DownloadError("student_navigation_loop")
                    self.state.update(status="current_student_list_complete", end_reason="returned_to_first")
                    return
                row = self.add_student()
                visited.add(row["student_id"])
                if is_test_student(row["student_name"]):
                    row["status"] = "test_student_skipped"
                    self.checkpoint()
                    print("Skipped the Test Student entry.", flush=True)
                    continue
                try:
                    _, target = self.locate_file()
                except DownloadError as error:
                    if str(error) != "learned_file_control_missing" or not self.no_submission():
                        raise
                    row["status"] = "no_upload_displayed"
                    self.checkpoint()
                    continue
                self.verify_student(row["student_id"])
                download = download_event(self.page, lambda: target.click(timeout=15000), timeout=self.timeout, check_cancel=self.check_cancel)
                self.save_received(row, 0, download)
                row["status"] = "saved"
                self.checkpoint()
                print(f"Saved {len(self.records)} student file(s).", flush=True)
            self.state.update(status="requested_limit_complete", end_reason="limit")
        except BaseException as error:
            category = str(error) if isinstance(error, DownloadError) else (
                "stopped_by_user" if isinstance(error, (KeyboardInterrupt, EOFError)) else "local_browser_or_download_error")
            self.state.update(status="stopped", error=category)
            for record in self.records:
                if record["status"] == "started":
                    record.update(status="failed", error=category)
            raise
        finally:
            self.checkpoint()
            try:
                write_collection_results(self.private, self.state)
            except OSError:
                print("The results CSV could not be saved; the private manifest remains available.", flush=True)
            self.page.remove_listener("request", self.request_started)
            self.page.remove_listener("requestfinished", self.request_done)
            self.page.remove_listener("requestfailed", self.request_done)


def write_status(path, status, *, saved=0, error=""):
    """Local progress only: never include student labels, IDs, URLs or paths."""
    target = Path(path)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"status": status, "saved_files": saved, "error": error}), encoding="utf-8")
    temporary.replace(target)


def safe_failure(error):
    if isinstance(error, (DownloadError, NativeEdgeError)):
        return str(error)
    if isinstance(error, (KeyboardInterrupt, EOFError)):
        return "stopped_by_user"
    if isinstance(error, FileNotFoundError):
        return "required_local_file_not_found"
    if isinstance(error, PermissionError):
        return "local_access_denied"
    if isinstance(error, ImportError):
        return "browser_dependency_unavailable"
    message = str(error).casefold()
    for term, category in (("econnrefused", "browser_connection_unavailable"),
                           ("timed out", "browser_operation_timed_out"),
                           ("timeout", "browser_operation_timed_out"),
                           ("has been closed", "browser_or_tab_closed")):
        if term in message:
            return category
    return "local_browser_or_download_error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=MAX_STUDENTS)
    parser.add_argument("--extension", help="File extension to accept, or all (default: ask at startup)")
    parser.add_argument("--download-dir", help="Parent folder for this run's files (default: offer Downloads or a folder picker)")
    parser.add_argument("--relearn-picker", action="store_true", help="Identify the student name control again")
    parser.add_argument("--attach", action="store_true", help="Use an existing dedicated pilot instead of opening Edge")
    args = parser.parse_args()
    browser = None
    native = None
    status_path = None
    stage = "validate_storage"
    print("Learn and Download 1.7", flush=True)
    try:
        if not 1 <= args.limit <= MAX_STUDENTS:
            raise DownloadError("invalid_student_limit")
        root = local_root()
        root.mkdir(parents=True, exist_ok=True)
        status_path = root / "learned_status.json"
        write_status(status_path, stage)
        out, profile = validate_locations(root / "private-work" / ("learned-run-" + secrets.token_hex(8)),
                                          root / "BrowserCheckProfile")
        if args.attach:
            stage = "read_existing_browser_endpoint"
            endpoint = parse_endpoint((profile / "DevToolsActivePort").read_text(encoding="utf-8"))
        else:
            stage = "open_normal_edge"
            write_status(status_path, stage)
            native = NativeEdge(profile)
            native.start("https://ufl.instructure.com/")
            stage = "waiting_for_manual_navigation"
            write_status(status_path, stage)
            print("Edge is open. Sign in, navigate to SpeedGrader, and select the first student.")
            print("Use the latest submission if Submission to view offers earlier attempts.")
            print("Wait for that student's submission to appear before activating downloads.")
            if input("When ready, type start here (anything else stops): ").strip().lower() != "start":
                raise DownloadError("stopped_by_user")
        stage = "choose_file_type"
        extension = file_type(args.extension if args.extension is not None else input(
            "File type: press Enter for all types, or enter an extension such as pdf, docx or model: "))
        print("File type filter: " + (extension or "all types") + ". One learned file per student.", flush=True)
        stage = "choose_download_folder"
        files_dir = choose_download_directory(profile, supplied=args.download_dir, prompt=input)
        stage = "start_browser_connection"
        from playwright.sync_api import sync_playwright
        with sync_playwright() as controller:
            incoming = out.parent / ("temporary-" + secrets.token_hex(8))
            incoming.mkdir(parents=True)
            if native:
                context = native.connect(controller, incoming)
                browser = native.browser
            else:
                browser = controller.chromium.connect_over_cdp(endpoint, artifacts_dir=str(incoming), is_local=True, timeout=10000)
            try:
                stage = "find_speedgrader_tab"
                if len(browser.contexts) != 1:
                    raise DownloadError("pilot_browser_context_not_unique")
                context = browser.contexts[0]
                pages = speedgrader_pages(context, "https://ufl.instructure.com/")
                if len(pages) != 1:
                    raise DownloadError("keep_one_speedgrader_tab_open")
                page = pages[0]
                prompt = lambda message: live_prompt(context, message)
                stage = "identify_student_name_control"
                if page.locator(STUDENTS).count() == 0:
                    cache = root / "student_picker_layout.json"
                    if not args.relearn_picker and restore_picker(page, cache):
                        print("Remembered student name control recognized. Skipping picker setup.", flush=True)
                    else:
                        write_status(status_path, "waiting_student_name_control")
                        confirm_student_picker(page, prompt,
                                               choice=lambda message: live_choice(context, message))
                        try:
                            remember_picker(page, cache)
                        except OSError:
                            print("The name control is ready, but its location could not be remembered.", flush=True)
                stage = "learn_and_run_download_loop"
                LearnedBatch(page, out, extension=extension, files_dir=files_dir, prompt=prompt, status_path=status_path).run(limit=args.limit)
                print("Automatic pass finished. Review the private manifest and saved files.")
            finally:
                if native:
                    native.close()
                else:
                    # Explicit attach mode preserves the existing pilot window.
                    browser.close()
        return 0
    except (Exception, KeyboardInterrupt) as error:
        reason = safe_failure(error)
        if status_path:
            try:
                if status_path.exists():
                    previous = json.loads(status_path.read_text(encoding="utf-8"))
                    write_status(status_path, "stopped", saved=previous.get("saved_files", 0), error=stage + ":" + reason)
            except OSError:
                pass
        if reason == "close_comparison_edge_window_then_retry":
            print("Close the earlier dedicated pilot Edge window, then reopen Learn and Download.", file=sys.stderr)
        print("Stopped at " + stage + " (" + reason + "). Completed files remain local.", file=sys.stderr)
        return 2
    finally:
        if native:
            native.close()


if __name__ == "__main__":
    raise SystemExit(main())
