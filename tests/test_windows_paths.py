"""Directory resolution checks using temporary folders, never student files."""
from pathlib import Path
import tempfile
import unittest
import csv

from windows_paths import physical_directory
from learned_download import write_collection_results


class WindowsPathTests(unittest.TestCase):
    def test_results_include_saved_and_skipped_rows_without_csv_formulas(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = {"students": [
                {"record_id": "R-one", "student_name": "Invented Name", "status": "saved"},
                {"record_id": "R-two", "student_name": "=Invented", "status": "no_upload_displayed"}],
                "records": [{"record_id": "R-one", "status": "saved", "file": "files/invented.model"}]}
            target = write_collection_results(temporary, state)
            with target.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["saved_file"], "files/invented.model")
            self.assertEqual(rows[1]["status"], "no_upload_displayed")
            self.assertEqual(rows[1]["student_name"], "'=Invented")

    def test_resolved_directory_exists_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "invented-output" / "files"
            folder.mkdir(parents=True)
            actual = physical_directory(folder)
            self.assertTrue(actual.is_absolute())
            self.assertTrue(actual.is_dir())
            self.assertEqual(physical_directory(actual), actual)
            self.assertFalse(str(actual).startswith("\\\\?\\"))

    def test_resolution_rejects_missing_directory(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows handle resolution")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(OSError):
                physical_directory(Path(temporary) / "does-not-exist")


if __name__ == "__main__":
    unittest.main()
