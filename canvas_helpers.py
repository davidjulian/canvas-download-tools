"""Shared local Canvas helpers adapted from canvas-quiz-log-exporter."""
import re
import unicodedata
from urllib.parse import urlsplit

class UIError(Exception):
    pass

def safe_error_category(error):
    """Classify locally; never return the exception message or unknown text."""
    message = str(error).casefold()
    rules = (
        (("trustedscript", "trusted type", "content security policy", "unsafe-eval"), "page_script_security_restriction"),
        (("execution context was destroyed", "cannot find context", "navigation"), "page_navigated_during_inspection"),
        (("has been closed", "target closed", "page closed"), "browser_or_tab_closed"),
        (("frame was detached", "frame has been detached"), "frame_detached"),
        (("not an htmlelement",), "non_html_element"),
        (("syntaxerror",), "page_script_syntax_error"),
        (("typeerror",), "page_script_type_error"),
        (("timeout", "timed out"), "inspection_timeout"),
    )
    return next((category for terms, category in rules if any(term in message for term in terms)), "browser_inspection_error")


def element_text(item):
    try:
        return item.inner_text(timeout=3000)
    except Exception as error:
        if safe_error_category(error) != "non_html_element":
            raise
        # SVG navigation links do not implement HTMLElement.innerText.
        return item.text_content(timeout=3000) or ""


def normalize_name(value):
    value = " ".join(unicodedata.normalize("NFC", value).split())
    return "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()


def filename_name(value):
    value = re.sub(r'[<>:"/\\|?*]', "_", normalize_name(value))
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")[:60].rstrip(" ._")
    return value or "student"


def csv_name(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def visible_frame(frame):
    current = frame
    while current.parent_frame is not None:
        if not current.frame_element().is_visible():
            return False
        current = current.parent_frame
    return True


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise UIError("Enter a valid HTTPS Canvas address without spaces.")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise UIError("Enter a valid HTTPS Canvas address.") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise UIError("Use your HTTPS Canvas home address without sign-in tokens, query parameters or fragments.")
    return value
