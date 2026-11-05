"""Small desktop boundaries shared by the Windows and Mac interface."""
import os
from pathlib import Path
import subprocess
import sys

from canvas_helpers import UIError
from windows_paths import physical_directory


def open_path(path):
    path = Path(path)
    if sys.platform == "darwin":
        try:
            subprocess.run(["/usr/bin/open", str(path.resolve())], check=True,
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            raise UIError("The item could not open. Use the displayed folder in Finder.") from None
    elif os.name == "nt":
        actual = physical_directory(path.parent) / path.name if path.is_file() else physical_directory(path)
        os.startfile(str(actual))
    else:
        raise UIError("Open the displayed folder in your file manager.")


def browse_folder():
    if sys.platform == "darwin":
        # AppKit/Tk cannot own a dialog on an HTTP worker thread. Reuse the
        # quiz log app's separate native dialog process instead.
        result = subprocess.run(["/usr/bin/osascript", "-e", '''try
return POSIX path of (choose folder with prompt "Choose where to save Canvas downloads")
on error number -128
return ""
end try'''], capture_output=True, text=True, check=True, timeout=300)
        return result.stdout.rstrip("\r\n")
    from download_destination import pick_directory
    from windows_paths import downloads_directory
    result = pick_directory(downloads_directory())
    return str(result) if result else ""
