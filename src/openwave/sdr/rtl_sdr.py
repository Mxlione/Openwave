"""RTL-SDR support, through the ``pyrtlsdr`` bindings to ``librtlsdr``.

.. warning::

   **This driver has never run on physical hardware.** The maintainer owns no RTL-SDR dongle. It
   is written from the ``pyrtlsdr`` and ``librtlsdr`` interfaces and is type-checked, but no
   sample has ever come out of it.

   Everything else in OpenWave is verified against simulated receivers, which cannot catch a
   wrong call, a wrong unit or a wrong assumption about what the hardware does. If you own a
   dongle, running this and reporting what happened -- including a crash -- is the most useful
   contribution available. See the issues labelled ``hardware validation`` and the
   **Hardware report** issue template.

   Specific things worth checking, because they are where a blind implementation most often
   goes wrong:

   * Does the gain list come back in dB or in tenths of a dB? This driver assumes ``pyrtlsdr``
     has already converted to dB, and picks the nearest supported step.
   * Does ``read_samples`` return samples already normalised to roughly the unit circle? This
     driver assumes it does, as the documentation states.
   * Is the frequency correction in parts per million applied before or after tuning?
   * Does closing and reopening a dongle in the same process work, or does it need a reset?

Install the optional extra to use it::

    pip install "openwave[rtlsdr]"
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np

from openwave.sdr.device import DeviceInfo, Gain, IqSamples, SdrDevice, TuningRange
from openwave.sdr.errors import (
    DeviceBusyError,
    DeviceNotFoundError,
    DriverUnavailableError,
    TuningFailedError,
    UnsupportedGainError,
    UnsupportedSampleRateError,
)

#: Tuning range of the R820T and R820T2 tuners used by almost every RTL-SDR dongle, in Hz.
#:
#: Other tuner chips differ: the E4000 reaches 1.1 GHz with a gap around 1.1-1.25 GHz, and the
#: FC0013 stops at 948 MHz. The driver narrows this to what the device reports where it can.
RTL_SDR_TUNING_RANGE: Final = TuningRange(min_hz=24e6, max_hz=1_766e6)

#: Sample rates librtlsdr accepts, in Hz.
#:
#: The hardware takes a continuous range, but rates between 300 kS/s and 900 kS/s are documented
#: as unreliable, and above 2.4 MS/s the USB link starts dropping samples on most machines.
#: These are the rates in common use.
RTL_SDR_SAMPLE_RATES: Final = (
    225_001.0,
    250_000.0,
    1_024_000.0,
    1_200_000.0,
    1_400_000.0,
    1_600_000.0,
    1_800_000.0,
    1_920_000.0,
    2_048_000.0,
    2_400_000.0,
    2_560_000.0,
    2_880_000.0,
    3_200_000.0,
)

#: Highest rate that is reliable over USB on a typical machine, in Hz.
RTL_SDR_SAFE_MAX_SAMPLE_RATE: Final = 2_400_000.0


def _import_rtlsdr() -> Any:
    """Import ``pyrtlsdr``, turning a missing dependency into a ``DeviceError``.

    Callers handling absent hardware should not also have to handle ``ImportError``.
    """
    try:
        import rtlsdr
    except ImportError as error:
        raise DriverUnavailableError(
            "the RTL-SDR driver needs the pyrtlsdr package, which is an optional extra; "
            'install it with: pip install "openwave[rtlsdr]"'
        ) from error
    return rtlsdr


def rtlsdr_available() -> bool:
    """Whether ``pyrtlsdr`` is installed. Does not touch any hardware."""
    try:
        _import_rtlsdr()
    except DriverUnavailableError:
        return False
    return True


def list_rtlsdr_devices() -> list[DeviceInfo]:
    """Enumerate attached RTL-SDR dongles.

    Returns an empty list when the driver is not installed or no dongle is attached, because
    "nothing found" is the normal answer during device discovery and not an error.
    """
    try:
        rtlsdr = _import_rtlsdr()
    except DriverUnavailableError:
        return []

    try:
        serials = rtlsdr.RtlSdr.get_device_serial_addresses()
    except Exception:  # noqa: BLE001 - librtlsdr reports absence in several different ways
        return []

    devices: list[DeviceInfo] = []
    for index, serial in enumerate(serials):
        text = str(serial) if serial else None
        devices.append(
            DeviceInfo(
                driver="rtlsdr",
                index=index,
                label="RTL-SDR dongle",
                serial=text,
            )
        )
    return devices


class RtlSdrDevice(SdrDevice):
    """An RTL-SDR dongle.

    .. warning::
       Never tested on hardware. See the module docstring.

    Example::

        with RtlSdrDevice() as device:
            device.set_sample_rate(2_400_000)
            device.set_gain("auto")
            device.set_center_freq(98_000_000)
            samples = device.read_samples(1 << 18)

    Args:
        index: Which attached dongle to use.
        serial: Serial number to open instead of an index. Takes precedence over ``index``,
            and is the reliable way to pick a dongle when several are attached, because index
            order is not stable across reboots.
        frequency_correction_ppm: Crystal error correction, in parts per million. An
            uncalibrated dongle is typically tens of ppm out, which at 100 MHz is a few kHz --
            enough to put a station in the wrong 100 kHz channel.
        bias_tee: Whether to power an external amplifier down the antenna cable. Leave it off
            unless you know the attached hardware expects it.
    """

    def __init__(
        self,
        index: int = 0,
        *,
        serial: str | None = None,
        frequency_correction_ppm: int = 0,
        bias_tee: bool = False,
    ) -> None:
        super().__init__()
        if index < 0:
            raise ValueError(f"device index must not be negative, got {index}")
        self._index = index
        self._serial = serial
        self._frequency_correction_ppm = frequency_correction_ppm
        self._bias_tee = bias_tee
        self._sdr: Any = None
        self._tuning_range = RTL_SDR_TUNING_RANGE

    # -- Identity and capabilities ------------------------------------------------------------

    @property
    def info(self) -> DeviceInfo:
        return DeviceInfo(
            driver="rtlsdr",
            index=self._index,
            label="RTL-SDR dongle",
            serial=self._serial,
        )

    @property
    def tuning_range(self) -> TuningRange:
        return self._tuning_range

    @property
    def supported_sample_rates_hz(self) -> tuple[float, ...]:
        return RTL_SDR_SAMPLE_RATES

    # -- Hardware state -----------------------------------------------------------------------

    @property
    def available_gains_db(self) -> tuple[float, ...]:
        """Gain steps the tuner offers, in dB. Empty until the device is opened."""
        if self._sdr is None:
            return ()
        try:
            return tuple(float(gain) for gain in self._sdr.valid_gains_db)
        except Exception:  # noqa: BLE001 - not every tuner chip reports its gain table
            return ()

    # -- Driver hooks -------------------------------------------------------------------------

    def _do_open(self) -> None:
        rtlsdr = _import_rtlsdr()
        try:
            if self._serial is not None:
                self._sdr = rtlsdr.RtlSdr(serial_number=self._serial)
            else:
                self._sdr = rtlsdr.RtlSdr(device_index=self._index)
        except Exception as error:
            raise _translate_open_failure(error, self._index, self._serial) from error

        if self._frequency_correction_ppm:
            try:
                self._sdr.freq_correction = self._frequency_correction_ppm
            except Exception as error:
                self._release()
                raise TuningFailedError(
                    f"could not set a frequency correction of "
                    f"{self._frequency_correction_ppm} ppm: {error}"
                ) from error

        if self._bias_tee:
            try:
                self._sdr.set_bias_tee(True)
            except Exception as error:
                self._release()
                raise DeviceNotFoundError(
                    f"this dongle does not support a bias tee: {error}"
                ) from error

    def _do_close(self) -> None:
        self._release()

    def _do_set_center_freq(self, freq_hz: float) -> None:
        try:
            self._sdr.center_freq = freq_hz
        except Exception as error:
            raise TuningFailedError(
                f"could not tune to {freq_hz / 1e6:.3f} MHz: {error}"
            ) from error

    def _do_set_sample_rate(self, rate_hz: float) -> None:
        try:
            self._sdr.sample_rate = rate_hz
        except Exception as error:
            raise UnsupportedSampleRateError(
                f"the dongle refused {rate_hz / 1e6:g} MS/s: {error}"
            ) from error

    def _do_set_gain(self, gain: Gain) -> None:
        try:
            if gain == "auto":
                self._sdr.gain = "auto"
                return
            self._sdr.gain = self._nearest_supported_gain(gain)
        except UnsupportedGainError:
            raise
        except Exception as error:
            raise UnsupportedGainError(f"the dongle refused a gain of {gain}: {error}") from error

    def _do_read_samples(self, count: int) -> IqSamples:
        try:
            raw = self._sdr.read_samples(count)
        except Exception as error:
            raise DeviceNotFoundError(
                f"reading {count} samples failed, which usually means the dongle was "
                f"unplugged: {error}"
            ) from error
        samples: IqSamples = np.asarray(raw, dtype=np.complex64)
        if samples.size != count:
            raise DeviceNotFoundError(
                f"asked the dongle for {count} samples and got {samples.size}"
            )
        return samples

    # -- Helpers ------------------------------------------------------------------------------

    def _nearest_supported_gain(self, gain_db: float) -> float:
        """The closest gain step the tuner offers.

        The tuner has a fixed set of steps rather than a continuous range, so a requested gain
        is snapped to the nearest one. A request far outside the range is an error rather than
        a silent clamp, because quietly applying 49 dB when 80 was asked for would make a weak
        signal look like a hardware fault.
        """
        available = self.available_gains_db
        if not available:
            return gain_db
        low, high = min(available), max(available)
        if not low - 1.0 <= gain_db <= high + 1.0:
            steps = ", ".join(f"{value:g}" for value in available)
            raise UnsupportedGainError(
                f"gain must be between {low:g} and {high:g} dB, or 'auto'; got {gain_db:g}. "
                f"This tuner offers: {steps}"
            )
        return min(available, key=lambda value: abs(value - gain_db))

    def _release(self) -> None:
        """Close the underlying handle, ignoring a failure on the way out."""
        if self._sdr is None:
            return
        try:
            self._sdr.close()
        except Exception:  # noqa: BLE001 - nothing useful to do if releasing fails
            pass
        finally:
            self._sdr = None

    def __repr__(self) -> str:
        target = f"serial={self._serial!r}" if self._serial else f"index={self._index}"
        return f"{type(self).__name__}({target})"


def _translate_open_failure(
    error: Exception, index: int, serial: str | None
) -> DeviceNotFoundError | DeviceBusyError:
    """Turn a librtlsdr open failure into the matching OpenWave error.

    ``librtlsdr`` reports "no such device" and "device already claimed" through the same
    exception types, so the text is all there is to go on. Guessing wrong only changes the
    message, not the behaviour.
    """
    target = f"serial {serial}" if serial else f"index {index}"
    text = str(error).lower()
    if "busy" in text or "claimed" in text or "in use" in text or "resource" in text:
        return DeviceBusyError(
            f"the RTL-SDR dongle at {target} is already in use by another program: {error}"
        )
    return DeviceNotFoundError(
        f"no RTL-SDR dongle at {target}: {error}. Check it is plugged in, and on Linux that "
        "the udev rules are installed so it can be opened without root."
    )
