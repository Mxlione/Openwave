"""DVB-T support through the Linux DVB API.

.. warning::

   **This driver has never run on physical hardware.** The maintainer owns no DVB-T tuner. It is
   written from the Linux DVB version 5 API headers and is type-checked, but no transport stream
   has ever come out of it.

   It is also the riskiest code in OpenWave, and for a specific reason: it talks to the kernel
   through ioctls, which means laying out C structures byte by byte from Python. A field in the
   wrong place does not raise an error -- it tunes to the wrong frequency, or returns a
   signal-to-noise ratio that is actually a pointer. The structure layouts below are the first
   thing to check against your kernel's headers.

   Specifically worth verifying, in ``/usr/include/linux/dvb/frontend.h`` and ``dmx.h``:

   * Is ``struct dtv_property`` still 76 bytes on your platform? It is declared packed, with a
     union whose largest member is the 56-byte buffer variant. A different size shifts every
     property after the first.
   * Does ``FE_SET_PROPERTY`` have the request number 82, and ``FE_READ_STATUS`` 69?
   * Does your tuner need ``SYS_DVBT`` or ``SYS_DVBT2``? A DVB-T2 broadcast will not lock with
     the delivery system set to DVB-T.
   * Does reading the demux device give the whole transport stream, or only the PIDs a filter
     selected? This driver asks for the whole stream with a filter on PID 0x2000, which is the
     convention, and a tuner that does not honour it returns nothing.

   If you own a tuner, running this and reporting what happened -- especially a wrong frequency
   or nonsense statistics -- is the most useful contribution available. See the issues labelled
   ``hardware validation`` and the **Hardware report** issue template.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import os
import struct
import time
from pathlib import Path
from typing import Final

from openwave.sdr.device import TS_PACKET_SIZE, DeviceInfo, DvbDevice, SignalQuality, TuningRange
from openwave.sdr.errors import (
    DeviceBusyError,
    DeviceNotFoundError,
    NoLockError,
    TuningFailedError,
)

#: Where the kernel exposes DVB adapters.
DVB_ROOT: Final = Path("/dev/dvb")

#: Tuning range of a typical DVB-T frontend, in Hz. VHF band III and the UHF bands.
LINUX_DVB_TUNING_RANGE: Final = TuningRange(min_hz=42e6, max_hz=870e6)

# -- ioctl request numbers -----------------------------------------------------------------------
#
# Linux encodes an ioctl request as direction, size, type and number. The DVB subsystem uses
# type 'o'. These are built the same way the kernel's _IOR and _IOW macros do, so that the
# arithmetic is visible rather than hard-coded as magic constants.

_IOC_NONE: Final = 0
_IOC_WRITE: Final = 1
_IOC_READ: Final = 2
_IOC_NRBITS: Final = 8
_IOC_TYPEBITS: Final = 8
_IOC_SIZEBITS: Final = 14


def _ioc(direction: int, type_code: str, number: int, size: int) -> int:
    """Build an ioctl request number, as the kernel's ``_IOC`` macro does."""
    if size >= (1 << _IOC_SIZEBITS):
        raise ValueError(f"ioctl argument of {size} bytes is too large to encode")
    return (
        (direction << (_IOC_NRBITS + _IOC_TYPEBITS + _IOC_SIZEBITS))
        | (ord(type_code) << _IOC_NRBITS)
        | number
        | (size << (_IOC_NRBITS + _IOC_TYPEBITS))
    )


#: Size of ``struct dtv_properties``: a count and a pointer, on a 64-bit platform.
_DTV_PROPERTIES_SIZE: Final = 16

#: Size of ``struct dtv_property``. Declared packed: a command, three reserved words, a 56-byte
#: union and a result. Getting this wrong shifts every property after the first.
_DTV_PROPERTY_SIZE: Final = 76

FE_READ_STATUS: Final = _ioc(_IOC_READ, "o", 69, ctypes.sizeof(ctypes.c_uint))
FE_SET_PROPERTY: Final = _ioc(_IOC_WRITE, "o", 82, _DTV_PROPERTIES_SIZE)
FE_GET_PROPERTY: Final = _ioc(_IOC_READ, "o", 83, _DTV_PROPERTIES_SIZE)

#: ``DMX_SET_PES_FILTER``, which tells the demux what to put on the output device.
DMX_SET_PES_FILTER: Final = _ioc(_IOC_WRITE, "o", 44, 14)

#: ``DMX_SET_BUFFER_SIZE``, which takes its argument directly rather than by pointer.
DMX_SET_BUFFER_SIZE: Final = _ioc(_IOC_NONE, "o", 45, 0)

# -- DTV property commands -----------------------------------------------------------------------

DTV_TUNE: Final = 1
DTV_CLEAR: Final = 2
DTV_FREQUENCY: Final = 3
DTV_BANDWIDTH_HZ: Final = 5
DTV_INVERSION: Final = 6
DTV_DELIVERY_SYSTEM: Final = 17
DTV_STAT_SIGNAL_STRENGTH: Final = 63
DTV_STAT_CNR: Final = 65

#: Delivery system numbers. A DVB-T2 broadcast will not lock as DVB-T.
SYS_DVBT: Final = 3
SYS_DVBT2: Final = 16

#: ``INVERSION_AUTO``, letting the frontend work out the spectral inversion itself.
INVERSION_AUTO: Final = 2

# -- Frontend status flags -----------------------------------------------------------------------

FE_HAS_SIGNAL: Final = 0x01
FE_HAS_CARRIER: Final = 0x02
FE_HAS_VITERBI: Final = 0x04
FE_HAS_SYNC: Final = 0x08
FE_HAS_LOCK: Final = 0x10

#: Scale values a statistic can report, from ``enum fecap_scale_params``.
FE_SCALE_NOT_AVAILABLE: Final = 0
FE_SCALE_DECIBEL: Final = 1
FE_SCALE_RELATIVE: Final = 2
FE_SCALE_COUNTER: Final = 3

#: Demux output destination: the ``dvr`` device.
DMX_OUT_TS_TAP: Final = 2

#: Demux input: the tuner's front end.
DMX_IN_FRONTEND: Final = 0

#: PID that asks the demux for the entire transport stream rather than selected streams.
DMX_PID_FULL_TS: Final = 0x2000

#: ``DMX_IMMEDIATE_START``, so the filter begins without a separate start call.
DMX_IMMEDIATE_START: Final = 4

#: Buffer the demux keeps, in bytes. A megabyte is a few tenths of a second of a multiplex.
DEFAULT_DEMUX_BUFFER_BYTES: Final = 1 << 20


def list_dvb_adapters() -> list[DeviceInfo]:
    """Enumerate the DVB adapters the kernel exposes.

    Returns an empty list when none are present, which is the normal answer during device
    discovery rather than an error.
    """
    if not DVB_ROOT.is_dir():
        return []

    adapters: list[DeviceInfo] = []
    for path in sorted(DVB_ROOT.glob("adapter*")):
        name = path.name.removeprefix("adapter")
        if not name.isdigit():
            continue
        if not (path / "frontend0").exists():
            continue
        adapters.append(
            DeviceInfo(
                driver="linuxdvb",
                index=int(name),
                label=f"DVB adapter {name} (never validated on hardware)",
                serial=None,
            )
        )
    return adapters


class LinuxDvbDevice(DvbDevice):
    """A DVB-T tuner through the Linux DVB API.

    .. warning::
       Never tested on hardware. See the module docstring, which lists what to check first.

    Example::

        with LinuxDvbDevice(adapter=0) as tuner:
            tuner.tune(498_000_000, 8e6)
            if tuner.wait_for_lock():
                packets = tuner.read_ts(188 * 1000)

    Args:
        adapter: Which adapter to use, matching ``/dev/dvb/adapterN``.
        frontend: Which frontend on that adapter.
        demux: Which demux on that adapter.
        delivery_system: ``SYS_DVBT`` or ``SYS_DVBT2``. A DVB-T2 broadcast will not lock as
            DVB-T, and most of Europe now transmits DVB-T2.
        buffer_bytes: How much the demux should buffer.
    """

    def __init__(
        self,
        adapter: int = 0,
        *,
        frontend: int = 0,
        demux: int = 0,
        delivery_system: int = SYS_DVBT,
        buffer_bytes: int = DEFAULT_DEMUX_BUFFER_BYTES,
    ) -> None:
        super().__init__()
        if adapter < 0:
            raise ValueError(f"adapter number must not be negative, got {adapter}")
        if delivery_system not in (SYS_DVBT, SYS_DVBT2):
            raise ValueError(
                f"delivery system must be SYS_DVBT ({SYS_DVBT}) or SYS_DVBT2 ({SYS_DVBT2}), "
                f"got {delivery_system}"
            )

        self._adapter = adapter
        self._frontend_index = frontend
        self._demux_index = demux
        self._delivery_system = delivery_system
        self._buffer_bytes = buffer_bytes

        self._frontend_fd: int | None = None
        self._demux_fd: int | None = None
        self._dvr_fd: int | None = None

    # -- Identity -----------------------------------------------------------------------------

    @property
    def info(self) -> DeviceInfo:
        system = "DVB-T2" if self._delivery_system == SYS_DVBT2 else "DVB-T"
        return DeviceInfo(
            driver="linuxdvb",
            index=self._adapter,
            label=f"DVB adapter {self._adapter} ({system}, never validated on hardware)",
            serial=None,
        )

    @property
    def tuning_range(self) -> TuningRange:
        return LINUX_DVB_TUNING_RANGE

    @property
    def frontend_path(self) -> Path:
        """Device node used for tuning."""
        return DVB_ROOT / f"adapter{self._adapter}" / f"frontend{self._frontend_index}"

    @property
    def demux_path(self) -> Path:
        """Device node used to say what the output should carry."""
        return DVB_ROOT / f"adapter{self._adapter}" / f"demux{self._demux_index}"

    @property
    def dvr_path(self) -> Path:
        """Device node the transport stream comes out of."""
        return DVB_ROOT / f"adapter{self._adapter}" / f"dvr{self._demux_index}"

    # -- Driver hooks -------------------------------------------------------------------------

    def _do_open(self) -> None:
        if not self.frontend_path.exists():
            raise DeviceNotFoundError(
                f"no DVB frontend at {self.frontend_path}. Check the tuner is plugged in, that "
                "its driver and firmware are loaded (dmesg usually says), and that you are in "
                "the video group so the device can be opened without root."
            )

        try:
            self._frontend_fd = os.open(self.frontend_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as error:
            raise _translate_open_failure(error, self.frontend_path) from error

        try:
            self._demux_fd = os.open(self.demux_path, os.O_RDWR)
            self._dvr_fd = os.open(self.dvr_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            self._release()
            raise _translate_open_failure(error, self.demux_path) from error

    def _do_close(self) -> None:
        self._release()

    def _do_tune(self, freq_hz: float, bandwidth_hz: float) -> None:
        if self._frontend_fd is None:
            raise DeviceNotFoundError("the frontend is not open")

        properties = [
            (DTV_CLEAR, 0),
            (DTV_DELIVERY_SYSTEM, self._delivery_system),
            (DTV_FREQUENCY, round(freq_hz)),
            (DTV_BANDWIDTH_HZ, round(bandwidth_hz)),
            (DTV_INVERSION, INVERSION_AUTO),
            (DTV_TUNE, 0),
        ]
        try:
            self._set_properties(properties)
        except OSError as error:
            raise TuningFailedError(
                f"the frontend refused {freq_hz / 1e6:.3f} MHz: {error}"
            ) from error

        self._start_full_ts_filter()

    def _do_wait_for_lock(self, timeout_s: float) -> bool:
        if self._frontend_fd is None:
            return False

        deadline = time.monotonic() + timeout_s
        while True:
            if self._read_status() & FE_HAS_LOCK:
                return True
            if time.monotonic() >= deadline:
                return False
            # A frontend takes a moment to sweep and acquire. Polling faster than this burns
            # processor time without finding out anything sooner.
            time.sleep(0.05)

    def _do_signal_quality(self) -> SignalQuality:
        if self._frontend_fd is None:
            return SignalQuality(locked=False)

        status = self._read_status()
        locked = bool(status & FE_HAS_LOCK)
        snr_db = self._read_statistic(DTV_STAT_CNR)
        strength = self._read_statistic(DTV_STAT_SIGNAL_STRENGTH)
        return SignalQuality(
            locked=locked,
            snr_db=snr_db,
            strength_dbm=strength,
            ber=None,
        )

    def _do_read_ts(self, size: int) -> bytes:
        if self._dvr_fd is None:
            raise NoLockError("the transport stream device is not open")

        collected = bytearray()
        deadline = time.monotonic() + 1.0
        while len(collected) < size and time.monotonic() < deadline:
            try:
                chunk = os.read(self._dvr_fd, size - len(collected))
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError as error:
                raise NoLockError(f"reading the transport stream failed: {error}") from error
            if not chunk:
                break
            collected.extend(chunk)

        # The interface promises whole packets, so a partial one at the end is held back rather
        # than handed on to a parser that would reject the whole block.
        usable = len(collected) - (len(collected) % TS_PACKET_SIZE)
        return bytes(collected[:usable])

    # -- ioctl plumbing -----------------------------------------------------------------------

    def _set_properties(self, properties: list[tuple[int, int]]) -> None:
        """Send a list of DTV properties to the frontend.

        Each property is a packed 76-byte structure, and they are passed as an array with a
        separate count-and-pointer structure. The buffer is kept alive in a local until the
        ioctl returns, because the kernel reads through the pointer.
        """
        if self._frontend_fd is None:
            raise DeviceNotFoundError("the frontend is not open")

        payload = bytearray()
        for command, value in properties:
            entry = bytearray(_DTV_PROPERTY_SIZE)
            struct.pack_into("<I", entry, 0, command)
            # The union starts after the command and three reserved words.
            struct.pack_into("<I", entry, 16, value & 0xFFFFFFFF)
            payload += entry

        buffer = ctypes.create_string_buffer(bytes(payload), len(payload))
        header = struct.pack("<IIQ", len(properties), 0, ctypes.addressof(buffer))
        fcntl.ioctl(self._frontend_fd, FE_SET_PROPERTY, header)

    def _read_status(self) -> int:
        """Read the frontend's status flags."""
        if self._frontend_fd is None:
            return 0
        try:
            raw = fcntl.ioctl(self._frontend_fd, FE_READ_STATUS, struct.pack("<I", 0))
        except OSError:
            return 0
        return int(struct.unpack("<I", raw)[0])

    def _read_statistic(self, command: int) -> float | None:
        """Read one signal statistic, in decibels where the frontend reports it that way.

        Returns ``None`` when the frontend declines the statistic, which the API lets it do for
        any of them -- so a tuner reporting no carrier-to-noise ratio is ordinary, not broken.
        """
        if self._frontend_fd is None:
            return None

        entry = bytearray(_DTV_PROPERTY_SIZE)
        struct.pack_into("<I", entry, 0, command)
        buffer = ctypes.create_string_buffer(bytes(entry), len(entry))
        header = struct.pack("<IIQ", 1, 0, ctypes.addressof(buffer))

        try:
            fcntl.ioctl(self._frontend_fd, FE_GET_PROPERTY, header)
        except OSError:
            return None

        # struct dtv_fe_stats: a length byte, then up to four { scale, value } pairs of nine
        # bytes each, packed. Only the first layer is read here.
        raw = bytes(buffer)
        length = raw[16]
        if length < 1:
            return None
        scale = raw[17]
        value = struct.unpack_from("<q", raw, 18)[0]
        if scale == FE_SCALE_DECIBEL:
            # Decibel statistics are reported in thousandths.
            return float(value) / 1000.0
        if scale == FE_SCALE_RELATIVE:
            # A relative figure is a fraction of full scale, not decibels, so it is not
            # returned as though it were.
            return None
        return None

    def _start_full_ts_filter(self) -> None:
        """Ask the demux to put the whole transport stream on the output device."""
        if self._demux_fd is None:
            return

        # Not fatal if the driver refuses: the default buffer is smaller but workable.
        with contextlib.suppress(OSError):
            fcntl.ioctl(self._demux_fd, DMX_SET_BUFFER_SIZE, self._buffer_bytes)

        # struct dmx_pes_filter_params: pid, input, output, pes_type, flags.
        params = struct.pack(
            "<HIIII",
            DMX_PID_FULL_TS,
            DMX_IN_FRONTEND,
            DMX_OUT_TS_TAP,
            16,  # DMX_PES_OTHER
            DMX_IMMEDIATE_START,
        )
        try:
            fcntl.ioctl(self._demux_fd, DMX_SET_PES_FILTER, params)
        except OSError as error:
            raise TuningFailedError(
                f"the demux refused a filter for the whole transport stream: {error}. "
                "Some drivers want individual PIDs instead."
            ) from error

    def _release(self) -> None:
        """Close every descriptor, ignoring failures on the way out."""
        for attribute in ("_dvr_fd", "_demux_fd", "_frontend_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is None:
                continue
            # Nothing useful to do if closing fails, and raising here would mask whatever
            # sent the caller into the context manager's exit in the first place.
            with contextlib.suppress(OSError):
                os.close(descriptor)
            setattr(self, attribute, None)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(adapter={self._adapter})"


def _translate_open_failure(error: OSError, path: Path) -> DeviceNotFoundError | DeviceBusyError:
    """Turn a failure to open a device node into the matching OpenWave error."""
    if error.errno == errno.EBUSY:
        return DeviceBusyError(
            f"{path} is already in use by another program: {error}. A recording or a media "
            "centre holding the tuner is the usual cause."
        )
    if error.errno in (errno.EACCES, errno.EPERM):
        return DeviceNotFoundError(
            f"not allowed to open {path}: {error}. On most distributions the device belongs to "
            "the video group, so adding your user to it and logging in again fixes this."
        )
    return DeviceNotFoundError(f"could not open {path}: {error}")
