"""WAV encoding.

Demodulated audio arrives as floating-point samples. Everything that plays it -- libVLC, a media
player, a browser -- wants a container with a header saying what the samples are. WAV is the
plainest one there is: a 44-byte header followed by the samples.

**Streaming a WAV of unknown length.** A WAV header states how many bytes of audio follow, which
is a problem for live radio: the length is not known, because the broadcast has not finished. The
convention, which every player follows, is to write the largest value the field can hold and let
the stream simply stop. :func:`wav_header` does that when no length is given.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import numpy.typing as npt

#: Size of a canonical WAV header, in bytes.
WAV_HEADER_SIZE: Final = 44

#: Written into the size fields when the length is not known in advance.
#:
#: The fields are unsigned 32-bit, so this is their maximum. A player reads it as "about four
#: gigabytes" and stops when the stream does, which is the behaviour wanted for live audio.
UNKNOWN_LENGTH: Final = 0xFFFFFFFF

#: WAV format tag for uncompressed integer PCM.
WAVE_FORMAT_PCM: Final = 1


@dataclass(frozen=True, slots=True)
class WavFormat:
    """What the samples in a WAV stream are."""

    sample_rate_hz: int
    channels: int = 2
    bits_per_sample: int = 16

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError(f"sample rate must be positive, got {self.sample_rate_hz}")
        if self.channels not in (1, 2):
            raise ValueError(f"channels must be 1 or 2, got {self.channels}")
        if self.bits_per_sample != 16:
            raise ValueError(
                f"only 16-bit samples are supported, got {self.bits_per_sample}. "
                "16 bits is more than FM broadcasting can carry: its signal-to-noise ratio "
                "tops out around 60 dB, and 16 bits allows about 96 dB."
            )

    @property
    def bytes_per_frame(self) -> int:
        """Bytes for one sample across all channels."""
        return self.channels * self.bits_per_sample // 8

    @property
    def byte_rate(self) -> int:
        """Bytes per second of audio."""
        return self.sample_rate_hz * self.bytes_per_frame


def wav_header(fmt: WavFormat, *, data_bytes: int | None = None) -> bytes:
    """Build a 44-byte WAV header.

    Args:
        fmt: What the samples are.
        data_bytes: How many bytes of audio follow, or ``None`` for a live stream of unknown
            length. See :data:`UNKNOWN_LENGTH`.
    """
    if data_bytes is not None and data_bytes < 0:
        raise ValueError(f"data length must not be negative, got {data_bytes}")

    data_size = UNKNOWN_LENGTH if data_bytes is None else data_bytes
    riff_size = UNKNOWN_LENGTH if data_bytes is None else data_bytes + WAV_HEADER_SIZE - 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,  # size of the format chunk that follows
        WAVE_FORMAT_PCM,
        fmt.channels,
        fmt.sample_rate_hz,
        fmt.byte_rate,
        fmt.bytes_per_frame,
        fmt.bits_per_sample,
        b"data",
        data_size,
    )
    if len(header) != WAV_HEADER_SIZE:
        raise AssertionError(f"header came out {len(header)} bytes, expected {WAV_HEADER_SIZE}")
    return header


def pcm_bytes(left: npt.NDArray[np.float32], right: npt.NDArray[np.float32] | None = None) -> bytes:
    """Convert floating-point audio to interleaved 16-bit PCM.

    Values outside -1.0 to 1.0 are clipped rather than wrapped. Clipping sounds like distortion;
    wrapping turns a loud passage into a burst of noise, because the loudest possible sample
    becomes the quietest.

    Args:
        left: Left channel, or the mono signal.
        right: Right channel. ``None`` produces a mono stream.
    """
    if right is not None and left.shape != right.shape:
        raise ValueError(f"channels have different lengths: {left.size} left, {right.size} right")

    if right is None:
        frames = np.asarray(left, dtype=np.float64)
    else:
        frames = np.empty(left.size * 2, dtype=np.float64)
        frames[0::2] = left
        frames[1::2] = right

    np.clip(frames, -1.0, 1.0, out=frames)
    # 32767, not 32768: scaling by 32768 would make a sample at exactly full scale overflow a
    # signed 16-bit integer and wrap to the most negative value.
    return (frames * 32767.0).astype("<i2").tobytes()


def wav_bytes(
    left: npt.NDArray[np.float32],
    right: npt.NDArray[np.float32] | None = None,
    *,
    sample_rate_hz: int,
) -> bytes:
    """A complete WAV file, header and audio, as bytes."""
    fmt = WavFormat(sample_rate_hz=sample_rate_hz, channels=1 if right is None else 2)
    audio = pcm_bytes(left, right)
    return wav_header(fmt, data_bytes=len(audio)) + audio


def write_wav(
    path: Path | str,
    left: npt.NDArray[np.float32],
    right: npt.NDArray[np.float32] | None = None,
    *,
    sample_rate_hz: int,
) -> Path:
    """Write audio to a WAV file. Returns the path written."""
    destination = Path(path)
    destination.write_bytes(wav_bytes(left, right, sample_rate_hz=sample_rate_hz))
    return destination
