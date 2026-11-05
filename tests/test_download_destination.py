"""Invented directories only; never open a real dialog or move student files."""
from pathlib import Path
import contextlib
import io
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import download_destination as app


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.default = self.root / "Downloads"
        self.default.mkdir()
        self.profile = self.root / "profile"
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.temporary.cleanup()

    def choose(self, replies, picker=None):
        answers = iter(replies)
        with patch.object(app, "downloads_directory", return_value=self.default):
            return app.choose_download_directory(self.profile, prompt=lambda _: next(answers), picker=picker)

    def test_enter_uses_configured_downloads_and_a_new_run_folder(self):
        selected = self.choose([""])
        self.assertEqual(selected.parent, app.physical_directory(self.default))
        self.assertTrue(selected.name.startswith("SpeedGrader-"))
        self.assertFalse(selected.exists())

    def test_p_opens_picker_and_cancel_returns_to_choice(self):
        custom = self.root / "Selected"
        custom.mkdir()
        picker = MagicMock(side_effect=[None, custom])
        selected = self.choose(["p", "p"], picker)
        self.assertEqual(selected.parent, app.physical_directory(custom))
        self.assertEqual(picker.call_count, 2)

    def test_missing_or_synced_directory_is_rejected_before_saving(self):
        synced = self.root / "OneDrive - Invented"
        synced.mkdir()
        selected = self.choose([str(self.root / "missing"), str(synced), ""])
        self.assertEqual(selected.parent, app.physical_directory(self.default))

    def test_picker_failure_allows_pasting_a_path(self):
        selected = self.choose(["p", str(self.default)], MagicMock(side_effect=OSError("invented")))
        self.assertEqual(selected.parent, app.physical_directory(self.default))

    def test_supplied_path_skips_prompt_and_existing_runs_are_not_reused(self):
        with patch.object(app, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "SpeedGrader-invented"
            existing = self.default / "SpeedGrader-invented"
            existing.mkdir()
            selected = app.choose_download_directory(self.profile, supplied=str(self.default),
                                                      prompt=MagicMock(side_effect=AssertionError("unexpected prompt")))
        self.assertEqual(selected.name, "SpeedGrader-invented (2)")
        self.assertTrue(existing.is_dir())

    def test_native_dialog_only_selects_folders_and_is_closed(self):
        tk = MagicMock()
        dialog = MagicMock(return_value=str(self.default))
        module = SimpleNamespace(Tk=tk, filedialog=SimpleNamespace(askdirectory=dialog))
        with patch.object(app.sys, "platform", "win32"), patch.dict("sys.modules", {"tkinter": module}):
            self.assertEqual(app.pick_directory(self.default), self.default)
        self.assertTrue(dialog.call_args.kwargs["mustexist"])
        tk.return_value.destroy.assert_called_once()
