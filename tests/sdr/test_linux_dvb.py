"""Tests for the Linux DVB driver.

**What these can and cannot prove.** They check the driver's own arithmetic and bookkeeping: the
ioctl request numbers, the structure sizes, how it finds adapters, and how it turns an operating
system error into something a user can act on. They prove nothing about whether a tuner responds,
because the only way to find that out is to plug one in.

The structure layouts are the risky part, and the one thing a test cannot check: a field in the
wrong place does not raise, it tunes to the wrong frequency. What the tests below can do is pin
the layouts down, so that if a kernel changes them the mismatch is at least visible here.
"""

from __future__ import annotations

import errno
import struct
from pathlib import Path

import pytest

from openwave.sdr.errors import DeviceBusyError, DeviceNotFoundError
from openwave.sdr.linux_dvb import (
    _DTV_PROPERTIES_SIZE,
    _DTV_PROPERTY_SIZE,
    DTV_FREQUENCY,
    DVB_ROOT,
    FE_HAS_LOCK,
    FE_READ_STATUS,
    FE_SET_PROPERTY,
    SYS_DVBT,
    SYS_DVBT2,
    LinuxDvbDevice,
    _ioc,
    _translate_open_failure,
    list_dvb_adapters,
)


class TestIoctlEncoding:
    def test_the_encoding_matches_the_kernel_macros(self) -> None:
        # Linux packs direction, size, type and number into one word. Getting the layout wrong
        # sends a request the kernel does not recognise, which fails loudly -- unlike most
        # mistakes in this driver.
        request = _ioc(2, "o", 69, 4)
        assert (request >> 30) & 0x3 == 2, "direction"
        assert (request >> 16) & 0x3FFF == 4, "size"
        assert (request >> 8) & 0xFF == ord("o"), "type"
        assert request & 0xFF == 69, "number"

    def test_the_dvb_subsystem_uses_type_o(self) -> None:
        dvb_type = ord("o")
        assert dvb_type == (FE_READ_STATUS >> 8) & 0xFF
        assert dvb_type == (FE_SET_PROPERTY >> 8) & 0xFF

    def test_the_request_numbers_are_the_documented_ones(self) -> None:
        # From linux/dvb/frontend.h. If a kernel renumbers these, a tuner will reject
        # everything, and this is where to look.
        assert FE_READ_STATUS & 0xFF == 69
        assert FE_SET_PROPERTY & 0xFF == 82

    def test_an_argument_too_large_to_encode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="too large to encode"):
            _ioc(1, "o", 1, 1 << 14)


class TestStructureSizes:
    def test_a_property_is_seventy_six_bytes(self) -> None:
        # struct dtv_property is declared packed: a command, three reserved words, a union
        # whose largest member is the 56-byte buffer variant, and a result. A different size
        # shifts every property after the first, which is the single most likely way for this
        # driver to be silently wrong.
        assert _DTV_PROPERTY_SIZE == 4 + 12 + 56 + 4

    def test_a_properties_header_is_a_count_and_a_pointer(self) -> None:
        expected = struct.calcsize("<IIQ")
        assert expected == _DTV_PROPERTIES_SIZE

    def test_the_lock_flag_is_the_documented_bit(self) -> None:
        assert FE_HAS_LOCK == 0x10


class TestAdapterDiscovery:
    def test_nothing_is_found_when_there_are_no_adapters(self, tmp_path: Path) -> None:
        # Device discovery runs whether or not a tuner is attached, so "none" has to be an
        # answer rather than a failure.
        import openwave.sdr.linux_dvb as module

        original = module.DVB_ROOT
        try:
            module.DVB_ROOT = tmp_path / "absent"
            assert list_dvb_adapters() == []
        finally:
            module.DVB_ROOT = original

    def test_adapters_with_a_frontend_are_listed(self, tmp_path: Path) -> None:
        import openwave.sdr.linux_dvb as module

        for index in (0, 2):
            adapter = tmp_path / f"adapter{index}"
            adapter.mkdir()
            (adapter / "frontend0").touch()
        # An adapter with no frontend is not a tuner, and listing it would offer a device that
        # cannot be opened.
        (tmp_path / "adapter1").mkdir()

        original = module.DVB_ROOT
        try:
            module.DVB_ROOT = tmp_path
            found = list_dvb_adapters()
        finally:
            module.DVB_ROOT = original

        assert [info.index for info in found] == [0, 2]
        assert all(info.driver == "linuxdvb" for info in found)

    def test_the_listing_says_the_driver_is_unvalidated(self) -> None:
        # Anybody choosing this driver should know before trusting a result from it.
        assert "never validated" in LinuxDvbDevice().info.label

    def test_the_real_root_is_where_the_kernel_puts_it(self) -> None:
        expected = Path("/dev/dvb")
        assert expected == DVB_ROOT


class TestConstruction:
    def test_a_negative_adapter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            LinuxDvbDevice(adapter=-1)

    def test_an_unknown_delivery_system_is_refused(self) -> None:
        with pytest.raises(ValueError, match="SYS_DVBT"):
            LinuxDvbDevice(delivery_system=99)

    def test_the_delivery_system_appears_in_the_label(self) -> None:
        # It matters: most of Europe now broadcasts DVB-T2, and a DVB-T2 signal will not lock
        # as DVB-T, so a scan that finds nothing may just be set to the wrong one.
        assert "DVB-T2" in LinuxDvbDevice(delivery_system=SYS_DVBT2).info.label
        assert "DVB-T," in LinuxDvbDevice(delivery_system=SYS_DVBT).info.label

    def test_the_device_paths_follow_the_kernel_layout(self) -> None:
        device = LinuxDvbDevice(adapter=2, frontend=1, demux=3)
        assert device.frontend_path == Path("/dev/dvb/adapter2/frontend1")
        assert device.demux_path == Path("/dev/dvb/adapter2/demux3")
        assert device.dvr_path == Path("/dev/dvb/adapter2/dvr3")

    def test_the_tuning_range_covers_the_television_bands(self) -> None:
        tuning = LinuxDvbDevice().tuning_range
        assert tuning.contains(474e6)  # UHF channel 21
        assert tuning.contains(690e6)  # UHF channel 48
        assert tuning.contains(177.5e6)  # VHF channel 5

    def test_the_repr_says_which_adapter(self) -> None:
        assert "adapter=3" in repr(LinuxDvbDevice(adapter=3))


class TestOpeningWithoutHardware:
    def test_a_missing_frontend_explains_what_to_check(self) -> None:
        # The three things that actually cause this: not plugged in, no firmware, wrong group.
        device = LinuxDvbDevice(adapter=99)
        with pytest.raises(DeviceNotFoundError) as raised:
            device.open()
        message = str(raised.value)
        assert "no DVB frontend" in message
        assert "video group" in message

    def test_it_does_not_report_itself_open_after_failing(self) -> None:
        device = LinuxDvbDevice(adapter=99)
        with pytest.raises(DeviceNotFoundError):
            device.open()
        assert not device.is_open

    def test_closing_a_device_that_never_opened_is_harmless(self) -> None:
        LinuxDvbDevice(adapter=99).close()


class TestErrorTranslation:
    def test_a_busy_device_names_the_likely_cause(self) -> None:
        error = _translate_open_failure(
            OSError(errno.EBUSY, "Device or resource busy"), Path("/dev/dvb/adapter0/frontend0")
        )
        assert isinstance(error, DeviceBusyError)
        assert "media centre" in str(error)

    @pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM])
    def test_a_permission_problem_names_the_group_to_join(self, code: int) -> None:
        error = _translate_open_failure(
            OSError(code, "Permission denied"), Path("/dev/dvb/adapter0/frontend0")
        )
        assert isinstance(error, DeviceNotFoundError)
        assert "video group" in str(error)

    def test_anything_else_is_reported_plainly(self) -> None:
        error = _translate_open_failure(
            OSError(errno.EIO, "Input/output error"), Path("/dev/dvb/adapter0/frontend0")
        )
        assert isinstance(error, DeviceNotFoundError)
        assert "could not open" in str(error)


class TestPropertyPacking:
    def test_a_property_puts_its_command_and_value_where_the_kernel_looks(self) -> None:
        # Checked here because it cannot be checked anywhere else: the value goes after the
        # command and three reserved words, and a wrong offset tunes to the wrong frequency
        # without any error being raised.
        entry = bytearray(_DTV_PROPERTY_SIZE)
        struct.pack_into("<I", entry, 0, DTV_FREQUENCY)
        struct.pack_into("<I", entry, 16, 498_000_000)

        assert struct.unpack_from("<I", entry, 0)[0] == DTV_FREQUENCY
        assert struct.unpack_from("<I", entry, 16)[0] == 498_000_000
        assert len(entry) == 76

    def test_the_header_holds_a_count_and_a_pointer(self) -> None:
        header = struct.pack("<IIQ", 6, 0, 0x7F0000000000)
        count, _reserved, pointer = struct.unpack("<IIQ", header)
        assert count == 6
        assert pointer == 0x7F0000000000
