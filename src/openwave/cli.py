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
from openwave.radio.station_detector import FmScanResult, IdentifySettings, scan_fm
from openwave.sdr import DeviceError, list_devices, open_device
from openwave.sdr.device_manager import describe_device, driver_names, open_dvb_device
from openwave.tv.band import TELEVISION_PLANS, TelevisionChannelPlan
from openwave.tv.demo import demo_tuner
from openwave.tv.dvb_scanner import (
    DEFAULT_LOCK_TIMEOUT_S,
    DvbScanResult,
    DvbScanSettings,
    scan_dvb,
)

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
    rds: Annotated[
        bool,
        typer.Option(
            "--rds/--no-rds",
            help=(
                "Read RDS to get each station's name. Needs about a second of signal per "
                "station, so it is what makes a scan slow."
            ),
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
            result = _run_scan(receiver, plan, settings, stereo=stereo, rds=rds, quiet=as_json)
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
    receiver: Any,
    plan: BandPlan,
    settings: ScanSettings,
    *,
    stereo: bool,
    rds: bool,
    quiet: bool,
) -> FmScanResult:
    """Run a scan, with a progress bar unless output is meant to be machine-readable."""
    identify_settings = IdentifySettings(rds=rds)
    if quiet:
        return scan_fm(
            receiver,
            plan=plan,
            scan_settings=settings,
            identify_settings=identify_settings,
            identify=stereo,
        )

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

        label = "Reading RDS" if rds else "Checking stereo"

        def on_identify(index: int, total: int, freq_hz: float) -> None:
            progress.update(
                identify,
                completed=index,
                total=total,
                visible=True,
                description=f"{label} at {format_frequency(freq_hz)}",
            )

        return scan_fm(
            receiver,
            plan=plan,
            scan_settings=settings,
            identify_settings=identify_settings,
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
        console.print(
            "[dim]No station names: none of these stations carry readable RDS. Pass "
            "[/dim]--rds[dim] if it was switched off, and note that a weak signal often "
            "carries a signal strong enough to hear but too weak to decode.[/dim]"
        )
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
def scan_tv_command(
    tuner: Annotated[
        str,
        typer.Option(
            "--tuner",
            "-u",
            help="DVB tuner to use: 'mockdvb' for the simulator, 'linuxdvb' for real hardware.",
        ),
    ] = "mockdvb",
    band: Annotated[
        str,
        typer.Option("--band", "-b", help=f"Channel plan: {', '.join(TELEVISION_PLANS)}."),
    ] = "uhf",
    demo: Annotated[
        bool,
        typer.Option(
            "--demo",
            help=(
                "Scan invented multiplexes, to see what a television scan looks like without "
                "a tuner. Overrides --tuner."
            ),
        ),
    ] = False,
    lock_timeout: Annotated[
        float,
        typer.Option(
            "--lock-timeout",
            help=(
                "Seconds to give the demodulator to lock onto each channel. Most channels are "
                "empty, so this is what a scan mostly spends its time on."
            ),
        ),
    ] = DEFAULT_LOCK_TIMEOUT_S,
    scrambled: Annotated[
        bool,
        typer.Option(
            "--scrambled/--no-scrambled",
            help="List services that are encrypted and so cannot be watched.",
        ),
    ] = True,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result as JSON instead of a table.")
    ] = False,
) -> None:
    """Scan the DVB-T channels and list the services found.

    Tunes each channel of the plan in turn, waits for the demodulator to lock, and reads the
    multiplex's own tables to find out what it carries and what each service is called.

    With no tuner attached, --demo scans invented multiplexes so the output can be seen.
    """
    plan = _television_plan(band)
    settings = DvbScanSettings(lock_timeout_s=lock_timeout, include_scrambled=scrambled)

    try:
        with demo_tuner() if demo else open_dvb_device(tuner) as receiver:
            if not as_json:
                label = "invented multiplexes" if demo else escape(receiver.info.label)
                console.print(f"Scanning {plan} with [bold]{label}[/bold]")
            result = _run_tv_scan(receiver, plan, settings, quiet=as_json)
    except DeviceError as error:
        errors.print(f"[red]{type(error).__name__}:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error

    if as_json:
        console.print_json(_tv_scan_as_json(result))
        return
    _print_channels(result)


def _television_plan(name: str) -> TelevisionChannelPlan:
    """Look up a channel plan by name, or exit with the list of valid names."""
    plan = TELEVISION_PLANS.get(name.lower())
    if plan is None:
        errors.print(
            f"[red]Unknown band {name!r}.[/red] Available bands: {', '.join(TELEVISION_PLANS)}."
        )
        raise typer.Exit(2)
    return plan


def _run_tv_scan(
    receiver: Any, plan: TelevisionChannelPlan, settings: DvbScanSettings, *, quiet: bool
) -> DvbScanResult:
    """Run a television scan, with a progress bar unless the output is machine-readable."""
    if quiet:
        return scan_dvb(receiver, plan, settings=settings)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Tuning channels", total=plan.channel_count)

        def on_channel(index: int, total: int, freq_hz: float) -> None:
            channel = plan.channel_of(freq_hz)
            where = f"channel {channel}" if channel is not None else format_frequency(freq_hz)
            progress.update(task, completed=index, total=total, description=f"Tuning {where}")

        return scan_dvb(receiver, plan, settings=settings, progress=on_channel)


def _print_channels(result: DvbScanResult) -> None:
    """Render a television scan result as a table."""
    if not result.channels:
        console.print(
            "[yellow]No services found.[/yellow] With the plain simulator this is expected: "
            "it carries nothing unless told to, so try [bold]--demo[/bold]. Against real "
            "hardware, check the aerial and that the band is the right one for your country."
        )
        return

    numbered = any(channel.logical_channel is not None for channel in result.channels)

    table = Table(header_style="bold", title_justify="left")
    if numbered:
        table.add_column("No.", justify="right", style="cyan", no_wrap=True)
    table.add_column("Service")
    table.add_column("Kind")
    table.add_column("Multiplex", justify="right", style="dim")
    table.add_column("Provider", style="dim")

    for channel in result.channels:
        row: list[str] = []
        if numbered:
            row.append(str(channel.logical_channel) if channel.logical_channel is not None else "-")
        name = channel.name
        if channel.scrambled:
            name += " [dim](scrambled)[/dim]"
        row.extend(
            [
                name,
                channel.kind.value,
                f"{channel.mux_freq_hz / 1e6:.0f} MHz",
                channel.provider or "",
            ]
        )
        table.add_row(*row)

    console.print(table)
    console.print(f"[dim]{result}[/dim]")

    if result.incomplete:
        console.print(
            f"[yellow]{len(result.incomplete)} multiplex(es) locked but could not be "
            "read.[/yellow] The signal is there and its tables are not, which usually means "
            "marginal reception: try a better aerial or a longer --lock-timeout."
        )
    if not numbered:
        console.print(
            "[dim]No channel numbers: these multiplexes do not publish them. The descriptor "
            "that carries them is a private extension, not part of the standard.[/dim]"
        )


def _tv_scan_as_json(result: DvbScanResult) -> str:
    """Serialise a television scan for scripts and for the API to reuse later."""
    payload = {
        "band": result.plan.name,
        "channels_tried": result.channels_tried,
        "duration_s": round(result.duration_s, 3),
        "multiplexes": [
            {
                "freq_hz": mux.freq_hz,
                "channel": mux.channel_number,
                "bandwidth_hz": mux.bandwidth_hz,
                "transport_stream_id": mux.tables.pat.transport_stream_id
                if mux.tables.pat
                else None,
                "network_name": mux.network_name or None,
                "snr_db": mux.quality.snr_db,
                "services": len(mux.channels),
            }
            for mux in result.muxes
        ],
        "incomplete": [mux.freq_hz for mux in result.incomplete],
        "channels": [channel.model_dump() for channel in result.channels],
    }
    return json.dumps(payload, indent=2)


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


if __name__ == "__main__":
    app()
