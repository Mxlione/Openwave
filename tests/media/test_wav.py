"""Tests for WAV encoding.

A header with a wrong field makes a file that some players open and others reject, which is a
worse failure than one nothing will open. Each field is checked against the specification.
"""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from openwave.media.wav import (
    UNKNOWN_LENGTH,
    WAV_HEADER_SIZE,
    WAVE_FORMAT_PCM,
    WavFormat,
    pcm_bytes,
    wav_bytes,
    wav_header,
    write_wav,
)

SAMPLE_RATE = 48_000


def tone(frequency_hz: float = 440.0, samples: int = 4800) -> np.ndarray:
    t = np.arange(samples) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)


class TestWavFormat:
    def test_stereo_sixteen_bit_frames_are_four_bytes(self) -> None:
        fmt = WavFormat(sample_rate_hz=SAMPLE_RATE)
        assert fmt.bytes_per_frame == 4
        assert fmt.byte_rate == SAMPLE_RATE * 4

    def test_mono_frames_are_half_the_size(self) -> None:
        assert WavFormat(sample_rate_hz=SAMPLE_RATE, channels=1).bytes_per_frame == 2

    @pytest.mark.parametrize("rate", [0, -48_000])
    def test_a_non_positive_rate_is_refused(self, rate: int) -> None:
        with pytest.raises(ValueError, match="sample rate must be positive"):
            WavFormat(sample_rate_hz=rate)

    @pytest.mark.parametrize("channels", [0, 3])
    def test_an_unsupported_channel_count_is_refused(self, channels: int) -> None:
        with pytest.raises(ValueError, match="channels must be 1 or 2"):
            WavFormat(sample_rate_hz=SAMPLE_RATE, channels=channels)

    def test_a_depth_other_than_sixteen_bits_is_refused_with_a_reason(self) -> None:
        with pytest.raises(ValueError, match="only 16-bit"):
            WavFormat(sample_rate_hz=SAMPLE_RATE, bits_per_sample=24)


class TestHeader:
    def test_it_is_the_canonical_length(self) -> None:
        assert len(wav_header(WavFormat(sample_rate_hz=SAMPLE_RATE))) == WAV_HEADER_SIZE

    def test_every_field_matches_the_specification(self) -> None:
        fmt = WavFormat(sample_rate_hz=SAMPLE_RATE, channels=2)
        header = wav_header(fmt, data_bytes=1000)
        (
            riff,
            riff_size,
            wave_tag,
            fmt_tag,
            fmt_size,
            audio_format,
            channels,
            rate,
            byte_rate,
            block_align,
            bits,
            data_tag,
            data_size,
        ) = struct.unpack("<4sI4s4sIHHIIHH4sI", header)

        assert riff == b"RIFF"
        assert wave_tag == b"WAVE"
        assert fmt_tag == b"fmt "
        assert data_tag == b"data"
        assert fmt_size == 16
        assert audio_format == WAVE_FORMAT_PCM
        assert channels == 2
        assert rate == SAMPLE_RATE
        assert byte_rate == SAMPLE_RATE * 4
        assert block_align == 4
        assert bits == 16
        assert data_size == 1000
        assert riff_size == 1000 + WAV_HEADER_SIZE - 8

    def test_an_unknown_length_fills_the_size_fields(self) -> None:
        # Live radio has no length: the broadcast has not finished. Every player reads the
        # maximum value as "keep going until the stream stops".
        header = wav_header(WavFormat(sample_rate_hz=SAMPLE_RATE))
        riff_size = struct.unpack_from("<I", header, 4)[0]
        data_size = struct.unpack_from("<I", header, 40)[0]
        assert riff_size == UNKNOWN_LENGTH
        assert data_size == UNKNOWN_LENGTH

    def test_a_negative_length_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            wav_header(WavFormat(sample_rate_hz=SAMPLE_RATE), data_bytes=-1)


class TestPcmConversion:
    def test_stereo_samples_are_interleaved(self) -> None:
        left = np.array([1.0, 0.0], dtype=np.float32)
        right = np.array([0.0, -1.0], dtype=np.float32)
        values = np.frombuffer(pcm_bytes(left, right), dtype="<i2")
        assert list(values) == [32767, 0, 0, -32767]

    def test_mono_produces_one_value_per_sample(self) -> None:
        mono = np.array([0.5, -0.5], dtype=np.float32)
        assert len(np.frombuffer(pcm_bytes(mono), dtype="<i2")) == 2

    def test_full_scale_does_not_overflow(self) -> None:
        # Scaling by 32768 rather than 32767 makes a sample at exactly full scale wrap to the
        # most negative value, turning the loudest moment of a broadcast into a crack.
        values = np.frombuffer(pcm_bytes(np.array([1.0], dtype=np.float32)), dtype="<i2")
        assert values[0] == 32767
        assert values[0] > 0

    def test_values_beyond_full_scale_are_clipped_not_wrapped(self) -> None:
        loud = np.array([2.0, -2.0], dtype=np.float32)
        values = np.frombuffer(pcm_bytes(loud), dtype="<i2")
        assert list(values) == [32767, -32767]

    def test_channels_of_different_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="different lengths"):
            pcm_bytes(np.zeros(10, dtype=np.float32), np.zeros(5, dtype=np.float32))

    def test_a_round_trip_keeps_the_waveform(self) -> None:
        original = tone()
        recovered = np.frombuffer(pcm_bytes(original), dtype="<i2").astype(np.float32) / 32767.0
        np.testing.assert_allclose(recovered, original, atol=1e-4)


class TestCompleteFiles:
    def test_the_python_standard_library_can_read_what_we_write(self) -> None:
        # The most convincing check available without a player: an independent implementation
        # of the format agrees about what the file says.
        data = wav_bytes(tone(), tone(220.0), sample_rate_hz=SAMPLE_RATE)
        with wave.open(io.BytesIO(data)) as reader:
            assert reader.getnchannels() == 2
            assert reader.getframerate() == SAMPLE_RATE
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == 4800

    def test_a_mono_file_is_half_the_size(self) -> None:
        stereo = wav_bytes(tone(), tone(), sample_rate_hz=SAMPLE_RATE)
        mono = wav_bytes(tone(), sample_rate_hz=SAMPLE_RATE)
        assert len(stereo) - WAV_HEADER_SIZE == 2 * (len(mono) - WAV_HEADER_SIZE)

    def test_writing_to_a_file(self, tmp_path: Path) -> None:
        path = write_wav(tmp_path / "out.wav", tone(), tone(220.0), sample_rate_hz=SAMPLE_RATE)
        assert path.is_file()
        with wave.open(str(path)) as reader:
            assert reader.getnframes() == 4800
