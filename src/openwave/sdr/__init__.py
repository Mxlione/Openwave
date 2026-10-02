"""Receiver access. The only layer that knows what a piece of hardware is.

Everything outside this package talks to :class:`SdrDevice` and :class:`DvbDevice`, and never to
a vendor library or a device node. That rule is what lets the rest of OpenWave be tested without
hardware, and what lets a new receiver be added without touching anything else.

Start with :func:`open_device`::

    from openwave.sdr import open_device

    with open_device("mock") as device:
        device.set_sample_rate(2_400_000)
        device.set_center_freq(98_000_000)
        samples = device.read_samples(1 << 18)
"""

from openwave.sdr.device import (
    DEFAULT_USABLE_FRACTION,
    TS_PACKET_SIZE,
    DeviceInfo,
    DvbDevice,
    Gain,
    IqSamples,
    SdrDevice,
    SignalQuality,
    TuningRange,
)
from openwave.sdr.device_manager import (
    DRIVERS,
    DriverEntry,
    describe_device,
    driver,
    driver_names,
    format_device_list,
    list_devices,
    open_device,
    open_dvb_device,
    parse_spec,
)
from openwave.sdr.errors import (
    CaptureError,
    DeviceBusyError,
    DeviceError,
    DeviceNotFoundError,
    DeviceNotOpenError,
    DriverUnavailableError,
    NoLockError,
    SampleOverflowError,
    TuningFailedError,
    UnsupportedGainError,
    UnsupportedSampleRateError,
)
from openwave.sdr.iq_file import (
    IqFileSdrDevice,
    IqFormat,
    IqMetadata,
    describe_capture,
    read_capture,
    read_metadata,
    write_capture,
    write_metadata,
)
from openwave.sdr.mock import MockDvbDevice, MockSdrDevice, SignalSource, SyntheticMux
from openwave.sdr.rtl_sdr import RtlSdrDevice, list_rtlsdr_devices, rtlsdr_available

__all__ = [
    "DEFAULT_USABLE_FRACTION",
    "DRIVERS",
    "TS_PACKET_SIZE",
    "CaptureError",
    "DeviceBusyError",
    "DeviceError",
    "DeviceInfo",
    "DeviceNotFoundError",
    "DeviceNotOpenError",
    "DriverEntry",
    "DriverUnavailableError",
    "DvbDevice",
    "Gain",
    "IqFileSdrDevice",
    "IqFormat",
    "IqMetadata",
    "IqSamples",
    "MockDvbDevice",
    "MockSdrDevice",
    "NoLockError",
    "RtlSdrDevice",
    "SampleOverflowError",
    "SdrDevice",
    "SignalQuality",
    "SignalSource",
    "SyntheticMux",
    "TuningFailedError",
    "TuningRange",
    "UnsupportedGainError",
    "UnsupportedSampleRateError",
    "describe_capture",
    "describe_device",
    "driver",
    "driver_names",
    "format_device_list",
    "list_devices",
    "list_rtlsdr_devices",
    "open_device",
    "open_dvb_device",
    "parse_spec",
    "read_capture",
    "read_metadata",
    "rtlsdr_available",
    "write_capture",
    "write_metadata",
]
