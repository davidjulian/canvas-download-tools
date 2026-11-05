"""Archive safety using invented private markers and explicit source members."""
from pathlib import Path
import tempfile
import unittest
import zipfile

import build_release as release


class ReleaseTests(unittest.TestCase):
    def test_only_allowlisted_files_ship_and_mac_launchers_are_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            members = list(release.FILES) + ["tests/" + name for name in release.TESTS]
            for member in members:
                path = root / member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("invented source", encoding="utf-8")
            for name in ("private/manifest.json", "BrowserProfile/session", ".venv/private", "unlisted.pdf"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("INVENTED_PRIVATE_MARKER", encoding="utf-8")
            outputs = release.build(root)
            self.assertEqual(len(outputs), 2)
            expected = {"canvas-download-tools/" + member for member in members}
            for output in outputs:
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(set(archive.namelist()), expected)
                    for item in archive.infolist():
                        self.assertNotIn(b"INVENTED_PRIVATE_MARKER", archive.read(item))
                        if item.filename.endswith(".command"):
                            self.assertEqual((item.external_attr >> 16) & 0o777, 0o755)
            self.assertTrue((root / "dist/SHA256SUMS.txt").is_file())

    def test_missing_source_stops_release_instead_of_shipping_an_incomplete_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "allowlist"):
                release.build(temporary)
