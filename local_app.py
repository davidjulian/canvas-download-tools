"""Canvas Download Tools: one local app with two independent collectors."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser

from canvas_helpers import UIError, validate_url
from desktop import browse_folder, open_path
from download_destination import run_folder
import file_app
import log_app
from http_ui import LocalServer
from windows_paths import downloads_directory

VERSION = "0.2.1"
MODES = {"logs", "files"}
TERMINAL = {"idle", "complete", "stopped", "error"}


def app_folder():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/CanvasDownloadTools"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "CanvasDownloadTools"


class CombinedApp:
    combined = True

    def __init__(self, root=None):
        self.root = Path(root) if root else app_folder()
        self.lock = threading.RLock()
        self.dialog_lock = threading.Lock()
        self.quitting = False
        self.preferences = {"canvas_url": "", "output_base": "",
                            "browser": "chrome" if sys.platform == "darwin" else "msedge"}
        try:
            self.preferences["output_base"] = str(downloads_directory())
        except OSError:
            pass
        try:
            saved = json.loads((self.root / "preferences.json").read_text(encoding="utf-8"))
            for key in self.preferences:
                if isinstance(saved.get(key), str):
                    self.preferences[key] = saved[key]
        except (OSError, ValueError):
            pass
        if self.preferences["browser"] not in {"chrome", "msedge"}:
            self.preferences["browser"] = "chrome" if sys.platform == "darwin" else "msedge"
        self.files = file_app.LocalApp(self.root / "SubmittedFiles")
        self.logs = log_app.LocalApp(profile=self.profile("logs", self.preferences["browser"]))
        self._apply_preferences()

    def profile(self, mode, channel):
        return self.root / "BrowserProfiles" / mode / channel

    def tool(self, mode):
        if mode not in MODES:
            raise UIError("Choose Quiz activity logs or Submitted files.")
        return self.logs if mode == "logs" else self.files

    def active_mode(self):
        for mode in ("logs", "files"):
            if self.tool(mode).snapshot()["phase"] not in TERMINAL:
                return mode
        return ""

    def _apply_preferences(self):
        values = {"canvas_url": self.preferences["canvas_url"], "output_base": self.preferences["output_base"]}
        if self.logs.snapshot()["phase"] in TERMINAL:
            self.logs.update(**values, default_browser=self.preferences["browser"])
        if self.files.snapshot()["phase"] in TERMINAL:
            self.files.update(**values, browser=self.preferences["browser"])

    def snapshot(self):
        with self.lock:
            tools = {}
            for mode in ("logs", "files"):
                state = self.tool(mode).snapshot()
                tools[mode] = {key: state.get(key, 0) for key in ("phase", "saved", "browser_open")}
            return {"version": VERSION, "active_mode": self.active_mode(),
                    "preferences": dict(self.preferences),
                    "tools": tools}

    def snapshot_for(self, mode):
        with self.lock:
            self._apply_preferences()
            state = self.tool(mode).snapshot()
            state["app_version"] = VERSION
            return state

    def set_preferences(self, payload):
        with self.lock:
            if self.quitting:
                raise UIError("The app is closing. Reopen it using Start App.")
            if self.active_mode():
                raise UIError("Finish or stop the current collection before changing shared settings.")
            self._save_preferences(payload)
            self._apply_preferences()
        return {}

    def _save_preferences(self, payload):
        values = dict(self.preferences)
        for key in values:
            if key in payload:
                if not isinstance(payload[key], str):
                    raise UIError("Use text for your Canvas address and folder settings.")
                values[key] = payload[key].strip()
        if values["canvas_url"]:
            validate_url(values["canvas_url"])
        if values["browser"] not in {"chrome", "msedge"}:
            raise UIError("Choose Google Chrome or Microsoft Edge.")
        if len(values["output_base"]) > 3000:
            raise UIError("Choose an existing local output folder.")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / "preferences.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(values), encoding="utf-8")
        temporary.replace(target)
        self.preferences = values

    def _close_idle_other(self, mode):
        other = "files" if mode == "logs" else "logs"
        engine = self.tool(other)
        snapshot = engine.snapshot()
        if snapshot["phase"] not in TERMINAL:
            raise UIError("Finish or stop the other collection before switching tools.")
        worker = engine.worker
        if worker and worker.is_alive():
            if not snapshot.get("browser_open"):
                worker.join(timeout=5)
            else:
                engine.shutdown()
                worker.join(timeout=45)
            if worker.is_alive():
                raise UIError("The other Canvas window is still closing. Wait, then switch tools again.")
            # Reset the quit flag on a fresh engine while preserving results
            # and the earlier label check for another collection in that mode.
            if other == "logs":
                replacement = log_app.LocalApp(profile=engine.profile)
                replacement.update(**dict(snapshot, browser_open=False))
                self.logs = replacement
            else:
                replacement = file_app.LocalApp(self.root / "SubmittedFiles")
                replacement.update(**dict(snapshot, browser_open=False))
                self.files = replacement

    def select_mode(self, mode):
        with self.lock:
            self.tool(mode)
            if self.quitting:
                raise UIError("The app is closing. Reopen it using Start App.")
            active = self.active_mode()
            if active and active != mode:
                raise UIError("Finish or stop the current collection before switching tools.")
            self._close_idle_other(mode)
            self._apply_preferences()
        return {}

    def command_for(self, mode, action, payload):
        with self.lock:
            if self.quitting:
                raise UIError("The app is closing. Reopen it using Start App.")
            engine = self.tool(mode)
            if action == "launch":
                active = self.active_mode()
                if active and active != mode:
                    raise UIError("Only one collection can run at a time. Finish or stop the current collection first.")
                url = validate_url(payload.get("url"))
                channel = payload.get("browser", self.preferences["browser"])
                if channel not in {"chrome", "msedge"}:
                    raise UIError("Choose Google Chrome or Microsoft Edge.")
                folder = payload.get("output")
                if not isinstance(folder, str) or not folder.strip():
                    raise UIError("Choose an existing local output folder.")
                try:
                    run_folder(Path(folder).expanduser(), self.profile(mode, channel))
                except Exception:
                    raise UIError("Choose an existing local folder outside OneDrive, the app and browser profiles.") from None
                self._close_idle_other(mode)
                if mode == "logs":
                    engine.profile = self.profile(mode, channel)
                self._save_preferences({"canvas_url": url, "output_base": folder, "browser": channel})
                try:
                    result = engine.command(action, payload)
                except log_app.UIError as error:
                    raise UIError(str(error)) from None
                return result
            try:
                return engine.command(action, payload)
            except log_app.UIError as error:
                raise UIError(str(error)) from None

    def browse(self):
        if not self.dialog_lock.acquire(blocking=False):
            raise UIError("A folder chooser is already open. Check your desktop.")
        try:
            return {"folder": browse_folder()}
        except Exception:
            raise UIError("The folder chooser could not open. Paste an existing local path instead.") from None
        finally:
            self.dialog_lock.release()

    def open_local_for(self, mode, **options):
        engine = self.tool(mode)
        if mode == "files" and (options.get("key") or options.get("summary")):
            raise UIError("That option belongs to Quiz activity logs.")
        try:
            return engine.open_local(**options)
        except log_app.UIError as error:
            raise UIError(str(error)) from None

    def shutdown(self):
        with self.lock:
            self.quitting = True
            self.logs.shutdown()
            self.files.shutdown()

    def join(self):
        for engine in (self.logs, self.files):
            if engine.worker:
                engine.worker.join(timeout=45)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    app = CombinedApp()
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
        app.join()
        server.server_close()


if __name__ == "__main__":
    main()
