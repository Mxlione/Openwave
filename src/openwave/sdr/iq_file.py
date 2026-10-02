"""Reading and writing recorded IQ captures.

A capture is a few seconds of raw samples from a real receiver. It matters more to this project
than it would to most: with no hardware available to the maintainer, a capture contributed by
somebody who does own a dongle is the only way a detector can be tested against a real band,
with real multipath, real interference and a real noise floor.

:class:`IqFileSdrDevice` replays a capture through the ordinary :class:`~.device.SdrDevice`
interface, so the scanner cannot tell a recording from hardware.

**Supported formats.** All three store interleaved I and Q, and differ only in the sample type:

===========  =====================  ============================================================
Extension    Sample type            Where it comes from
===========  =====================  ============================================================
``.cf32``    float32, -1.0 to 1.0   GNU Radio, and the format OpenWave writes by default
``.cu8``     uint8, offset by 127.5 ``rtl_sdr``, so the most common format for shared captures
``.cs16``    int16                  SoapySDR and various SDR applications
===========  =====================  ============================================================

**Metadata.** Raw samples alone are useless -- a file of numbers does not say what frequency it
was recorded at, and a capture without that cannot be interpreted. OpenWave writes a JSON
sidecar next to the data, named by appending ``.json``, and refuses to replay a capture that has
none. The SigMF standard covers the same ground in more depth and would be a good thing to
support as well.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import numpy as np

from openwave.sdr.device import DeviceInfo, Gain, IqSamples, SdrDevice, TuningRange
from openwave.sdr.errors import CaptureError

#: Suffix appended to a capture's filename to find its metadata sidecar.
METADATA_SUFFIX: Final = ".json"

#: How far a replaying device may be retuned from the recorded frequency, in Hz.
#:
#: Zero: a capture holds one window of spectrum and nothing outside it, so quietly accepting a
#: different frequency would hand back samples of the wrong part of the band.
REPLAY_TUNING_TOLERANCE_HZ: Final = 0.0


class IqFormat(StrEnum):
    """Sample format of a capture file."""

    CF32 = "cf32"
    """Interleaved 32-bit floats, nominally between -1.0 and 1.0."""

    CU8 = "cu8"
    """Interleaved unsigned bytes centred on 127.5, as written by ``rtl_sdr``."""

    CS16 = "cs16"
    """Interleaved signed 16-bit integers."""

    @property
    def numpy_dtype(self) -> np.dtype[Any]:
        """The dtype one interleaved component is stored as."""
        dtypes: dict[IqFormat, np.dtype[Any]] = {
            IqFormat.CF32: np.dtype(np.float32),
            IqFormat.CU8: np.dtype(np.uint8),
            IqFormat.CS16: np.dtype(np.int16),
        }
        return dtypes[self]

    @property
    def bytes_per_sample(self) -> int:
        """Bytes per complex sample, counting both components."""
        return 2 * self.numpy_dtype.itemsize

    @property
    def offset(self) -> float:
        """Value that represents zero."""
        return 127.5 if self is IqFormat.CU8 else 0.0

    @property
    def scale(self) -> float:
        """Value that represents full scale, after the offset is removed."""
        return {IqFormat.CF32: 1.0, IqFormat.CU8: 127.5, IqFormat.CS16: 32768.0}[self]

    @property
    def extension(self) -> str:
        """Conventional file extension, including the dot."""
        return f".{self.value}"

    @classmethod
    def from_path(cls, path: Path | str) -> IqFormat:
        """Infer the format from a filename's extension.

        Raises:
            CaptureError: if the extension is not one of the supported formats.
        """
        suffix = Path(path).suffix.lower().lstrip(".")
        try:
            return cls(suffix)
        except ValueError:
            supported = ", ".join(fmt.extension for fmt in cls)
            raise CaptureError(
                f"cannot tell the sample format of {path} from its extension; "
                f"supported extensions are {supported}, or pass the format explicitly"
            ) from None


@dataclass(frozen=True, slots=True)
class IqMetadata:
    """What a capture needs to say about itself to be interpretable.

    The first two fields are required, because samples without a frequency and a sample rate
    cannot be placed anywhere in the spectrum. The rest is context that makes a capture useful
    to somebody who did not record it.
    """

    sample_rate_hz: float
    center_freq_hz: float
    format: IqFormat = IqFormat.CF32
    sample_count: int | None = None
    recorded_at: str | None = None
    """When it was recorded, as an ISO 8601 timestamp."""

    gain_db: float | None = None
    receiver: str | None = None
    """Hardware it came from, for example ``"RTL-SDR Blog V4"``."""

    antenna: str | None = None
    location: str | None = None
    """Roughly where it was recorded. Keep it coarse: a city is useful, an address is not."""

    notes: str | None = None
    """Anything else, especially what is known to be on the air in this capture."""

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise CaptureError(f"sample rate must be positive, got {self.sample_rate_hz}")
        if self.center_freq_hz <= 0:
            raise CaptureError(f"centre frequency must be positive, got {self.center_freq_hz}")
        if self.sample_count is not None and self.sample_count < 0:
            raise CaptureError(f"sample count must not be negative, got {self.sample_count}")

    @property
    def duration_s(self) -> float | None:
        """How long the capture lasts, if its length is known."""
        if self.sample_count is None:
            return None
        return self.sample_count / self.sample_rate_hz

    def to_dict(self) -> dict[str, Any]:
        """As a JSON-serialisable dictionary, leaving out anything unset."""
        data = asdict(self)
        # str() rather than .value: IqFormat is a StrEnum, so this gives the same result while
        # also surviving metadata built with a plain string in place of the enum member.
        data["format"] = str(self.format)
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, data: object) -> IqMetadata:
        """Parse metadata that came from a file.

        Treats its input as untrusted, because a sidecar arrives from whoever recorded the
        capture. Everything is checked, and a clear error is raised rather than letting a bad
        value propagate into the signal processing as a nonsensical frequency.
        """
        if not isinstance(data, dict):
            raise CaptureError(f"metadata must be a JSON object, got {type(data).__name__}")

        known = set(IqMetadata.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise CaptureError(
                f"metadata has unrecognised keys: {', '.join(sorted(unknown))}; "
                f"recognised keys are {', '.join(sorted(known))}"
            )

        for required in ("sample_rate_hz", "center_freq_hz"):
            if required not in data:
                raise CaptureError(f"metadata is missing {required}")

        parsed: dict[str, Any] = {}
        for key, value in data.items():
            if key == "format":
                parsed[key] = _parse_format(value)
            elif key == "sample_count":
                parsed[key] = _parse_int(key, value)
            elif key in ("sample_rate_hz", "center_freq_hz", "gain_db"):
                parsed[key] = _parse_float(key, value)
            else:
                parsed[key] = _parse_str(key, value)
        return cls(**parsed)


def _parse_format(value: object) -> IqFormat:
    if not isinstance(value, str):
        raise CaptureError(f"metadata format must be a string, got {type(value).__name__}")
    try:
        return IqFormat(value.lower())
    except ValueError:
        supported = ", ".join(fmt.value for fmt in IqFormat)
        raise CaptureError(
            f"metadata names an unknown sample format {value!r}; supported formats are {supported}"
        ) from None


def _parse_float(key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CaptureError(f"metadata {key} must be a number, got {type(value).__name__}")
    if not np.isfinite(value):
        raise CaptureError(f"metadata {key} must be finite, got {value}")
    return float(value)


def _parse_int(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaptureError(f"metadata {key} must be an integer, got {type(value).__name__}")
    return value


def _parse_str(key: str, value: object) -> str:
    if not isinstance(value, str):
        raise CaptureError(f"metadata {key} must be a string, got {type(value).__name__}")
    return value


def metadata_path(path: Path | str) -> Path:
    """Where the metadata sidecar for ``path`` lives."""
    return Path(f"{path}{METADATA_SUFFIX}")


def write_metadata(path: Path | str, metadata: IqMetadata) -> Path:
    """Write ``metadata`` as the sidecar for the capture at ``path``. Returns the sidecar path."""
    sidecar = metadata_path(path)
    sidecar.write_text(json.dumps(metadata.to_dict(), indent=2) + "\n", encoding="utf-8")
    return sidecar


def read_metadata(path: Path | str) -> IqMetadata:
    """Read the metadata sidecar for the capture at ``path``.

    Raises:
        CaptureError: if the sidecar is missing, is not valid JSON, or holds bad values.
    """
    sidecar = metadata_path(path)
    if not sidecar.is_file():
        raise CaptureError(
            f"{path} has no metadata sidecar at {sidecar}; a capture without its sample rate "
            "and centre frequency cannot be interpreted"
        )
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CaptureError(f"{sidecar} is not valid JSON: {error}") from error
    return IqMetadata.from_dict(raw)


def write_capture(
    path: Path | str,
    samples: IqSamples,
    *,
    metadata: IqMetadata,
) -> Path:
    """Write ``samples`` to ``path`` with a metadata sidecar.

    The sample format comes from ``metadata.format``, and values outside full scale are clipped
    rather than wrapping around, which is what an ADC does.

    Returns:
        The path the samples were written to.

    Raises:
        CaptureError: if the metadata contradicts the samples or the filename.
    """
    path = Path(path)
    fmt = metadata.format
    if path.suffix and path.suffix.lower() != fmt.extension:
        raise CaptureError(
            f"{path.name} does not match the format in its metadata ({fmt.value}); "
            f"name it with {fmt.extension} or change the metadata"
        )
    if metadata.sample_count is not None and metadata.sample_count != len(samples):
        raise CaptureError(
            f"metadata says {metadata.sample_count} samples but {len(samples)} were given"
        )

    interleaved = np.empty(2 * len(samples), dtype=np.float64)
    interleaved[0::2] = samples.real
    interleaved[1::2] = samples.imag
    np.clip(interleaved, -1.0, 1.0, out=interleaved)
    interleaved = interleaved * fmt.scale + fmt.offset
    if fmt is not IqFormat.CF32:
        # Clip to what the integer type can actually hold. The asymmetry matters: full scale
        # scaled by 32768 is 32768, which an int16 cannot represent, so a sample at +1.0 would
        # wrap round to the most negative value and turn a loud signal into a loud glitch.
        np.round(interleaved, out=interleaved)
        limits = np.iinfo(fmt.numpy_dtype)
        np.clip(interleaved, limits.min, limits.max, out=interleaved)

    interleaved.astype(fmt.numpy_dtype).tofile(path)
    if metadata.sample_count is None:
        metadata = replace(metadata, sample_count=len(samples))
    write_metadata(path, metadata)
    return path


def read_capture(
    path: Path | str,
    *,
    count: int | None = None,
    start_index: int = 0,
    fmt: IqFormat | None = None,
) -> IqSamples:
    """Read complex samples from a capture.

    Args:
        path: The capture file.
        count: How many samples to read, or ``None`` for all of them from ``start_index``.
        start_index: Sample to start at, counted in complex samples rather than bytes.
        fmt: Sample format, inferred from the extension when not given.

    Raises:
        CaptureError: if the file is missing, truncated, or not a whole number of samples.
    """
    path = Path(path)
    if not path.is_file():
        raise CaptureError(f"no capture at {path}")
    if start_index < 0:
        raise CaptureError(f"start index must not be negative, got {start_index}")
    if count is not None and count <= 0:
        raise CaptureError(f"sample count must be positive, got {count}")

    fmt = fmt if fmt is not None else IqFormat.from_path(path)
    total_bytes = path.stat().st_size
    if total_bytes % fmt.bytes_per_sample != 0:
        raise CaptureError(
            f"{path} is {total_bytes} bytes, not a whole number of {fmt.value} samples "
            f"({fmt.bytes_per_sample} bytes each); it may be truncated or in another format"
        )
    available = total_bytes // fmt.bytes_per_sample
    if start_index > available:
        raise CaptureError(
            f"cannot start at sample {start_index}: {path} holds {available} samples"
        )
    wanted = available - start_index if count is None else count
    if start_index + wanted > available:
        raise CaptureError(
            f"asked for {wanted} samples from index {start_index}, but {path} holds "
            f"{available} samples"
        )

    raw = np.fromfile(
        path,
        dtype=fmt.numpy_dtype,
        count=2 * wanted,
        offset=start_index * fmt.bytes_per_sample,
    )
    scaled = (raw.astype(np.float32) - np.float32(fmt.offset)) / np.float32(fmt.scale)
    samples: IqSamples = (scaled[0::2] + 1j * scaled[1::2]).astype(np.complex64)
    return samples


class IqFileSdrDevice(SdrDevice):
    """Replays a recorded capture through the receiver interface.

    The scanner cannot tell this from hardware, which is the point: a capture contributed by
    somebody with a real dongle becomes a test case, and a bug seen on the air becomes
    reproducible.

    Because a capture holds one window of spectrum recorded at one rate, the frequency and the
    sample rate are fixed. Asking for anything else raises, rather than silently returning
    samples of a different part of the band.

    Example::

        with IqFileSdrDevice("fm-band-98MHz.cu8") as device:
            device.set_sample_rate(device.metadata.sample_rate_hz)
            device.set_center_freq(device.metadata.center_freq_hz)
            samples = device.read_samples(1 << 18)

    Args:
        path: The capture to replay.
        fmt: Sample format, inferred from the extension when not given.
        loop: Whether to start again from the beginning when the capture runs out. Looping keeps
            a long scan fed from a short recording; without it, running out raises.
        index: Device index to report.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        fmt: IqFormat | None = None,
        loop: bool = True,
        index: int = 0,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._format = fmt if fmt is not None else IqFormat.from_path(self._path)
        self._loop = loop
        self._index = index
        self._metadata = read_metadata(self._path)
        if self._metadata.format is not self._format:
            raise CaptureError(
                f"{self._path} is being read as {self._format.value} but its metadata says "
                f"{self._metadata.format.value}"
            )
        self._available = self._count_samples()
        self._position = 0

    # -- Identity and capabilities ------------------------------------------------------------

    @property
    def info(self) -> DeviceInfo:
        return DeviceInfo(
            driver="file",
            index=self._index,
            label=f"Capture {self._path.name} ({self._available} samples)",
            serial=None,
        )

    @property
    def tuning_range(self) -> TuningRange:
        """The single frequency this capture was recorded at."""
        recorded = self._metadata.center_freq_hz
        return TuningRange(
            min_hz=recorded - REPLAY_TUNING_TOLERANCE_HZ,
            max_hz=recorded + REPLAY_TUNING_TOLERANCE_HZ,
        )

    @property
    def supported_sample_rates_hz(self) -> tuple[float, ...]:
        """The single rate this capture was recorded at."""
        return (self._metadata.sample_rate_hz,)

    # -- Capture state ------------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """The capture being replayed."""
        return self._path

    @property
    def metadata(self) -> IqMetadata:
        """What the capture says about itself."""
        return self._metadata

    @property
    def sample_count(self) -> int:
        """How many complex samples the capture holds."""
        return self._available

    @property
    def position(self) -> int:
        """How far through the capture the next read will start."""
        return self._position

    def rewind(self) -> None:
        """Start again from the beginning of the capture."""
        self._position = 0

    # -- Driver hooks -------------------------------------------------------------------------

    def _do_open(self) -> None:
        if not self._path.is_file():
            raise CaptureError(f"no capture at {self._path}")
        self._position = 0

    def _do_close(self) -> None:
        pass

    def _do_set_center_freq(self, freq_hz: float) -> None:
        pass

    def _do_set_sample_rate(self, rate_hz: float) -> None:
        pass

    def _do_set_gain(self, gain: Gain) -> None:
        # A recording's gain is whatever it was when the samples were taken.
        pass

    def _do_read_samples(self, count: int) -> IqSamples:
        if count > self._available and not self._loop:
            raise CaptureError(
                f"asked for {count} samples but {self._path} holds only {self._available}"
            )

        chunks: list[IqSamples] = []
        remaining = count
        while remaining > 0:
            if self._position >= self._available:
                if not self._loop:
                    raise CaptureError(
                        f"{self._path} ran out after {self._available} samples; "
                        "pass loop=True to replay it from the start"
                    )
                self._position = 0
            take = min(remaining, self._available - self._position)
            chunks.append(
                read_capture(self._path, count=take, start_index=self._position, fmt=self._format)
            )
            self._position += take
            remaining -= take

        if len(chunks) == 1:
            return chunks[0]
        joined: IqSamples = np.concatenate(chunks)
        return joined

    # -- Helpers ------------------------------------------------------------------------------

    def _count_samples(self) -> int:
        """How many complex samples the file holds."""
        if not self._path.is_file():
            raise CaptureError(f"no capture at {self._path}")
        total_bytes = self._path.stat().st_size
        if total_bytes == 0:
            raise CaptureError(f"{self._path} is empty")
        if total_bytes % self._format.bytes_per_sample != 0:
            raise CaptureError(
                f"{self._path} is {total_bytes} bytes, not a whole number of "
                f"{self._format.value} samples"
            )
        return total_bytes // self._format.bytes_per_sample

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._path)!r}, loop={self._loop})"


def describe_capture(path: Path | str) -> str:
    """A one-line human-readable summary of a capture, for a device listing or an error."""
    metadata = read_metadata(path)
    duration = metadata.duration_s
    parts = [
        f"{metadata.center_freq_hz / 1e6:.3f} MHz",
        f"{metadata.sample_rate_hz / 1e6:g} MS/s",
        metadata.format.value,
    ]
    if duration is not None:
        parts.append(f"{duration:.2f} s")
    if metadata.receiver:
        parts.append(metadata.receiver)
    return ", ".join(parts)


__all__ = [
    "METADATA_SUFFIX",
    "IqFileSdrDevice",
    "IqFormat",
    "IqMetadata",
    "describe_capture",
    "metadata_path",
    "read_capture",
    "read_metadata",
    "write_capture",
    "write_metadata",
]
