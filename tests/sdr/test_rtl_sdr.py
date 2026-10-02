"""Tests for the RTL-SDR driver, against a stand-in for ``pyrtlsdr``.

**What these tests can and cannot prove.** They check the driver's own logic: that it sets the
attributes it means to, snaps a gain to a step the tuner offers, turns a librtlsdr failure into
the right OpenWave error, and converts samples to the expected dtype. They prove nothing about
real hardware, because the stand-in below behaves the way the documentation says a dongle
behaves -- which is exactly the assumption that needs checking on a physical device.

So a green run here does not clear the ``hardware validation`` issue. It only means the driver
is wrong for interesting reasons rather than obvious ones.
"""

from __future__ import annotations

import re
import sys
from types import ModuleType
from typing import Any, ClassVar

import numpy as np
import pytest

from openwave.sdr.errors import (
    DeviceBusyError,
    DeviceNotFoundError,
    DriverUnavailableError,
    TuningFailedError,
    UnsupportedGainError,
    UnsupportedSampleRateError,
)
from openwave.sdr.rtl_sdr import (
    RTL_SDR_SAMPLE_RATES,
    RtlSdrDevice,
    list_rtlsdr_devices,
    rtlsdr_available,
)

#: Gain steps an R820T reports, in dB. Taken from the librtlsdr tuner tables.
R820T_GAINS = (
    0.0,
    0.9,
    1.4,
    2.7,
    3.7,
    7.7,
    8.7,
    12.5,
    14.4,
    15.7,
    16.6,
    19.7,
    20.7,
    22.9,
    25.4,
    28.0,
    29.7,
    32.8,
    33.8,
    36.4,
    37.2,
    38.6,
    40.2,
    42.1,
    43.4,
    43.9,
    44.5,
    48.0,
    49.6,
)


class FakeRtlSdr:
    """Stands in for ``rtlsdr.RtlSdr``, behaving as its documentation describes."""

    opened: ClassVar[list[FakeRtlSdr]] = []
    serials: ClassVar[tuple[str, ...]] = ("00000001", "00000013")
    fail_on_open: ClassVar[Exception | None] = None
    fail_on_enumerate: ClassVar[Exception | None] = None
    short_read_by: ClassVar[int] = 0

    def __init__(self, device_index: int = 0, serial_number: str | None = None) -> None:
        if type(self).fail_on_open is not None:
            raise type(self).fail_on_open
        self.device_index = device_index
        self.serial_number = serial_number
        self.center_freq = 0.0
        self.sample_rate = 0.0
        self.gain: Any = "auto"
        self.freq_correction = 0
        self.bias_tee: bool | None = None
        self.closed = False
        self.valid_gains_db = list(R820T_GAINS)
        type(self).opened.append(self)

    @staticmethod
    def get_device_serial_addresses() -> tuple[str, ...]:
        if FakeRtlSdr.fail_on_enumerate is not None:
            raise FakeRtlSdr.fail_on_enumerate
        return FakeRtlSdr.serials

    def set_bias_tee(self, enabled: bool) -> None:
        self.bias_tee = enabled

    def read_samples(self, count: int) -> np.ndarray:
        # pyrtlsdr hands back complex128 normalised to roughly the unit circle.
        size = count - type(self).short_read_by
        return np.full(size, 0.5 + 0.5j, dtype=np.complex128)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_rtlsdr(monkeypatch: pytest.MonkeyPatch) -> type[FakeRtlSdr]:
    """Install the stand-in as the ``rtlsdr`` module for the duration of a test."""
    FakeRtlSdr.opened = []
    FakeRtlSdr.serials = ("00000001", "00000013")
    FakeRtlSdr.fail_on_open = None
    FakeRtlSdr.fail_on_enumerate = None
    FakeRtlSdr.short_read_by = 0

    module = ModuleType("rtlsdr")
    module.RtlSdr = FakeRtlSdr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rtlsdr", module)
    return FakeRtlSdr


def opened_device(**kwargs: Any) -> tuple[RtlSdrDevice, FakeRtlSdr]:
    """An open driver and the stand-in it is talking to."""
    device = RtlSdrDevice(**kwargs)
    device.open()
    return device, FakeRtlSdr.opened[-1]


class TestWithoutTheDependency:
    def test_availability_is_reported_rather_than_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "rtlsdr", None)
        assert not rtlsdr_available()

    def test_enumeration_finds_nothing_instead_of_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Device discovery runs whether or not the extra is installed, so a missing dependency
        # has to look like "no dongles" and not like a crash.
        monkeypatch.setitem(sys.modules, "rtlsdr", None)
        assert list_rtlsdr_devices() == []

    def test_opening_explains_how_to_install_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "rtlsdr", None)
        with pytest.raises(DriverUnavailableError, match=re.escape('"openwave[rtlsdr]"')):
            RtlSdrDevice().open()


class TestEnumeration:
    def test_it_reports_one_entry_per_attached_dongle(self) -> None:
        found = list_rtlsdr_devices()
        assert [device.index for device in found] == [0, 1]
        assert [device.serial for device in found] == ["00000001", "00000013"]
        assert {device.driver for device in found} == {"rtlsdr"}

    def test_a_driver_that_cannot_enumerate_reports_nothing(self) -> None:
        # librtlsdr signals "no dongle" through several different exception types, and none of
        # them should stop a device listing that also covers other drivers.
        FakeRtlSdr.fail_on_enumerate = OSError("usb_init failed")
        assert list_rtlsdr_devices() == []

    def test_no_dongles_is_an_empty_list_not_an_error(self) -> None:
        FakeRtlSdr.serials = ()
        assert list_rtlsdr_devices() == []


class TestOpening:
    def test_by_index(self) -> None:
        _, fake = opened_device(index=1)
        assert fake.device_index == 1
        assert fake.serial_number is None

    def test_by_serial(self) -> None:
        # The reliable way to pick a dongle: index order is not stable across reboots.
        _, fake = opened_device(serial="00000013")
        assert fake.serial_number == "00000013"

    def test_a_negative_index_is_refused_before_touching_anything(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            RtlSdrDevice(index=-1)

    def test_an_absent_dongle_mentions_the_udev_rules(self) -> None:
        # On Linux this is the single most common cause, and the error should say so rather
        # than leaving somebody to discover it.
        FakeRtlSdr.fail_on_open = OSError("No such device")
        with pytest.raises(DeviceNotFoundError, match="udev"):
            RtlSdrDevice().open()

    @pytest.mark.parametrize(
        "message",
        ["Device or resource busy", "device already claimed", "dongle is in use"],
    )
    def test_a_claimed_dongle_is_distinguished_from_a_missing_one(self, message: str) -> None:
        # librtlsdr reports both through the same exception type, so the text is all there is
        # to go on, and telling them apart is what makes the message useful.
        FakeRtlSdr.fail_on_open = OSError(message)
        with pytest.raises(DeviceBusyError, match="already in use"):
            RtlSdrDevice().open()

    def test_closing_releases_the_handle(self) -> None:
        device, fake = opened_device()
        device.close()
        assert fake.closed

    def test_a_handle_that_fails_to_close_does_not_propagate(self) -> None:
        # Nothing useful can be done about it, and raising here would mask whatever error sent
        # the caller into the context manager's exit in the first place.
        device, fake = opened_device()
        fake.close = lambda: (_ for _ in ()).throw(OSError("already gone"))  # type: ignore[method-assign]
        device.close()
        assert not device.is_open


class TestConfiguration:
    def test_tuning_sets_the_centre_frequency(self) -> None:
        device, fake = opened_device()
        device.set_center_freq(98_000_000.0)
        assert fake.center_freq == 98_000_000.0

    def test_a_frequency_the_tuner_rejects_is_reported_as_a_tuning_failure(self) -> None:
        device, fake = opened_device()
        type(fake).center_freq = property(  # type: ignore[assignment]
            lambda self: 0.0,
            lambda self, value: (_ for _ in ()).throw(OSError("PLL not locked")),
        )
        try:
            with pytest.raises(TuningFailedError, match=re.escape("could not tune to 98.000 MHz")):
                device.set_center_freq(98_000_000.0)
        finally:
            del type(fake).center_freq

    def test_setting_a_supported_sample_rate(self) -> None:
        device, fake = opened_device()
        device.set_sample_rate(2_400_000.0)
        assert fake.sample_rate == 2_400_000.0

    def test_an_unsupported_sample_rate_never_reaches_the_hardware(self) -> None:
        device, fake = opened_device()
        with pytest.raises(UnsupportedSampleRateError, match="does not support"):
            device.set_sample_rate(5_000_000.0)
        assert fake.sample_rate == 0.0

    def test_the_documented_rates_include_the_usual_one(self) -> None:
        assert 2_400_000.0 in RTL_SDR_SAMPLE_RATES

    def test_a_frequency_correction_is_applied_on_open(self) -> None:
        # An uncalibrated crystal is tens of ppm out, which at 100 MHz is enough to put a
        # station in the wrong 100 kHz channel.
        _, fake = opened_device(frequency_correction_ppm=42)
        assert fake.freq_correction == 42

    def test_the_bias_tee_stays_off_unless_asked_for(self) -> None:
        _, fake = opened_device()
        assert fake.bias_tee is None

    def test_the_bias_tee_can_be_switched_on(self) -> None:
        _, fake = opened_device(bias_tee=True)
        assert fake.bias_tee is True


class TestGain:
    def test_auto_is_passed_straight_through(self) -> None:
        device, fake = opened_device()
        device.set_gain("auto")
        assert fake.gain == "auto"

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [(0.0, 0.0), (20.0, 19.7), (28.3, 28.0), (49.6, 49.6), (45.0, 44.5)],
    )
    def test_a_gain_is_snapped_to_a_step_the_tuner_offers(
        self, requested: float, expected: float
    ) -> None:
        # The tuner has a fixed table of steps rather than a continuous range.
        device, fake = opened_device()
        device.set_gain(requested)
        assert fake.gain == expected

    def test_the_driver_reports_the_requested_gain_not_the_snapped_one(self) -> None:
        device, _ = opened_device()
        device.set_gain(20.0)
        assert device.gain == 20.0

    def test_a_gain_far_outside_the_range_is_an_error_not_a_silent_clamp(self) -> None:
        # Quietly applying 49.6 dB when 80 was asked for would make a weak signal look like a
        # hardware fault instead of a mistaken request.
        device, _ = opened_device()
        with pytest.raises(UnsupportedGainError, match=re.escape("must be between 0 and 49.6 dB")):
            device.set_gain(80.0)

    def test_the_error_lists_the_steps_on_offer(self) -> None:
        device, _ = opened_device()
        with pytest.raises(UnsupportedGainError, match="This tuner offers"):
            device.set_gain(80.0)

    def test_the_gain_table_is_exposed_once_open(self) -> None:
        device, _ = opened_device()
        assert device.available_gains_db == R820T_GAINS

    def test_the_gain_table_is_empty_before_opening(self) -> None:
        assert RtlSdrDevice().available_gains_db == ()

    def test_a_tuner_with_no_gain_table_accepts_what_it_is_given(self) -> None:
        # Not every tuner chip reports its steps. Refusing every gain in that case would make
        # the dongle unusable, so the request is passed through unchanged.
        device, fake = opened_device()
        del fake.valid_gains_db
        device.set_gain(33.0)
        assert fake.gain == 33.0


class TestReadingSamples:
    def test_samples_come_back_as_complex64(self) -> None:
        # pyrtlsdr produces complex128. Keeping that would double the memory a scan needs for
        # no extra precision, since the samples started as 8-bit integers.
        device, _ = opened_device()
        device.set_sample_rate(2_400_000.0)
        device.set_center_freq(98_000_000.0)
        samples = device.read_samples(1024)
        assert samples.dtype == np.complex64
        assert samples.shape == (1024,)

    def test_a_short_read_is_caught_rather_than_passed_on(self) -> None:
        # A silently short read would show up downstream as a mysteriously shifted spectrum.
        device, _ = opened_device()
        device.set_sample_rate(2_400_000.0)
        FakeRtlSdr.short_read_by = 24
        with pytest.raises(DeviceNotFoundError, match="got 1000"):
            device.read_samples(1024)

    def test_a_read_that_fails_blames_the_usual_cause(self) -> None:
        device, fake = opened_device()
        device.set_sample_rate(2_400_000.0)
        fake.read_samples = lambda count: (_ for _ in ()).throw(OSError("libusb error -5"))  # type: ignore[method-assign]
        with pytest.raises(DeviceNotFoundError, match="unplugged"):
            device.read_samples(1024)


class TestIdentity:
    def test_it_identifies_itself_as_an_rtl_sdr(self) -> None:
        info = RtlSdrDevice(index=1, serial="00000013").info
        assert info.driver == "rtlsdr"
        assert info.index == 1
        assert info.serial == "00000013"

    def test_the_repr_says_how_the_dongle_was_selected(self) -> None:
        assert "serial='00000013'" in repr(RtlSdrDevice(serial="00000013"))
        assert "index=2" in repr(RtlSdrDevice(index=2))

    def test_the_tuning_range_covers_the_fm_band(self) -> None:
        tuning = RtlSdrDevice().tuning_range
        assert tuning.contains(87.5e6)
        assert tuning.contains(108e6)
