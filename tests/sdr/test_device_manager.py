"""Tests for finding and opening receivers.

The specification string comes from a command line or an API request, so it is untrusted input
and every malformed form has to produce a message that says what to write instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from openwave.sdr.device_manager import (
    DRIVERS,
    describe_device,
    driver,
    driver_names,
    format_device_list,
    list_devices,
    open_device,
    open_dvb_device,
    parse_spec,
)
from openwave.sdr.errors import CaptureError, DeviceNotFoundError
from openwave.sdr.iq_file import IqFileSdrDevice, IqMetadata, write_capture
from openwave.sdr.mock import MockDvbDevice, MockSdrDevice


class TestParseSpec:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("mock", ("mock", None)),
            ("rtlsdr", ("rtlsdr", None)),
            ("rtlsdr:1", ("rtlsdr", "1")),
            ("rtlsdr:00000013", ("rtlsdr", "00000013")),
            ("file:/tmp/band.cu8", ("file", "/tmp/band.cu8")),
            ("  mock  ", ("mock", None)),
        ],
    )
    def test_it_splits_on_the_first_colon(
        self, spec: str, expected: tuple[str, str | None]
    ) -> None:
        assert parse_spec(spec) == expected

    def test_a_windows_path_keeps_its_drive_letter(self) -> None:
        # Splitting on every colon would turn C:/captures/band.cu8 into just "C".
        assert parse_spec("file:C:/captures/band.cu8") == ("file", "C:/captures/band.cu8")

    @pytest.mark.parametrize("spec", ["", "   "])
    def test_an_empty_specification_lists_the_options(self, spec: str) -> None:
        with pytest.raises(DeviceNotFoundError, match="expected one of"):
            parse_spec(spec)

    def test_a_trailing_colon_says_what_to_write_instead(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="names nothing after it"):
            parse_spec("rtlsdr:")


class TestDriverRegistry:
    def test_every_driver_has_a_distinct_name(self) -> None:
        names = driver_names()
        assert len(set(names)) == len(names)

    def test_the_simulator_and_the_file_reader_need_no_hardware(self) -> None:
        # The property that lets the whole test suite run in CI.
        for name in ("mock", "file"):
            assert not driver(name).needs_hardware

    def test_the_rtl_sdr_driver_is_marked_as_needing_hardware(self) -> None:
        assert driver("rtlsdr").needs_hardware

    def test_an_unknown_driver_lists_the_known_ones(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="available drivers are"):
            driver("hackrf")

    def test_every_driver_describes_itself(self) -> None:
        for entry in DRIVERS:
            assert entry.description.strip()


class TestOpenDevice:
    def test_the_simulator_is_the_default(self) -> None:
        assert isinstance(open_device(), MockSdrDevice)

    def test_a_device_comes_back_closed(self) -> None:
        # So the caller decides when to claim the hardware, and a context manager can own it.
        assert not open_device("mock").is_open

    def test_the_simulator_can_be_opened_by_index(self) -> None:
        assert open_device("mock:3").info.index == 3

    def test_an_unknown_driver_is_refused(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="no driver called 'hackrf'"):
            open_device("hackrf")

    def test_the_file_driver_needs_a_path(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="needs a path"):
            open_device("file")

    def test_a_missing_capture_is_reported_as_such(self) -> None:
        with pytest.raises(CaptureError):
            open_device("file:/nonexistent/band.cu8")

    def test_a_capture_opens_through_the_same_entry_point_as_hardware(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        samples = np.zeros(64, dtype=np.complex64)
        write_capture(
            path,
            samples,
            metadata=IqMetadata(sample_rate_hz=2.4e6, center_freq_hz=98e6),
        )
        device = open_device(f"file:{path}")
        assert isinstance(device, IqFileSdrDevice)
        assert device.metadata.center_freq_hz == 98e6


class TestListDevices:
    def test_the_simulator_is_always_listed(self) -> None:
        drivers = {device.driver for device in list_devices()}
        assert "mock" in drivers

    def test_captures_are_not_discoverable(self) -> None:
        # There is no way to guess which file somebody wants, so the file driver enumerates
        # nothing rather than scanning the disk.
        assert "file" not in {device.driver for device in list_devices()}

    def test_hardware_can_be_left_out(self) -> None:
        listed = list_devices(include_hardware=False)
        assert "rtlsdr" not in {device.driver for device in listed}
        assert "mock" in {device.driver for device in listed}


class TestDescribeDevice:
    def test_it_describes_a_driver_without_opening_anything(self) -> None:
        assert "Simulated" in describe_device("mock")

    def test_it_says_the_rtl_sdr_driver_is_unvalidated(self) -> None:
        # Anybody listing devices should see this before trusting a result from one.
        assert "never validated" in describe_device("rtlsdr")

    def test_it_summarises_a_capture(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(
            path,
            np.zeros(64, dtype=np.complex64),
            metadata=IqMetadata(sample_rate_hz=2.4e6, center_freq_hz=98e6),
        )
        assert "98.000 MHz" in describe_device(f"file:{path}")

    def test_an_unreadable_capture_still_produces_a_description(self, tmp_path: Path) -> None:
        # Describing a device must never be the thing that raises: it is used in error paths.
        description = describe_device(f"file:{tmp_path / 'absent.cf32'}")
        assert "unreadable" in description

    def test_an_unknown_driver_is_still_refused(self) -> None:
        with pytest.raises(DeviceNotFoundError):
            describe_device("hackrf")


class TestOpenDvbDevice:
    def test_the_simulator_is_the_default(self) -> None:
        assert isinstance(open_dvb_device(), MockDvbDevice)

    def test_it_can_be_opened_by_index(self) -> None:
        assert open_dvb_device("mockdvb:2").info.index == 2

    def test_a_real_tuner_says_when_it_will_arrive(self) -> None:
        with pytest.raises(DeviceNotFoundError, match=re.escape("v0.4")):
            open_dvb_device("linuxdvb")


class TestFormatDeviceList:
    def test_it_renders_one_device_per_line(self) -> None:
        text = format_device_list(list_devices(include_hardware=False))
        assert text.startswith("mock:0 ")

    def test_an_empty_listing_says_so_rather_than_printing_nothing(self) -> None:
        assert format_device_list([]) == "no receivers found"
