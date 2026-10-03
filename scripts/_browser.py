"""A very small Chrome driver, enough to load a page, click something and take a picture.

Why this exists rather than Playwright or Selenium: the only thing the project needs a browser
for is producing screenshots, and those two bring a toolchain and a download step with them.
The DevTools protocol is a WebSocket carrying JSON, and ``websockets`` is already installed
because the API serves WebSockets. The whole driver is about a hundred lines.

What it deliberately does not do: wait for elements, handle frames, or survive a page that
navigates away. It is for taking screenshots of a page that is known to work, not for testing.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from websockets.sync.client import connect

# How long any single DevTools call may take. A screenshot of a busy canvas is the slowest of
# them and still finishes in well under a second.
CALL_TIMEOUT_S: Final = 30.0


def find_browser() -> str:
    """Find Chrome or Chromium, or say which names were tried."""
    override = os.environ.get("CHROME")
    if override:
        return override
    names = ("google-chrome", "chromium", "chromium-browser", "chrome", "google-chrome-stable")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(f"no browser found; tried {', '.join(names)}. Set CHROME to its path.")


def _free_port() -> int:
    """Ask the operating system for a port nobody is using."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Page:
    """One browser tab, driven over the DevTools protocol."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._next_id = 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        """Send one command and return its result, raising whatever the browser reported."""
        self._next_id += 1
        message_id = self._next_id
        self._websocket.send(json.dumps({"id": message_id, "method": method, "params": params}))
        deadline = time.monotonic() + CALL_TIMEOUT_S
        while time.monotonic() < deadline:
            # Events arrive on the same socket as replies. Anything that is not the reply being
            # waited for is an event, and this driver has no use for events.
            message = json.loads(self._websocket.recv(timeout=CALL_TIMEOUT_S))
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            result: dict[str, Any] = message.get("result", {})
            return result
        raise TimeoutError(f"{method} did not answer within {CALL_TIMEOUT_S}s")

    def goto(self, url: str) -> None:
        """Load a page and wait for it to settle."""
        self.call("Page.enable")
        self.call("Page.navigate", url=url)
        # A single-page application finishes loading long before it finishes fetching, so the
        # load event is a starting point, not an answer. Callers wait for what they need.
        self.wait_for("document.readyState === 'complete'")

    def evaluate(self, expression: str) -> Any:
        """Run JavaScript in the page and return its value."""
        result = self.call(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(f"{expression}: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout_s: float = 30.0, what: str = "") -> None:
        """Poll an expression until it is true."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.1)
        raise TimeoutError(f"timed out waiting for {what or expression}")

    def click(self, selector: str) -> None:
        """Click the first element matching a CSS selector."""
        clicked = self.evaluate(
            f"(() => {{ const e = document.querySelector({selector!r});"
            f" if (!e) return false; e.click(); return true; }})()"
        )
        if not clicked:
            raise RuntimeError(f"nothing matched {selector!r}")

    def screenshot(self, destination: Path) -> Path:
        """Save a picture of the visible page."""
        result = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        destination.write_bytes(base64.b64decode(result["data"]))
        return destination


@contextmanager
def browser(width: int, height: int, profile: Path) -> Iterator[Page]:
    """Run a headless browser and yield its one tab."""
    port = _free_port()
    process = subprocess.Popen(
        [
            find_browser(),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            f"--remote-debugging-port={port}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = _wait_for_target(port, process)
        with connect(target, max_size=64 * 1024 * 1024) as websocket:
            page = Page(websocket)
            # --window-size counts the whole window, so the page itself ends up shorter than
            # asked for and screenshots come out cropped. This sets the viewport to exactly the
            # size wanted.
            page.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=height,
                deviceScaleFactor=1,
                mobile=False,
            )
            yield page
    finally:
        process.terminate()
        process.wait(timeout=30)


def _wait_for_target(port: int, process: subprocess.Popen[bytes]) -> str:
    """Wait for the browser to open its debugging port and report a tab to drive."""
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"the browser exited with {process.returncode}")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=5
            ) as response:
                targets = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(0.25)
            continue
        for entry in targets:
            if entry.get("type") == "page" and entry.get("webSocketDebuggerUrl"):
                return str(entry["webSocketDebuggerUrl"])
        time.sleep(0.25)
    raise SystemExit("the browser never reported a tab to drive")
