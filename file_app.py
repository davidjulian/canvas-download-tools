"""Separate local SpeedGrader interface, adapted from the quiz log app."""
import argparse
import json
from pathlib import Path
import secrets
import sys
import threading
import webbrowser

from canvas_helpers import UIError, validate_url
from desktop import browse_folder, open_path
from download_destination import run_folder
from http_ui import LocalServer
from learned_download import LearnedBatch, file_type, safe_failure
from native_edge import NativeEdge
from speedgrader_download import (DownloadError, STUDENTS, local_root, point_student_picker,
                                  speedgrader_pages, validate_locations)
from student_picker_cache import remember_picker, restore_picker
from windows_paths import downloads_directory

VERSION = "0.1.0"
TERMINAL = {"idle", "complete", "stopped", "error"}
MESSAGES = {
    "selected_browser_not_found": "Install the selected Chrome or Edge browser, or choose the other browser.",
    "close_comparison_edge_window_then_retry": "Close the previous dedicated Canvas window, then open Canvas again.",
    "normal_edge_local_connection_unavailable": "The dedicated browser connection did not become available. Close it and try again.",
    "keep_one_speedgrader_tab_open": "Keep one SpeedGrader tab open in the dedicated Canvas window, wait for its submission to load, then try again.",
    "stopped_by_user": "Stopped. Completed downloads are preserved.",
    "start_with_a_real_student": "Start with the first real student, rather than Test Student.",
    "next_submission_did_not_become_ready": "The next submission did not become ready. Completed files are preserved; check the Canvas window.",
    "file_download_not_received": "No file download arrived. Click the submitted filename that downloads the actual file, then start a new run.",
    "unexpected_download_type": "A download did not match the chosen file type. Check the filter or choose All file types.",
    "local_access_denied": "The app could not write to a local folder. Check its permissions or choose another folder.",
}


class WebBatch(LearnedBatch):
    def __init__(self, page, out, app, **options):
        self.app = app
        super().__init__(page, out, progress=self.report, check_cancel=app.check_cancel,
                         prompt=lambda _: app.approve_first(page), open_folder=False, **options)

    def learn_click(self):
        self.app.update(phase="learning")
        return super().learn_click()

    def report(self, state):
        records = state.get("records", [])
        saved = [r for r in records if r.get("status") == "saved"]
        skipped = sum(r.get("status") in {"no_upload_displayed", "test_student_skipped"}
                      for r in state.get("students", []))
        values = {"saved": len(saved), "skipped": skipped, "visited": len(state.get("students", []))}
        if saved:
            path = Path(saved[0]["file"])
            values["first_file"] = str(path if path.is_absolute() else self.private / path)
        if state.get("status") == "running":
            values["phase"] = "running"
        self.app.update(**values)


class LocalApp:
    def __init__(self, root=None):
        self.root = Path(root) if root else local_root() / "SharedApp"
        self.condition = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.worker = None
        self.pending = None
        self.dialog_lock = threading.Lock()
        self.quitting = False
        self.state = {"version": VERSION, "phase": "idle", "saved": 0, "skipped": 0,
                      "visited": 0, "output": "", "first_file": "", "error": "", "error_code": "",
                      "browser_open": False, "run_id": "", "canvas_url": "", "extension": "",
                      "output_base": "", "browser": "chrome" if sys.platform == "darwin" else "msedge"}
        try:
            self.state["output_base"] = str(downloads_directory())
        except OSError:
            pass
        try:
            settings = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
            for key in ("canvas_url", "output_base", "extension", "browser"):
                if isinstance(settings.get(key), str):
                    self.state[key] = settings[key]
        except (OSError, ValueError):
            pass

    def snapshot(self):
        with self.condition:
            return dict(self.state)

    def update(self, **values):
        with self.condition:
            self.state.update(values)

    def check_cancel(self):
        if self.stop_event.is_set():
            raise DownloadError("stopped_by_user")

    def shutdown(self):
        self.quitting = True
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()

    def wait_for_action(self, phase, page=None, **values):
        with self.condition:
            self.pending = None
            self.state.update(phase=phase, **values)
        while True:
            self.check_cancel()
            with self.condition:
                if self.pending:
                    self.pending = None
                    return
                if page is None:
                    self.condition.wait(.1)
            if page is not None:
                page.wait_for_timeout(100)

    def approve_first(self, page):
        self.wait_for_action("checking", page)
        return "run"

    def command(self, action, payload):
        with self.condition:
            if action == "launch":
                if self.quitting:
                    raise UIError("The app is closing. Reopen it using Start App.")
                if self.worker and self.worker.is_alive():
                    raise UIError("Finish or stop the current run before opening another.")
                url = validate_url(payload.get("url"))
                channel = payload.get("browser", self.state["browser"])
                if channel not in {"chrome", "msedge"}:
                    raise UIError("Choose Google Chrome or Microsoft Edge.")
                extension = payload.get("extension", "")
                if not isinstance(extension, str):
                    raise UIError("Enter a file extension, or leave All file types selected.")
                try:
                    extension = file_type(extension)
                except DownloadError:
                    raise UIError("Use one extension such as pdf, docx or model, or leave it blank for all types.") from None
                folder = payload.get("output")
                if not isinstance(folder, str) or not folder.strip() or len(folder) > 3000:
                    raise UIError("Choose an existing local download folder.")
                profile = self.root / "BrowserProfiles" / channel
                try:
                    files = run_folder(Path(folder).expanduser(), profile)
                    out, _ = validate_locations(self.root / "private-work" / ("run-" + secrets.token_hex(8)), profile)
                except (OSError, DownloadError):
                    raise UIError("Choose an existing local folder outside OneDrive, the app and its browser profile.") from None
                relearn = payload.get("relearn", False)
                if not isinstance(relearn, bool):
                    raise UIError("Choose whether to identify the student name again.")
                self.stop_event.clear()
                self.state.update(phase="opening", saved=0, skipped=0, visited=0, output=str(files),
                                  first_file="", error="", error_code="", browser_open=False,
                                  canvas_url=url, output_base=str(files.parent), browser=channel,
                                  extension=extension or "", run_id=secrets.token_hex(8))
                self.root.mkdir(parents=True, exist_ok=True)
                settings = {k: self.state[k] for k in ("canvas_url", "output_base", "extension", "browser")}
                (self.root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
                self.worker = threading.Thread(target=self.run, args=(url, profile, out, files, channel, extension, relearn), daemon=False)
                self.worker.start()
                return {}
            if action == "stop":
                if self.state["phase"] not in TERMINAL:
                    self.stop_event.set()
                    self.state["phase"] = "stopping"
                    self.condition.notify_all()
                return {}
            expected = {"ready": "navigate", "checked": "checking", "retry": "picker_retry"}
            if (action not in expected or self.state["phase"] != expected[action] or self.pending is not None
                    or payload.get("run_id") != self.state["run_id"]):
                raise UIError("That step is no longer waiting. Use the current buttons.")
            if action == "checked" and payload.get("reviewed") is not True:
                raise UIError("Check the first-file review box before collecting the rest.")
            self.pending = action
            self.state["phase"] = "connecting" if action == "ready" else "verifying"
            self.condition.notify_all()
            return {}

    def run(self, url, profile, out, files, channel, extension, relearn):
        native = NativeEdge(profile, channel=channel)
        try:
            self.check_cancel()
            native.start(url)
            self.update(browser_open=True)
            self.wait_for_action("navigate")
            from playwright.sync_api import sync_playwright
            with sync_playwright() as driver:
                try:
                    context = native.connect(driver, out.parent / ("temporary-" + secrets.token_hex(8)))
                    while True:
                        self.check_cancel()
                        pages = speedgrader_pages(context, url)
                        if len(pages) == 1:
                            break
                        # Pump the existing context while the instructor fixes tabs.
                        pump = context.pages[0] if context.pages else None
                        if pump is None:
                            raise DownloadError("browser_or_tab_closed")
                        self.wait_for_action("navigate", pump, error=MESSAGES["keep_one_speedgrader_tab_open"])
                    page = pages[0]
                    self.update(error="")
                    if page.locator(STUDENTS).count() == 0:
                        cache = self.root / ("student-picker-" + channel + ".json")
                        if relearn or not restore_picker(page, cache):
                            while True:
                                self.update(phase="pick_student", error="")
                                try:
                                    point_student_picker(page, check_cancel=self.check_cancel)
                                    break
                                except DownloadError as error:
                                    if str(error) == "stopped_by_user":
                                        raise
                                    self.wait_for_action("picker_retry", page, error="The click did not identify the current student name. Try again without changing students.")
                            try:
                                remember_picker(page, cache)
                            except OSError:
                                pass
                    batch = WebBatch(page, out, self, extension=extension, files_dir=files)
                    batch.run()
                    self.update(phase="complete", error="")
                finally:
                    native.close()
        except Exception as error:
            code = safe_failure(error)
            self.update(phase="stopped" if code == "stopped_by_user" else "error", error_code=code,
                        error=MESSAGES.get(code, "Collection stopped. Completed files are preserved. Check Canvas and begin a new run. You may report the error code without student data."))
        finally:
            native.close()
            self.update(browser_open=False)

    def browse(self):
        if not self.dialog_lock.acquire(blocking=False):
            raise UIError("A folder chooser is already open. Check your desktop.")
        try:
            return {"folder": browse_folder()}
        except Exception:
            raise UIError("The folder chooser could not open. Paste an existing folder path instead.") from None
        finally:
            self.dialog_lock.release()

    def open_local(self, first=False):
        state = self.snapshot()
        path = Path(state["first_file"] if first else state["output"])
        if not str(state["first_file"] if first else state["output"]) or not path.exists():
            raise UIError("This item will be available after the first file is saved.")
        open_path(path)
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    app = LocalApp()
    server = LocalServer(app)
    if args.no_open:
        print(server.url)
    else:
        webbrowser.open(server.url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        if app.worker:
            app.worker.join(timeout=45)
        server.server_close()


if __name__ == "__main__":
    main()
