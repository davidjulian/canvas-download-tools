"""Choose the attachment destination; browser and working data stay separate."""
from datetime import datetime
from pathlib import Path
import sys

from speedgrader_download import DownloadError, validate_locations
from windows_paths import downloads_directory, physical_directory


def pick_directory(initial):
    if sys.platform == "darwin":
        from desktop import browse_folder
        result = browse_folder()
        return Path(result) if result else None
    import tkinter as tk
    from tkinter import filedialog
    window = tk.Tk()
    window.withdraw()
    try:
        window.attributes("-topmost", True)
        result = filedialog.askdirectory(parent=window, title="Choose where to save Canvas downloads",
                                        initialdir=str(initial), mustexist=True)
        return Path(result) if result else None
    finally:
        window.destroy()


def run_folder(folder, profile):
    folder = physical_directory(folder)
    if not folder.is_dir():
        raise DownloadError("download_folder_not_found")
    if str(folder).startswith("\\\\"):
        raise DownloadError("choose_local_download_folder")
    name = datetime.now().strftime("SpeedGrader-%Y-%m-%d_%H%M%S")
    candidate, number = folder / name, 2
    while candidate.exists():
        candidate = folder / (name + f" ({number})")
        number += 1
    actual_profile = physical_directory(profile) if Path(profile).is_dir() else profile
    target, _ = validate_locations(candidate, actual_profile)
    return target


def choose_download_directory(profile, *, supplied=None, prompt=input, picker=None):
    if supplied is not None:
        return run_folder(Path(supplied).expanduser(), profile)
    default = downloads_directory()
    print("Default download folder: " + str(default), flush=True)
    while True:
        answer = prompt("Save files: Enter for Downloads, P to pick a folder, or paste a folder path: ").strip()
        if answer.lower() == "p":
            try:
                folder = (picker or pick_directory)(default)
            except Exception:
                print("The folder picker could not open. Paste a folder path here instead.", flush=True)
                continue
            if folder is None:
                continue
        else:
            folder = Path(answer.strip('"')).expanduser() if answer else default
        try:
            return run_folder(folder, profile)
        except (DownloadError, OSError):
            print("Choose an existing local folder outside OneDrive, the app and its browser profile.", flush=True)
