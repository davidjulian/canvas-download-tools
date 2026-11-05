"""Local descriptive focus-event summaries; never misconduct classifications."""
import csv
import re
from pathlib import Path

from quick_capture import csv_name

CLOCK = re.compile(r"^(\d{1,6}:\d{2}(?::\d{2})?)(?:\s+(.*))?$")
STOP = re.compile(r"^Stopped\s+viewing\s+the\s+(?:Canvas\s+)?quiz[- ]taking\s+page\b", re.I)
RESUME = re.compile(r"^Resumed\b", re.I)
SUBMIT = re.compile(r"^(?:Quiz\s+submitted|Submitted\s+(?:the\s+)?quiz|Session\s+(?:submitted|finished)|Quiz\s+finished)\b", re.I)
RAW_STOP = re.compile(r"^(?:\d{1,6}:\d{2}(?::\d{2})?\s+)?Stopped\s+viewing\s+the\s+(?:Canvas\s+)?quiz[- ]taking\s+page\b", re.I | re.M)

METHOD = """LOCAL QUIZ EVENT SUMMARY

One row per downloaded View Log control/attempt, in collection order. Repeated
names remain separate. Only the text actually downloaded is summarized.

Stopped viewing events: recorded stop entries before a recognized submission
event; later events are excluded. If submission is absent, all visible events
are used and elapsed time is the last visible elapsed value, not a verified
total attempt duration. Clock formats: MM:SS or HH:MM:SS. Calendar dates and
clock times in the page header are not used for these calculations. An unreadable
earlier timestamp does not discard a readable submission time or final timed
event. Away durations remain unavailable when any event timestamp is invalid.
An unreadable final timed event leaves the fallback unavailable; an earlier
readable event is not silently substituted for it. Mixed event order or
conflicting submission times still leave elapsed time unavailable.

Average away seconds: arithmetic mean of unambiguous Stopped viewing -> Resumed
pairs. Other event types do not end an away period. Zero-second pairs are kept.
Consecutive stops make that cluster ambiguous: its duration is excluded.
Unmatched stops, returns without stops, timestamp problems and missing
submission are noted. No return time is invented for a final unmatched stop.
Blank duration means unavailable, not zero. Paired interval count is the mean's
denominator, which may be smaller than the recorded stop count.

Total paired away seconds is the sum of valid pair durations; longest paired
away seconds is their maximum. These exclude unmatched/ambiguous periods and
may understate actual away time. Percentage away is 100 * total paired away
seconds / the displayed elapsed seconds. When elapsed time is only the last
visible value, the percentage is also provisional and flagged. Zero elapsed
time has no defined percentage. No stop events means zero recorded away time;
an unavailable parser result or only unpaired stops means unknown, not zero.

The private named CSV contains identified education records even in coded mode.
It is not included in the log sharing ZIP. It has no AI or network dependency.
The original log downloads remain unchanged if summary parsing fails.

Focus events cannot identify the website or application used or establish
ChatGPT use, prohibited activity or misconduct. Instructure advises against
using quiz logs to validate academic integrity or identify cheating:
https://community.instructure.com/en/kb/articles/661091-unknown
"""


def clock_seconds(value):
    parts = [int(x) for x in value.split(":")]
    if len(parts) not in (2, 3) or parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
        raise ValueError("invalid_elapsed_clock")
    return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]


def elapsed_display(seconds):
    if seconds is None:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def summarize_text(text):
    result = {"total_elapsed_seconds": None, "stopped_viewing_events": None,
              "average_away_seconds": None, "paired_intervals": 0,
              "total_away_seconds": None, "percent_elapsed_away": None, "longest_away_seconds": None,
              "unpaired_stop_events": None, "elapsed_basis": "unavailable", "notes": []}
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().casefold() == "elapsed time"]
    if len(starts) != 1:
        result["notes"] = ["A single Elapsed time heading was not found; no metrics calculated."]
        return result
    body = "\n".join(lines[starts[0] + 1:])
    events, block, at = [], [], None
    invalid_clock = False
    clock_validity = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        match = CLOCK.fullmatch(line)
        if match:
            if at is not None:
                events.append((at, " ".join(block)))
            try:
                at = clock_seconds(match[1])
            except ValueError:
                invalid_clock, at = True, None
            clock_validity.append(at is not None)
            block = [match[2]] if match[2] else []
        elif re.match(r"^\d{1,6}:\S+", line):
            invalid_clock = True
            clock_validity.append(False)
            if at is not None:
                block.append(line)
        elif at is not None:
            block.append(line)
    if at is not None:
        events.append((at, " ".join(block)))
    if not events:
        result["notes"] = ["No readable elapsed event rows; no metrics calculated."]
        return result

    notes = result["notes"]
    raw_stops = len(RAW_STOP.findall(body))
    recognized_stops = sum(bool(STOP.match(description)) for _, description in events)
    result["stopped_viewing_events"] = raw_stops
    result["unpaired_stop_events"] = raw_stops
    times = [t for t, _ in events]
    if all(a >= b for a, b in zip(times, times[1:])) and times[0] > times[-1]:
        events.reverse()
        times.reverse()
        clock_validity.reverse()
        notes.append("Reverse chronological rows were reversed for pairing.")
    if any(a > b for a, b in zip(times, times[1:])):
        notes.append("Out-of-order elapsed timestamps; elapsed time and away durations not calculated.")
        return result
    submits = [(i, t) for i, (t, d) in enumerate(events) if SUBMIT.match(d)]
    if len({t for _, t in submits}) > 1:
        notes.append("Conflicting submission times; durations not calculated.")
        return result
    if submits:
        end, elapsed = submits[0]
        if end + 1 < len(events):
            notes.append("Events after submission were excluded.")
        events = events[:end + 1]
        result["total_elapsed_seconds"] = elapsed
        result["elapsed_basis"] = "submission event"
        result["stopped_viewing_events"] = sum(bool(STOP.match(d)) for _, d in events)
    elif clock_validity[-1]:
        result["total_elapsed_seconds"] = times[-1]
        result["elapsed_basis"] = "last visible event"
        notes.append("No recognized submission event: elapsed time is the last visible value, not a verified total.")
    else:
        notes.append("The final timed event is unreadable; elapsed time is unavailable. An earlier event was not substituted.")
    if times[0] != 0:
        notes.append("Visible elapsed events do not begin at zero; earlier events may be missing.")
    if invalid_clock:
        if submits and result["stopped_viewing_events"] != raw_stops:
            notes.append("Stop count includes all visible stop entries because some timestamps are unreadable; the submission boundary could not be verified for every entry.")
        result["stopped_viewing_events"] = raw_stops
        result["unpaired_stop_events"] = raw_stops
        notes.append("Invalid elapsed timestamps: away durations were not calculated. A readable submission or final timed event is retained for elapsed time when available.")
        return result
    if raw_stops != recognized_stops:
        result["stopped_viewing_events"] = raw_stops
        result["unpaired_stop_events"] = raw_stops
        notes.append("Some stop entries lack a readable elapsed timestamp; away durations not calculated.")
        return result

    pending, ambiguous, durations = None, False, []
    orphan_resumes, consecutive_stops = 0, False
    for at, description in events:
        if STOP.match(description):
            if pending is not None:
                ambiguous, consecutive_stops = True, True
            else:
                pending = at
        elif RESUME.match(description):
            if pending is None:
                orphan_resumes += 1
            else:
                if not ambiguous:
                    durations.append(at - pending)
                pending, ambiguous = None, False
    result["paired_intervals"] = len(durations)
    result["unpaired_stop_events"] = result["stopped_viewing_events"] - len(durations)
    if durations:
        result["average_away_seconds"] = round(sum(durations) / len(durations), 2)
        result["total_away_seconds"] = sum(durations)
        result["longest_away_seconds"] = max(durations)
    elif result["stopped_viewing_events"] == 0:
        result["total_away_seconds"] = 0
    elapsed = result["total_elapsed_seconds"]
    if elapsed and result["total_away_seconds"] is not None:
        result["percent_elapsed_away"] = round(100 * result["total_away_seconds"] / elapsed, 2)
        if result["elapsed_basis"] == "last visible event":
            notes.append("Percentage uses only the last visible elapsed value, not a verified full attempt duration.")
    elif elapsed == 0:
        notes.append("Zero elapsed time: percentage away is undefined.")
    if consecutive_stops:
        notes.append("Consecutive stop entries: ambiguous clusters excluded from the average.")
    if result["unpaired_stop_events"]:
        notes.append("Some stop events have no unambiguous return; excluded from the average.")
        notes.append("Total, percentage and longest away time use paired intervals only; actual away time may be greater.")
    if orphan_resumes:
        notes.append("Return entries without a preceding stop were found.")
    return result


def write_summary(private_folder, rows):
    folder = Path(private_folder)
    temporary = folder / "event_summary.csv.tmp"
    columns = ("Student name", "Total elapsed time (HH:MM:SS)", "Stopped viewing events",
               "Average away time (seconds)", "Total paired away time (seconds)", "Elapsed time away (%)",
               "Longest paired away period (seconds)", "View Log position", "Record ID", "Paired intervals",
               "Unpaired stop events", "Elapsed time basis", "Notes")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow((csv_name(row["student_name"]), elapsed_display(row["total_elapsed_seconds"]),
                             row["stopped_viewing_events"], row["average_away_seconds"], row["total_away_seconds"],
                             row["percent_elapsed_away"], row["longest_away_seconds"], row["position"],
                             row.get("record_id", ""), row["paired_intervals"], row["unpaired_stop_events"],
                             row["elapsed_basis"], " ".join(row["notes"])))
    temporary.replace(folder / "event_summary.csv")
    (folder / "summary_method.txt").write_text(METHOD, encoding="utf-8", newline="\n")
