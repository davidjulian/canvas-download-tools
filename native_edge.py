"""Start a dedicated normal Edge window, then attach only on local activation.

The browser endpoint stays in memory. Never print it or inspect browser records
from a connector. This module runs inside the instructor's local application.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import time


class NativeEdgeError(Exception):
    """Fixed error categories only."""


def browser_executable(channel):
    if channel not in ("chrome", "msedge"):
        raise NativeEdgeError("unsupported_browser")
    if sys.platform == "darwin":
        application, executable = (("Google Chrome", "Google Chrome") if channel == "chrome" else
                                   ("Microsoft Edge", "Microsoft Edge"))
        for base in (Path("/Applications"), Path.home() / "Applications"):
            candidate = base / (application + ".app") / "Contents" / "MacOS" / executable
            if candidate.is_file():
                return candidate
        raise NativeEdgeError("selected_browser_not_found")
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            relative = ("Google/Chrome/Application/chrome.exe" if channel == "chrome" else
                        "Microsoft/Edge/Application/msedge.exe")
            candidate = Path(base) / relative
            if candidate.is_file():
                return candidate
    raise NativeEdgeError("selected_browser_not_found")


def edge_executable():
    return browser_executable("msedge")


def parse_endpoint(text):
    """Only allow a local browser endpoint, not an arbitrary URL from disk."""
    lines = text.splitlines()
    if (len(lines) != 2 or not lines[0].isdigit()
            or not 1 <= int(lines[0]) <= 65535
            or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", lines[1])):
        raise NativeEdgeError("local_browser_endpoint_invalid")
    return "ws://127.0.0.1:" + lines[0] + lines[1]


class NativeEdge:
    def __init__(self, profile, *, headless=False, channel=None):
        self.profile = Path(profile).resolve()
        self.headless = headless
        self.channel = channel or ("chrome" if sys.platform == "darwin" else "msedge")
        self.process = None
        self.browser = None
        self.endpoint = None

    def start(self, url, timeout=20):
        self.profile.mkdir(parents=True, exist_ok=True)
        arguments = [str(browser_executable(self.channel)), "--user-data-dir=" + str(self.profile),
                     "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                     "--new-window", "--no-first-run"]
        if self.headless:
            arguments.append("--headless=new")
        arguments.append(url)
        started = time.time_ns()
        self.process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                # A prior session's endpoint must never be reused. Edge rewrites
                # this file for this newly started process with its selected port.
                if active.stat().st_mtime_ns >= started:
                    self.endpoint = parse_endpoint(active.read_text(encoding="utf-8"))
                    return
            except (OSError, NativeEdgeError):
                pass
            time.sleep(.1)  # No Playwright connection exists at this stage.
        if self.process.poll() is not None:
            raise NativeEdgeError("close_comparison_edge_window_then_retry")
        raise NativeEdgeError("normal_edge_local_connection_unavailable")

    def connect(self, playwright, artifacts):
        # Edge may relaunch itself and let its initial launcher process exit.
        # The fresh endpoint, not the launcher PID, identifies this session.
        if self.process is None or self.endpoint is None:
            raise NativeEdgeError("normal_edge_window_closed")
        artifacts = Path(artifacts).resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        self.browser = playwright.chromium.connect_over_cdp(
            self.endpoint, artifacts_dir=str(artifacts), is_local=True, timeout=15000)
        if len(self.browser.contexts) != 1:
            raise NativeEdgeError("normal_edge_context_not_unique")
        return self.browser.contexts[0]

    def close(self):
        closed = False
        if self.browser:
            try:
                # Browser.close() on a CDP connection only disconnects. Explicitly
                # close the owned dedicated browser so its local endpoint stops.
                session = self.browser.new_browser_cdp_session()
                session.send("Browser.close")
                closed = True
            except Exception:
                pass
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if not closed and self.endpoint is not None:
            # Also close an owned browser if collection was cancelled before
            # activation. This connection performs no page or cookie inspection.
            from playwright.sync_api import sync_playwright
            try:
                with sync_playwright() as controller:
                    browser = controller.chromium.connect_over_cdp(
                        self.endpoint, no_defaults=True, timeout=3000)
                    try:
                        browser.new_browser_cdp_session().send("Browser.close")
                    finally:
                        browser.close()
            except Exception:
                pass
        self.endpoint = None
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Only the process started above, never an existing Edge session.
                self.process.terminate()
                self.process.wait(timeout=5)
