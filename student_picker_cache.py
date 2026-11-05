"""Remember a confirmed control's structure locally, without student data."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from canvas_helpers import collection_origin

from speedgrader_download import (assignment_key, bind_name_control, bound_frame,
    combo_student_name, frame_signature, modern_student_combo, modern_student_record,
    navigation_button)


def origin_signature(page):
    scheme, host, _, _ = assignment_key(page)
    return hashlib.sha256((scheme + "://" + host).encode()).hexdigest()


def control_signature(item):
    # Values, names, text, IDs and page addresses are never cached.
    shape = item.evaluate("""e => {
      const describe = n => [n.tagName, n.getAttribute('role'), n.getAttribute('type'),
                            n.getAttribute('aria-haspopup'), n.getAttribute('contenteditable')];
      return [describe(e), Array.from(e.children).map(describe),
              e.parentElement ? Array.from(e.parentElement.children).map(describe) : []];
    }""")
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def saved_picker_available(path, home_url):
    """Report structural cache availability without returning names or page data."""
    try:
        target = Path(path)
        if not home_url or target.stat().st_size > 65536:
            return False
        data = json.loads(target.read_text(encoding="utf-8"))
        parsed = urlsplit(collection_origin(home_url))
        origin = hashlib.sha256((parsed.scheme + "://" + parsed.netloc).encode()).hexdigest()
        return (data.get("version") == 1 and data.get("origin") == origin
                and isinstance(data.get("frame_path"), list)
                and all(isinstance(part, str) for part in data["frame_path"])
                and all(isinstance(data.get(key), str) and bool(data[key])
                        for key in ("selector", "tag", "frame_signature", "control_signature")))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def remember_picker(page, path):
    binding = getattr(page, "_pilot_confirmed_student_picker", None)
    if not isinstance(binding, dict):
        return
    item = modern_student_combo(page)
    combo_student_name(item)
    data = {"version": 1, "origin": origin_signature(page),
            "frame_path": binding["frame_path"], "selector": binding["selector"],
            "tag": binding["tag"], "frame_signature": binding["frame_signature"],
            "control_signature": control_signature(item)}
    target = Path(path)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(target)


def restore_picker(page, path):
    previous = getattr(page, "_pilot_confirmed_student_picker", None)
    restored = False
    try:
        target = Path(path)
        if target.stat().st_size > 65536:
            return False
        data = json.loads(target.read_text(encoding="utf-8"))
        if data["version"] != 1 or data["origin"] != origin_signature(page):
            return False
        frame = bound_frame(page, data["frame_path"])
        if frame_signature(frame) != data["frame_signature"]:
            return False
        item = frame.locator(data["selector"])
        if (item.count() != 1 or not item.is_visible()
                or item.evaluate("e => e.tagName") != data["tag"]
                or control_signature(item) != data["control_signature"]):
            return False
        navigation_button(page, "next")
        navigation_button(page, "previous")
        bind_name_control(page, frame, item, assignment_key(page))
        modern_student_record(page)
        restored = True
        return True
    except Exception:
        return False
    finally:
        # A failed restoration must not leave a partially installed binding.
        if not restored:
            page._pilot_confirmed_student_picker = previous
