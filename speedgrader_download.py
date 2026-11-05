"""Supervised local New Quizzes file downloads. No Canvas API or AI uploads."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import time
from urllib.parse import parse_qs, urlsplit

from canvas_helpers import validate_url
from native_edge import NativeEdge, NativeEdgeError
from canvas_helpers import csv_name, element_text, filename_name, normalize_name, visible_frame


DEFAULT_FILES = 'a,button,[role="button"],[role="link"]'
STUDENTS = "select#students_selectmenu"
MAX_STUDENTS = 5000
PILOT_VERSION = "0.5.0"


class DownloadError(Exception):
    """Only fixed categories may cross the local worker boundary."""


class ConsoleInput:
    """Poll terminal input without blocking Playwright's owning thread."""
    def __init__(self):
        self.characters = []
        if not sys.stdin.isatty():
            raise DownloadError("interactive_terminal_required")

    def poll(self):
        if os.name != "nt":
            import select
            if not select.select([sys.stdin], [], [], 0)[0]:
                return None
            line = sys.stdin.readline()
            if not line:
                raise EOFError
            return line.rstrip("\r\n")
        import msvcrt
        while msvcrt.kbhit():
            character = clean_choice_tail(msvcrt.getwch())
            if character is None:
                continue
            if character in ("\x00", "\xe0"):
                msvcrt.getwch()  # Skip an extended key code, such as an arrow.
            elif character == "\x03":
                raise KeyboardInterrupt
            elif character == "\x1a":
                raise EOFError
            elif character in ("\r", "\n"):
                print(flush=True)
                return "".join(self.characters)
            elif character == "\b":
                if self.characters:
                    self.characters.pop()
                    print("\b \b", end="", flush=True)
            elif character.isprintable():
                self.characters.append(character)
                print(character, end="", flush=True)
        return None


_choice_tail = ""
_choice_enter = False


def clean_choice_tail(character):
    """Allow full words after a shortcut without leaking their tail to a later prompt."""
    global _choice_tail, _choice_enter
    if _choice_tail:
        if character in ("\r", "\n"):
            _choice_tail, _choice_enter = "", False
            return None
        if character.casefold() == _choice_tail[0]:
            _choice_tail = _choice_tail[1:]
            return None
        _choice_tail = ""
    if _choice_enter and character in ("\r", "\n"):
        _choice_enter = False
        return None
    _choice_enter = False
    return character


def live_choice(context, message):
    """Windows menu shortcuts take one key; ordinary terminal prompts stay line based."""
    global _choice_tail, _choice_enter
    if os.name != "nt":
        return live_prompt(context, message)
    import msvcrt
    buffer = []
    shortcuts = {"n": ("next", "ext"), " ": ("next", ""), "y": ("student", "es"),
                 "q": ("stop", "uit"), "p": ("point", "oint"), "r": ("rescan", "escan")}
    def poll():
        global _choice_tail, _choice_enter
        while msvcrt.kbhit():
            character = clean_choice_tail(msvcrt.getwch())
            if character is None:
                continue
            if character == "\x03":
                raise KeyboardInterrupt
            if character == "\x1b":
                return "stop"
            if character in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if not buffer and character.casefold() in shortcuts:
                action, _choice_tail = shortcuts[character.casefold()]
                _choice_enter = True
                print("Space" if character == " " else character, flush=True)
                return action
            if character in ("\r", "\n"):
                print(flush=True)
                return "".join(buffer)
            if character == "\b":
                if buffer:
                    buffer.pop()
                    print("\b \b", end="", flush=True)
            elif character.isprintable():
                buffer.append(character)
                print(character, end="", flush=True)
        return None
    return live_prompt(context, message, poll=poll)


def live_prompt(context, message, poll=None):
    """Keep new tabs and frame events moving while awaiting a local answer.

    Every Playwright call stays on the owning thread. No record inspection,
    screenshots or network logging is needed to service its event loop.
    """
    reader = ConsoleInput().poll if poll is None else poll
    print(message, end="", flush=True)
    while True:
        answer = reader()
        if answer is not None:
            return answer
        pages = [page for page in context.pages if not page.is_closed()]
        if not pages:
            raise DownloadError("browser_or_tab_closed")
        pages[0].wait_for_timeout(100)


def await_speedgrader(context, origin, prompt):
    while True:
        prompt("Wait for the SpeedGrader submission to load, then press Enter here: ")
        try:
            return choose_page(context, origin)
        except DownloadError as error:
            if str(error) in ("speedgrader_student_selector_not_recognized", "speedgrader_student_selector_ambiguous"):
                candidates = speedgrader_pages(context, origin)
                if len(candidates) == 1:
                    page = candidates[0]
                    if 1 <= page.get_by_role("combobox").count() <= 8:
                        confirm_student_picker(page, prompt)
                        return choose_page(context, origin)
            messages = {
                "no_speedgrader_tab_recognized": "The SpeedGrader address format was not recognized in this browser.",
                "multiple_speedgrader_tabs_recognized": "More than one SpeedGrader tab was recognized. Keep one SpeedGrader tab open.",
                "speedgrader_student_selector_not_recognized": "One SpeedGrader tab was recognized, but its student selector was not recognized.",
                "speedgrader_student_selector_ambiguous": "One SpeedGrader tab was recognized, but more than one student selector was found.",
                "current_student_name_not_available": "The student picker was recognized, but its selected name is not loaded yet.",
                "student_url_identity_missing": "The student picker was recognized, but the current student identifier is not available in the page address.",
                "student_url_identity_ambiguous": "The page address has conflicting student identifiers. Select the student again before retrying.",
                "open_exactly_one_speedgrader_tab": "SpeedGrader was not recognized.",
            }
            if str(error) not in messages:
                raise
            print(messages[str(error)])
            print("The quiz tab may stay open. The browser stays open. Ctrl+C stops the pilot.")
            print("Discovery counts (no names, addresses, IDs or page text): " +
                  json.dumps(discovery_counts(context, origin)))


def local_root():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "CanvasSpeedGraderDownloader"


def validate_locations(out, profile):
    out, profile = Path(out).expanduser().resolve(), Path(profile).expanduser().resolve()
    source = Path(__file__).resolve().parent
    if out.exists():
        raise DownloadError("output_already_exists")
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for a, b in ((out, profile), (out, source), (profile, source))):
        raise DownloadError("output_profile_and_source_must_be_separate")
    # This workspace is in OneDrive. Defaults deliberately live in local app data.
    # Also reject explicit locations in common OneDrive roots for this local pilot.
    for path in (out, profile):
        if any(part.casefold().startswith("onedrive") for part in path.parts):
            raise DownloadError("choose_storage_outside_onedrive")
        for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
            if os.environ.get(key):
                root = Path(os.environ[key]).resolve()
                if path == root or path.is_relative_to(root):
                    raise DownloadError("choose_storage_outside_onedrive")
    return out, profile


def modern_student_combo(page):
    """Identify the student control without assuming it is the first combobox.

    Prefer its accessible Student label. Otherwise require the single combobox
    in the same small ancestor as both known student navigation buttons.
    """
    confirmed = getattr(page, "_pilot_confirmed_student_picker", None)
    if confirmed:
        if isinstance(confirmed, dict):
            if confirmed["assignment"] != assignment_key(page):
                raise DownloadError("confirmed_student_picker_changed")
            frame = bound_frame(page, confirmed["frame_path"])
            if frame_signature(frame) != confirmed["frame_signature"]:
                raise DownloadError("confirmed_student_picker_changed")
            chosen = frame.locator(confirmed["selector"])
            if (chosen.count() != 1 or not chosen.is_visible()
                    or chosen.evaluate("e => e.tagName") != confirmed["tag"]):
                raise DownloadError("confirmed_student_picker_changed")
            return chosen
        ordinal, total, assignment, tag = confirmed
        combos = page.get_by_role("combobox")
        if assignment != assignment_key(page) or combos.count() != total:
            raise DownloadError("confirmed_student_picker_changed")
        chosen = combos.nth(ordinal)
        if not chosen.is_visible() or chosen.evaluate("e => e.tagName") != tag:
            raise DownloadError("confirmed_student_picker_changed")
        return chosen
    try:
        navigation_button(page, "next")
        navigation_button(page, "previous")
    except DownloadError:
        raise DownloadError("speedgrader_student_selector_not_recognized") from None
    named = []
    for frame in visible_documents(page):
        matches = frame.get_by_role("combobox", name=re.compile(r"\bstudents?\b", re.I))
        named.extend(matches.nth(index) for index in range(matches.count()) if matches.nth(index).is_visible())
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise DownloadError("speedgrader_student_selector_ambiguous")
    candidates = []
    for frame in visible_documents(page):
        local_next = frame.get_by_role("button", name=re.compile(r"next student", re.I))
        local_previous = frame.get_by_role("button", name=re.compile(r"previous student", re.I))
        if local_next.count() != 1 or local_previous.count() != 1:
            continue
        combinations = frame.get_by_role("combobox")
        handles = {"next": local_next.element_handle(), "previous": local_previous.element_handle()}
        for index in range(combinations.count()):
            combo = combinations.nth(index)
            if not combo.is_visible():
                continue
            scoped = combo.evaluate("""(element, buttons) => {
          let parent=element.parentElement;
          for(let depth=0;parent && depth<8;depth++,parent=parent.parentElement){
            if(parent.contains(buttons.next) && parent.contains(buttons.previous)){
              return parent.querySelectorAll('[role="combobox"],select').length===1;
            }
          }
          return false;
            }""", handles)
            if scoped:
                candidates.append(combo)
    if len(candidates) != 1:
        raise DownloadError("speedgrader_student_selector_ambiguous" if candidates else
                            "speedgrader_student_selector_not_recognized")
    return candidates[0]


def combo_student_name(combo):
    name = normalize_name(combo.evaluate("""e => {
      const input=e.querySelector('input');
      return (e.tagName==='SELECT' && e.selectedOptions.length ? e.selectedOptions[0].textContent : '') ||
             (typeof e.value==='string' ? e.value : '') || (input ? input.value : '') ||
             e.getAttribute('aria-valuetext') || e.innerText || e.getAttribute('placeholder') || '';
    }"""))
    if (not name or len(name) > 200 or not any(character.isalpha() for character in name)
            or re.fullmatch(r"(?:select|choose|search)(?:\s+(?:a|for))?\s+students?", name, re.I)
            or name.casefold() in {"submitted", "not submitted", "unsubmitted", "graded", "not graded",
                                   "ungraded", "late", "missing", "excused", "all sections", "all students"}):
        raise DownloadError("current_student_name_not_available")
    return name


ELEMENT_PATH = """e => {
  const parts=[];
  for(let node=e;node && node.nodeType===1;node=node.parentElement){
    let position=1;
    for(let prior=node.previousElementSibling;prior;prior=prior.previousElementSibling)
      if(prior.localName===node.localName)position++;
    parts.unshift(node.localName+':nth-of-type('+position+')');
    if(node.localName==='body')break;
  }
  return parts.join(' > ');
}"""


def visible_documents(page):
    frames = []
    for frame in page.frames:
        try:
            if visible_frame(frame):
                frames.append(frame)
        except Exception:
            pass
    return frames


def frame_path(frame):
    path = []
    while frame.parent_frame is not None:
        path.insert(0, frame.frame_element().evaluate(ELEMENT_PATH))
        frame = frame.parent_frame
    return path


def bound_frame(page, path):
    frame = page.main_frame
    for selector in path:
        item = frame.locator(selector)
        if item.count() != 1:
            raise DownloadError("confirmed_student_picker_changed")
        frame = item.element_handle().content_frame()
        if frame is None or not visible_frame(frame):
            raise DownloadError("confirmed_student_picker_changed")
    return frame


def frame_signature(frame):
    parsed = urlsplit(frame.url)
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "#", parsed.path, flags=re.I)
    path = re.sub(r"\d+", "#", path)
    return hashlib.sha256((parsed.scheme + "://" + parsed.netloc + path).encode()).hexdigest()


def bind_name_control(page, frame, item, assignment):
    combo_student_name(item)
    page._pilot_confirmed_student_picker = {"frame_path": frame_path(frame),
        "selector": item.evaluate(ELEMENT_PATH), "tag": item.evaluate("e => e.tagName"),
        "assignment": assignment, "frame_signature": frame_signature(frame)}


def navigation_button(page, direction):
    matches = []
    for frame in visible_documents(page):
        controls = frame.get_by_role("button", name=re.compile(direction + r" student", re.I))
        for index in range(controls.count()):
            if controls.nth(index).is_visible():
                matches.append(controls.nth(index))
    if len(matches) != 1:
        raise DownloadError(direction + "_student_button_not_unique")
    return matches[0]


def point_student_picker(page, timeout=120000, check_cancel=None, bring_to_front=True):
    assignment, student = assignment_key(page), student_url_identity(page)
    key = "__local_student_control_" + secrets.token_hex(8)
    armed = []
    deadline = time.monotonic() + timeout / 1000
    def arm(frame):
        try:
            frame.evaluate("""key => {
                      window[key]=null;
                      document.addEventListener('click',event => {
                        const leaf=event.composedPath().find(node=>node instanceof Element);
                        if(leaf)window[key]=leaf.closest('input,select,button,[role="combobox"],[role="button"],[aria-haspopup]')||leaf;
                      },true);
            }""", key)
            armed.append(frame)
        except Exception:
            pass
    for frame in visible_documents(page):
        arm(frame)
    print("Click the currently displayed student's NAME once in SpeedGrader, in whichever frame contains it. "
          "Click the displayed name, not a different student in its menu. Ctrl+C stops setup.", flush=True)
    if bring_to_front:
        page.bring_to_front()
    while time.monotonic() < deadline:
        if check_cancel:
            check_cancel()
        for frame in visible_documents(page):
            if frame not in armed:
                arm(frame)
            try:
                handle = frame.evaluate_handle("key => window[key]", key)
                item = handle.as_element()
                if item is None:
                    continue
                if assignment_key(page) != assignment or student_url_identity(page) != student:
                    raise DownloadError("selected_student_changed")
                bind_name_control(page, frame, item, assignment)
                try:
                    item.press("Escape")
                except Exception:
                    pass
                if modern_student_record(page)[0] != student:
                    raise DownloadError("selected_student_changed")
                print("Student name control selected locally.", flush=True)
                return
            except DownloadError:
                raise
            except Exception:
                pass
        page.wait_for_timeout(100)
    raise DownloadError("student_name_pointing_timed_out")


def confirm_student_picker(page, prompt, choice=None):
    """Instructor identifies the control visually; its value stays local."""
    read = choice or prompt
    while True:
        try:
            scan_student_picker(page, prompt, read)
            modern_student_record(page)
            return
        except Exception as error:
            if str(error) in ("stopped_by_user", "student_url_identity_missing", "student_url_identity_ambiguous"):
                raise
            print("The student name control was not selected. The browser remains open.")
            answer = read("P: point directly to the name; R: rescan; Q: stop. ").strip().lower()
            if answer in ("p", "point"):
                try:
                    return point_student_picker(page)
                except Exception as error:
                    if str(error) == "stopped_by_user":
                        raise
                    print("That click could not identify the current name. Try setup again.")
                    continue
            if answer not in ("r", "rescan"):
                raise DownloadError("stopped_by_user")


def scan_student_picker(page, prompt, read):
    initial_assignment = assignment_key(page)
    initial_student = student_url_identity(page)
    handles = []
    for frame in visible_documents(page):
        controls = frame.get_by_role("combobox").or_(frame.locator('select,[aria-haspopup]:not([aria-haspopup="false"]),[aria-expanded][aria-controls]'))
        for index in range(controls.count()):
            if controls.nth(index).is_visible():
                handles.append((frame, controls.nth(index).element_handle()))
    if not 1 <= len(handles) <= 80:
        raise DownloadError("speedgrader_student_selector_not_recognized")
    print("Checking visible dropdowns and menu controls across SpeedGrader's frames. "
          "Press P at any candidate to click the student's name directly.")
    for number, (frame, handle) in enumerate(handles, 1):
        old = handle.evaluate("""e => {
          const old={outline:e.style.getPropertyValue('outline'),
                     outlinePriority:e.style.getPropertyPriority('outline'),
                     offset:e.style.getPropertyValue('outline-offset'),
                     offsetPriority:e.style.getPropertyPriority('outline-offset')};
          e.style.setProperty('outline','4px solid #f59e0b','important');
          e.style.setProperty('outline-offset','3px','important');
          return old;
        }""")
        try:
            page.bring_to_front()
            raw = read(f"Control {number}/{len(handles)} is outlined in orange. "
                       "Y: student name; N or Space: next; P: point directly; Q: stop. ")
            answer = "next" if raw == " " else raw.strip().lower()
            if answer in ("student", "y", "yes"):
                if assignment_key(page) != initial_assignment or student_url_identity(page) != initial_student:
                    raise DownloadError("selected_student_changed")
                try:
                    combo_student_name(handle)
                except DownloadError:
                    print("That control does not contain a usable student name. Check the next dropdown.")
                    continue
                bind_name_control(page, frame, handle, initial_assignment)
                return
            if answer in ("p", "point"):
                return point_student_picker(page)
            if answer not in ("next", "n"):
                raise DownloadError("stopped_by_user")
        finally:
            try:
                handle.evaluate("""(e, old) => {
                  if(old.outline) e.style.setProperty('outline',old.outline,old.outlinePriority);
                  else e.style.removeProperty('outline');
                  if(old.offset) e.style.setProperty('outline-offset',old.offset,old.offsetPriority);
                  else e.style.removeProperty('outline-offset');
                }""", old)
            except Exception:
                pass
    raise DownloadError("student_picker_not_confirmed")


def student_url_identity(page):
    """Use the rendered page address locally; never print or save that URL."""
    parsed = urlsplit(page.url)
    query, fragment = parse_qs(parsed.query), parse_qs(parsed.fragment.lstrip("?"))
    for key in ("student_id", "anonymous_id"):
        values = query.get(key, []) + fragment.get(key, [])
        if values:
            if len(set(values)) != 1:
                raise DownloadError("student_url_identity_ambiguous")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", values[0]):
                raise DownloadError("student_url_identity_missing")
            return values[0] if key == "student_id" else "anonymous:" + values[0]
    raise DownloadError("student_url_identity_missing")


def modern_student_record(page):
    combo = modern_student_combo(page)
    return student_url_identity(page), combo_student_name(combo)


def read_roster(page):
    select = page.locator(STUDENTS)
    if select.count() == 0:
        # Modern pilot reads only the selected student; it does not claim that
        # a virtual or unopened dropdown exposes the entire filtered roster.
        return (modern_student_record(page),)
    if select.count() != 1:
        raise DownloadError("student_selector_not_unique")
    rows = select.locator("option").evaluate_all(
        "items => items.map(e => ({id:e.value, name:e.textContent, disabled:e.disabled}))")
    rows = tuple((row["id"], normalize_name(row["name"])) for row in rows if not row["disabled"])
    if not rows or len(rows) > MAX_STUDENTS or any(not key or not name for key, name in rows):
        raise DownloadError("student_list_not_supported")
    if len({key for key, _ in rows}) != len(rows):
        raise DownloadError("duplicate_student_identifiers")
    return rows


def selected_student(page):
    if page.locator(STUDENTS).count() == 0:
        return student_url_identity(page)
    return page.locator(STUDENTS).input_value()


def discovery_counts(context, origin):
    """Only fixed fields, booleans and counts may leave this local probe."""
    expected = urlsplit(origin)
    result = {"tabs": []}
    for index, page in enumerate(context.pages[:10], 1):
        parsed = urlsplit(page.url)
        row = {"tab": index, "same_canvas_origin": (parsed.scheme, parsed.netloc) ==
               (expected.scheme, expected.netloc),
               "expected_speedgrader_route": bool(re.fullmatch(r"/courses/\d+/gradebook/speed_grader/?", parsed.path)),
               "speedgrader_route_variant": bool(re.search(r"speed_?grader?", parsed.path, re.I)),
               "assignment_query_present": bool(parse_qs(parsed.query).get("assignment_id")),
               "frames": []}
        for number, frame in enumerate(page.frames[:20]):
            try:
                row["frames"].append({
                    "frame": number, "main_document": frame == page.main_frame,
                    "legacy_student_selects": frame.locator(STUDENTS).count(),
                    "legacy_student_buttons": frame.locator("#students_selectmenu-button").count(),
                    "native_selects": frame.locator("select").count(),
                    "comboboxes": frame.get_by_role("combobox").count(),
                    "student_named_comboboxes": frame.get_by_role("combobox", name=re.compile(r"\bstudents?\b", re.I)).count(),
                    "combobox_values_present": frame.get_by_role("combobox").evaluate_all(
                        "items => items.map(e => !!(e.value || e.getAttribute('aria-valuetext') || e.innerText))"),
                    "next_student_buttons": frame.get_by_role("button", name=re.compile("next student", re.I)).count(),
                    "previous_student_buttons": frame.get_by_role("button", name=re.compile("previous student", re.I)).count(),
                    "document_complete": frame.evaluate("document.readyState === 'complete'")})
            except Exception:
                row["frames"].append({"frame": number, "inspection_unavailable": True})
        result["tabs"].append(row)
    return result


def speedgrader_pages(context, origin):
    matches = []
    expected = urlsplit(origin)
    for page in context.pages:
        parsed = urlsplit(page.url)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            continue
        if not re.fullmatch(r"/courses/\d+/gradebook/speed_grader/?", parsed.path):
            continue
        matches.append(page)
    return matches


def choose_page(context, origin):
    matches = speedgrader_pages(context, origin)
    if not matches:
        raise DownloadError("no_speedgrader_tab_recognized")
    if len(matches) > 1:
        raise DownloadError("multiple_speedgrader_tabs_recognized")
    count = matches[0].locator(STUDENTS).count()
    if not count:
        modern_student_record(matches[0])
    if count > 1:
        raise DownloadError("speedgrader_student_selector_ambiguous")
    return matches[0]


def assignment_key(page):
    parsed = urlsplit(page.url)
    assignment = parse_qs(parsed.query).get("assignment_id", [])
    if (parsed.scheme not in ("https", "http") or len(assignment) != 1
            or not assignment[0].isdigit()
            or not re.fullmatch(r"/courses/\d+/gradebook/speed_grader/?", parsed.path)):
        raise DownloadError("speedgrader_assignment_not_recognized")
    return parsed.scheme, parsed.netloc, parsed.path, assignment[0]


def file_controls(page, extension, selector=DEFAULT_FILES):
    """Only visible controls in child frames; never click sidebar resources."""
    pattern = re.compile(r"^[^\r\n]{1,240}" + re.escape(extension) + r"\s*$", re.I)
    matches = []
    for frame in page.frames:
        if frame == page.main_frame or not visible_frame(frame):
            continue
        controls = frame.locator(selector)
        frame_matches = []
        for i in range(controls.count()):
            item = controls.nth(i)
            if item.is_visible() and item.is_enabled() and pattern.fullmatch(element_text(item).strip()):
                frame_matches.append(item)
        # Handles belong to their own frame. Never compare across frame contexts.
        for i, item in enumerate(frame_matches):
            for other in frame_matches[i + 1:]:
                if item.evaluate("(e, other) => e.contains(other) || other.contains(e)", other.element_handle()):
                    raise DownloadError("nested_file_controls_choose_specific_selector")
        matches.extend(frame_matches)
    return matches


def control_token(item):
    # DOM text/attributes stay in memory; never persist a signed download URL.
    value = (element_text(item), item.get_attribute("href"), item.get_attribute("aria-label"))
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def download_filename(original, student, record_id, position, extension):
    # IDs and control positions stay in the private manifest, not the filename.
    # A comma is authoritative for Canvas's sortable name; otherwise use the
    # final word as the surname. Do not guess compound surnames from a roster.
    student = normalize_name(student)
    if "," in student:
        last, first = student.split(",", 1)
        student = last.strip() + " " + first.strip()
    else:
        parts = student.rsplit(None, 1)
        student = " ".join(reversed(parts))
    name = filename_name(student)
    name = name.encode("utf-8")[:96].decode("utf-8", errors="ignore").rstrip(" ._") or "student"
    base = original.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .") or "upload"
    actual_extension = Path(base).suffix
    if len(actual_extension.encode("utf-8")) > 32:
        actual_extension = ""
    stem = base[:-len(actual_extension)] if actual_extension else base
    # Leave room for a collision suffix within Windows's component limit.
    remaining = 240 - len((name + "_" + actual_extension).encode("utf-16-le")) // 2
    stem = stem.encode("utf-16-le")[:remaining * 2].decode("utf-16-le", errors="ignore").rstrip(" .")
    byte_budget = 240 - len((name + "_" + actual_extension).encode("utf-8"))
    stem = stem.encode("utf-8")[:byte_budget].decode("utf-8", errors="ignore").rstrip(" .") or "upload"
    return f"{name}_{stem}{actual_extension}"


def collision_target(folder, filename, number=1):
    path = Path(filename)
    suffix = "" if number == 1 else f" ({number})"
    return Path(folder) / (path.stem + suffix + path.suffix)


class FileBatch:
    def __init__(self, page, out, *, extension=".model", selector=DEFAULT_FILES,
                 prompt=input, timeout=30000, quiet=1, student_reader=None, files_dir=None):
        if extension is not None and not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", extension):
            raise DownloadError("invalid_file_extension")
        self.page, self.out = page, Path(out)
        self.extension, self.selector = extension.lower() if extension else None, selector
        self.prompt, self.timeout, self.quiet = prompt, timeout, quiet
        self.assignment = assignment_key(page)
        self.modern_picker = page.locator(STUDENTS).count() == 0
        self.roster = (student_reader(),) if student_reader else read_roster(page)
        self.records = []
        self.state = {"version": 1, "status": "prepared", "records": self.records,
                      "all_students_verified": False, "all_attempts_verified": False,
                      "question_mapping_verified": False, "contents_deidentified": False,
                      "first_file_locally_checked": False,
                      "student_picker": "modern_current_student" if self.modern_picker else "classic_roster",
                      "scope": "current_displayed_attempt_and_matching_visible_controls"}
        self.private = self.out / "private"
        self.out.mkdir(parents=True, exist_ok=False)
        self.private.mkdir()
        self.files_dir = Path(files_dir).resolve() if files_dir is not None else self.private / "files"
        self.files_dir.mkdir(parents=True, exist_ok=False)
        self.state["download_directory"] = str(self.files_dir)
        (self.private / "incoming").mkdir()
        self.checkpoint()

    def checkpoint(self):
        target = self.private / "manifest.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        temp.replace(target)

    def verify_student(self, expected):
        if assignment_key(self.page) != self.assignment:
            raise DownloadError("assignment_changed")
        if selected_student(self.page) != expected:
            raise DownloadError("selected_student_changed")
        if read_roster(self.page) != self.roster:
            raise DownloadError("student_list_changed")

    def snapshot(self, expected):
        self.verify_student(expected)
        controls = file_controls(self.page, self.extension, self.selector)
        tokens = tuple(control_token(item) for item in controls)
        self.verify_student(expected)
        return controls, tokens

    def stable_controls(self, expected):
        deadline = time.monotonic() + self.timeout / 1000
        previous, since = None, time.monotonic()
        while time.monotonic() < deadline:
            controls, tokens = self.snapshot(expected)
            if tokens != previous:
                previous, since = tokens, time.monotonic()
            elif time.monotonic() - since >= self.quiet:
                return controls, tokens
            self.page.wait_for_timeout(100)
        raise DownloadError("file_controls_not_stable")

    def confirm(self, message, word):
        if self.prompt(message).strip().lower() != word:
            raise DownloadError("stopped_by_user")

    def save_one(self, row, index, tokens):
        current, fresh = self.snapshot(row["student_id"])
        if fresh != tokens:
            raise DownloadError("file_controls_changed")
        record = {"record_id": row["record_id"], "control_position": index + 1,
                  "status": "started"}
        self.records.append(record)
        self.checkpoint()
        # Capture the browser event tied to this click, without extracting its URL.
        with self.page.expect_download(timeout=self.timeout) as event:
            current[index].click(timeout=self.timeout)
        download = event.value
        return self.save_received(row, index, download, record)

    def save_received(self, row, index, download, record=None):
        if record is None:
            record = {"record_id": row["record_id"], "control_position": index + 1, "status": "started"}
            self.records.append(record)
            self.checkpoint()
        staging = self.private / "incoming" / (secrets.token_hex(16) + ".part")
        try:
            download.save_as(staging)
            self.verify_student(row["student_id"])
            if download.failure() is not None:
                raise DownloadError("download_failed")
            suggested = download.suggested_filename
            # Never use a remote filename as a filesystem path or change file type.
            if self.extension and not suggested.lower().endswith(self.extension):
                raise DownloadError("unexpected_download_type")
            if not staging.stat().st_size:
                raise DownloadError("empty_download")
            checksum = hashlib.sha256()
            with staging.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    checksum.update(block)
            filename = download_filename(suggested, row["student_name"], row["record_id"], index + 1, self.extension)
            number = 1
            while True:
                target = collision_target(self.files_dir, filename, number)
                try:
                    destination = target.open("xb")
                    break
                except FileExistsError:
                    number += 1
            try:
                with destination, staging.open("rb") as source:
                    shutil.copyfileobj(source, destination)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            filename = target.name
            saved_path = "files/" + filename if self.files_dir == self.private / "files" else str(target)
            record.update(status="saved", file=saved_path,
                          original_filename=suggested, bytes=target.stat().st_size,
                          sha256=checksum.hexdigest())
            self.checkpoint()
        finally:
            if staging.exists():
                staging.unlink()
            download.delete()

    def run(self, students=1, all_files=False):
        try:
            if self.modern_picker and students != 1:
                raise DownloadError("modern_picker_requires_one_student_pilot_first")
            first = selected_student(self.page)
            start = next((i for i, row in enumerate(self.roster) if row[0] == first), None)
            if start is None:
                raise DownloadError("current_student_not_in_list")
            scope = self.roster[start:] if students == "all" else self.roster[start:start + students]
            if students != "all" and len(scope) != students:
                raise DownloadError("not_enough_remaining_students")
            rows = [{"record_id": "R-" + secrets.token_hex(8), "student_id": key,
                     "student_name": name} for key, name in scope]
            self.state.update(status="running", planned_students=len(rows),
                              pilot_one_file=not all_files, students=rows)
            # The ID key and original names are private, even when filenames use codes.
            with (self.private / "name_key.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["record_id", "student_id", "student_name"])
                writer.writerows((row["record_id"], csv_name(row["student_id"]),
                                  csv_name(row["student_name"])) for row in rows)
            self.checkpoint()
            for number, row in enumerate(rows):
                self.verify_student(first if number == 0 else rows[number - 1]["student_id"])
                if number:
                    self.page.locator(STUDENTS).select_option(row["student_id"], timeout=self.timeout)
                self.confirm(
                    f"Student {number + 1}/{len(rows)}: wait for the quiz iframe. Check the selected "
                    "student, displayed submission and attempt IN THE BROWSER. "
                    "Type ready here only after they match (anything else stops): ", "ready")
                controls, tokens = self.stable_controls(row["student_id"])
                row["matching_visible_controls"] = len(controls)
                print(f"Matching visible file controls: {len(controls)}")
                if not controls:
                    # Absence is unverified: the layout might be unsupported or still unloaded.
                    row["status"] = "no_matching_controls_unverified"
                    self.checkpoint()
                    raise DownloadError("no_matching_controls_unverified")
                self.confirm("Type download to click these file controls (anything else stops): ", "download")
                for index in range(len(controls)):
                    self.save_one(row, index, tokens)
                    if len([r for r in self.records if r["status"] == "saved"]) == 1:
                        self.confirm("Open private/files and private/name_key.csv locally. Check the "
                                     "first file, its contents and student association. Type checked "
                                     "to continue (anything else stops): ", "checked")
                        self.state["first_file_locally_checked"] = True
                        self.checkpoint()
                    if not all_files:
                        self.state["status"] = "pilot_complete"
                        return
                row["status"] = "matching_visible_controls_saved"
                self.checkpoint()
            self.state["status"] = "requested_scope_complete"
        except BaseException as error:
            # Never persist raw browser exceptions: they can include signed URLs/names.
            category = str(error) if type(error) is DownloadError else (
                "stopped_by_user" if isinstance(error, (KeyboardInterrupt, EOFError)) else "download_or_browser_error")
            self.state.update(status="stopped", error=category)
            for record in self.records:
                if record["status"] == "started":
                    record.update(status="failed", error=category)
            raise
        finally:
            self.checkpoint()


def student_count(value):
    if value == "all":
        return value
    try:
        count = int(value)
        if 1 <= count <= MAX_STUDENTS:
            return count
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Use 1 to 5000, or all.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://ufl.instructure.com/", help="HTTPS Canvas home address")
    parser.add_argument("--out", type=Path, help="New run folder outside source, profile and OneDrive")
    parser.add_argument("--channel", choices=("msedge", "chrome", "chromium"), default="msedge")
    parser.add_argument("--students", type=student_count, default=1, help="Count from current student onward, or all")
    parser.add_argument("--all-files", action="store_true", help="Supervised batch; default stops after one file")
    parser.add_argument("--extension", default=".model")
    parser.add_argument("--file-selector", default=DEFAULT_FILES, help="Local CSS adapter for file click targets")
    parser.add_argument("--diagnose", action="store_true", help="Print counts only; no clicks or exports")
    parser.add_argument("--normal-edge", action="store_true", help="Navigate in normal Edge first, then activate locally")
    args = parser.parse_args()
    print("SpeedGrader local pilot " + PILOT_VERSION, flush=True)
    context = None
    native = None
    incoming = None
    try:
        url = validate_url(args.url)
        if args.students != 1 and not args.all_files and not args.diagnose:
            raise DownloadError("batch_requires_all_files")
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", args.extension):
            raise DownloadError("invalid_file_extension")
        root = local_root()
        out = args.out or root / "private-work" / ("speedgrader-" + secrets.token_hex(8))
        profile = root / ("BrowserCheckProfile" if args.normal_edge else "BrowserProfile")
        out, profile = validate_locations(out, profile)
        incoming = root / "private-work" / ("temporary-" + secrets.token_hex(8))
        incoming.mkdir(parents=True)
        if args.normal_edge:
            if args.channel != "msedge":
                raise DownloadError("normal_edge_requires_edge_channel")
            native = NativeEdge(profile)
            native.start(url)
            print("Normal Edge is open. Navigate to SpeedGrader and choose the first student.")
            print("The downloader has not connected yet. Check the loaded submission in the browser.")
            # No Playwright instance exists yet, so a normal terminal read cannot
            # block its event processing or affect the browser's initial launch.
            if input("When ready, type start to activate the local downloader (anything else stops): ").strip().lower() != "start":
                raise DownloadError("stopped_by_user")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            options = {"headless": False, "accept_downloads": not args.diagnose}
            if args.channel != "chromium":
                options["channel"] = args.channel
            # Browser temporary downloads use the same private local storage root.
            # No real student data is read by a connector or sent to this chat.
            if not args.diagnose:
                options["downloads_path"] = str(incoming)
            context = (native.connect(playwright, incoming) if native else
                       playwright.chromium.launch_persistent_context(str(profile), **options))
            try:
                context.set_default_timeout(10000)
                page = context.pages[0] if context.pages else context.new_page()
                if not native:
                    page.goto(url, wait_until="domcontentloaded")
                elif args.diagnose:
                    session = native.browser.new_browser_cdp_session()
                    session.send("Browser.setDownloadBehavior", {"behavior": "deny", "eventsEnabled": True})
                print("SpeedGrader local pilot. Sign in yourself in the dedicated browser.")
                print("Open a completed New Quiz in SpeedGrader. Choose the FIRST student to process.")
                prompt = lambda message: live_prompt(context, message)
                page = await_speedgrader(context, url, prompt)
                if args.diagnose:
                    print(json.dumps({"students_in_current_list": len(read_roster(page)),
                                      "matching_visible_file_controls": len(file_controls(page, args.extension, args.file_selector))}))
                else:
                    print("All output is private. Files keep their original contents, including identifiers.")
                    print("Output folder: " + str(out))
                    FileBatch(page, out, extension=args.extension, selector=args.file_selector, prompt=prompt).run(
                        students=args.students, all_files=args.all_files)
            finally:
                if native:
                    native.close()
                else:
                    context.close()
                context = None
        print("Diagnostic counts complete." if args.diagnose else
              "Finished requested scope. Check the private manifest for limits and results.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        category = str(error) if type(error) in (DownloadError, NativeEdgeError) else (
            "stopped_by_user" if isinstance(error, (KeyboardInterrupt, EOFError)) else "local_setup_or_browser_error")
        if category == "close_comparison_edge_window_then_retry":
            print("Close the separate Edge comparison window, then restart this launcher. "
                  "Your usual Edge window can stay open.", file=sys.stderr)
        if category == "modern_picker_requires_one_student_pilot_first":
            print("The modern student picker currently supports the one-student pilot. "
                  "Validate that download before enabling modern student traversal.", file=sys.stderr)
        print("Stopped (" + category + "). Completed files remain local; inspect the private manifest.", file=sys.stderr)
        return 2
    finally:
        if native:
            native.close()
        if incoming is not None:
            # Remove only an empty temporary directory; retain unexpected files.
            try:
                incoming.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
