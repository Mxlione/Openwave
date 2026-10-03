"""Screenshot the interface against the simulator, for the README and the documentation site.

The screenshots are taken from the real interface talking to the real API, with the demo
receiver supplying the signals. Nothing here is mocked at the browser's level: the station list
in the picture is the one the scanner produced from a synthesised band.

It needs the interface built (``npm run build`` in ``frontend``) and Chrome or Chromium. Set
``CHROME`` if it is somewhere unusual.

Run it with::

    python scripts/capture_interface.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _browser import Page, browser

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT: Final = ROOT / "docs" / "images"

PORT: Final = 8732
BASE: Final = f"http://127.0.0.1:{PORT}"
API: Final = f"{BASE}/api/v1"

# A desktop-shaped window. Narrow enough that the image stays readable inlined in a README,
# wide enough that the layout is the one a desktop visitor sees rather than the phone fallback.
WINDOW: Final = (1280, 860)

# How many frames the waterfall needs before it has covered its canvas, read from the page
# rather than guessed. Each frame scrolls the canvas up by one row and draws one row at the
# bottom, so the last of the blank space leaves the top after as many frames as the canvas is
# tall in rows. A row is at least one pixel, so the canvas height is always enough. Guessing
# instead gave a picture with a black band across the top of the waterfall.
WATERFALL_FRAMES_NEEDED: Final = "document.querySelector('canvas.waterfall').height"

# ScanState, as the API spells it. A scan that reached one of these will not change again.
SETTLED: Final = frozenset({"complete", "failed", "cancelled"})

# Counting the rows of a list is how this script knows a page has finished fetching, rather
# than sleeping and hoping.
ROWS_PRESENT: Final = "document.querySelectorAll('tbody tr').length"


def request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Make one API call and return the decoded response."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    call = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(call, timeout=120) as response:
        return json.loads(response.read())


@contextmanager
def server(data_home: Path) -> Iterator[None]:
    """Run the API against the simulator, with its own data directory.

    The favourites file lives under XDG_DATA_HOME. Pointing it at a temporary directory keeps
    the screenshots from showing, or overwriting, whatever the person running this has saved.
    """
    environment = {**os.environ, "XDG_DATA_HOME": str(data_home)}
    process = subprocess.Popen(
        [sys.executable, "-m", "openwave.cli", "serve", "--demo", "--port", str(PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    try:
        for _ in range(120):
            if process.poll() is not None:
                output = process.stdout.read().decode() if process.stdout else ""
                raise SystemExit(f"the server exited before it was ready:\n{output}")
            try:
                request("GET", "/health")
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.5)
        else:
            raise SystemExit("the server never answered /health")
        yield
    finally:
        process.terminate()
        process.wait(timeout=30)


def run_scan(band: str) -> list[dict[str, Any]]:
    """Start a scan, wait for it, and return what it found."""
    started = request("POST", "/scans", {"band": band})
    scan_id = started["id"]
    for _ in range(600):
        detail = request("GET", f"/scans/{scan_id}")
        if detail["state"] in SETTLED:
            if detail["state"] != "complete":
                raise SystemExit(f"the {band} scan ended {detail['state']}: {detail.get('error')}")
            results = detail.get(band) or {}
            return list(results.get("stations") or results.get("channels") or [])
        time.sleep(0.5)
    raise SystemExit(f"the {band} scan never finished")


def shoot(page: Page, stem: str) -> Path:
    """Save the current page and report what it weighs."""
    destination = page.screenshot(OUTPUT / f"{stem}.png")
    print(f"{destination.relative_to(ROOT)}  {destination.stat().st_size / 1024:.0f} kB")
    return destination


def capture_lists(page: Page) -> None:
    """Photograph the station and channel lists, once their rows have arrived."""
    for stem, path in (("ui-stations", "/stations"), ("ui-channels", "/channels")):
        page.goto(f"{BASE}{path}")
        page.wait_for(f"{ROWS_PRESENT} > 0", what=f"rows on {path}")
        shoot(page, stem)


def capture_spectrum(page: Page) -> None:
    """Photograph the live spectrum, with the waterfall actually filled in.

    The feed only runs once somebody presses Start, so a screenshot taken on load is two black
    rectangles: a picture of the project's most visual feature looking broken. Pressing the
    button and waiting for frames is the whole reason this script drives the browser rather
    than asking Chrome for a screenshot of a URL.
    """
    page.goto(f"{BASE}/spectrum")
    page.wait_for("!!document.querySelector('.controls button.primary')", what="the Start button")
    page.click(".controls button.primary")

    # The readout prints "N frames". Reading it back measures how much the waterfall has drawn,
    # which is the thing that has to be true for the picture to be worth taking.
    frames_drawn = (
        "(document.querySelector('.readout')?.textContent?.match(/(\\d+) frames/)?.[1] ?? 0)"
    )
    needed = int(page.evaluate(WATERFALL_FRAMES_NEEDED))
    page.wait_for(
        f"Number({frames_drawn}) >= {needed}",
        timeout_s=180.0,
        what=f"{needed} spectrum frames, enough to cover the waterfall",
    )
    print(f"spectrum: {page.evaluate(frames_drawn)} frames drawn")
    shoot(page, "ui-spectrum")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        with server(workspace / "data"):
            stations = run_scan("fm")
            channels = run_scan("tv")
            print(f"scanned: {len(stations)} stations, {len(channels)} channels")
            if not stations or not channels:
                raise SystemExit("the demo scans found nothing; there is nothing to photograph")

            # One favourite, so the list shows both states of the button rather than only the
            # empty one.
            first = stations[0]
            request(
                "POST",
                "/favourites",
                {
                    "kind": "station",
                    "name": first.get("name") or f"{first['freq_hz'] / 1e6:.1f} MHz",
                    "freq_hz": first["freq_hz"],
                },
            )

            with browser(WINDOW[0], WINDOW[1], workspace / "profile") as page:
                capture_lists(page)
                capture_spectrum(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
