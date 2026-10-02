"""The ``openwave`` command line entry point."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from openwave import __version__
from openwave.core.units import format_frequency
from openwave.sdr import DeviceError, list_devices, open_device
from openwave.sdr.device_manager import describe_device, driver_names

app = typer.Typer(
    name="openwave",
    help="Scan, detect and play publicly broadcast radio and TV services.",
    no_args_is_help=True,
    add_completion=False,
)

scan_app = typer.Typer(help="Scan a band and list the services found.", no_args_is_help=True)
app.add_typer(scan_app, name="scan")

console = Console()
errors = Console(stderr=True)

DeviceOption = Annotated[
    str,
    typer.Option(
        "--device",
        "-d",
        help=(
            "Receiver to use: 'mock' for the simulator, 'rtlsdr' or 'rtlsdr:1' for a dongle, "
            "or 'file:path.cu8' to replay a capture."
        ),
    ),
]


@app.command()
def version() -> None:
    """Print the OpenWave version."""
    console.print(f"OpenWave {__version__}")


@app.command()
def devices() -> None:
    """List the receivers OpenWave can currently open.

    Finding nothing is the normal result without hardware attached: the simulator is always
    listed, and it is enough to develop and test against.
    """
    found = list_devices()
    table = Table(title="Receivers", title_justify="left", header_style="bold")
    table.add_column("Specification", style="cyan", no_wrap=True)
    table.add_column("Device")
    table.add_column("Serial", style="dim")

    for device in found:
        table.add_row(
            f"{device.driver}:{device.index}",
            device.label,
            device.serial or "-",
        )
    console.print(table)

    if not any(device.driver == "rtlsdr" for device in found):
        # escape(), or Rich reads the [rtlsdr] extra as a markup tag and silently prints an
        # install command that does not install the driver.
        install = escape('pip install "openwave[rtlsdr]"')
        console.print(
            f"\n[dim]No RTL-SDR dongle found. Install the driver with[/dim] {install} "
            "[dim]and check the udev rules if one is plugged in.[/dim]"
        )
    console.print(
        f"\n[dim]Drivers available: {', '.join(driver_names())}. "
        "A capture is opened as file:<path> and is not listed above.[/dim]"
    )


@app.command()
def probe(device: DeviceOption = "mock") -> None:
    """Open a receiver, report what it can do, and read a few samples.

    The quickest way to tell whether a receiver works at all, and the first thing worth running
    when validating the RTL-SDR driver against real hardware.
    """
    try:
        # describe_device parses the specification, so it has to be inside the handler: an
        # unknown driver must produce the same clean message as a missing one, not a traceback.
        console.print(f"[bold]{escape(describe_device(device))}[/bold]")
        with open_device(device) as receiver:
            info = receiver.info
            rates = receiver.supported_sample_rates_hz
            tuning = receiver.tuning_range

            console.print(f"  driver          {info.driver}")
            console.print(f"  device          {info.label}")
            if info.serial:
                console.print(f"  serial          {info.serial}")
            console.print(
                "  tuning range    "
                f"{format_frequency(tuning.min_hz)} to {format_frequency(tuning.max_hz)}"
            )
            if rates is None:
                console.print("  sample rates    continuous")
            else:
                console.print(
                    "  sample rates    " + ", ".join(f"{rate / 1e6:g}" for rate in rates) + " MS/s"
                )

            rate = 2_400_000.0 if rates is None else rates[-1]
            receiver.set_sample_rate(rate)
            receiver.set_center_freq(max(tuning.min_hz, min(98_000_000.0, tuning.max_hz)))
            samples = receiver.read_samples(4096)
            console.print(
                f"  read            {samples.size} samples at {rate / 1e6:g} MS/s, "
                f"tuned to {format_frequency(receiver.center_freq_hz)}"
            )
            console.print("[green]  the receiver works[/green]")
    except DeviceError as error:
        # The message is escaped because it routinely contains square brackets, from the
        # optional-extra install command to a numpy shape.
        errors.print(f"[red]{type(error).__name__}:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error


@scan_app.command("fm")
def scan_fm(device: DeviceOption = "mock") -> None:
    """Scan the FM broadcast band (87.5-108 MHz) and list the stations found."""
    raise typer.Exit(_not_implemented("FM band scanning", "v0.1", task=22))


@scan_app.command("tv")
def scan_tv(device: DeviceOption = "mock") -> None:
    """Scan the DVB-T multiplexes and list the channels found."""
    raise typer.Exit(_not_implemented("DVB-T scanning", "v0.4", task=42))


def _not_implemented(what: str, milestone: str, task: int) -> int:
    """Report an unimplemented command, pointing at the task that will deliver it."""
    errors.print(f"[yellow]{what} is not implemented yet.[/yellow]")
    errors.print(f"It lands in [bold]{milestone}[/bold] — task {task} in TASKS.md.")
    errors.print("Contributions welcome: https://github.com/Mxlione/Openwave")
    return 1


if __name__ == "__main__":
    app()
