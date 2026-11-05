"""Save displayed log text with coded IDs and a separate private name key.

No event parsing, classification, metrics or analysis. Identifier substitutions
are limited safeguards; each export still needs local review before sharing.
"""
import re
import zipfile
from pathlib import Path

from quick_capture import log_from_began

ID = re.compile(r"R-[0-9a-f]{16}\Z")
REVIEW_TEXT = """CODED QUIZ LOG DOWNLOADS

These files retain the displayed event text and elapsed times. No computed
summary is embedded in these logs. A separate named descriptive event summary
may be saved in private/; it is excluded from this ZIP. Names in the reviewed Moderate
mapping are replaced locally. Start and finish calendar dates and clock times, recognizable
email addresses, web addresses and IPv4 addresses are omitted.

Keep private/ and its name_key.csv on approved storage. The private key links
each record ID to its reviewed name and position on Moderate. It is not in
this ZIP. IDs identify displayed View Log controls/attempts, not independently
verified unique people. IDs are newly generated on each run.

Review every text file locally before external sharing. Other names, answers,
identifiers, dates or distinctive patterns can remain in free text. This app
does not guarantee anonymization or certify FERPA compliance. No AI upload
occurs automatically. Focus events alone cannot establish misconduct.

Only rendered text is downloaded. The local web app traverses recognized Moderate
student pages with current filters retained. Hidden attempts, extra event pages,
virtual lists and collapsed details are not handled automatically.
"""


def omit_finished_timestamp(text):
    """Redact the Finished calendar/clock metadata, not elapsed event rows."""
    calendar_or_clock = re.compile(
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"January|February|March|April|May|June|July|August|September|October|November|December)\b"
        r"|\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
        r"|\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s*[AP]M)?\b", re.I)
    lines, result, index = text.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        match = re.fullmatch(r"([ \t]*Finished)\b[ \t]*:?[ \t]*(.*)", line, re.I)
        if match:
            value = match[2].strip()
            split = not value and index + 1 < len(lines)
            if split:
                value = lines[index + 1].strip()
            if calendar_or_clock.search(value):
                result.append(match[1] + " [calendar date and clock time omitted]")
                index += 2 if split else 1
                continue
        result.append(line)
        index += 1
    return "\n".join(result) + ("\n" if text.endswith("\n") else "")


def coded_text(record_id, text, names=()):
    if not ID.fullmatch(record_id):
        raise ValueError("invalid_generated_record_id")
    _, _, body = log_from_began(text).partition("\n")
    # These are generated locally in private captures, not event descriptions.
    body = re.sub(r"^Record ID: R-[0-9a-f]{16}\s*\n?", "", body, flags=re.M)
    body = omit_finished_timestamp(body)
    for name in sorted(set(names), key=len, reverse=True):
        words = name.split()
        if words:
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"
            body = re.sub(pattern, "[name removed]", body, flags=re.I)
    body = re.sub(r"\b[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email removed]", body)
    body = re.sub(r"(?:https?://|www\.)[^\s<>]+", "[web address removed]", body, flags=re.I)
    body = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP address removed]", body)
    return f"Began [calendar date and clock time omitted]\nRecord ID: {record_id}\n\n{body.strip()}\n"


class CodedExport:
    archive_name = "coded-logs.zip"
    review_text = REVIEW_TEXT

    def __init__(self, root, *, existing=False):
        self.root = Path(root)
        self.folder = self.root / "logs"
        self.folder.mkdir(exist_ok=existing)
        self.files = {}

    def add_record(self, record_id, text, names=()):
        content = coded_text(record_id, text, names)
        filename = f"log_{record_id}.txt"
        (self.folder / filename).write_text(content, encoding="utf-8", newline="\n")
        self.files[record_id] = filename
        return filename

    def bundle(self):
        # Only named outputs created by this exporter are included. Never walk
        # the run directory or include the private name key or raw captures.
        target = self.root / self.archive_name
        temporary = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for _, name in sorted(self.files.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, (self.folder / name).read_bytes())
            entry = zipfile.ZipInfo("REVIEW_BEFORE_SHARING.txt", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, self.review_text.encode("utf-8"))
        temporary.replace(target)


class NamedExport(CodedExport):
    """Download identified text without coding, redaction or a name key."""
    archive_name = "named-logs.zip"
    review_text = ("NAMED QUIZ LOG DOWNLOADS\n\nThese logs contain student names and original dates, times and event text. "
                   "No coding or identifier removal is performed. No name key is created. "
                   "Use only storage and services authorized for identified education records and their required data classification. "
                   "Follow all institutional privacy and FERPA rules. A separate named event summary may be saved in private/; it is excluded from this ZIP. No automatic AI upload is performed.\n")

    def add_named_record(self, filename, content):
        if Path(filename).name != filename or not filename.startswith("log_") or not filename.endswith(".txt") or "/" in filename or "\\" in filename:
            raise ValueError("invalid_generated_log_filename")
        (self.folder / filename).write_text(content, encoding="utf-8", newline="\n")
        self.files[filename] = filename
