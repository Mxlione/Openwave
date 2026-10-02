"""The ``openwave`` command line entry point."""

from __future__ import annotations

import typer
from rich.console import Console

from openwave import __version__

app = typer.Typer(
    name="openwave",
    help="Scan, detect and play publicly broadcast radio and TV services.",
    no_args_is_help=True,
    add_completion=False,
)

scan_app = typer.Typer(help="Scan a band and list the services found.", no_args_is_help=True)
app.add_typer(scan_app, name="scan")

console = Console()


@app.command()
def version() -> None:
    """Print the OpenWave version."""
    console.print(f"OpenWave {__version__}")


@scan_app.command("fm")
def scan_fm() -> None:
    """Scan the FM broadcast band (87.5-108 MHz) and list the stations found."""
    raise typer.Exit(_not_implemented("FM band scanning", "v0.1", task=22))


@scan_app.command("tv")
def scan_tv() -> None:
    """Scan the DVB-T multiplexes and list the channels found."""
    raise typer.Exit(_not_implemented("DVB-T scanning", "v0.4", task=42))


def _not_implemented(what: str, milestone: str, task: int) -> int:
    """Report an unimplemented command, pointing at the task that will deliver it."""
    console.print(f"[yellow]{what} is not implemented yet.[/yellow]")
    console.print(f"It lands in [bold]{milestone}[/bold] — task {task} in TASKS.md.")
    console.print("Contributions welcome: https://github.com/Mxlione/Openwave")
    return 1


if __name__ == "__main__":
    app()
