"""Platform boundaries, with invented paths and mocked native UI processes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import log_app as web


class PlatformTests(unittest.TestCase):
    def test_mac_profile_and_default_browser(self):
        with patch.object(web.sys, "platform", "darwin"):
            self.assertEqual(web.profile_folder(), Path.home() / "Library" / "Application Support" /
                             "CanvasQuizExporter" / "BrowserProfile")
            self.assertEqual(web.LocalApp().snapshot()["default_browser"], "chrome")

    def test_windows_profile_stays_at_existing_location(self):
        with patch.object(web.sys, "platform", "win32"), patch.dict(web.os.environ, {"LOCALAPPDATA": str(Path.home() / "SyntheticLocal")}):
            self.assertEqual(web.profile_folder(), Path.home() / "SyntheticLocal" / "CanvasQuizExporter" / "BrowserProfile")
            self.assertEqual(web.LocalApp().snapshot()["default_browser"], "msedge")

    def test_mac_folder_selection_and_cancel(self):
        app = web.LocalApp()
        for selected in ("/Synthetic folder/O'Brien/\n", "\n"):
            with patch.object(web.sys, "platform", "darwin"), patch.object(web.subprocess, "run", return_value=Mock(stdout=selected)) as run:
                self.assertEqual(app.browse(), {"folder": selected.rstrip("\r\n")})
                args, kwargs = run.call_args
                self.assertEqual(args[0][:2], ["/usr/bin/osascript", "-e"])
                self.assertNotIn("shell", kwargs)
                self.assertNotIn("O'Brien", args[0][2])
        self.assertFalse(app.dialog_lock.locked())

    def test_mac_chooser_failure_allows_path_fallback_and_retry(self):
        app = web.LocalApp()
        with patch.object(web.sys, "platform", "darwin"), patch.object(web.subprocess, "run", side_effect=subprocess.TimeoutExpired("osascript", 300)):
            with self.assertRaisesRegex(web.UIError, "Paste an existing folder path"):
                app.browse()
        self.assertFalse(app.dialog_lock.locked())

    def test_mac_open_file_folder_and_key_without_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "Synthetic O'Brien folder"
            (out / "private").mkdir(parents=True)
            (out / "private" / "name_key.csv").touch()
            (out / "log.txt").touch()
            app = web.LocalApp()
            app.update(output=str(out), first_file="log.txt", key_ready=True)
            for options, expected in (({}, out), ({"first": True}, out / "log.txt"),
                                      ({"key": True}, out / "private" / "name_key.csv")):
                with patch.object(web.sys, "platform", "darwin"), patch.object(web.subprocess, "run") as run:
                    app.open_local(**options)
                    self.assertEqual(run.call_args.args[0], ["/usr/bin/open", str(expected.resolve())])
                    self.assertNotIn("shell", run.call_args.kwargs)

    def test_mac_open_failure_has_safe_fallback(self):
        with patch.object(web.sys, "platform", "darwin"), patch.object(web.subprocess, "run", side_effect=OSError("synthetic private path")):
            with self.assertRaisesRegex(web.UIError, "Finder") as error:
                web.open_path(Path("synthetic.txt"))
            self.assertNotIn("synthetic private path", str(error.exception))
