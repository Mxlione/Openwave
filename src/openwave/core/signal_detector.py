"""Measuring what is on the air in a window of spectrum.

Everything here is a pure function of samples: no device, no I/O, no state. That is what lets a
detector be tested by injecting a station at a known frequency and asserting it is found.

**Channel power, not peaks.** The obvious approach -- compute a spectrum, find the peaks -- does
not work for broadcast FM. A 75 kHz deviation gives a modulation index around 75, and at that
index the carrier is almost entirely suppressed while the energy piles up at the two extremes of
the deviation. A station at 98.0 MHz produces peaks near 97.93 and 98.07 MHz and little at
98.0 MHz, so peak picking reports two stations that do not exist and misses the one that does.

What works is integrating the power across each channel on the grid and comparing it with the
noise floor. That is :func:`measure_channels`. A strong station also lifts its neighbours, so
:func:`select_occupied` then keeps only the local maximum.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import signal as scipy_signal

from openwave.core.units import SILENCE_DBFS, db_ratio, format_frequency, power_to_dbfs
from openwave.sdr.device import IqSamples

#: Default length of each FFT in the Welch average.
#:
#: At 2.4 MS/s this gives a resolution of about 586 Hz, which is far finer than needed to
#: integrate a 200 kHz channel, and leaves enough averaging in a quarter-second of samples to
#: make the noise floor estimate steady.
DEFAULT_FFT_SIZE = 4096

#: Percentile of the spectrum taken as the noise floor.
#:
#: The median would be the obvious choice, and is what a sparsely occupied band wants. But a
#: single 2.4 MHz window of the FM band can hold several active channels, and once more than
#: half of a window is occupied the median stops measuring noise and starts measuring stations,
#: which makes every signal look weaker than it is. The lower quartile keeps working in a
#: crowded window, at the cost of reading one or two dB below the true floor everywhere -- a
#: uniform optimism that a detection threshold absorbs.
DEFAULT_NOISE_PERCENTILE = 25.0

#: Default signal-to-noise ratio for a channel to count as occupied, in dB.
DEFAULT_DETECTION_THRESHOLD_DB = 10.0


@dataclass(frozen=True, slots=True)
class Spectrum:
    """The power spectral density of one window of samples.

    Frequencies are absolute, not offsets from the tuned frequency, so measurements from
    different segments of a sweep can be compared without further bookkeeping.
    """

    freqs_hz: npt.NDArray[np.float64]
    """Bin centre frequencies, ascending."""

    psd: npt.NDArray[np.float64]
    """Power spectral density per bin, linear, in units of full-scale power per hertz."""

    def __post_init__(self) -> None:
        if self.freqs_hz.shape != self.psd.shape:
            raise ValueError(
                f"spectrum has {self.freqs_hz.size} frequencies but {self.psd.size} values"
            )
        if self.freqs_hz.size < 2:
            raise ValueError("a spectrum needs at least two bins")

    @property
    def resolution_hz(self) -> float:
        """Width of one bin."""
        return float(self.freqs_hz[1] - self.freqs_hz[0])

    @property
    def start_hz(self) -> float:
        """Lowest frequency in the spectrum."""
        return float(self.freqs_hz[0])

    @property
    def end_hz(self) -> float:
        """Highest frequency in the spectrum."""
        return float(self.freqs_hz[-1])

    def noise_density(self, percentile: float = DEFAULT_NOISE_PERCENTILE) -> float:
        """Estimated noise power per hertz.

        See :data:`DEFAULT_NOISE_PERCENTILE` for why this is a low percentile rather than the
        median.
        """
        if not 0.0 < percentile < 100.0:
            raise ValueError(f"percentile must be in (0, 100), got {percentile}")
        return float(np.percentile(self.psd, percentile))

    def mask_for(self, center_freq_hz: float, bandwidth_hz: float) -> npt.NDArray[np.bool_]:
        """Which bins fall inside a channel of ``bandwidth_hz`` centred on ``center_freq_hz``."""
        if bandwidth_hz <= 0:
            raise ValueError(f"bandwidth must be positive, got {bandwidth_hz}")
        mask: npt.NDArray[np.bool_] = np.abs(self.freqs_hz - center_freq_hz) <= bandwidth_hz / 2.0
        return mask

    def band_power(self, center_freq_hz: float, bandwidth_hz: float) -> float:
        """Total power in a channel, integrated across its bandwidth.

        Raises:
            ValueError: if the channel falls wholly outside the spectrum, which means the
                caller is asking a window a question it cannot answer.
        """
        mask = self.mask_for(center_freq_hz, bandwidth_hz)
        if not mask.any():
            raise ValueError(
                f"a {format_frequency(bandwidth_hz)} channel at "
                f"{format_frequency(center_freq_hz)} lies outside this spectrum "
                f"({format_frequency(self.start_hz)} to {format_frequency(self.end_hz)})"
            )
        return float(np.sum(self.psd[mask]) * self.resolution_hz)

    def contains_channel(self, center_freq_hz: float, bandwidth_hz: float) -> bool:
        """Whether a whole channel fits inside this spectrum."""
        half = bandwidth_hz / 2.0
        return self.start_hz <= center_freq_hz - half and center_freq_hz + half <= self.end_hz


@dataclass(frozen=True, slots=True)
class ChannelMeasurement:
    """What was measured in one channel.

    Levels are in dBFS, relative to full scale, because a consumer SDR has no calibrated power
    reference. See :mod:`openwave.core.units`.
    """

    freq_hz: float
    power_dbfs: float
    """Total power integrated across the channel."""

    noise_dbfs: float
    """Power the noise floor alone would put in a channel of the same width."""

    bandwidth_hz: float

    @property
    def snr_db(self) -> float:
        """How far the channel stands above the noise floor."""
        return self.power_dbfs - self.noise_dbfs

    def is_occupied(self, threshold_db: float = DEFAULT_DETECTION_THRESHOLD_DB) -> bool:
        """Whether this channel is carrying something."""
        return self.snr_db >= threshold_db

    def __str__(self) -> str:
        return f"{format_frequency(self.freq_hz)} {self.snr_db:+.1f} dB SNR"


def power_spectrum(
    samples: IqSamples,
    *,
    sample_rate_hz: float,
    center_freq_hz: float,
    fft_size: int = DEFAULT_FFT_SIZE,
) -> Spectrum:
    """Estimate the power spectral density of ``samples`` by Welch's method.

    Welch averages the spectra of overlapping blocks. The averaging is the point: a single FFT
    of noise is wildly variable from bin to bin, and a noise floor estimated from one would move
    by several dB between reads, making a detection threshold meaningless.

    Args:
        samples: Complex baseband samples.
        sample_rate_hz: Rate they were sampled at.
        center_freq_hz: Frequency the receiver was tuned to, used to label the bins with
            absolute frequencies.
        fft_size: Length of each block. Must not exceed the number of samples.

    Raises:
        ValueError: if the arguments are inconsistent or there are too few samples.
    """
    if sample_rate_hz <= 0:
        raise ValueError(f"sample rate must be positive, got {sample_rate_hz}")
    if fft_size < 2:
        raise ValueError(f"FFT size must be at least 2, got {fft_size}")
    if samples.size < fft_size:
        raise ValueError(
            f"need at least {fft_size} samples for an FFT of that size, got {samples.size}"
        )

    offsets_hz, psd = scipy_signal.welch(
        samples,
        fs=sample_rate_hz,
        nperseg=fft_size,
        return_onesided=False,
        detrend=False,
        scaling="density",
    )
    # welch returns a complex spectrum in FFT order, with the negative frequencies last.
    order = np.argsort(offsets_hz)
    return Spectrum(
        freqs_hz=np.asarray(offsets_hz, dtype=np.float64)[order] + center_freq_hz,
        psd=np.asarray(psd, dtype=np.float64)[order],
    )


def measure_channels(
    spectrum: Spectrum,
    channels: Iterable[float],
    *,
    bandwidth_hz: float,
    noise_percentile: float = DEFAULT_NOISE_PERCENTILE,
) -> tuple[ChannelMeasurement, ...]:
    """Measure the power in each of ``channels``.

    Channels that do not fit wholly inside ``spectrum`` are skipped rather than measured badly:
    a channel straddling the edge of a window would read low, and reporting that as a weak
    station is worse than reporting nothing. The sweep planner in
    :mod:`openwave.core.frequency_manager` assigns every channel to a window that can measure
    it, so a skipped channel means the plan and the samples disagree.

    Returns:
        Measurements in the order the channels were given, minus any that were skipped.
    """
    if bandwidth_hz <= 0:
        raise ValueError(f"bandwidth must be positive, got {bandwidth_hz}")

    noise_density = spectrum.noise_density(noise_percentile)
    resolution_hz = spectrum.resolution_hz

    measurements: list[ChannelMeasurement] = []
    for channel_hz in channels:
        if not spectrum.contains_channel(channel_hz, bandwidth_hz):
            continue
        mask = spectrum.mask_for(channel_hz, bandwidth_hz)
        power = float(np.sum(spectrum.psd[mask]) * resolution_hz)
        # The noise is integrated over the same bins as the signal, not over a bin count
        # derived from the bandwidth. Otherwise rounding puts the two totals over slightly
        # different widths, which shows up as a fraction of a dB of bias in every channel.
        noise_power = noise_density * int(mask.sum()) * resolution_hz
        measurements.append(
            ChannelMeasurement(
                freq_hz=channel_hz,
                power_dbfs=power_to_dbfs(power) if power > 0 else SILENCE_DBFS,
                noise_dbfs=(power_to_dbfs(noise_power) if noise_power > 0 else SILENCE_DBFS),
                bandwidth_hz=bandwidth_hz,
            )
        )
    return tuple(measurements)


def select_occupied(
    measurements: Sequence[ChannelMeasurement],
    *,
    threshold_db: float = DEFAULT_DETECTION_THRESHOLD_DB,
    merge_window_hz: float | None = None,
) -> tuple[ChannelMeasurement, ...]:
    """Reduce channel measurements to the stations actually present.

    Two steps. Channels below ``threshold_db`` are dropped. Then the strongest survivor is
    accepted as a station and everything within ``merge_window_hz`` of it is discarded as its
    spill, and that repeats with the strongest of what remains. A station's energy reaches the
    channels either side of it, so a transmitter at 98.0 MHz also puts 97.9 and 98.1 above the
    threshold, and reporting all three would turn one station into three.

    **Why strongest-first and not simply local maxima.** Suppressing a channel because a
    stronger one sits nearby is wrong when that stronger neighbour is itself about to be
    discarded as spill. Measured against the simulator with stations every 300 kHz, comparing
    each candidate against all the others lost a real station at 98.0 MHz to the spill channel
    at 98.2 MHz, which was then itself dropped in favour of the real station at 98.3 MHz --
    one transmitter eliminated by another that did not exist. Accepting in descending order of
    strength, and suppressing only against detections already accepted, finds all of them.

    **The limitation this has.** Two real transmitters closer together than ``merge_window_hz``
    cannot both be reported; the weaker one is read as spill from the stronger. Broadcast
    planning keeps co-sited stations much further apart than that, so it rarely bites in
    practice, but it is a genuine blind spot rather than an approximation -- and one that only
    real hardware can reveal the true extent of.

    Args:
        measurements: Channel measurements, in any order.
        threshold_db: Minimum signal-to-noise ratio to consider.
        merge_window_hz: How far apart two detections must be to count as separate stations.
            Defaults to the channel bandwidth of the measurements.

    Returns:
        Surviving measurements, in ascending frequency order.
    """
    candidates = [m for m in measurements if m.is_occupied(threshold_db)]
    if not candidates:
        return ()

    window_hz = (
        merge_window_hz if merge_window_hz is not None else max(m.bandwidth_hz for m in candidates)
    )
    if window_hz < 0:
        raise ValueError(f"merge window must not be negative, got {window_hz}")

    # Strongest first, and ties by frequency so the result does not depend on input order.
    candidates.sort(key=lambda m: (-m.snr_db, m.freq_hz))

    accepted: list[ChannelMeasurement] = []
    for candidate in candidates:
        if all(abs(candidate.freq_hz - station.freq_hz) > window_hz for station in accepted):
            accepted.append(candidate)

    accepted.sort(key=lambda m: m.freq_hz)
    return tuple(accepted)


def snr_db(power: float, noise_power: float) -> float:
    """Signal-to-noise ratio of two linear powers, in dB. A thin wrapper for readability."""
    return db_ratio(power, noise_power)
