"""Receiver interfaces.

OpenWave supports two fundamentally different kinds of hardware, so it exposes two interfaces
rather than pretending they are the same thing:

:class:`SdrDevice`
    A raw IQ sample source. An RTL-SDR hands over complex baseband samples and leaves every
    demodulation step to software.

:class:`DvbDevice`
    A demodulating tuner. A DVB-T frontend demodulates in hardware and hands over a finished
    MPEG transport stream.

Both interfaces keep the device state (open, frequency, sample rate) in the base class and
validate every request before it reaches the driver, so a driver only implements the parts that
actually talk to hardware.

Nothing outside :mod:`openwave.sdr` may import a vendor library or open a device node. That rule
is what makes the rest of the codebase testable without hardware.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Self

import numpy as np
import numpy.typing as npt

from openwave.sdr.errors import (
    DeviceNotOpenError,
    TuningFailedError,
    UnsupportedSampleRateError,
)

#: Complex baseband samples, normalised to roughly the unit circle (full scale = 1.0).
IqSamples = npt.NDArray[np.complex64]

#: Gain in dB, or ``"auto"`` to let the receiver's AGC decide.
Gain = float | Literal["auto"]

#: Fraction of the sample rate that is actually usable. The edges of an SDR's passband are
#: shaped by the anti-aliasing filter and are not trustworthy for measurement, so the scanner
#: only believes the middle of each window and overlaps its segments accordingly.
DEFAULT_USABLE_FRACTION = 0.8

#: Size of one MPEG transport stream packet, in bytes. Fixed by ISO/IEC 13818-1.
TS_PACKET_SIZE = 188


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity of a receiver, as reported by its driver."""

    driver: str
    """Driver name, as accepted by :func:`openwave.sdr.device_manager.open_device`."""

    index: int
    """Position within the devices that driver found, starting at zero."""

    label: str
    """Human-readable description, for display in a device picker."""

    serial: str | None = None
    """Serial number, where the hardware reports one."""

    def __str__(self) -> str:
        suffix = f" [{self.serial}]" if self.serial else ""
        return f"{self.driver}:{self.index} {self.label}{suffix}"


@dataclass(frozen=True, slots=True)
class TuningRange:
    """The span of centre frequencies a receiver can reach.

    A range may be a single frequency, where ``min_hz`` equals ``max_hz``. That is not a
    degenerate case to guard against but the honest description of a replayed capture: a
    recording holds one window of spectrum and nothing outside it.
    """

    min_hz: float
    max_hz: float

    def __post_init__(self) -> None:
        if self.min_hz <= 0:
            raise ValueError(f"tuning range must start above zero, got {self.min_hz} Hz")
        if self.max_hz < self.min_hz:
            raise ValueError(f"tuning range is inverted: {self.min_hz} Hz to {self.max_hz} Hz")

    def contains(self, freq_hz: float) -> bool:
        """Whether ``freq_hz`` is reachable."""
        return self.min_hz <= freq_hz <= self.max_hz


@dataclass(frozen=True, slots=True)
class SignalQuality:
    """What a demodulating tuner reports about the signal it is receiving.

    Fields are ``None`` when the driver cannot measure them, which is common: the Linux DVB API
    lets a frontend decline any individual statistic.
    """

    locked: bool
    """Whether the demodulator has acquired the signal."""

    snr_db: float | None = None
    """Carrier-to-noise ratio in dB."""

    strength_dbm: float | None = None
    """Received signal strength in dBm, where the frontend provides a calibrated figure."""

    ber: float | None = None
    """Post-Viterbi bit error rate, as a fraction between 0 and 1."""


class SdrDevice(ABC):
    """A source of raw complex baseband samples.

    Use it as a context manager, which guarantees the device is released even if a scan raises::

        with open_device("mock") as device:
            device.set_sample_rate(2_400_000)
            device.set_center_freq(98_000_000)
            samples = device.read_samples(262_144)

    Subclasses implement the ``_do_*`` hooks. The base class owns the state and the validation.
    """

    def __init__(self) -> None:
        self._is_open = False
        self._center_freq_hz = 0.0
        self._sample_rate_hz = 0.0
        self._gain: Gain = "auto"

    # -- Identity and capabilities ------------------------------------------------------------

    @property
    @abstractmethod
    def info(self) -> DeviceInfo:
        """Identity of this receiver."""

    @property
    @abstractmethod
    def tuning_range(self) -> TuningRange:
        """Centre frequencies this receiver can reach."""

    @property
    @abstractmethod
    def supported_sample_rates_hz(self) -> tuple[float, ...] | None:
        """Discrete sample rates the receiver accepts, or ``None`` if the rate is continuous.

        A continuous range is still bounded; a driver that reports ``None`` validates the rate
        itself in :meth:`_do_set_sample_rate`.
        """

    @property
    def usable_fraction(self) -> float:
        """Fraction of each window the scanner should trust. See :data:`DEFAULT_USABLE_FRACTION`."""
        return DEFAULT_USABLE_FRACTION

    # -- State --------------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the device is currently claimed by this process."""
        return self._is_open

    @property
    def center_freq_hz(self) -> float:
        """The frequency the receiver is currently tuned to."""
        return self._center_freq_hz

    @property
    def sample_rate_hz(self) -> float:
        """The current sample rate."""
        return self._sample_rate_hz

    @property
    def gain(self) -> Gain:
        """The current gain setting."""
        return self._gain

    @property
    def usable_bandwidth_hz(self) -> float:
        """Bandwidth the scanner may trust around the centre frequency.

        Narrower than the sample rate, because the edges of the passband are shaped by the
        anti-aliasing filter.
        """
        return self._sample_rate_hz * self.usable_fraction

    # -- Lifecycle ----------------------------------------------------------------------------

    def open(self) -> None:
        """Claim the receiver. Calling this on an already-open device does nothing."""
        if self._is_open:
            return
        self._do_open()
        self._is_open = True

    def close(self) -> None:
        """Release the receiver. Calling this on a closed device does nothing."""
        if not self._is_open:
            return
        try:
            self._do_close()
        finally:
            self._is_open = False

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- Configuration ------------------------------------------------------------------------

    def set_center_freq(self, freq_hz: float) -> None:
        """Tune to ``freq_hz``.

        Raises:
            DeviceNotOpenError: if the device is not open.
            TuningFailedError: if the frequency is outside :attr:`tuning_range`.
        """
        self._require_open()
        if not self.tuning_range.contains(freq_hz):
            raise TuningFailedError(
                f"{freq_hz / 1e6:.3f} MHz is outside the tuning range of {self.info.driver} "
                f"({self.tuning_range.min_hz / 1e6:.1f}-{self.tuning_range.max_hz / 1e6:.1f} MHz)"
            )
        self._do_set_center_freq(freq_hz)
        self._center_freq_hz = freq_hz

    def set_sample_rate(self, rate_hz: float) -> None:
        """Set the sampling rate.

        Raises:
            DeviceNotOpenError: if the device is not open.
            UnsupportedSampleRateError: if the receiver does not accept this rate.
        """
        self._require_open()
        if rate_hz <= 0:
            raise UnsupportedSampleRateError(f"sample rate must be positive, got {rate_hz}")
        supported = self.supported_sample_rates_hz
        if supported is not None and rate_hz not in supported:
            offered = ", ".join(f"{r / 1e6:g}" for r in supported)
            raise UnsupportedSampleRateError(
                f"{self.info.driver} does not support {rate_hz / 1e6:g} MS/s; "
                f"supported rates in MS/s: {offered}"
            )
        self._do_set_sample_rate(rate_hz)
        self._sample_rate_hz = rate_hz

    def set_gain(self, gain: Gain = "auto") -> None:
        """Set the receiver gain in dB, or ``"auto"`` for the hardware AGC.

        Raises:
            DeviceNotOpenError: if the device is not open.
            UnsupportedGainError: if the receiver does not accept this gain.
        """
        self._require_open()
        self._do_set_gain(gain)
        self._gain = gain

    # -- Sample acquisition -------------------------------------------------------------------

    def read_samples(self, count: int) -> IqSamples:
        """Read exactly ``count`` complex samples.

        Blocks until they are available. ``count`` should be a power of two: both the hardware
        and the FFTs downstream are happier that way.

        Raises:
            DeviceNotOpenError: if the device is not open.
            ValueError: if ``count`` is not positive.
            SampleOverflowError: if samples were dropped before they could be read.
        """
        self._require_open()
        if count <= 0:
            raise ValueError(f"sample count must be positive, got {count}")
        if self._sample_rate_hz <= 0:
            raise UnsupportedSampleRateError(
                "sample rate has not been set; call set_sample_rate() before reading"
            )
        samples = self._do_read_samples(count)
        if samples.shape != (count,):
            raise AssertionError(
                f"{type(self).__name__} returned {samples.shape} samples, expected ({count},)"
            )
        return samples

    # -- Helpers for subclasses ---------------------------------------------------------------

    def _require_open(self) -> None:
        """Raise unless the device is open."""
        if not self._is_open:
            raise DeviceNotOpenError(
                f"{self.info.driver} device is not open; call open() or use it as a context manager"
            )

    # -- Driver hooks -------------------------------------------------------------------------

    @abstractmethod
    def _do_open(self) -> None:
        """Claim the hardware. Raise ``DeviceNotFoundError`` or ``DeviceBusyError`` on failure."""

    @abstractmethod
    def _do_close(self) -> None:
        """Release the hardware. Must not raise for an already-released device."""

    @abstractmethod
    def _do_set_center_freq(self, freq_hz: float) -> None:
        """Tune the hardware. The frequency is already known to be in range."""

    @abstractmethod
    def _do_set_sample_rate(self, rate_hz: float) -> None:
        """Set the hardware sample rate. Already validated against the supported rates."""

    @abstractmethod
    def _do_set_gain(self, gain: Gain) -> None:
        """Set the hardware gain."""

    @abstractmethod
    def _do_read_samples(self, count: int) -> IqSamples:
        """Return exactly ``count`` complex64 samples."""


class DvbDevice(ABC):
    """A demodulating tuner that produces an MPEG transport stream.

    Unlike :class:`SdrDevice`, the demodulation happens in hardware. OpenWave tunes a channel,
    waits for lock, then reads transport stream packets.
    """

    def __init__(self) -> None:
        self._is_open = False
        self._center_freq_hz = 0.0
        self._bandwidth_hz = 0.0

    # -- Identity -----------------------------------------------------------------------------

    @property
    @abstractmethod
    def info(self) -> DeviceInfo:
        """Identity of this tuner."""

    @property
    @abstractmethod
    def tuning_range(self) -> TuningRange:
        """Centre frequencies this tuner can reach."""

    # -- State --------------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the tuner is currently claimed by this process."""
        return self._is_open

    @property
    def center_freq_hz(self) -> float:
        """The channel centre frequency currently tuned."""
        return self._center_freq_hz

    @property
    def bandwidth_hz(self) -> float:
        """The channel bandwidth currently configured."""
        return self._bandwidth_hz

    # -- Lifecycle ----------------------------------------------------------------------------

    def open(self) -> None:
        """Claim the tuner. Calling this on an already-open device does nothing."""
        if self._is_open:
            return
        self._do_open()
        self._is_open = True

    def close(self) -> None:
        """Release the tuner. Calling this on a closed device does nothing."""
        if not self._is_open:
            return
        try:
            self._do_close()
        finally:
            self._is_open = False

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- Tuning -------------------------------------------------------------------------------

    def tune(self, freq_hz: float, bandwidth_hz: float) -> None:
        """Tune to a channel and start the demodulator.

        This returns as soon as the request is accepted. It does not wait for lock: use
        :meth:`wait_for_lock`, because a scan wants to move on quickly from an empty channel.

        Raises:
            DeviceNotOpenError: if the tuner is not open.
            TuningFailedError: if the frequency is outside :attr:`tuning_range`.
            ValueError: if ``bandwidth_hz`` is not positive.
        """
        self._require_open()
        if not self.tuning_range.contains(freq_hz):
            raise TuningFailedError(
                f"{freq_hz / 1e6:.3f} MHz is outside the tuning range of {self.info.driver} "
                f"({self.tuning_range.min_hz / 1e6:.1f}-{self.tuning_range.max_hz / 1e6:.1f} MHz)"
            )
        if bandwidth_hz <= 0:
            raise ValueError(f"bandwidth must be positive, got {bandwidth_hz}")
        self._do_tune(freq_hz, bandwidth_hz)
        self._center_freq_hz = freq_hz
        self._bandwidth_hz = bandwidth_hz

    def wait_for_lock(self, timeout_s: float = 2.0) -> bool:
        """Wait up to ``timeout_s`` for the demodulator to lock.

        Returns ``True`` on lock and ``False`` on timeout. It returns a boolean rather than
        raising, because an empty channel is the expected result for most of a scan.
        """
        self._require_open()
        if timeout_s < 0:
            raise ValueError(f"timeout must not be negative, got {timeout_s}")
        return self._do_wait_for_lock(timeout_s)

    def signal_quality(self) -> SignalQuality:
        """Read the demodulator's current statistics."""
        self._require_open()
        return self._do_signal_quality()

    def read_ts(self, size: int) -> bytes:
        """Read up to ``size`` bytes of transport stream.

        The result is always a whole number of 188-byte TS packets, and may be shorter than
        ``size`` if the stream pauses. An empty result means no data arrived.

        Raises:
            DeviceNotOpenError: if the tuner is not open.
            NoLockError: if the demodulator is not locked.
            ValueError: if ``size`` is not a positive multiple of 188.
        """
        self._require_open()
        if size <= 0 or size % TS_PACKET_SIZE != 0:
            raise ValueError(
                f"size must be a positive multiple of {TS_PACKET_SIZE} bytes, got {size}"
            )
        data = self._do_read_ts(size)
        if len(data) % TS_PACKET_SIZE != 0:
            raise AssertionError(
                f"{type(self).__name__} returned {len(data)} bytes, "
                f"not a multiple of {TS_PACKET_SIZE}"
            )
        return data

    # -- Helpers for subclasses ---------------------------------------------------------------

    def _require_open(self) -> None:
        """Raise unless the tuner is open."""
        if not self._is_open:
            raise DeviceNotOpenError(
                f"{self.info.driver} tuner is not open; call open() or use it as a context manager"
            )

    # -- Driver hooks -------------------------------------------------------------------------

    @abstractmethod
    def _do_open(self) -> None:
        """Claim the hardware."""

    @abstractmethod
    def _do_close(self) -> None:
        """Release the hardware."""

    @abstractmethod
    def _do_tune(self, freq_hz: float, bandwidth_hz: float) -> None:
        """Tune the hardware. The frequency is already known to be in range."""

    @abstractmethod
    def _do_wait_for_lock(self, timeout_s: float) -> bool:
        """Wait for lock, returning whether it was acquired."""

    @abstractmethod
    def _do_signal_quality(self) -> SignalQuality:
        """Read the demodulator statistics."""

    @abstractmethod
    def _do_read_ts(self, size: int) -> bytes:
        """Return whole TS packets, at most ``size`` bytes."""
