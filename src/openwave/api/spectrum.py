"""Streaming a live spectrum.

A spectrum display wants a new frame twenty times a second. Sending each one as JSON would mean
a thousand numbers formatted as decimal text per frame -- around 10 kB where 1 kB will do, and a
parse on every frame in the browser. So frames go out as binary.

**Why eight bits per bin is enough.** A display is a few hundred pixels tall and a colour ramp
has nowhere near 256 distinguishable steps, so a byte per bin quantised over a stated decibel
range loses nothing a viewer could see. It makes a 1024-bin frame exactly 1024 bytes of payload,
which at twenty frames a second is 20 kB/s -- small enough to leave running.

The frame carries its own scale, so a client needs no prior agreement about what the bytes mean
and an old client cannot misread a new frame as a loud signal.
"""

from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from openwave.core.signal_detector import power_spectrum
from openwave.core.units import SILENCE_DBFS
from openwave.sdr.device import SdrDevice

#: Marks a spectrum frame, so a client reading the wrong socket finds out at once.
FRAME_MAGIC: Final = b"OWSP"

#: Frame format version. Incremented when the layout changes.
FRAME_VERSION: Final = 1

#: Layout of a frame's header, as a struct format.
#:
#: Written as one format string rather than assembled in pieces, so its size and the offsets
#: within it cannot drift apart -- which they did when the timestamp was packed separately and
#: the stated header size still said 32.
#:
#: =======  ======  ==================================================================
#: Offset   Type    Field
#: =======  ======  ==================================================================
#: 0        4s      magic, ``OWSP``
#: 4        B       format version
#: 5        B       flags, reserved
#: 6        H       number of bins
#: 8        d       centre frequency in hertz
#: 16       d       span in hertz
#: 24       f       reference level in dBFS, which the highest byte value means
#: 28       f       decibels the byte range covers, below the reference
#: 32       Q       timestamp in milliseconds since the epoch
#: 40       ...     one byte per bin, lowest frequency first
#: =======  ======  ==================================================================
FRAME_HEADER_FORMAT: Final = "<4sBBHddffQ"

#: Size of the header before the bins, in bytes. Derived, not asserted.
FRAME_HEADER_SIZE: Final = struct.calcsize(FRAME_HEADER_FORMAT)

#: Bins in a frame by default.
#:
#: Wider than most displays, so a client can average down to its own width rather than being
#: given fewer points than it can draw.
DEFAULT_BINS: Final = 1024

#: Frames per second by default.
DEFAULT_FRAME_RATE: Final = 20.0

#: Decibel range a frame's bytes cover, below its reference level.
#:
#: 90 dB is more than any consumer receiver's usable range, so nothing interesting is clipped
#: at either end, and each byte step is about a third of a decibel.
DEFAULT_RANGE_DB: Final = 90.0

#: Samples read per frame.
#:
#: Enough for a Welch average over several blocks, so the noise floor does not shimmer between
#: frames, and few enough that a frame is ready twenty times a second.
DEFAULT_SAMPLES_PER_FRAME: Final = 1 << 16


@dataclass(frozen=True, slots=True)
class SpectrumFrame:
    """One decoded spectrum frame.

    Attributes:
        center_freq_hz: Middle of the span.
        span_hz: Width covered by the bins.
        reference_dbfs: Level the highest byte value means.
        range_db: Decibels the byte range covers, below the reference.
        timestamp_ms: When the frame was made, in milliseconds since the epoch.
        magnitudes: One byte per bin, lowest frequency first.
    """

    center_freq_hz: float
    span_hz: float
    reference_dbfs: float
    range_db: float
    timestamp_ms: int
    magnitudes: npt.NDArray[np.uint8]

    @property
    def bins(self) -> int:
        """How many bins the frame holds."""
        return int(self.magnitudes.size)

    @property
    def start_hz(self) -> float:
        """Frequency of the first bin."""
        return self.center_freq_hz - self.span_hz / 2.0

    def levels_dbfs(self) -> npt.NDArray[np.float64]:
        """The bins back in decibels, which is what a test or a marker needs."""
        scale = self.range_db / 255.0
        levels: npt.NDArray[np.float64] = (
            self.reference_dbfs - (255 - self.magnitudes.astype(np.float64)) * scale
        )
        return levels

    def frequencies_hz(self) -> npt.NDArray[np.float64]:
        """The centre frequency of each bin."""
        if self.bins < 2:
            return np.array([self.center_freq_hz], dtype=np.float64)
        step = self.span_hz / self.bins
        return self.start_hz + step * (np.arange(self.bins, dtype=np.float64) + 0.5)


def encode_frame(
    levels_dbfs: npt.NDArray[np.float64],
    *,
    center_freq_hz: float,
    span_hz: float,
    reference_dbfs: float | None = None,
    range_db: float = DEFAULT_RANGE_DB,
    timestamp_ms: int | None = None,
) -> bytes:
    """Pack a spectrum into one binary frame.

    The reference level defaults to the loudest bin, so a display is always using its full
    colour range whatever the gain happens to be. Passing one fixes the scale instead, which is
    what a client wants when it is comparing frames over time.
    """
    if levels_dbfs.size == 0:
        raise ValueError("a frame needs at least one bin")
    if levels_dbfs.size > 0xFFFF:
        raise ValueError(f"a frame holds at most {0xFFFF} bins, got {levels_dbfs.size}")
    if range_db <= 0:
        raise ValueError(f"range must be positive, got {range_db}")

    reference = float(np.max(levels_dbfs)) if reference_dbfs is None else reference_dbfs
    scale = 255.0 / range_db
    offsets = np.clip((levels_dbfs - (reference - range_db)) * scale, 0.0, 255.0)
    magnitudes = offsets.astype(np.uint8)

    header = struct.pack(
        FRAME_HEADER_FORMAT,
        FRAME_MAGIC,
        FRAME_VERSION,
        0,  # flags, reserved
        levels_dbfs.size,
        center_freq_hz,
        span_hz,
        reference,
        range_db,
        timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
    )
    return header + magnitudes.tobytes()


def decode_frame(data: bytes) -> SpectrumFrame:
    """Unpack a binary frame.

    Provided so that a test, or a Python client, reads frames through the same definition the
    server writes them with -- rather than a second copy of the layout that can drift.

    Raises:
        ValueError: if the data is not a frame, or is a version this code does not know.
    """
    if len(data) < FRAME_HEADER_SIZE:
        raise ValueError(f"a frame is at least {FRAME_HEADER_SIZE} bytes, got {len(data)}")

    (
        magic,
        version,
        _flags,
        bins,
        center,
        span,
        reference,
        range_db,
        timestamp_ms,
    ) = struct.unpack_from(FRAME_HEADER_FORMAT, data, 0)
    if magic != FRAME_MAGIC:
        raise ValueError(f"not a spectrum frame: it starts with {magic!r}")
    if version != FRAME_VERSION:
        raise ValueError(
            f"spectrum frame version {version} is not version {FRAME_VERSION}, which is what "
            "this build understands"
        )

    expected = FRAME_HEADER_SIZE + bins
    if len(data) != expected:
        raise ValueError(
            f"frame claims {bins} bins, so should be {expected} bytes, got {len(data)}"
        )

    return SpectrumFrame(
        center_freq_hz=center,
        span_hz=span,
        reference_dbfs=reference,
        range_db=range_db,
        timestamp_ms=timestamp_ms,
        magnitudes=np.frombuffer(data, dtype=np.uint8, offset=FRAME_HEADER_SIZE).copy(),
    )


class SpectrumStreamer:
    """Keeps a receiver tuned and produces spectrum frames.

    Example::

        streamer = SpectrumStreamer(device_factory=lambda: open_device("mock"))
        for frame in streamer.frames(98_000_000, sample_rate_hz=2_400_000):
            ...

    **One viewer at a time, and no sharing with a scan.** A receiver can only be tuned to one
    place, so a real dongle already busy with a scan will refuse to open and the error says so.
    The simulator allows it, which is why the demonstration mode can show both at once.

    Args:
        device_factory: How to build a receiver.
        bins: Bins per frame.
        frame_rate: Frames per second to aim for.
        samples_per_frame: Samples read per frame.
    """

    def __init__(
        self,
        *,
        device_factory: Callable[[], SdrDevice],
        bins: int = DEFAULT_BINS,
        frame_rate: float = DEFAULT_FRAME_RATE,
        samples_per_frame: int = DEFAULT_SAMPLES_PER_FRAME,
    ) -> None:
        if bins < 2:
            raise ValueError(f"a frame needs at least two bins, got {bins}")
        if frame_rate <= 0:
            raise ValueError(f"frame rate must be positive, got {frame_rate}")
        if samples_per_frame < bins:
            raise ValueError(
                f"samples_per_frame ({samples_per_frame}) must be at least the bin count "
                f"({bins}), or there is nothing to average"
            )

        self._device_factory = device_factory
        self._bins = bins
        self._frame_rate = frame_rate
        self._samples_per_frame = samples_per_frame
        self._lock = threading.Lock()
        self._viewers = 0

    @property
    def viewers(self) -> int:
        """How many clients are watching."""
        with self._lock:
            return self._viewers

    def frames(
        self,
        center_freq_hz: float,
        *,
        sample_rate_hz: float = 2_400_000.0,
        reference_dbfs: float | None = None,
        max_frames: int | None = None,
    ) -> Iterator[bytes]:
        """Yield spectrum frames until the caller stops asking.

        Args:
            center_freq_hz: Where to tune.
            sample_rate_hz: Sample rate, which also sets the span.
            reference_dbfs: Fix the decibel scale rather than following the loudest bin. A
                waterfall wants this, because a scale that moves makes an unchanging signal
                appear to change colour.
            max_frames: Stop after this many, which is what makes the stream testable.

        Raises:
            DeviceError: if the receiver cannot be opened or tuned.
        """
        interval_s = 1.0 / self._frame_rate
        with self._lock:
            self._viewers += 1
        try:
            device = self._device_factory()
            with device:
                device.set_sample_rate(sample_rate_hz)
                device.set_center_freq(center_freq_hz)

                produced = 0
                while max_frames is None or produced < max_frames:
                    started = time.monotonic()
                    samples = device.read_samples(self._samples_per_frame)
                    levels = self._levels(
                        samples,
                        sample_rate_hz=device.sample_rate_hz,
                        center_freq_hz=device.center_freq_hz,
                    )
                    yield encode_frame(
                        levels,
                        center_freq_hz=device.center_freq_hz,
                        span_hz=device.sample_rate_hz,
                        reference_dbfs=reference_dbfs,
                    )
                    produced += 1

                    # Pace the output rather than producing as fast as the receiver allows:
                    # a display cannot use more than its refresh rate, and the spare time is
                    # better left to whatever else is running.
                    remaining = interval_s - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
        finally:
            with self._lock:
                self._viewers = max(0, self._viewers - 1)

    def _levels(
        self,
        samples: npt.NDArray[np.complex64],
        *,
        sample_rate_hz: float,
        center_freq_hz: float,
    ) -> npt.NDArray[np.float64]:
        """Compute a spectrum and average it down to the frame's bin count.

        Averaging rather than taking every nth bin: a decimated spectrum misses narrow signals
        entirely, which on a display looks like a station flickering in and out.
        """
        spectrum = power_spectrum(
            samples,
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=center_freq_hz,
            fft_size=min(4096, samples.size),
        )
        psd = spectrum.psd
        if psd.size >= self._bins:
            usable = (psd.size // self._bins) * self._bins
            grouped = psd[:usable].reshape(self._bins, -1).mean(axis=1)
        else:
            grouped = np.interp(
                np.linspace(0.0, psd.size - 1, self._bins),
                np.arange(psd.size),
                psd,
            )

        with np.errstate(divide="ignore"):
            levels = 10.0 * np.log10(grouped * sample_rate_hz)
        return np.nan_to_num(levels, neginf=SILENCE_DBFS, posinf=0.0)
