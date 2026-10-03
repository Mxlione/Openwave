"""Record the command line's real output as SVG, for the README and the documentation site.

Why SVG rather than an animated GIF: the output is text, and a GIF of text is a pile of
compressed bitmaps that is heavy to load, blurry when the page scales it, impossible to
copy from, and invisible to a screen reader. Rich can export exactly what it printed, so the
image in the README is the same characters and the same colours the terminal showed.

Why run the commands rather than write the output by hand: a screenshot that was typed out
drifts from the program the first time an option changes, and nothing fails when it does. These
come from the real commands against the simulator, so regenerating them is the way to notice.

Run it with::

    python scripts/capture_cli.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from rich.console import Console
from typer.testing import CliRunner

from openwave import cli

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT: Final = ROOT / "docs" / "images"

# Wide enough for the scan table's widest row without wrapping, which a reader cannot undo.
TERMINAL_WIDTH: Final = 100

# What to record, as (filename stem, title, arguments).
CAPTURES: Final = (
    ("cli-scan-fm", "openwave scan fm --demo", ["scan", "fm", "--demo"]),
    ("cli-scan-tv", "openwave scan tv --demo", ["scan", "tv", "--demo"]),
    ("cli-devices", "openwave devices", ["devices"]),
    ("cli-help", "openwave --help", ["--help"]),
)


def capture(stem: str, title: str, args: list[str]) -> Path:
    """Run one command with a recording console and write its output as SVG."""
    recorder = Console(
        record=True,
        width=TERMINAL_WIDTH,
        force_terminal=True,
        # A recorded progress bar is a single frozen frame of something that was moving, which
        # reads as a bar stuck half way. The scan prints its table after, and that is the part
        # worth showing.
        force_interactive=False,
    )
    original = cli.console
    cli.console = recorder
    try:
        result = CliRunner().invoke(cli.app, args, color=True)
    finally:
        cli.console = original

    if result.exit_code != 0:
        raise SystemExit(f"{title} exited {result.exit_code}:\n{result.output}")

    # `--help` is printed by Click through its own console, not through ours, so for those the
    # captured output is empty and the runner's text is what there is.
    if not recorder.export_text(clear=False).strip():
        recorder.print(result.output.rstrip("\n"), markup=False, highlight=False)

    destination = OUTPUT / f"{stem}.svg"
    destination.write_text(recorder.export_svg(title=title, clear=False))
    return destination


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for stem, title, args in CAPTURES:
        destination = capture(stem, title, args)
        size_kb = destination.stat().st_size / 1024
        print(f"{destination.relative_to(ROOT)}  {size_kb:.0f} kB  ({title})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
