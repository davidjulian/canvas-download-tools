"""Remember a confirmed control's structure locally, without student data."""
import hashlib
import json
from pathlib import Path

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
