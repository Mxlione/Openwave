"""Typed device errors.

The interface raises specific exceptions rather than a generic failure, because the user
interface has to say something useful. "Scan failed" is not an acceptable message when the real
cause is that the dongle is unplugged.
"""

from __future__ import annotations


class DeviceError(Exception):
    """Base class for every receiver problem."""


class DeviceNotFoundError(DeviceError):
    """No receiver matched the requested driver, index or serial."""


class DeviceBusyError(DeviceError):
    """The receiver exists but is already claimed by another process."""


class DeviceNotOpenError(DeviceError):
    """An operation needing an open device was attempted before ``open()``."""


class DriverUnavailableError(DeviceError):
    """The driver's Python dependency is not installed.

    Raised instead of ``ImportError`` so callers can handle a missing optional extra the same way
    they handle missing hardware.
    """


class TuningFailedError(DeviceError):
    """The receiver refused or could not reach the requested frequency."""


class UnsupportedSampleRateError(DeviceError):
    """The requested sample rate is outside what the receiver supports."""


class UnsupportedGainError(DeviceError):
    """The requested gain is outside what the receiver supports."""


class NoLockError(DeviceError):
    """A demodulating tuner could not lock onto a signal at the requested channel."""


class SampleOverflowError(DeviceError):
    """Samples were dropped because they were not read fast enough."""


class CaptureError(DeviceError):
    """A recorded IQ capture is missing, malformed or inconsistent.

    A subclass of :class:`DeviceError` because a capture stands in for a receiver: code that
    handles a broken device should handle a broken recording the same way.
    """
