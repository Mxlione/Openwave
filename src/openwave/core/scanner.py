"""Sweeping a band and reporting what is on it.

The scanner is the part that drives a receiver. It takes a band plan, works out the tuning
positions from :mod:`openwave.core.frequency_manager`, and at each one reads samples and hands
them to :mod:`openwave.core.signal_detector`.

It is modulation-agnostic: it reports *channels carrying energy*, not stations. Turning a
detection into a named FM station, with its stereo flag and its RDS name, is the job of
:mod:`openwave.radio.station_detector`, which knows what FM is.

A receiver that cannot reach the whole band is handled rather than refused. A recorded capture
covers one window, so scanning one reports the channels inside that window and says which
segments it could not reach, instead of failing outright.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from openwave.core.frequency_manager import BandPlan, Segment, plan_segments
from openwave.core.signal_detector import (
    DEFAULT_DETECTION_THRESHOLD_DB,
    DEFAULT_FFT_SIZE,
    DEFAULT_NOISE_PERCENTILE,
    ChannelMeasurement,
    measure_channels,
    power_spectrum,
    select_occupied,
)
from openwave.core.units import format_frequency
from openwave.sdr.device import SdrDevice
from openwave.sdr.errors import TuningFailedError

#: Sample rate used when the receiver does not say what it supports, in Hz.
#:
#: 2.4 MS/s is the highest rate an RTL-SDR sustains reliably over USB on a typical machine.
#: Faster rates drop samples, which a scan would see as a raised noise floor.
DEFAULT_SCAN_SAMPLE_RATE_HZ = 2_400_000.0

#: Samples read and thrown away after each retune.
#:
#: Hardware needs a moment after being tuned: the PLL settles, and the automatic gain control
#: readjusts to a window that may be much louder or quieter than the last one. Measuring those
#: first samples would read the transition rather than the band. The simulator does not need
#: this, which is exactly why it is here by default -- code that only ever ran against the
#: simulator would omit it, and then under-read every segment on real hardware.
DEFAULT_SETTLING_SAMPLES = 1 << 14

#: Samples measured per segment.
#:
#: At 2.4 MS/s this is about 55 ms, which gives Welch enough blocks to average for a steady
#: noise floor while keeping a sweep of the FM band under a couple of seconds.
DEFAULT_SAMPLES_PER_SEGMENT = 1 << 17

#: Called with the segment index, the total, and the segment, before each one is measured.
ProgressCallback = Callable[[int, int, Segment], None]


@dataclass(frozen=True, slots=True)
class ScanSettings:
    """How thoroughly to scan, and how sensitive to be.

    Attributes:
        sample_rate_hz: Rate to sample at, or ``None`` to take the fastest the receiver offers
            up to :data:`DEFAULT_SCAN_SAMPLE_RATE_HZ`.
        samples_per_segment: Samples measured at each tuning position. More samples average the
            noise floor better and take longer.
        settling_samples: Samples discarded after each retune. See
            :data:`DEFAULT_SETTLING_SAMPLES`.
        fft_size: Length of each FFT in the Welch average.
        threshold_db: Signal-to-noise ratio a channel needs to count as occupied.
        noise_percentile: Percentile of the spectrum taken as the noise floor.
        merge_window_hz: How far apart two detections must be to count as separate stations.
            ``None`` uses the plan's channel bandwidth.
    """

    sample_rate_hz: float | None = None
    samples_per_segment: int = DEFAULT_SAMPLES_PER_SEGMENT
    settling_samples: int = DEFAULT_SETTLING_SAMPLES
    fft_size: int = DEFAULT_FFT_SIZE
    threshold_db: float = DEFAULT_DETECTION_THRESHOLD_DB
    noise_percentile: float = DEFAULT_NOISE_PERCENTILE
    merge_window_hz: float | None = None

    def __post_init__(self) -> None:
        if self.sample_rate_hz is not None and self.sample_rate_hz <= 0:
            raise ValueError(f"sample rate must be positive, got {self.sample_rate_hz}")
        if self.samples_per_segment < self.fft_size:
            raise ValueError(
                f"samples_per_segment ({self.samples_per_segment}) must be at least the FFT "
                f"size ({self.fft_size}), or there is nothing to transform"
            )
        if self.settling_samples < 0:
            raise ValueError(f"settling_samples must not be negative, got {self.settling_samples}")
        if self.fft_size < 2:
            raise ValueError(f"FFT size must be at least 2, got {self.fft_size}")


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Everything one sweep found.

    Attributes:
        plan: The band that was scanned.
        sample_rate_hz: Rate the sweep actually used.
        measurements: Every channel measured, in ascending frequency order. Kept because a
            spectrum display and a "why was nothing found" question both need the channels that
            came back empty, not just the ones that did not.
        detections: Channels carrying something, after spill has been suppressed.
        skipped_segments: Tuning positions the receiver could not reach. Empty for hardware
            covering the band; populated when scanning a recorded capture.
        duration_s: Wall-clock time the sweep took.
    """

    plan: BandPlan
    sample_rate_hz: float
    measurements: tuple[ChannelMeasurement, ...] = field(default_factory=tuple)
    detections: tuple[ChannelMeasurement, ...] = field(default_factory=tuple)
    segments: tuple[Segment, ...] = field(default_factory=tuple)
    skipped_segments: tuple[Segment, ...] = field(default_factory=tuple)
    duration_s: float = 0.0

    @property
    def channels_measured(self) -> int:
        """How many channels were actually measured."""
        return len(self.measurements)

    @property
    def is_complete(self) -> bool:
        """Whether the whole band was covered."""
        return not self.skipped_segments

    @property
    def coverage(self) -> float:
        """Fraction of the planned tuning positions that were reached, from 0.0 to 1.0."""
        total = len(self.segments) + len(self.skipped_segments)
        return len(self.segments) / total if total else 0.0

    def __str__(self) -> str:
        found = len(self.detections)
        plural = "" if found == 1 else "s"
        summary = (
            f"{found} station{plural} across {self.channels_measured} channels "
            f"in {self.duration_s:.1f} s"
        )
        if not self.is_complete:
            summary += f" ({self.coverage:.0%} of the band covered)"
        return summary


def choose_sample_rate(device: SdrDevice, requested_hz: float | None = None) -> float:
    """Pick a sample rate for a sweep.

    A faster rate means fewer tuning positions and a quicker sweep, so the fastest rate the
    receiver supports is preferred -- up to :data:`DEFAULT_SCAN_SAMPLE_RATE_HZ`, beyond which an
    RTL-SDR starts dropping samples over USB and the extra speed buys a worse measurement.

    Raises:
        UnsupportedSampleRateError: if ``requested_hz`` is not something the receiver accepts.
    """
    supported = device.supported_sample_rates_hz
    if requested_hz is not None:
        return requested_hz
    if supported is None:
        return DEFAULT_SCAN_SAMPLE_RATE_HZ
    affordable = [rate for rate in supported if rate <= DEFAULT_SCAN_SAMPLE_RATE_HZ]
    return max(affordable) if affordable else min(supported)


def scan_segment(
    device: SdrDevice,
    segment: Segment,
    *,
    settings: ScanSettings | None = None,
) -> tuple[ChannelMeasurement, ...]:
    """Tune to one position and measure the channels it is responsible for.

    The receiver must already be open and set to ``segment.sample_rate_hz``.
    """
    settings = settings or ScanSettings()
    device.set_center_freq(segment.center_freq_hz)

    if settings.settling_samples:
        device.read_samples(settings.settling_samples)

    samples = device.read_samples(settings.samples_per_segment)
    spectrum = power_spectrum(
        samples,
        sample_rate_hz=segment.sample_rate_hz,
        center_freq_hz=segment.center_freq_hz,
        fft_size=settings.fft_size,
    )
    return measure_channels(
        spectrum,
        segment.channels,
        bandwidth_hz=segment.channel_bandwidth_hz,
        noise_percentile=settings.noise_percentile,
    )


def scan_band(
    device: SdrDevice,
    plan: BandPlan,
    *,
    settings: ScanSettings | None = None,
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Sweep ``plan`` with ``device`` and report the channels carrying something.

    Opens the receiver if it is not open already, and leaves it as it was found.

    Args:
        device: The receiver to sweep with.
        plan: The band to cover.
        settings: How thoroughly to scan. Defaults are reasonable.
        progress: Called before each segment, with its index, the total, and the segment. Used
            for a progress bar or a WebSocket update.

    Returns:
        A :class:`ScanResult`. Its ``skipped_segments`` is non-empty when the receiver could
        not reach part of the band, which is the normal case for a recorded capture.

    Raises:
        TuningFailedError: if the receiver cannot reach any part of the band at all.
        ValueError: if the receiver's window is too narrow for the plan's channels.
    """
    settings = settings or ScanSettings()
    was_open = device.is_open
    started = time.monotonic()

    if not was_open:
        device.open()
    try:
        sample_rate_hz = choose_sample_rate(device, settings.sample_rate_hz)
        device.set_sample_rate(sample_rate_hz)

        planned = plan_segments(
            plan,
            sample_rate_hz=sample_rate_hz,
            usable_fraction=device.usable_fraction,
        )
        reachable, skipped = _split_by_reach(device, planned)
        if not reachable:
            raise TuningFailedError(
                f"{device.info.driver} cannot reach any part of the {plan.name} band. It tunes "
                f"{format_frequency(device.tuning_range.min_hz)} to "
                f"{format_frequency(device.tuning_range.max_hz)}; the band needs "
                f"{format_frequency(planned[0].center_freq_hz)} to "
                f"{format_frequency(planned[-1].center_freq_hz)}."
            )

        measurements: list[ChannelMeasurement] = []
        for index, segment in enumerate(reachable):
            if progress is not None:
                progress(index, len(reachable), segment)
            measurements.extend(scan_segment(device, segment, settings=settings))
    finally:
        if not was_open:
            device.close()

    measurements.sort(key=lambda m: m.freq_hz)
    detections = select_occupied(
        measurements,
        threshold_db=settings.threshold_db,
        merge_window_hz=settings.merge_window_hz or plan.channel_bandwidth_hz,
    )
    return ScanResult(
        plan=plan,
        sample_rate_hz=sample_rate_hz,
        measurements=tuple(measurements),
        detections=detections,
        segments=reachable,
        skipped_segments=skipped,
        duration_s=time.monotonic() - started,
    )


def _split_by_reach(
    device: SdrDevice, segments: tuple[Segment, ...]
) -> tuple[tuple[Segment, ...], tuple[Segment, ...]]:
    """Separate the segments the receiver can tune to from the ones it cannot."""
    reachable: list[Segment] = []
    skipped: list[Segment] = []
    for segment in segments:
        target = reachable if device.tuning_range.contains(segment.center_freq_hz) else skipped
        target.append(segment)
    return tuple(reachable), tuple(skipped)
