"""The ``openwave`` command line entry point."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from openwave import __version__
from openwave.core.frequency_manager import BandPlan
from openwave.core.scanner import ScanSettings
from openwave.core.signal_detector import DEFAULT_DETECTION_THRESHOLD_DB
from openwave.core.units import format_frequency
from openwave.media.libvlc import LibVlcPlayer, PlaybackUnavailableError
from openwave.media.wav import WAV_HEADER_SIZE, WavFormat, wav_header
from openwave.radio.band import FM_BAND_PLAN, FM_BAND_PLANS
from openwave.radio.demo import demo_receiver
from openwave.radio.listener import FmListener, ListenSettings
from openwave.radio.station import Station
from openwave.radio.station_detector import FmScanResult, scan_fm
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
def scan_fm_command(
    device: DeviceOption = "mock",
    band: Annotated[
        str,
        typer.Option("--band", "-b", help=f"Band plan: {', '.join(FM_BAND_PLANS)}."),
    ] = "fm",
    threshold_db: Annotated[
        float,
        typer.Option(
            "--threshold",
            "-t",
            help="Signal-to-noise ratio in dB for a channel to count as occupied.",
        ),
    ] = DEFAULT_DETECTION_THRESHOLD_DB,
    sample_rate: Annotated[
        float | None,
        typer.Option("--sample-rate", "-r", help="Sample rate in Hz. Default: the fastest usable."),
    ] = None,
    stereo: Annotated[
        bool,
        typer.Option(
            "--stereo/--no-stereo",
            help="Demodulate each station to decide stereo. Skipping it is quicker.",
        ),
    ] = True,
    demo: Annotated[
        bool,
        typer.Option(
            "--demo",
            help=(
                "Scan an invented band carrying eight stations, to see what a scan looks like "
                "without a receiver. Overrides --device."
            ),
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result as JSON instead of a table.")
    ] = False,
) -> None:
    """Scan the FM broadcast band and list the stations found.

    Measures the power in every channel on the grid, then returns to each occupied one and
    demodulates it to decide whether it is stereo.

    With no receiver attached, --demo scans an invented band so the output can be seen.
    """
    plan = _band_plan(band)
    settings = ScanSettings(sample_rate_hz=sample_rate, threshold_db=threshold_db)

    try:
        with demo_receiver() if demo else open_device(device) as receiver:
            if not as_json:
                label = "an invented band" if demo else escape(receiver.info.label)
                console.print(f"Scanning {plan} with [bold]{label}[/bold]")
            result = _run_scan(receiver, plan, settings, stereo=stereo, quiet=as_json)
    except DeviceError as error:
        errors.print(f"[red]{type(error).__name__}:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error

    if as_json:
        console.print_json(_scan_as_json(result, plan))
        return
    _print_stations(result)


def _band_plan(name: str) -> BandPlan:
    """Look up a band plan by name, or exit with the list of valid names."""
    plan = FM_BAND_PLANS.get(name.lower())
    if plan is None:
        errors.print(
            f"[red]Unknown band {name!r}.[/red] Available bands: {', '.join(FM_BAND_PLANS)}."
        )
        raise typer.Exit(2)
    return plan


def _run_scan(
    receiver: Any, plan: BandPlan, settings: ScanSettings, *, stereo: bool, quiet: bool
) -> FmScanResult:
    """Run a scan, with a progress bar unless output is meant to be machine-readable."""
    if quiet:
        return scan_fm(receiver, plan=plan, scan_settings=settings, identify=stereo)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        sweep = progress.add_task("Sweeping the band", total=None)
        identify = progress.add_task("Checking stereo", total=None, visible=False)

        def on_sweep(index: int, total: int, segment: object) -> None:
            progress.update(sweep, completed=index, total=total)

        def on_identify(index: int, total: int, freq_hz: float) -> None:
            progress.update(
                identify,
                completed=index,
                total=total,
                visible=True,
                description=f"Checking stereo at {format_frequency(freq_hz)}",
            )

        return scan_fm(
            receiver,
            plan=plan,
            scan_settings=settings,
            identify=stereo,
            sweep_progress=on_sweep,
            identify_progress=on_identify,
        )


def _print_stations(result: FmScanResult) -> None:
    """Render a scan result as a table."""
    if not result.stations:
        console.print(
            "[yellow]No stations found.[/yellow] With the plain simulator this is expected: it "
            "carries nothing unless told to, so try [bold]--demo[/bold] to see what a scan "
            "looks like. Against real hardware, check the antenna, lower [bold]--threshold[/bold] "
            "and raise the gain."
        )
        return

    # The name column only appears once there is something to put in it. RDS decoding lands in
    # v0.3, so until then an always-empty column reads as a bug rather than as a future feature.
    named = any(station.name for station in result.stations)

    table = Table(header_style="bold", title_justify="left")
    table.add_column("Frequency", justify="right", style="cyan", no_wrap=True)
    table.add_column("SNR", justify="right")
    table.add_column("Power", justify="right", style="dim")
    table.add_column("Mode")
    if named:
        table.add_column("Name")

    for station in result.stations:
        row = [
            f"{station.freq_mhz:.1f} MHz",
            f"{station.snr_db:.1f} dB",
            f"{station.power_dbfs:.1f} dBFS",
            _mode_of(station),
        ]
        if named:
            row.append(station.name or "")
        table.add_row(*row)
    console.print(table)
    console.print(f"[dim]{result}[/dim]")
    if not named:
        console.print("[dim]Station names arrive with RDS decoding in v0.3.[/dim]")
    if not result.scan.is_complete:
        console.print(
            f"[yellow]Only {result.scan.coverage:.0%} of the band was reachable[/yellow] "
            f"({len(result.scan.skipped_segments)} tuning positions skipped). This is normal "
            "when replaying a capture, which holds one window of spectrum."
        )


def _mode_of(station: Station) -> str:
    """How to show a station's stereo status, including when it was not checked."""
    if station.stereo is None:
        return "[dim]unchecked[/dim]"
    return "stereo" if station.stereo else "mono"


def _scan_as_json(result: FmScanResult, plan: BandPlan) -> str:
    """Serialise a scan result for scripts and for the API to reuse later."""
    payload = {
        "band": plan.name,
        "sample_rate_hz": result.scan.sample_rate_hz,
        "duration_s": round(result.scan.duration_s, 3),
        "channels_measured": result.scan.channels_measured,
        "coverage": round(result.scan.coverage, 4),
        "complete": result.scan.is_complete,
        "stations": [station.model_dump() for station in result.stations],
    }
    return json.dumps(payload, indent=2)


@scan_app.command("tv")
def scan_tv(device: DeviceOption = "mock") -> None:
    """Scan the DVB-T multiplexes and list the channels found."""
    raise typer.Exit(_not_implemented("DVB-T scanning", "v0.4", task=42))


@app.command()
def listen(
    frequency: Annotated[
        float,
        typer.Argument(
            help="Frequency to listen to, in MHz. For example 98.0, or 98000000 in hertz."
        ),
    ],
    device: DeviceOption = "mock",
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Listen to an invented band instead of a receiver."),
    ] = False,
    seconds: Annotated[
        float | None,
        typer.Option("--seconds", "-s", help="Stop after this long. Default: until interrupted."),
    ] = None,
    record: Annotated[
        Path | None,
        typer.Option(
            "--record",
            "-o",
            help="Write the audio to a WAV file instead of playing it. Needs --seconds.",
        ),
    ] = None,
) -> None:
    """Tune one FM station and play it through libVLC.

    Press Ctrl-C to stop. With --demo, listens to an invented band, which is a way to hear that
    the demodulator works without owning a receiver.
    """
    freq_hz = frequency * 1e6 if frequency < 1e6 else frequency
    if not FM_BAND_PLAN.contains(freq_hz) and not record:
        console.print(
            f"[yellow]{format_frequency(freq_hz)} is outside the FM broadcast band[/yellow] "
            f"({format_frequency(FM_BAND_PLAN.start_hz)} to "
            f"{format_frequency(FM_BAND_PLAN.end_hz)}). Carrying on anyway."
        )
    if record is not None and seconds is None:
        errors.print("[red]--record needs --seconds:[/red] a file has to end somewhere.")
        raise typer.Exit(2)

    try:
        receiver = demo_receiver() if demo else open_device(device)
        with receiver:
            listener = FmListener(receiver, freq_hz, settings=ListenSettings())
            if record is not None:
                _record(listener, record, seconds=seconds or 0.0)
            else:
                _play(listener, seconds=seconds)
    except DeviceError as error:
        errors.print(f"[red]{type(error).__name__}:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    except PlaybackUnavailableError as error:
        errors.print(f"[red]Cannot play audio:[/red] {escape(str(error))}")
        errors.print("[dim]Use --record to write a WAV file instead.[/dim]")
        raise typer.Exit(1) from error


def _play(listener: FmListener, *, seconds: float | None) -> None:
    """Play a station through libVLC until the time runs out or the user interrupts."""
    console.print(f"Listening to [bold]{format_frequency(listener.freq_hz)}[/bold]")
    with listener:
        player = LibVlcPlayer(listener.stream, sample_rate_hz=listener.audio_rate_hz)
        try:
            player.play()
            console.print("[dim]Press Ctrl-C to stop.[/dim]")
            deadline = None if seconds is None else time.monotonic() + seconds
            reported_stereo: bool | None = None
            while listener.is_running and (deadline is None or time.monotonic() < deadline):
                if listener.is_stereo != reported_stereo:
                    reported_stereo = listener.is_stereo
                    if reported_stereo is not None:
                        console.print(f"  {'stereo' if reported_stereo else 'mono'}")
                time.sleep(0.2)
        except KeyboardInterrupt:
            console.print("\nStopped.")
        finally:
            player.stop()

    if listener.error is not None:
        errors.print(f"[red]Reception stopped:[/red] {escape(str(listener.error))}")
        raise typer.Exit(1)


def _record(listener: FmListener, path: Path, *, seconds: float) -> None:
    """Write a station's audio to a WAV file, for a machine with no audio output."""
    console.print(
        f"Recording [bold]{format_frequency(listener.freq_hz)}[/bold] for {seconds:g} s to {path}"
    )
    collected = bytearray()
    wanted = int(seconds * listener.audio_rate_hz) * 4  # 2 channels, 2 bytes each
    with listener:
        try:
            while len(collected) < wanted and listener.is_running:
                chunk = listener.stream.read(1 << 16, timeout_s=1.0)
                if not chunk:
                    break
                collected.extend(chunk)
        except KeyboardInterrupt:
            console.print("\nStopped early.")

    if listener.error is not None:
        errors.print(f"[red]Reception stopped:[/red] {escape(str(listener.error))}")
        raise typer.Exit(1)

    audio = bytes(collected[:wanted]) if wanted else bytes(collected)
    fmt = WavFormat(sample_rate_hz=listener.audio_rate_hz, channels=2)
    path.write_bytes(wav_header(fmt, data_bytes=len(audio)) + audio)
    duration = len(audio) / fmt.byte_rate
    console.print(
        f"Wrote {len(audio) + WAV_HEADER_SIZE} bytes, {duration:.2f} s of "
        f"{'stereo' if listener.is_stereo else 'mono'} audio."
    )


def _not_implemented(what: str, milestone: str, task: int) -> int:
    """Report an unimplemented command, pointing at the task that will deliver it."""
    errors.print(f"[yellow]{what} is not implemented yet.[/yellow]")
    errors.print(f"It lands in [bold]{milestone}[/bold] — task {task} in TASKS.md.")
    errors.print("Contributions welcome: https://github.com/Mxlione/Openwave")
    return 1


if __name__ == "__main__":
    app()
