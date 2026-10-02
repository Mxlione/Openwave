"""Simulated receivers.

These are not placeholders waiting to be deleted. The maintainer owns no RTL-SDR dongle and no
DVB-T tuner, so they are how the entire codebase is tested: they run in CI on every commit, they
let contributors work without buying hardware, and they are the only way a test can state the
right answer in advance.

:class:`MockSdrDevice` assembles a band from signal sources, adds a noise floor, and applies the
imperfections a receiver introduces. It knows nothing about FM, which is deliberate: the
modulation lives in :mod:`openwave.radio.synthesis`, so adding AM or DAB synthesis later means
touching that layer and not this one.

:class:`MockDvbDevice` replays transport stream bytes for the channels it is told carry a
multiplex, and reports no lock everywhere else.

**The simplifications, stated openly.** A signal whose carrier falls outside the digitised window
is dropped rather than folded back, where real hardware would alias it. The noise is white and
Gaussian, where a real tuner adds spurs, phase noise and a sloping noise floor. There is no
multipath and no adjacent-channel splatter. Anything that passes here may still fail on an
antenna -- which is what the ``hardware validation`` issues are for.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from openwave.core.units import SILENCE_DBFS, dbfs_to_power, format_frequency
from openwave.sdr.device import (
    TS_PACKET_SIZE,
    DeviceInfo,
    DvbDevice,
    Gain,
    IqSamples,
    SdrDevice,
    SignalQuality,
    TuningRange,
)
from openwave.sdr.errors import (
    DeviceBusyError,
    DeviceNotFoundError,
    NoLockError,
    UnsupportedGainError,
)

#: Tuning range of the R820T tuner found in most RTL-SDR dongles, in Hz.
R820T_TUNING_RANGE = TuningRange(min_hz=24e6, max_hz=1_766e6)

#: Tuning range of a typical DVB-T frontend, in Hz. Spans VHF band III and the UHF bands.
DVB_T_TUNING_RANGE = TuningRange(min_hz=42e6, max_hz=870e6)

#: Default attenuation applied at the very edge of the digitised window, in dB.
DEFAULT_EDGE_ATTENUATION_DB = 20.0

#: Default integrated noise power across the window, in dBFS.
DEFAULT_NOISE_FLOOR_DBFS = -70.0


@runtime_checkable
class SignalSource(Protocol):
    """Something that can produce IQ samples of one transmission.

    Implemented by :class:`openwave.radio.synthesis.SyntheticFmStation`. Implement it to add a
    new modulation to the simulator; nothing in :mod:`openwave.sdr` needs to change.
    """

    @property
    def freq_hz(self) -> float:
        """Centre frequency of the transmission."""

    def samples(
        self,
        *,
        center_freq_hz: float,
        sample_rate_hz: float,
        start_index: int,
        count: int,
    ) -> IqSamples:
        """Produce ``count`` samples as seen by a receiver tuned to ``center_freq_hz``.

        ``start_index`` is an absolute position on the receiver's timeline: consecutive ranges
        must join up continuously, and the same range must always give the same samples.
        """


@dataclass(frozen=True, slots=True)
class SyntheticMux:
    """A DVB multiplex to simulate, with the transport stream it carries."""

    freq_hz: float
    """Centre frequency of the channel."""

    transport_stream: bytes
    """TS bytes to replay, looped. Must be a whole number of 188-byte packets."""

    bandwidth_hz: float = 8e6
    """Channel bandwidth. 8 MHz for UHF DVB-T in most of the world, 7 MHz for VHF."""

    snr_db: float = 25.0
    """Carrier-to-noise ratio the frontend will report."""

    ber: float = 0.0
    """Post-Viterbi bit error rate the frontend will report."""

    strength_dbm: float = -45.0
    """Signal strength the frontend will report."""

    label: str | None = None
    """Name for this multiplex, to make test failures readable."""

    def __post_init__(self) -> None:
        if self.freq_hz <= 0:
            raise ValueError(f"multiplex frequency must be positive, got {self.freq_hz}")
        if self.bandwidth_hz <= 0:
            raise ValueError(f"bandwidth must be positive, got {self.bandwidth_hz}")
        if not self.transport_stream:
            raise ValueError("multiplex carries no transport stream bytes")
        if len(self.transport_stream) % TS_PACKET_SIZE != 0:
            raise ValueError(
                f"transport stream is {len(self.transport_stream)} bytes, "
                f"not a whole number of {TS_PACKET_SIZE}-byte packets"
            )
        if not 0.0 <= self.ber <= 1.0:
            raise ValueError(f"bit error rate must be between 0 and 1, got {self.ber}")


class MockSdrDevice(SdrDevice):
    """A receiver that synthesises the band it is pointed at.

    Example::

        from openwave.radio.synthesis import SyntheticFmStation

        device = MockSdrDevice(
            sources=[
                SyntheticFmStation(freq_hz=98.0e6, power_dbfs=-20.0, stereo=True),
                SyntheticFmStation(freq_hz=101.4e6, power_dbfs=-35.0),
            ],
            noise_floor_dbfs=-70.0,
        )
        with device:
            device.set_sample_rate(2_400_000)
            device.set_center_freq(99_000_000)
            samples = device.read_samples(262_144)

    Both stations are inside a 2.4 MHz window centred on 99 MHz, so both appear in the samples.

    The sample timeline advances monotonically across reads and across retunes, which is what
    hardware does: time passes while the tuner settles.

    Output is fully determined by the seed and the position on that timeline. Both the signal
    and the noise are evaluated from the absolute sample index, so one read of 4096 samples is
    bit-for-bit identical to two reads of 2048, and :meth:`reset_timeline` reproduces a run
    exactly. That is what makes a failing test reproducible from a seed and an offset.

    Args:
        sources: Transmissions present on the air.
        noise_floor_dbfs: Integrated noise power across the whole window, in dBFS. Note that
            this is total power, not a density: widening the sample rate spreads the same power
            over more bandwidth rather than adding more of it. Pass
            :data:`~openwave.core.units.SILENCE_DBFS` for a band with no noise at all.
        seed: Seed for the noise generator. Fixed by default, so a failing test fails the same
            way twice. Pass ``None`` for genuinely random noise.
        tuning_range: Frequencies this receiver can reach.
        supported_sample_rates_hz: Discrete rates to accept, or ``None`` to accept any positive
            rate. Set it to exercise the validation in :meth:`SdrDevice.set_sample_rate`.
        gain_range_db: Lowest and highest gain accepted, in dB.
        edge_attenuation_db: Attenuation applied to a signal sitting at the very edge of the
            window, modelling the receiver's anti-aliasing filter.
        dc_offset: Constant added to every sample, modelling the DC spike at the centre of a
            direct-conversion tuner's window.
        frequency_error_ppm: Local oscillator error, in parts per million. A real dongle's
            crystal is off by tens of ppm unless calibrated, which at 100 MHz is a few kHz.
        clip: Whether to clip the result to full scale, as an ADC would.
        present: Set ``False`` to make :meth:`open` raise ``DeviceNotFoundError``.
        busy: Set ``True`` to make :meth:`open` raise ``DeviceBusyError``.
        index: Device index to report.
    """

    def __init__(
        self,
        sources: Sequence[SignalSource] = (),
        *,
        noise_floor_dbfs: float = DEFAULT_NOISE_FLOOR_DBFS,
        seed: int | None = 0,
        tuning_range: TuningRange = R820T_TUNING_RANGE,
        supported_sample_rates_hz: tuple[float, ...] | None = None,
        gain_range_db: tuple[float, float] = (0.0, 49.6),
        edge_attenuation_db: float = DEFAULT_EDGE_ATTENUATION_DB,
        dc_offset: complex = 0j,
        frequency_error_ppm: float = 0.0,
        clip: bool = True,
        present: bool = True,
        busy: bool = False,
        index: int = 0,
    ) -> None:
        super().__init__()
        if noise_floor_dbfs > 0:
            raise ValueError(f"noise floor must not exceed full scale, got {noise_floor_dbfs}")
        if edge_attenuation_db < 0:
            raise ValueError(f"edge attenuation must not be negative, got {edge_attenuation_db}")
        if gain_range_db[1] < gain_range_db[0]:
            raise ValueError(f"gain range is inverted: {gain_range_db}")

        self._sources = tuple(sources)
        self._noise_floor_dbfs = noise_floor_dbfs
        self._tuning_range = tuning_range
        self._supported_sample_rates_hz = supported_sample_rates_hz
        self._gain_range_db = gain_range_db
        self._edge_attenuation_db = edge_attenuation_db
        self._dc_offset = dc_offset
        self._frequency_error_ppm = frequency_error_ppm
        self._clip = clip
        self._present = present
        self._busy = busy
        self._index = index

        # A seed is always resolved to a concrete number, even when the caller asked for
        # randomness, so that a surprising run can be reproduced from the reported seed.
        self._seed = secrets.randbits(63) if seed is None else seed
        self._sample_index = 0

    # -- Identity and capabilities ------------------------------------------------------------

    @property
    def info(self) -> DeviceInfo:
        count = len(self._sources)
        plural = "" if count == 1 else "s"
        return DeviceInfo(
            driver="mock",
            index=self._index,
            label=f"Simulated receiver ({count} transmission{plural})",
            serial=f"MOCK-{self._index:04d}",
        )

    @property
    def tuning_range(self) -> TuningRange:
        return self._tuning_range

    @property
    def supported_sample_rates_hz(self) -> tuple[float, ...] | None:
        return self._supported_sample_rates_hz

    # -- Simulation state ---------------------------------------------------------------------

    @property
    def sources(self) -> tuple[SignalSource, ...]:
        """The transmissions being simulated."""
        return self._sources

    @property
    def noise_floor_dbfs(self) -> float:
        """Integrated noise power across the window, in dBFS."""
        return self._noise_floor_dbfs

    @property
    def sample_index(self) -> int:
        """Position on the receiver's timeline: how many samples have been produced so far."""
        return self._sample_index

    @property
    def seed(self) -> int:
        """The seed in use.

        Always a concrete number, even when the caller passed ``None`` and asked for a random
        band, so that an unexpected result can be reproduced by passing this seed back.
        """
        return self._seed

    def reset_timeline(self) -> None:
        """Rewind the sample clock to zero, so the next read reproduces the first one."""
        self._sample_index = 0

    def wideband_snr_db(self, source: SignalSource) -> float:
        """Signal-to-noise ratio of ``source`` across the whole window, in dB.

        This is what a test should compare a detector's output against, bearing in mind that a
        detector measuring a narrow slice of the spectrum sees a *better* ratio than this,
        because it rejects most of the noise.
        """
        power_dbfs = getattr(source, "power_dbfs", None)
        if power_dbfs is None:
            raise TypeError(f"{type(source).__name__} does not report a power level")
        return float(power_dbfs) - self._noise_floor_dbfs

    # -- Driver hooks -------------------------------------------------------------------------

    def _do_open(self) -> None:
        if not self._present:
            raise DeviceNotFoundError(f"simulated receiver {self._index} is configured as absent")
        if self._busy:
            raise DeviceBusyError(
                f"simulated receiver {self._index} is configured as claimed by another process"
            )

    def _do_close(self) -> None:
        pass

    def _do_set_center_freq(self, freq_hz: float) -> None:
        pass

    def _do_set_sample_rate(self, rate_hz: float) -> None:
        pass

    def _do_set_gain(self, gain: Gain) -> None:
        if gain == "auto":
            return
        low, high = self._gain_range_db
        if not low <= gain <= high:
            raise UnsupportedGainError(
                f"gain must be between {low} and {high} dB, or 'auto'; got {gain}"
            )

    def _do_read_samples(self, count: int) -> IqSamples:
        samples = self._synthesise(count)
        self._sample_index += count
        return samples

    # -- Synthesis ----------------------------------------------------------------------------

    def _synthesise(self, count: int) -> IqSamples:
        """Build the band: every visible transmission, plus noise, plus the receiver's flaws."""
        sample_rate_hz = self.sample_rate_hz
        effective_center_hz = self.center_freq_hz * (1.0 + self._frequency_error_ppm / 1e6)

        total: IqSamples = np.zeros(count, dtype=np.complex64)
        for source in self._sources:
            attenuation = self._window_attenuation(source.freq_hz - effective_center_hz)
            if attenuation is None:
                continue
            contribution = source.samples(
                center_freq_hz=effective_center_hz,
                sample_rate_hz=sample_rate_hz,
                start_index=self._sample_index,
                count=count,
            )
            total += contribution * np.complex64(attenuation)

        total += self._noise(count)
        total += np.complex64(self._dc_offset)
        total *= np.complex64(self._linear_gain())

        if self._clip:
            total = _clip_to_full_scale(total)
        return total

    def _window_attenuation(self, offset_hz: float) -> float | None:
        """Linear gain to apply to a signal ``offset_hz`` from the centre of the window.

        Returns ``None`` for a signal outside the digitised window, which the caller drops.

        Inside the usable fraction of the window the response is flat. Beyond it the signal is
        attenuated, reaching ``edge_attenuation_db`` at the Nyquist edge -- a crude stand-in for
        the skirt of the receiver's anti-aliasing filter, which is enough to make the scanner's
        habit of overlapping its segments testable.
        """
        half_window_hz = self.sample_rate_hz / 2.0
        distance_hz = abs(offset_hz)
        if distance_hz > half_window_hz:
            return None

        usable_half_hz = half_window_hz * self.usable_fraction
        if distance_hz <= usable_half_hz:
            return 1.0

        skirt = (distance_hz - usable_half_hz) / (half_window_hz - usable_half_hz)
        return float(10.0 ** (-self._edge_attenuation_db * skirt / 20.0))

    def _noise(self, count: int) -> IqSamples:
        """Complex additive white Gaussian noise at the configured floor.

        The power is split equally between the real and imaginary parts, so the total comes out
        at :attr:`noise_floor_dbfs`.

        The noise is *addressable*: it is a function of the seed and the absolute sample index,
        not of how many times the device has been read. Reading 4096 samples gives exactly the
        same noise as reading 2048 twice, and a read at index 50_000 always reproduces. That
        property is worth the hand-rolled generator below, because it means a failing run can be
        reproduced from a seed and an offset alone.

        It works because ``Generator.random`` consumes exactly one 64-bit word per value, so
        ``advance`` can position the stream precisely. ``standard_normal`` has no such guarantee
        -- it draws a variable number of words per value -- hence Box-Muller applied to uniforms
        rather than a direct normal draw.
        """
        # A floor at or below the project's silence sentinel means exactly silence, so that a
        # test wanting a noiseless band gets zeros rather than values around 1e-16.
        if self._noise_floor_dbfs <= SILENCE_DBFS:
            return np.zeros(count, dtype=np.complex64)

        sigma = float(np.sqrt(dbfs_to_power(self._noise_floor_dbfs) / 2.0))
        if sigma == 0.0:
            return np.zeros(count, dtype=np.complex64)

        bit_generator = np.random.PCG64(self._seed)
        bit_generator.advance(2 * self._sample_index)
        uniforms = np.random.Generator(bit_generator).random(2 * count)

        magnitude = sigma * np.sqrt(-2.0 * np.log1p(-uniforms[0::2]))
        angle = 2.0 * np.pi * uniforms[1::2]
        noise: IqSamples = (magnitude * (np.cos(angle) + 1j * np.sin(angle))).astype(np.complex64)
        return noise

    def _linear_gain(self) -> float:
        """The gain setting as a linear factor. ``"auto"`` means unity."""
        gain = self.gain
        if gain == "auto":
            return 1.0
        return float(10.0 ** (gain / 20.0))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sources={len(self._sources)}, "
            f"noise_floor_dbfs={self._noise_floor_dbfs}, "
            f"center={format_frequency(self.center_freq_hz)})"
        )


class MockDvbDevice(DvbDevice):
    """A DVB-T tuner that replays transport streams for the channels it is told are occupied.

    Example::

        device = MockDvbDevice(muxes=[SyntheticMux(freq_hz=498e6, transport_stream=ts_bytes)])
        with device:
            device.tune(498e6, 8e6)
            assert device.wait_for_lock()
            packets = device.read_ts(188 * 100)

    Args:
        muxes: Multiplexes present on the air.
        tuning_tolerance_hz: How far off a requested frequency may be and still find a
            multiplex. Real tuners pull in a signal a little off centre.
        lock_delay_s: How long the demodulator takes to lock. :meth:`wait_for_lock` reports
            failure if it is given less time than this, which is how an impatient scan behaves.
        tuning_range: Frequencies this tuner can reach.
        present: Set ``False`` to make :meth:`open` raise ``DeviceNotFoundError``.
        busy: Set ``True`` to make :meth:`open` raise ``DeviceBusyError``.
        index: Device index to report.
    """

    def __init__(
        self,
        muxes: Sequence[SyntheticMux] = (),
        *,
        tuning_tolerance_hz: float = 200e3,
        lock_delay_s: float = 0.0,
        tuning_range: TuningRange = DVB_T_TUNING_RANGE,
        present: bool = True,
        busy: bool = False,
        index: int = 0,
    ) -> None:
        super().__init__()
        if tuning_tolerance_hz < 0:
            raise ValueError(f"tuning tolerance must not be negative, got {tuning_tolerance_hz}")
        if lock_delay_s < 0:
            raise ValueError(f"lock delay must not be negative, got {lock_delay_s}")

        self._muxes = tuple(muxes)
        self._tuning_tolerance_hz = tuning_tolerance_hz
        self._lock_delay_s = lock_delay_s
        self._tuning_range = tuning_range
        self._present = present
        self._busy = busy
        self._index = index

        self._tuned_mux: SyntheticMux | None = None
        self._locked = False
        self._read_offset = 0

    # -- Identity -----------------------------------------------------------------------------

    @property
    def info(self) -> DeviceInfo:
        count = len(self._muxes)
        plural = "" if count == 1 else "es"
        return DeviceInfo(
            driver="mockdvb",
            index=self._index,
            label=f"Simulated DVB-T tuner ({count} multiplex{plural})",
            serial=f"MOCKDVB-{self._index:04d}",
        )

    @property
    def tuning_range(self) -> TuningRange:
        return self._tuning_range

    @property
    def muxes(self) -> tuple[SyntheticMux, ...]:
        """The multiplexes being simulated."""
        return self._muxes

    # -- Driver hooks -------------------------------------------------------------------------

    def _do_open(self) -> None:
        if not self._present:
            raise DeviceNotFoundError(f"simulated DVB tuner {self._index} is configured as absent")
        if self._busy:
            raise DeviceBusyError(
                f"simulated DVB tuner {self._index} is configured as claimed by another process"
            )

    def _do_close(self) -> None:
        self._tuned_mux = None
        self._locked = False

    def _do_tune(self, freq_hz: float, bandwidth_hz: float) -> None:
        self._locked = False
        self._read_offset = 0
        self._tuned_mux = self._find_mux(freq_hz, bandwidth_hz)

    def _do_wait_for_lock(self, timeout_s: float) -> bool:
        if self._tuned_mux is None:
            return False
        # The simulation does not actually sleep: tests should not pay for a lock delay in wall
        # clock time. It only reports whether the caller was prepared to wait long enough.
        self._locked = timeout_s >= self._lock_delay_s
        return self._locked

    def _do_signal_quality(self) -> SignalQuality:
        mux = self._tuned_mux
        if mux is None or not self._locked:
            return SignalQuality(locked=False)
        return SignalQuality(
            locked=True,
            snr_db=mux.snr_db,
            strength_dbm=mux.strength_dbm,
            ber=mux.ber,
        )

    def _do_read_ts(self, size: int) -> bytes:
        mux = self._tuned_mux
        if mux is None or not self._locked:
            raise NoLockError(
                f"no lock at {format_frequency(self.center_freq_hz)}; "
                "call tune() and wait_for_lock() first"
            )
        return _read_looping(mux.transport_stream, self._advance_offset(size, mux), size)

    # -- Helpers ------------------------------------------------------------------------------

    def _find_mux(self, freq_hz: float, bandwidth_hz: float) -> SyntheticMux | None:
        """The multiplex at ``freq_hz``, if one is within the tuning tolerance."""
        for mux in self._muxes:
            if (
                abs(mux.freq_hz - freq_hz) <= self._tuning_tolerance_hz
                and mux.bandwidth_hz == bandwidth_hz
            ):
                return mux
        return None

    def _advance_offset(self, size: int, mux: SyntheticMux) -> int:
        """Return the current read offset and advance it, wrapping around the stream."""
        offset = self._read_offset
        self._read_offset = (offset + size) % len(mux.transport_stream)
        return offset


def _read_looping(data: bytes, offset: int, size: int) -> bytes:
    """Read ``size`` bytes from ``data`` starting at ``offset``, wrapping around the end."""
    if size <= len(data) - offset:
        return data[offset : offset + size]
    chunks = [data[offset:]]
    remaining = size - len(chunks[0])
    whole, tail = divmod(remaining, len(data))
    chunks.extend([data] * whole)
    chunks.append(data[:tail])
    return b"".join(chunks)


def _clip_to_full_scale(samples: IqSamples) -> IqSamples:
    """Clip the real and imaginary parts to the range an ADC can represent."""
    clipped: IqSamples = (
        np.clip(samples.real, -1.0, 1.0) + 1j * np.clip(samples.imag, -1.0, 1.0)
    ).astype(np.complex64)
    return clipped
