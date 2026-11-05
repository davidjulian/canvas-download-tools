"""Build source-only release ZIPs from an explicit allowlist."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = (
    ".gitignore", ".gitattributes", "README.md", "SECURITY.md", "LICENSE-STATUS.md", "VALIDATION.md", "PUBLISHING.md",
    "requirements.txt", "Install.cmd", "Install.command", "Start App.cmd", "Start App.command",
    "local_app.py", "file_app.py", "log_app.py", "quick_capture.py", "moderate_pages.py", "coded_export.py", "event_summary.py",
    "http_ui.py", "canvas_helpers.py", "desktop.py", "native_edge.py",
    "learned_download.py", "speedgrader_download.py", "student_picker_cache.py", "windows_paths.py",
    "download_destination.py", "build_release.py", "web/index.html", "web/common.js", "web/style.css",
    "web/logs/index.html", "web/logs/app.js", "web/logs/style.css", "web/files/index.html", "web/files/app.js", "web/files/style.css",
    "docs/index.html", "docs/style.css", "docs/.nojekyll", ".github/workflows/tests.yml",
)
TESTS = ("test_app.py", "test_speedgrader_download.py", "test_learned_download.py",
         "test_native_edge.py", "test_windows_paths.py", "test_download_destination.py", "test_release.py",
         "test_combined.py", "test_log_app.py", "test_log_platform.py", "test_quick_capture.py",
         "test_moderate_pages.py", "test_coded_export.py", "test_event_summary.py")


def build(root=ROOT, destination=None):
    root = Path(root).resolve()
    destination = Path(destination) if destination else root / "dist"
    members = list(FILES) + ["tests/" + name for name in TESTS]
    for member in members:
        path = root / member
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("release_allowlist_file_missing_or_unsafe")
    destination.mkdir(parents=True, exist_ok=True)
    hashes, outputs = [], []
    for platform in ("windows", "macos"):
        output = destination / f"canvas-download-tools-{platform}.zip"
        with zipfile.ZipFile(output, "w") as archive:
            for member in sorted(members):
                item = zipfile.ZipInfo("canvas-download-tools/" + member, date_time=(2026, 1, 1, 0, 0, 0))
                item.create_system = 3
                mode = 0o755 if member.endswith(".command") else 0o644
                item.external_attr = (0o100000 | mode) << 16
                data = (root / member).read_bytes().replace(b"\r\n", b"\n")
                if member.endswith(".cmd"):
                    data = data.replace(b"\n", b"\r\n")
                archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED)
        hashes.append(hashlib.sha256(output.read_bytes()).hexdigest() + "  " + output.name)
        outputs.append(output)
    (destination / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    outputs = build(destination=args.output)
    print(f"Built {len(outputs)} source-only release ZIPs and SHA256SUMS.txt.")
