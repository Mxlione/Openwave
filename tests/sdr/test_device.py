"""Tests for the receiver interfaces themselves.

The base classes own the state and the validation so that drivers do not each reimplement it.
These tests pin down that shared behaviour, using a deliberately minimal driver rather than the
simulator, so a failure points at the interface and not at the simulation.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwave.sdr.device import (
    DEFAULT_USABLE_FRACTION,
    TS_PACKET_SIZE,
    DeviceInfo,
    Gain,
    IqSamples,
    SdrDevice,
    SignalQuality,
    TuningRange,
)


class CountingDevice(SdrDevice):
    """The smallest possible driver: records what the base class asked it to do."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.returns: int | None = None
        """Sample count to return, overriding what was asked for. Used to test the check."""

    @property
    def info(self) -> DeviceInfo:
        return DeviceInfo(driver="counting", index=0, label="Counting device")

    @property
    def tuning_range(self) -> TuningRange:
        return TuningRange(min_hz=80e6, max_hz=120e6)

    @property
    def supported_sample_rates_hz(self) -> tuple[float, ...] | None:
        return None

    def _do_open(self) -> None:
        self.calls.append("open")

    def _do_close(self) -> None:
        self.calls.append("close")

    def _do_set_center_freq(self, freq_hz: float) -> None:
        self.calls.append(f"freq={freq_hz}")

    def _do_set_sample_rate(self, rate_hz: float) -> None:
        self.calls.append(f"rate={rate_hz}")

    def _do_set_gain(self, gain: Gain) -> None:
        self.calls.append(f"gain={gain}")

    def _do_read_samples(self, count: int) -> IqSamples:
        self.calls.append(f"read={count}")
        size = count if self.returns is None else self.returns
        return np.zeros(size, dtype=np.complex64)


class TestTuningRange:
    def test_it_knows_what_it_contains(self) -> None:
        tuning_range = TuningRange(min_hz=87.5e6, max_hz=108e6)
        assert tuning_range.contains(98e6)
        assert tuning_range.contains(87.5e6)
        assert tuning_range.contains(108e6)
        assert not tuning_range.contains(87.4e6)

    def test_a_single_frequency_is_a_valid_range(self) -> None:
        # This is what a replayed capture reports: one window of spectrum, nothing else.
        tuning_range = TuningRange(min_hz=98e6, max_hz=98e6)
        assert tuning_range.contains(98e6)
        assert not tuning_range.contains(98.1e6)

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="inverted"):
            TuningRange(min_hz=108e6, max_hz=87.5e6)

    @pytest.mark.parametrize("min_hz", [0.0, -1e6])
    def test_a_range_starting_at_or_below_zero_is_refused(self, min_hz: float) -> None:
        with pytest.raises(ValueError, match="above zero"):
            TuningRange(min_hz=min_hz, max_hz=108e6)


class TestDeviceInfo:
    def test_it_prints_as_a_selectable_specification(self) -> None:
        info = DeviceInfo(driver="rtlsdr", index=1, label="RTL-SDR dongle", serial="00000013")
        assert str(info) == "rtlsdr:1 RTL-SDR dongle [00000013]"

    def test_the_serial_is_left_out_when_there_is_none(self) -> None:
        assert str(DeviceInfo(driver="mock", index=0, label="Simulated")) == "mock:0 Simulated"


class TestSignalQuality:
    def test_statistics_a_frontend_cannot_measure_stay_unset(self) -> None:
        # The Linux DVB API lets a frontend decline any individual statistic, so None has to
        # mean "not measured" rather than zero, which would read as a terrible signal.
        quality = SignalQuality(locked=True)
        assert quality.snr_db is None
        assert quality.ber is None
        assert quality.strength_dbm is None


class TestTheBaseClassOwnsTheState:
    def test_the_driver_is_only_asked_to_open_once(self) -> None:
        device = CountingDevice()
        device.open()
        device.open()
        assert device.calls.count("open") == 1

    def test_the_driver_is_only_asked_to_close_once(self) -> None:
        device = CountingDevice()
        device.open()
        device.close()
        device.close()
        assert device.calls.count("close") == 1

    def test_a_driver_that_fails_while_closing_is_still_marked_closed(self) -> None:
        class FailsOnClose(CountingDevice):
            def _do_close(self) -> None:
                raise OSError("the USB handle had already gone")

        device = FailsOnClose()
        device.open()
        with pytest.raises(OSError, match="USB handle"):
            device.close()
        assert not device.is_open

    def test_validation_happens_before_the_driver_is_touched(self) -> None:
        # A driver should never see a frequency outside its own range: the base class rejects
        # it first, so every driver gets the same error message for free.
        device = CountingDevice()
        device.open()
        with pytest.raises(Exception, match="outside the tuning range"):
            device.set_center_freq(200e6)
        assert not any(call.startswith("freq=") for call in device.calls)

    def test_state_is_recorded_only_after_the_driver_accepts(self) -> None:
        class RefusesAboveNinetyNine(CountingDevice):
            def _do_set_center_freq(self, freq_hz: float) -> None:
                if freq_hz > 99e6:
                    raise OSError("the PLL would not lock")
                super()._do_set_center_freq(freq_hz)

        device = RefusesAboveNinetyNine()
        device.open()
        device.set_center_freq(98e6)
        with pytest.raises(OSError, match="PLL"):
            device.set_center_freq(100e6)
        # The reported frequency is still the one that actually took effect.
        assert device.center_freq_hz == 98e6

    def test_a_driver_returning_the_wrong_number_of_samples_is_caught(self) -> None:
        # Silently short reads are a classic SDR bug, and one that would show up downstream as
        # a mysteriously shifted spectrum rather than as an error.
        device = CountingDevice()
        device.open()
        device.set_sample_rate(2.4e6)
        device.returns = 100
        with pytest.raises(AssertionError, match="expected \\(256,\\)"):
            device.read_samples(256)

    def test_usable_bandwidth_follows_the_sample_rate(self) -> None:
        device = CountingDevice()
        device.open()
        device.set_sample_rate(2.4e6)
        assert device.usable_bandwidth_hz == pytest.approx(2.4e6 * DEFAULT_USABLE_FRACTION)

    def test_a_continuous_driver_accepts_any_positive_rate(self) -> None:
        device = CountingDevice()
        device.open()
        device.set_sample_rate(1_234_567.0)
        assert device.sample_rate_hz == 1_234_567.0


def test_the_transport_stream_packet_size_is_the_standard_one() -> None:
    # Fixed by ISO/IEC 13818-1. Every TS read is a multiple of it, so it is worth asserting
    # rather than trusting that nobody will ever "tidy" it to 184 or 204.
    assert TS_PACKET_SIZE == 188
