"""Verify text download fidelity and private-key exclusion with invented data."""
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coded_export import CodedExport, NamedExport, coded_text, omit_finished_timestamp

RECORD = "R-0000000000000001"


class CodedDownloadTests(unittest.TestCase):
    def test_original_wording_and_elapsed_text_are_preserved(self):
        body = ("Elapsed time\nEvent\n00:00\nSession started.\n00:08\nStopped viewing the quiz-taking page.\n"
                "00:10\nResumed.\n00:32\nAnswered question 7: a sample response\n"
                "34:23\nSession submitted.\nCustom footer text")
        result = coded_text(RECORD, "Menu\nBegan Tuesday, September 29 at 11:46 AM\n" + body)
        self.assertTrue(result.startswith("Began [calendar date and clock time omitted]\nRecord ID: " + RECORD))
        self.assertTrue(result.endswith(body + "\n"))
        self.assertNotIn("other_unclassified", result)
        self.assertNotIn("Parser status:", result)
        self.assertNotIn("page_blurred", result)
        self.assertNotIn("September", result)

    def test_no_event_recognition_or_elapsed_heading_required(self):
        source = "Began today\nA completely different event layout\nUnfamiliar text\n0 minutes 12 seconds"
        result = coded_text(RECORD, source)
        self.assertIn("A completely different event layout\nUnfamiliar text\n0 minutes 12 seconds", result)

    def test_only_identifiers_are_substituted(self):
        source = ("Began today\n00:10 Renée Synthetic\nALICE  EXAMPLE\nstudent@private.invalid\n"
                  "https://private.invalid/student/123\n192.0.2.42\n00:12 Unfamiliar event text remains")
        result = coded_text(RECORD, source, names=("Renée Synthetic", "Alice Example"))
        for private in ("Renée", "ALICE", "student@private.invalid", "https://private.invalid", "192.0.2.42"):
            self.assertNotIn(private, result)
        self.assertIn("00:12 Unfamiliar event text remains", result)
        self.assertIn("[name removed]", result)
        self.assertIn("[email removed]", result)

    def test_archive_contains_only_created_logs_and_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            private = root / "private"
            private.mkdir()
            (private / "name_key.csv").write_text("PRIVATE_SYNTHETIC_NAME")
            download = CodedExport(root)
            download.add_record(RECORD, "Began today\nOriginal unfamiliar event text")
            (download.folder / "unrelated.txt").write_text("PRIVATE_SYNTHETIC_NAME")
            download.bundle()
            self.assertFalse((root / "analysis").exists())
            self.assertFalse((root / "logs" / "events.csv").exists())
            with zipfile.ZipFile(root / "coded-logs.zip") as archive:
                self.assertEqual(set(archive.namelist()), {f"log_{RECORD}.txt", "REVIEW_BEFORE_SHARING.txt"})
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertNotIn(b"PRIVATE_SYNTHETIC_NAME", archive.read(entry))

    def test_invalid_record_ids_cannot_name_files(self):
        with tempfile.TemporaryDirectory() as temp:
            download = CodedExport(temp)
            for invalid in ("Student_001", "R-Alice", "../outside", "R-0000000000000001\n"):
                with self.assertRaises(ValueError):
                    download.add_record(invalid, "Began today\nEvent")

    def test_order_and_unusual_timestamps_are_not_analyzed(self):
        body = "00:30 Resumed.\n00:10 Session started.\nnot-a-time Unknown event"
        self.assertTrue(coded_text(RECORD, "Began today\n" + body).endswith(body + "\n"))

    def test_short_names_do_not_remove_parts_of_other_words(self):
        text = coded_text(RECORD, "Began today\nLi\nAlice\nLiving\nRecord ID: R-0000000000000001\nEvent", ("Li",))
        self.assertIn("[name removed]\nAlice\nLiving", text)
        self.assertEqual(text.count("Record ID:"), 1)

    def test_finished_calendar_and_clock_are_omitted(self):
        for value in ("Tuesday, September 29, 2026 at 12:20 PM", "2026-09-29 12:20:00",
                      "09/29/2026 12:20 PM", "12:20 PM", "23:20"):
            with self.subTest(value=value):
                result = coded_text(RECORD, "Began today\nFinished " + value + "\nElapsed time\n34:23\nSession submitted.")
                self.assertIn("Finished [calendar date and clock time omitted]", result)
                self.assertNotIn(value, result)
                self.assertIn("Elapsed time\n34:23\nSession submitted.", result)

    def test_split_finished_header_and_elapsed_event_text(self):
        source = ("Finished\nTuesday, September 29, 2026 at 12:20 PM\n"
                  "00:20 Finished answering a question\nFinished after 34 minutes\nFinished question 7")
        result = omit_finished_timestamp(source)
        self.assertTrue(result.startswith("Finished [calendar date and clock time omitted]\n00:20"))
        self.assertIn("00:20 Finished answering a question\nFinished after 34 minutes\nFinished question 7", result)
        self.assertEqual(omit_finished_timestamp(result), result)

    def test_named_export_preserves_names_dates_and_addresses_without_coding(self):
        with tempfile.TemporaryDirectory() as temp:
            download = NamedExport(temp)
            name = "log_001_Synthetic_Student.txt"
            content = "Began Tuesday, September 29 at 11:46 AM\nStudent: Synthetic Student\nFinished Tuesday at 12:20 PM\n00:10 Resumed.\nstudent@private.invalid\n192.0.2.42"
            download.add_named_record(name, content)
            download.bundle()
            self.assertEqual((Path(temp) / "logs" / name).read_text(), content)
            self.assertFalse((Path(temp) / "coded-logs.zip").exists())
            self.assertFalse((Path(temp) / "private" / "name_key.csv").exists())
            with zipfile.ZipFile(Path(temp) / "named-logs.zip") as archive:
                self.assertEqual(archive.read(name).decode(), content)
                self.assertNotIn("Record ID:", archive.read(name).decode())

    def test_named_export_rejects_paths_outside_log_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            download = NamedExport(temp)
            for name in ("../log_001.txt", "log_001/../../outside.txt", "log_001\\outside.txt"):
                with self.assertRaises(ValueError):
                    download.add_named_record(name, "Synthetic text")


if __name__ == "__main__":
    unittest.main()
