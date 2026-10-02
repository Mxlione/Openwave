"""Turning channel measurements into identified FM stations.

:mod:`openwave.core.scanner` reports channels carrying energy. It does not know what FM is, so
it cannot say whether a channel holds a stereo broadcast or how the station is called. This
module does the second pass: it returns to each detection, demodulates it, and fills in what
only a demodulator can tell you.

The second pass costs a few hundred milliseconds per station, so it is separate from the sweep.
A spectrum display wants the sweep alone; a station list wants both.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from openwave.core.frequency_manager import BandPlan
from openwave.core.scanner import (
    DEFAULT_SETTLING_SAMPLES,
    ScanResult,
    ScanSettings,
    scan_band,
)
from openwave.core.scanner import ProgressCallback as SweepProgressCallback
from openwave.core.signal_detector import ChannelMeasurement
from openwave.radio.band import FM_BAND_PLAN
from openwave.radio.fm_demodulator import (
    MPX_SAMPLE_RATE_HZ,
    decimate_to,
    pilot_level_db,
    quadrature_demodulate,
    shift_to_baseband,
)
from openwave.radio.station import Station
from openwave.sdr.device import SdrDevice

#: How far from the centre of the window to place a station being examined, in Hz.
#:
#: A direct-conversion tuner leaves a spike of its own at the exact centre of its window, so
#: tuning a station to zero hertz lands it on top of the one place the receiver is least able to
#: measure. Real receivers offset-tune for exactly this reason, and so does this.
DC_AVOIDANCE_OFFSET_HZ = 250_000.0

#: Samples read per station when deciding stereo.
#:
#: The pilot measurement needs enough samples to resolve 19 kHz finely after decimation. At
#: 2.4 MS/s this is about 110 ms.
DEFAULT_IDENTIFY_SAMPLES = 1 << 18

#: Called with the station index, the total, and the frequency, before each is examined.
ProgressCallback = Callable[[int, int, float], None]


@dataclass(frozen=True, slots=True)
class IdentifySettings:
    """How to examine each detected station.

    Attributes:
        samples: Samples read per station.
        settling_samples: Samples discarded after each retune, as hardware needs.
        stereo_threshold_db: How far the pilot must stand above the multiplex around it to
            count as stereo.
        dc_avoidance_offset_hz: Where to place the station within the window. See
            :data:`DC_AVOIDANCE_OFFSET_HZ`.
    """

    samples: int = DEFAULT_IDENTIFY_SAMPLES
    settling_samples: int = DEFAULT_SETTLING_SAMPLES
    stereo_threshold_db: float = 10.0
    dc_avoidance_offset_hz: float = DC_AVOIDANCE_OFFSET_HZ

    def __post_init__(self) -> None:
        if self.samples <= 0:
            raise ValueError(f"samples must be positive, got {self.samples}")
        if self.settling_samples < 0:
            raise ValueError(f"settling_samples must not be negative, got {self.settling_samples}")


def station_from_measurement(
    measurement: ChannelMeasurement,
    *,
    stereo: bool | None = None,
    pilot_level_db: float | None = None,
    name: str | None = None,
) -> Station:
    """Build a :class:`Station` from a channel measurement.

    ``stereo`` stays ``None`` when it was not checked, which is different from ``False``: a
    sweep on its own cannot tell a mono station from one it never demodulated.
    """
    return Station(
        freq_hz=measurement.freq_hz,
        power_dbfs=measurement.power_dbfs,
        snr_db=measurement.snr_db,
        bandwidth_hz=measurement.bandwidth_hz,
        stereo=stereo,
        pilot_level_db=pilot_level_db,
        name=name,
    )


def examine_station(
    device: SdrDevice,
    measurement: ChannelMeasurement,
    *,
    settings: IdentifySettings | None = None,
) -> Station:
    """Demodulate one station and report what the demodulator adds.

    The receiver must already be open with its sample rate set. It is left tuned near the
    station.

    Falls back to reporting the measurement unchanged, with ``stereo`` left unknown, when the
    station cannot be reached or there are too few samples to judge the pilot. A scan that gives
    up entirely because one station out of thirty could not be examined would be worse than one
    that reports thirty stations and is honest about one unknown.
    """
    settings = settings or IdentifySettings()

    offset_hz = settings.dc_avoidance_offset_hz
    center_freq_hz = measurement.freq_hz - offset_hz
    if not device.tuning_range.contains(center_freq_hz):
        # Offset the other way if that falls off the end of the tuner's range.
        center_freq_hz = measurement.freq_hz + offset_hz
        offset_hz = -offset_hz
        if not device.tuning_range.contains(center_freq_hz):
            return station_from_measurement(measurement)

    device.set_center_freq(center_freq_hz)
    if settings.settling_samples:
        device.read_samples(settings.settling_samples)
    samples = device.read_samples(settings.samples)

    shifted = shift_to_baseband(samples, offset_hz=offset_hz, sample_rate_hz=device.sample_rate_hz)
    baseband, mpx_rate_hz = decimate_to(
        shifted, sample_rate_hz=device.sample_rate_hz, target_rate_hz=MPX_SAMPLE_RATE_HZ
    )
    mpx = quadrature_demodulate(baseband, sample_rate_hz=mpx_rate_hz)

    try:
        pilot_db = pilot_level_db(mpx, sample_rate_hz=mpx_rate_hz)
    except ValueError:
        # Too few samples, or a rate too low to represent 19 kHz. Report the measurement and
        # leave stereo unknown rather than guessing.
        return station_from_measurement(measurement)

    return station_from_measurement(
        measurement,
        stereo=pilot_db >= settings.stereo_threshold_db,
        pilot_level_db=pilot_db,
    )


def identify_stations(
    device: SdrDevice,
    detections: Sequence[ChannelMeasurement],
    *,
    settings: IdentifySettings | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[Station, ...]:
    """Examine every detection and return the stations, in ascending frequency order.

    The receiver must already be open with its sample rate set.
    """
    settings = settings or IdentifySettings()
    stations: list[Station] = []
    for index, measurement in enumerate(detections):
        if progress is not None:
            progress(index, len(detections), measurement.freq_hz)
        stations.append(examine_station(device, measurement, settings=settings))
    stations.sort(key=lambda station: station.freq_hz)
    return tuple(stations)


@dataclass(frozen=True, slots=True)
class FmScanResult:
    """A complete FM scan: the sweep, and the stations identified from it."""

    scan: ScanResult
    stations: tuple[Station, ...]

    @property
    def station_count(self) -> int:
        """How many stations were found."""
        return len(self.stations)

    @property
    def stereo_count(self) -> int:
        """How many of them are stereo."""
        return sum(1 for station in self.stations if station.is_stereo)

    def __str__(self) -> str:
        plural = "" if self.station_count == 1 else "s"
        summary = f"{self.station_count} station{plural}"
        if self.stations and any(s.stereo is not None for s in self.stations):
            summary += f", {self.stereo_count} in stereo"
        summary += f", in {self.scan.duration_s:.1f} s"
        if not self.scan.is_complete:
            summary += f" ({self.scan.coverage:.0%} of the band covered)"
        return summary


def scan_fm(
    device: SdrDevice,
    *,
    plan: BandPlan = FM_BAND_PLAN,
    scan_settings: ScanSettings | None = None,
    identify_settings: IdentifySettings | None = None,
    identify: bool = True,
    sweep_progress: SweepProgressCallback | None = None,
    identify_progress: ProgressCallback | None = None,
) -> FmScanResult:
    """Scan the FM band and return the stations found.

    This is the whole v0.1 feature in one call: sweep the band measuring channel power, then
    return to each occupied channel and demodulate it to decide stereo.

    Args:
        device: The receiver. Opened if necessary and left as it was found.
        plan: Band to scan. Defaults to the FM broadcast band on the 100 kHz grid.
        scan_settings: How thoroughly to sweep.
        identify_settings: How to examine each station.
        identify: Set ``False`` to skip the second pass, which is quicker and leaves ``stereo``
            unknown. A spectrum display wants this.
        sweep_progress: Progress callback for the sweep.
        identify_progress: Progress callback for the second pass.
    """
    was_open = device.is_open
    if not was_open:
        device.open()
    try:
        result = scan_band(
            device,
            plan,
            settings=scan_settings,
            progress=sweep_progress,
        )
        if not identify:
            stations = tuple(
                station_from_measurement(measurement) for measurement in result.detections
            )
        else:
            device.set_sample_rate(result.sample_rate_hz)
            stations = identify_stations(
                device,
                result.detections,
                settings=identify_settings,
                progress=identify_progress,
            )
    finally:
        if not was_open:
            device.close()
    return FmScanResult(scan=result, stations=stations)
