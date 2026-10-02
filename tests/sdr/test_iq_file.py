"""Tests for recorded IQ captures.

A capture contributed by somebody with real hardware is the only path this project has to real
signals, so the reader has to be right about sample formats and has to refuse a file it cannot
interpret rather than quietly producing nonsense.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from openwave.sdr.errors import CaptureError, TuningFailedError, UnsupportedSampleRateError
from openwave.sdr.iq_file import (
    IqFileSdrDevice,
    IqFormat,
    IqMetadata,
    describe_capture,
    metadata_path,
    read_capture,
    read_metadata,
    write_capture,
    write_metadata,
)

SAMPLE_RATE_HZ = 2_400_000.0
CENTER_FREQ_HZ = 98_000_000.0


def sample_metadata(fmt: IqFormat = IqFormat.CF32, **overrides: object) -> IqMetadata:
    fields: dict[str, object] = {
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "center_freq_hz": CENTER_FREQ_HZ,
        "format": fmt,
    }
    fields.update(overrides)
    return IqMetadata(**fields)  # type: ignore[arg-type]


def ramp(count: int = 64) -> np.ndarray:
    """Samples spread over the full scale, so clipping and rounding show up."""
    phase = np.linspace(0.0, 4.0 * np.pi, count)
    return (0.9 * np.cos(phase) + 0.9j * np.sin(phase)).astype(np.complex64)


class TestIqFormat:
    @pytest.mark.parametrize(
        ("fmt", "bytes_per_sample"),
        [(IqFormat.CF32, 8), (IqFormat.CU8, 2), (IqFormat.CS16, 4)],
    )
    def test_sample_size(self, fmt: IqFormat, bytes_per_sample: int) -> None:
        assert fmt.bytes_per_sample == bytes_per_sample

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("band.cf32", IqFormat.CF32),
            ("band.cu8", IqFormat.CU8),
            ("band.CS16", IqFormat.CS16),
            ("/tmp/a.b.cu8", IqFormat.CU8),
        ],
    )
    def test_inferred_from_the_extension(self, filename: str, expected: IqFormat) -> None:
        assert IqFormat.from_path(filename) == expected

    def test_an_unknown_extension_is_refused_with_the_supported_ones_listed(self) -> None:
        with pytest.raises(CaptureError, match=re.escape(".cf32, .cu8, .cs16")):
            IqFormat.from_path("band.wav")

    def test_unsigned_bytes_are_centred_on_the_midpoint(self) -> None:
        # rtl_sdr writes bytes where 127.5 is zero, not 0. Getting this wrong puts a huge DC
        # offset into every capture read from the most common format there is.
        assert IqFormat.CU8.offset == 127.5
        assert IqFormat.CF32.offset == 0.0


class TestMetadata:
    def test_a_sample_rate_is_required(self) -> None:
        with pytest.raises(CaptureError, match="missing sample_rate_hz"):
            IqMetadata.from_dict({"center_freq_hz": CENTER_FREQ_HZ})

    def test_a_centre_frequency_is_required(self) -> None:
        with pytest.raises(CaptureError, match="missing center_freq_hz"):
            IqMetadata.from_dict({"sample_rate_hz": SAMPLE_RATE_HZ})

    @pytest.mark.parametrize("sample_rate_hz", [0.0, -1.0])
    def test_a_non_positive_sample_rate_is_refused(self, sample_rate_hz: float) -> None:
        with pytest.raises(CaptureError, match="sample rate must be positive"):
            IqMetadata(sample_rate_hz=sample_rate_hz, center_freq_hz=CENTER_FREQ_HZ)

    def test_round_trip_through_a_dictionary(self) -> None:
        original = sample_metadata(
            IqFormat.CU8,
            sample_count=1024,
            receiver="RTL-SDR Blog V4",
            notes="89.1 strong, 95.6 weak",
        )
        assert IqMetadata.from_dict(original.to_dict()) == original

    def test_unset_fields_are_left_out_rather_than_written_as_null(self) -> None:
        assert "notes" not in sample_metadata().to_dict()

    def test_duration_is_derived_from_the_length(self) -> None:
        metadata = sample_metadata(sample_count=int(SAMPLE_RATE_HZ))
        assert metadata.duration_s == pytest.approx(1.0)

    def test_duration_is_unknown_without_a_length(self) -> None:
        assert sample_metadata().duration_s is None


class TestMetadataIsTreatedAsUntrusted:
    """A sidecar arrives from whoever recorded the capture, so every value is checked."""

    def test_a_non_object_is_refused(self) -> None:
        with pytest.raises(CaptureError, match="must be a JSON object"):
            IqMetadata.from_dict([1, 2, 3])

    def test_a_string_where_a_number_belongs_is_refused(self) -> None:
        with pytest.raises(CaptureError, match="sample_rate_hz must be a number"):
            IqMetadata.from_dict({"sample_rate_hz": "2.4e6", "center_freq_hz": CENTER_FREQ_HZ})

    def test_a_non_finite_number_is_refused(self) -> None:
        # json.loads happily produces Infinity, which would poison every later calculation.
        with pytest.raises(CaptureError, match="must be finite"):
            IqMetadata.from_dict({"sample_rate_hz": float("inf"), "center_freq_hz": CENTER_FREQ_HZ})

    def test_a_boolean_is_not_accepted_as_a_number(self) -> None:
        with pytest.raises(CaptureError, match="must be a number"):
            IqMetadata.from_dict({"sample_rate_hz": True, "center_freq_hz": CENTER_FREQ_HZ})

    def test_an_unknown_sample_format_is_refused(self) -> None:
        with pytest.raises(CaptureError, match="unknown sample format"):
            IqMetadata.from_dict(
                {
                    "sample_rate_hz": SAMPLE_RATE_HZ,
                    "center_freq_hz": CENTER_FREQ_HZ,
                    "format": "cf64",
                }
            )

    def test_an_unrecognised_key_is_reported_rather_than_ignored(self) -> None:
        # Silently dropping a key would hide a typo in somebody's hand-written sidecar.
        with pytest.raises(CaptureError, match="unrecognised keys: freq"):
            IqMetadata.from_dict(
                {"sample_rate_hz": SAMPLE_RATE_HZ, "center_freq_hz": CENTER_FREQ_HZ, "freq": 98}
            )


class TestWriteAndRead:
    @pytest.mark.parametrize("fmt", list(IqFormat))
    def test_a_capture_survives_a_round_trip(self, fmt: IqFormat, tmp_path: Path) -> None:
        # Tolerance by format: float32 is exact, int16 quantises to about 3e-5, and 8-bit bytes
        # to about 8e-3. Anything worse than that means the scaling is wrong.
        tolerance = {IqFormat.CF32: 1e-6, IqFormat.CS16: 1e-4, IqFormat.CU8: 1e-2}[fmt]
        path = tmp_path / f"band{fmt.extension}"
        original = ramp()

        write_capture(path, original, metadata=sample_metadata(fmt))
        recovered = read_capture(path)

        assert recovered.dtype == np.complex64
        assert len(recovered) == len(original)
        np.testing.assert_allclose(recovered.real, original.real, atol=tolerance)
        np.testing.assert_allclose(recovered.imag, original.imag, atol=tolerance)

    def test_writing_records_the_length_in_the_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(path, ramp(100), metadata=sample_metadata())
        assert read_metadata(path).sample_count == 100

    def test_the_sidecar_sits_next_to_the_data(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(path, ramp(), metadata=sample_metadata())
        assert metadata_path(path) == tmp_path / "band.cf32.json"
        assert metadata_path(path).is_file()

    def test_the_sidecar_is_readable_json(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(path, ramp(), metadata=sample_metadata(receiver="RTL-SDR Blog V4"))
        data = json.loads(metadata_path(path).read_text())
        assert data["receiver"] == "RTL-SDR Blog V4"
        assert data["center_freq_hz"] == CENTER_FREQ_HZ

    def test_an_extension_contradicting_the_metadata_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CaptureError, match="does not match the format"):
            write_capture(tmp_path / "band.cu8", ramp(), metadata=sample_metadata(IqFormat.CF32))

    def test_a_length_contradicting_the_samples_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CaptureError, match="metadata says 999 samples"):
            write_capture(
                tmp_path / "band.cf32", ramp(10), metadata=sample_metadata(sample_count=999)
            )

    def test_samples_beyond_full_scale_are_clipped_not_wrapped(self, tmp_path: Path) -> None:
        # The failure this guards against is specific: full scale times 32768 is 32768, which an
        # int16 cannot hold, so a loud sample would wrap to the most negative value and turn a
        # strong signal into a loud glitch.
        path = tmp_path / "loud.cs16"
        loud = np.array([1.0 + 1.0j, -1.0 - 1.0j, 2.0 + 2.0j], dtype=np.complex64)
        write_capture(path, loud, metadata=sample_metadata(IqFormat.CS16))
        recovered = read_capture(path)
        # Every value stays at the positive or negative extreme. A wrap would show up as a
        # strongly negative value where a strongly positive one belongs.
        np.testing.assert_allclose(recovered.real, [0.99997, -1.0, 0.99997], atol=1e-4)
        np.testing.assert_allclose(recovered.imag, [0.99997, -1.0, 0.99997], atol=1e-4)

    def test_reading_part_of_a_capture(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        original = ramp(100)
        write_capture(path, original, metadata=sample_metadata())
        chunk = read_capture(path, count=10, start_index=20)
        np.testing.assert_allclose(chunk, original[20:30], atol=1e-6)


class TestReadingRefusesBadInput:
    def test_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CaptureError, match="no capture at"):
            read_capture(tmp_path / "absent.cf32")

    def test_a_truncated_file(self, tmp_path: Path) -> None:
        path = tmp_path / "half.cf32"
        path.write_bytes(b"\x00" * 13)
        with pytest.raises(CaptureError, match="not a whole number"):
            read_capture(path)

    def test_reading_past_the_end(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(path, ramp(10), metadata=sample_metadata())
        with pytest.raises(CaptureError, match="holds 10 samples"):
            read_capture(path, count=50)

    def test_starting_past_the_end(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(path, ramp(10), metadata=sample_metadata())
        with pytest.raises(CaptureError, match="cannot start at sample 99"):
            read_capture(path, count=1, start_index=99)

    def test_a_missing_sidecar(self, tmp_path: Path) -> None:
        path = tmp_path / "orphan.cf32"
        path.write_bytes(b"\x00" * 8)
        with pytest.raises(CaptureError, match="no metadata sidecar"):
            read_metadata(path)

    def test_a_corrupt_sidecar(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        path.write_bytes(b"\x00" * 8)
        metadata_path(path).write_text("{not json")
        with pytest.raises(CaptureError, match="not valid JSON"):
            read_metadata(path)


class TestReplay:
    @pytest.fixture
    def capture(self, tmp_path: Path) -> Path:
        path = tmp_path / "fm.cf32"
        write_capture(
            path,
            ramp(1000),
            metadata=sample_metadata(receiver="RTL-SDR Blog V4", notes="test fixture"),
        )
        return path

    def test_it_reads_like_a_receiver(self, capture: Path) -> None:
        with IqFileSdrDevice(capture) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            device.set_center_freq(CENTER_FREQ_HZ)
            samples = device.read_samples(100)
        assert samples.shape == (100,)
        assert samples.dtype == np.complex64

    def test_it_knows_how_long_the_capture_is(self, capture: Path) -> None:
        assert IqFileSdrDevice(capture).sample_count == 1000

    def test_it_exposes_what_the_capture_says_about_itself(self, capture: Path) -> None:
        assert IqFileSdrDevice(capture).metadata.receiver == "RTL-SDR Blog V4"

    def test_reads_walk_through_the_capture(self, capture: Path) -> None:
        with IqFileSdrDevice(capture) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            device.set_center_freq(CENTER_FREQ_HZ)
            first = device.read_samples(50)
            second = device.read_samples(50)
        expected = read_capture(capture, count=100)
        np.testing.assert_array_equal(np.concatenate([first, second]), expected)

    def test_it_loops_when_the_capture_runs_out(self, capture: Path) -> None:
        with IqFileSdrDevice(capture, loop=True) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            device.set_center_freq(CENTER_FREQ_HZ)
            samples = device.read_samples(1500)
        assert len(samples) == 1500
        np.testing.assert_array_equal(samples[:500], samples[1000:])

    def test_without_looping_running_out_is_an_error(self, capture: Path) -> None:
        with IqFileSdrDevice(capture, loop=False) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            device.set_center_freq(CENTER_FREQ_HZ)
            with pytest.raises(CaptureError, match="holds only 1000"):
                device.read_samples(1500)

    def test_rewinding_starts_again(self, capture: Path) -> None:
        with IqFileSdrDevice(capture) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            device.set_center_freq(CENTER_FREQ_HZ)
            first = device.read_samples(50)
            device.rewind()
            np.testing.assert_array_equal(device.read_samples(50), first)

    def test_retuning_elsewhere_is_refused(self, capture: Path) -> None:
        # A capture holds one window of spectrum. Handing back these samples for a different
        # frequency would be answering a question the file cannot answer.
        with IqFileSdrDevice(capture) as device:
            device.set_sample_rate(SAMPLE_RATE_HZ)
            with pytest.raises(TuningFailedError, match="outside the tuning range"):
                device.set_center_freq(CENTER_FREQ_HZ + 1e6)

    def test_another_sample_rate_is_refused(self, capture: Path) -> None:
        device = IqFileSdrDevice(capture)
        with device, pytest.raises(UnsupportedSampleRateError, match="does not support"):
            device.set_sample_rate(1.024e6)

    def test_an_empty_capture_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.cf32"
        path.write_bytes(b"")
        write_metadata(path, sample_metadata())
        with pytest.raises(CaptureError, match="is empty"):
            IqFileSdrDevice(path)

    def test_a_capture_without_metadata_cannot_be_replayed(self, tmp_path: Path) -> None:
        path = tmp_path / "orphan.cf32"
        path.write_bytes(b"\x00" * 800)
        with pytest.raises(CaptureError, match="no metadata sidecar"):
            IqFileSdrDevice(path)

    def test_metadata_contradicting_the_filename_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cu8"
        path.write_bytes(b"\x80" * 200)
        write_metadata(path, sample_metadata(IqFormat.CF32))
        with pytest.raises(CaptureError, match="metadata says cf32"):
            IqFileSdrDevice(path)

    def test_the_device_identifies_itself_as_a_capture(self, capture: Path) -> None:
        info = IqFileSdrDevice(capture).info
        assert info.driver == "file"
        assert "fm.cf32" in info.label


class TestDescribeCapture:
    def test_it_summarises_the_essentials(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cu8"
        write_capture(
            path,
            ramp(2400),
            metadata=sample_metadata(IqFormat.CU8, receiver="RTL-SDR Blog V4"),
        )
        summary = describe_capture(path)
        assert "98.000 MHz" in summary
        assert "2.4 MS/s" in summary
        assert "cu8" in summary
        assert "RTL-SDR Blog V4" in summary
