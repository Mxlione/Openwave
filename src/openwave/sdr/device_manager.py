"""Finding and opening receivers.

One function opens anything OpenWave can read samples from::

    open_device("mock")                      # the simulator
    open_device("rtlsdr")                    # the first dongle attached
    open_device("rtlsdr:1")                  # the second one
    open_device("rtlsdr:00000013")           # a specific one, by serial number
    open_device("file:captures/fm.cu8")      # a recorded capture, replayed

The specification is a string so that it can come straight from a command line argument or an
API request without the caller having to know which drivers exist.

Drivers register themselves here, which is the whole point of the registry: adding support for a
new receiver means implementing :class:`~.device.SdrDevice` and adding one entry, with nothing
else in the codebase to change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from openwave.sdr.device import DeviceInfo, DvbDevice, SdrDevice
from openwave.sdr.errors import DeviceNotFoundError
from openwave.sdr.iq_file import IqFileSdrDevice, describe_capture
from openwave.sdr.mock import MockDvbDevice, MockSdrDevice
from openwave.sdr.rtl_sdr import RtlSdrDevice, list_rtlsdr_devices

#: Separator between a driver name and the device it selects, as in ``rtlsdr:1``.
SPEC_SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class DriverEntry:
    """How to enumerate and open one kind of receiver."""

    name: str
    description: str
    open: Callable[[str | None], SdrDevice]
    """Opens a device. The argument is whatever followed the colon, or ``None``."""

    enumerate: Callable[[], list[DeviceInfo]]
    """Lists attached devices. Returns an empty list when none are found."""

    needs_hardware: bool
    """Whether this driver talks to physical hardware, and so cannot be used in CI."""


def _open_mock(target: str | None) -> SdrDevice:
    """Open the simulator.

    It carries no transmissions by default: a band with nothing but noise in it. Construct
    :class:`~.mock.MockSdrDevice` directly to populate it, which is what tests do.
    """
    index = int(target) if target else 0
    return MockSdrDevice(index=index)


def _open_rtlsdr(target: str | None) -> SdrDevice:
    """Open an RTL-SDR dongle by index or by serial number.

    A target that is all digits and short is read as an index; anything else is a serial. The
    ambiguity is real -- some dongles have all-numeric serials -- so a serial that looks like an
    index can be forced by passing it with a leading zero, which no index has.
    """
    if target is None:
        return RtlSdrDevice()
    if target.isdigit() and not target.startswith("0"):
        return RtlSdrDevice(index=int(target))
    return RtlSdrDevice(serial=target)


def _open_file(target: str | None) -> SdrDevice:
    """Replay a recorded capture."""
    if not target:
        raise DeviceNotFoundError("the file driver needs a path, as in file:captures/fm-band.cu8")
    return IqFileSdrDevice(Path(target))


def _enumerate_mock() -> list[DeviceInfo]:
    """The simulator is always available, and there is exactly one of it."""
    return [MockSdrDevice().info]


def _enumerate_file() -> list[DeviceInfo]:
    """Captures are not discoverable: there is no way to know which file is wanted."""
    return []


#: The drivers OpenWave knows about, in the order a device listing shows them.
DRIVERS: tuple[DriverEntry, ...] = (
    DriverEntry(
        name="mock",
        description="Simulated receiver, synthesising whatever it is told is on the air",
        open=_open_mock,
        enumerate=_enumerate_mock,
        needs_hardware=False,
    ),
    DriverEntry(
        name="rtlsdr",
        description="RTL-SDR dongle (never validated on hardware)",
        open=_open_rtlsdr,
        enumerate=list_rtlsdr_devices,
        needs_hardware=True,
    ),
    DriverEntry(
        name="file",
        description="Recorded IQ capture, replayed through the receiver interface",
        open=_open_file,
        enumerate=_enumerate_file,
        needs_hardware=False,
    ),
)


def driver(name: str) -> DriverEntry:
    """Look up a driver by name.

    Raises:
        DeviceNotFoundError: if no driver goes by that name.
    """
    for entry in DRIVERS:
        if entry.name == name:
            return entry
    known = ", ".join(entry.name for entry in DRIVERS)
    raise DeviceNotFoundError(f"no driver called {name!r}; available drivers are {known}")


def driver_names() -> tuple[str, ...]:
    """The names of every registered driver."""
    return tuple(entry.name for entry in DRIVERS)


def parse_spec(spec: str) -> tuple[str, str | None]:
    """Split a device specification into a driver name and a target.

    >>> parse_spec("mock")
    ('mock', None)
    >>> parse_spec("rtlsdr:1")
    ('rtlsdr', '1')
    >>> parse_spec("file:/tmp/band.cu8")
    ('file', '/tmp/band.cu8')

    A Windows path keeps its drive letter, because only the first colon separates::

        >>> parse_spec("file:C:/captures/band.cu8")
        ('file', 'C:/captures/band.cu8')

    Raises:
        DeviceNotFoundError: if the specification is empty or names no driver.
    """
    text = spec.strip()
    if not text:
        known = ", ".join(entry.name for entry in DRIVERS)
        raise DeviceNotFoundError(f"no device given; expected one of {known}")

    name, separator, target = text.partition(SPEC_SEPARATOR)
    if not separator:
        return name, None
    if not target:
        raise DeviceNotFoundError(
            f"{spec!r} ends with a colon but names nothing after it; "
            f"write {name!r} on its own for the default device"
        )
    return name, target


def open_device(spec: str = "mock") -> SdrDevice:
    """Open the receiver named by ``spec``, without claiming it yet.

    The returned device is closed. Open it, or use it as a context manager, which is what
    guarantees it is released if a scan raises::

        with open_device("rtlsdr") as device:
            ...

    Args:
        spec: ``driver`` or ``driver:target``. See the module docstring for the forms.

    Raises:
        DeviceNotFoundError: if no such driver exists, or the device is not there.
        DeviceBusyError: if another program already holds it.
        DriverUnavailableError: if the driver's optional dependency is not installed.
        CaptureError: if a capture is missing or malformed.
    """
    name, target = parse_spec(spec)
    return driver(name).open(target)


def list_devices(*, include_hardware: bool = True) -> list[DeviceInfo]:
    """Every receiver that can currently be opened.

    Enumerating hardware touches the USB bus, which is slow and can fail, so a driver that finds
    nothing is simply skipped rather than reported as an error -- "no dongle attached" is the
    normal case, not a fault.

    Args:
        include_hardware: Set ``False`` to list only drivers that need no hardware, which is
            what CI wants.
    """
    found: list[DeviceInfo] = []
    for entry in DRIVERS:
        if entry.needs_hardware and not include_hardware:
            continue
        found.extend(entry.enumerate())
    return found


def describe_device(spec: str) -> str:
    """A one-line description of what ``spec`` refers to, without opening anything.

    Used by the command line to say what it is about to do, and in error messages.
    """
    name, target = parse_spec(spec)
    entry = driver(name)
    if name == "file" and target:
        try:
            return f"capture {target} ({describe_capture(target)})"
        except Exception as error:  # noqa: BLE001 - a description must never be the thing that fails
            return f"capture {target} (unreadable: {error})"
    if target:
        return f"{entry.description}, device {target}"
    return entry.description


def open_dvb_device(spec: str = "mockdvb") -> DvbDevice:
    """Open a demodulating DVB tuner.

    Only the simulator exists so far. The Linux DVB backend arrives with the DVB-T scan in v0.4
    (task 35 in TASKS.md), at which point this grows a registry like the one above.

    Raises:
        DeviceNotFoundError: if no such tuner driver exists.
    """
    name, target = parse_spec(spec)
    if name != "mockdvb":
        raise DeviceNotFoundError(
            f"no DVB tuner driver called {name!r}; only 'mockdvb' exists so far. "
            "Real DVB-T support arrives in v0.4."
        )
    return MockDvbDevice(index=int(target) if target else 0)


def format_device_list(devices: Iterable[DeviceInfo]) -> str:
    """Render a device listing as lines of text, for the command line."""
    lines = [str(device) for device in devices]
    return "\n".join(lines) if lines else "no receivers found"


__all__ = [
    "DRIVERS",
    "DriverEntry",
    "describe_device",
    "driver",
    "driver_names",
    "format_device_list",
    "list_devices",
    "open_device",
    "open_dvb_device",
    "parse_spec",
]
