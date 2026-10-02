"""Sweeping the television bands.

A television scan is unlike an FM scan. There is no spectrum to measure: the demodulator either
locks onto a channel or it does not, and once it locks, the multiplex says in words what it
carries. So the sweep is a loop of tune, wait for lock, read the tables, move on.

The expensive part is waiting. Most channels are empty, and a tuner given two seconds to lock
onto each of twenty-eight will spend most of a minute finding nothing. So an empty channel is
abandoned as soon as the tuner says it has no lock, and an occupied one is abandoned as soon as
its tables have all arrived rather than after a fixed time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from openwave.core.units import format_frequency
from openwave.sdr.device import TS_PACKET_SIZE, DvbDevice, SignalQuality
from openwave.sdr.errors import NoLockError
from openwave.tv.band import UHF_PLAN, TelevisionChannelPlan
from openwave.tv.channel import Channel
from openwave.tv.mux_parser import MuxParser, MuxTables
from openwave.tv.service_parser import channels_from_tables

#: Transport stream bytes read at a time, as a whole number of packets.
#:
#: About 188 kB, which at a typical 24 Mbit/s multiplex rate is some 60 ms of stream -- long
#: enough to contain a full set of tables, short enough that an empty channel is not waited on.
DEFAULT_READ_BYTES: Final = TS_PACKET_SIZE * 1000

#: How many reads to attempt before giving up on a locked channel.
#:
#: A multiplex sends its tables several times a second, so a handful of reads is plenty. The
#: limit exists for the case that matters: a tuner that locks onto noise and produces a stream
#: with no tables in it, which without a limit would be read for ever.
DEFAULT_MAX_READS: Final = 30

#: How long to wait for the demodulator to lock, in seconds.
DEFAULT_LOCK_TIMEOUT_S: Final = 2.0

#: Called with the channel index, the total, and the frequency, before each is tried.
ProgressCallback = Callable[[int, int, float], None]


@dataclass(frozen=True, slots=True)
class DvbScanSettings:
    """How to sweep the television bands.

    Attributes:
        lock_timeout_s: How long to give the demodulator to lock onto each channel.
        read_bytes: Transport stream bytes to read at a time.
        max_reads: Reads to attempt on a locked channel before giving up. See
            :data:`DEFAULT_MAX_READS`.
        include_scrambled: Whether to list services that cannot be watched.
        include_not_running: Whether to list services the multiplex says are off the air.
    """

    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S
    read_bytes: int = DEFAULT_READ_BYTES
    max_reads: int = DEFAULT_MAX_READS
    include_scrambled: bool = True
    include_not_running: bool = False

    def __post_init__(self) -> None:
        if self.lock_timeout_s < 0:
            raise ValueError(f"lock timeout must not be negative, got {self.lock_timeout_s}")
        if self.read_bytes <= 0 or self.read_bytes % TS_PACKET_SIZE:
            raise ValueError(
                f"read_bytes must be a positive multiple of {TS_PACKET_SIZE}, got {self.read_bytes}"
            )
        if self.max_reads <= 0:
            raise ValueError(f"max_reads must be positive, got {self.max_reads}")


@dataclass(frozen=True, slots=True)
class MuxFound:
    """One multiplex a scan locked onto.

    Attributes:
        freq_hz: Where the tuner heard it, which is not necessarily where the NIT says it is.
        channel_number: The channel number on the plan being scanned, if the frequency is on it.
        bandwidth_hz: Channel bandwidth used to tune.
        quality: What the tuner reported about the signal.
        tables: The tables read from it.
        channels: The services it carries.
        reads: How many blocks of stream were read before the tables were complete. A useful
            diagnostic: a multiplex needing many reads is one being received marginally.
    """

    freq_hz: float
    bandwidth_hz: float
    quality: SignalQuality
    tables: MuxTables
    channels: tuple[Channel, ...] = ()
    channel_number: int | None = None
    reads: int = 0

    @property
    def network_name(self) -> str:
        """What the network calls itself, if its NIT arrived."""
        return self.tables.nit.network_name if self.tables.nit else ""

    def __str__(self) -> str:
        where = (
            f"channel {self.channel_number}"
            if self.channel_number is not None
            else format_frequency(self.freq_hz)
        )
        plural = "" if len(self.channels) == 1 else "s"
        return f"{where}: {len(self.channels)} service{plural}"


@dataclass(frozen=True, slots=True)
class DvbScanResult:
    """Everything a television sweep found.

    Attributes:
        plan: The channel plan that was swept.
        muxes: The multiplexes found, in frequency order.
        channels: Every service found, across all of them, in viewing order.
        channels_tried: How many channels of the plan were tuned.
        incomplete: Multiplexes that locked but whose tables never fully arrived. Reported
            separately because they are the interesting failure: the signal is there and the
            data is not, which usually means marginal reception.
        duration_s: Wall-clock time the sweep took.
    """

    plan: TelevisionChannelPlan
    muxes: tuple[MuxFound, ...] = ()
    channels: tuple[Channel, ...] = ()
    channels_tried: int = 0
    incomplete: tuple[MuxFound, ...] = ()
    duration_s: float = 0.0

    @property
    def mux_count(self) -> int:
        """How many multiplexes were found."""
        return len(self.muxes)

    @property
    def channel_count(self) -> int:
        """How many services were found."""
        return len(self.channels)

    @property
    def television_count(self) -> int:
        """How many of them carry pictures."""
        return sum(1 for channel in self.channels if channel.is_television)

    def __str__(self) -> str:
        mux_plural = "" if self.mux_count == 1 else "es"
        summary = (
            f"{self.channel_count} services in {self.mux_count} multiplex{mux_plural}, "
            f"from {self.channels_tried} channels tried, in {self.duration_s:.1f} s"
        )
        if self.incomplete:
            summary += f" ({len(self.incomplete)} locked but incomplete)"
        return summary


def scan_channel(
    device: DvbDevice,
    freq_hz: float,
    *,
    bandwidth_hz: float,
    settings: DvbScanSettings | None = None,
) -> MuxFound | None:
    """Tune one channel and read whatever multiplex is on it.

    The tuner must already be open.

    Returns ``None`` when nothing locks, which is the result for most of a band and not an
    error. A multiplex that locks but whose tables never arrive is returned with an incomplete
    :attr:`MuxFound.tables`, because "something is transmitting here and I could not read it" is
    a different answer from "nothing is here".
    """
    settings = settings or DvbScanSettings()

    device.tune(freq_hz, bandwidth_hz)
    if not device.wait_for_lock(timeout_s=settings.lock_timeout_s):
        return None

    quality = device.signal_quality()
    parser = MuxParser()
    reads = 0

    while reads < settings.max_reads and not parser.is_complete:
        try:
            block = device.read_ts(settings.read_bytes)
        except NoLockError:
            # The lock was lost partway through. Whatever was read is still worth having.
            break
        reads += 1
        if not block:
            break
        parser.feed(block)

    tables = parser.tables
    return MuxFound(
        freq_hz=freq_hz,
        bandwidth_hz=bandwidth_hz,
        quality=quality,
        tables=tables,
        channels=channels_from_tables(
            tables,
            mux_freq_hz=freq_hz,
            quality=quality,
            include_scrambled=settings.include_scrambled,
            include_not_running=settings.include_not_running,
        ),
        reads=reads,
    )


def scan_dvb(
    device: DvbDevice,
    plan: TelevisionChannelPlan = UHF_PLAN,
    *,
    settings: DvbScanSettings | None = None,
    progress: ProgressCallback | None = None,
) -> DvbScanResult:
    """Sweep a television channel plan and list what is on the air.

    Opens the tuner if it is not open already, and leaves it as it was found.

    Args:
        device: The tuner to sweep with.
        plan: Which channel plan to cover.
        settings: How long to wait and how much to read.
        progress: Called before each channel, with its index, the total, and its frequency.

    Returns:
        A :class:`DvbScanResult`. Channels the tuner could not reach are skipped rather than
        failing the scan, because a tuner that covers UHF but not VHF is ordinary.
    """
    settings = settings or DvbScanSettings()
    was_open = device.is_open
    started = time.monotonic()

    if not was_open:
        device.open()
    try:
        found: list[MuxFound] = []
        incomplete: list[MuxFound] = []
        tried = 0

        frequencies = plan.frequencies()
        for index, freq_hz in enumerate(frequencies):
            if progress is not None:
                progress(index, len(frequencies), freq_hz)
            if not device.tuning_range.contains(freq_hz):
                continue

            tried += 1
            mux = scan_channel(device, freq_hz, bandwidth_hz=plan.bandwidth_hz, settings=settings)
            if mux is None:
                continue

            numbered = _with_channel_number(mux, plan)
            if numbered.tables.has_services:
                found.append(numbered)
            else:
                incomplete.append(numbered)
    finally:
        if not was_open:
            device.close()

    channels = tuple(
        sorted(
            (channel for mux in found for channel in mux.channels),
            key=_viewing_order,
        )
    )
    return DvbScanResult(
        plan=plan,
        muxes=tuple(found),
        channels=channels,
        channels_tried=tried,
        incomplete=tuple(incomplete),
        duration_s=time.monotonic() - started,
    )


def _with_channel_number(mux: MuxFound, plan: TelevisionChannelPlan) -> MuxFound:
    """Label a multiplex with its channel number on the plan being swept."""
    return MuxFound(
        freq_hz=mux.freq_hz,
        bandwidth_hz=mux.bandwidth_hz,
        quality=mux.quality,
        tables=mux.tables,
        channels=mux.channels,
        channel_number=plan.channel_of(mux.freq_hz),
        reads=mux.reads,
    )


def _viewing_order(channel: Channel) -> tuple[int, int, float, int]:
    """Sort channels the way a viewer's receiver would list them.

    By the number a viewer types where the network publishes one, and by frequency and service
    identifier otherwise -- which at least groups the unnumbered ones by multiplex.
    """
    if channel.logical_channel is None:
        return (1, 0, channel.mux_freq_hz, channel.service_id)
    return (0, channel.logical_channel, channel.mux_freq_hz, channel.service_id)
