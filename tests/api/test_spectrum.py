"""Tests for the live spectrum feed.

The frame format is a wire format, so it is pinned down here: a change to it is a change every
client has to be told about, and these tests are what makes that visible.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openwave.api.app import API_PREFIX, create_app
from openwave.api.spectrum import (
    DEFAULT_RANGE_DB,
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    FRAME_VERSION,
    SpectrumStreamer,
    decode_frame,
    encode_frame,
)
from openwave.radio.demo import demo_receiver


@pytest.fixture
def streamer() -> SpectrumStreamer:
    """A streamer wired to the invented band, running as fast as it can."""
    return SpectrumStreamer(device_factory=demo_receiver, frame_rate=200.0)


def levels(count: int = 16, *, peak_dbfs: float = -20.0, floor_dbfs: float = -80.0):
    """A spectrum with one loud bin, for testing the encoding."""
    values = np.full(count, floor_dbfs, dtype=np.float64)
    values[count // 2] = peak_dbfs
    return values


class TestFrameFormat:
    def test_the_header_size_is_derived_from_its_layout(self) -> None:
        # Stating the size separately is how it came to disagree with the layout once already.
        from_layout = struct.calcsize(FRAME_HEADER_FORMAT)
        assert from_layout == FRAME_HEADER_SIZE
        assert from_layout == 40

    def test_a_frame_is_its_header_plus_one_byte_per_bin(self) -> None:
        frame = encode_frame(levels(1024), center_freq_hz=98e6, span_hz=2.4e6)
        assert len(frame) == FRAME_HEADER_SIZE + 1024

    def test_a_frame_starts_with_the_magic_and_version(self) -> None:
        # So a client reading the wrong socket finds out at once rather than drawing noise.
        frame = encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6)
        assert frame[:4] == FRAME_MAGIC
        assert frame[4] == FRAME_VERSION

    def test_the_wire_size_is_small_enough_to_leave_running(self) -> None:
        # A thousand numbers as JSON would be ten times this and need parsing every frame.
        per_frame = FRAME_HEADER_SIZE + 1024
        assert per_frame * 20 < 25_000  # bytes per second at twenty frames


class TestEncoding:
    def test_a_round_trip_keeps_the_scale(self) -> None:
        frame = decode_frame(
            encode_frame(levels(64), center_freq_hz=98e6, span_hz=2.4e6, timestamp_ms=1234)
        )
        assert frame.center_freq_hz == 98e6
        assert frame.span_hz == 2.4e6
        assert frame.bins == 64
        assert frame.timestamp_ms == 1234
        assert frame.range_db == DEFAULT_RANGE_DB

    def test_levels_survive_the_round_trip_within_a_quantisation_step(self) -> None:
        # One byte over 90 dB is about a third of a decibel, which no display can show.
        original = levels(32, peak_dbfs=-20.0, floor_dbfs=-80.0)
        recovered = decode_frame(
            encode_frame(original, center_freq_hz=98e6, span_hz=2.4e6)
        ).levels_dbfs()
        assert np.max(np.abs(recovered - original)) < DEFAULT_RANGE_DB / 255.0 + 1e-6

    def test_the_reference_follows_the_loudest_bin_by_default(self) -> None:
        # So a display always uses its full colour range, whatever the gain happens to be.
        frame = decode_frame(
            encode_frame(levels(peak_dbfs=-33.0), center_freq_hz=98e6, span_hz=2.4e6)
        )
        assert frame.reference_dbfs == pytest.approx(-33.0)
        assert int(np.max(frame.magnitudes)) == 255

    def test_the_reference_can_be_fixed(self) -> None:
        # Which a waterfall needs: a scale that moves makes an unchanging signal appear to
        # change colour.
        frame = decode_frame(
            encode_frame(
                levels(peak_dbfs=-33.0),
                center_freq_hz=98e6,
                span_hz=2.4e6,
                reference_dbfs=0.0,
            )
        )
        assert frame.reference_dbfs == pytest.approx(0.0)
        assert int(np.max(frame.magnitudes)) < 255

    def test_levels_below_the_range_are_clipped_rather_than_wrapped(self) -> None:
        values = np.array([-10.0, -500.0], dtype=np.float64)
        frame = decode_frame(encode_frame(values, center_freq_hz=98e6, span_hz=2.4e6))
        assert int(frame.magnitudes[0]) == 255
        assert int(frame.magnitudes[1]) == 0

    def test_bin_frequencies_span_the_window(self) -> None:
        frame = decode_frame(encode_frame(levels(100), center_freq_hz=98e6, span_hz=2.4e6))
        frequencies = frame.frequencies_hz()
        assert frequencies[0] > frame.start_hz
        assert frequencies[-1] < frame.start_hz + frame.span_hz
        assert np.all(np.diff(frequencies) > 0)

    def test_an_empty_spectrum_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one bin"):
            encode_frame(np.array([], dtype=np.float64), center_freq_hz=98e6, span_hz=2.4e6)

    def test_too_many_bins_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            encode_frame(np.zeros(70_000), center_freq_hz=98e6, span_hz=2.4e6)

    def test_a_non_positive_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="range must be positive"):
            encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6, range_db=0.0)


class TestDecodingRefusesBadInput:
    def test_something_that_is_not_a_frame_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a spectrum frame"):
            decode_frame(b"JUNK" + bytes(FRAME_HEADER_SIZE))

    def test_a_truncated_frame_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 40 bytes"):
            decode_frame(b"OWSP")

    def test_a_frame_whose_length_disagrees_with_its_bin_count_is_refused(self) -> None:
        frame = encode_frame(levels(16), center_freq_hz=98e6, span_hz=2.4e6)
        with pytest.raises(ValueError, match="should be"):
            decode_frame(frame[:-4])

    def test_a_future_version_is_refused_rather_than_misread(self) -> None:
        # An old client misreading a new frame could draw a loud signal that is not there.
        frame = bytearray(encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6))
        frame[4] = FRAME_VERSION + 1
        with pytest.raises(ValueError, match="is not version"):
            decode_frame(bytes(frame))


class TestStreaming:
    def test_frames_are_produced(self, streamer: SpectrumStreamer) -> None:
        frames = list(streamer.frames(98e6, max_frames=3))
        assert len(frames) == 3
        assert all(len(frame) == FRAME_HEADER_SIZE + 1024 for frame in frames)

    def test_a_frame_reports_where_the_receiver_was_tuned(self, streamer: SpectrumStreamer) -> None:
        frame = decode_frame(next(iter(streamer.frames(101.7e6, max_frames=1))))
        assert frame.center_freq_hz == 101.7e6
        assert frame.span_hz == 2_400_000.0

    def test_a_station_is_louder_than_an_empty_span(self, streamer: SpectrumStreamer) -> None:
        # The claim worth testing: the frames carry a real spectrum rather than noise.
        on_station = decode_frame(next(iter(streamer.frames(98e6, max_frames=1))))
        between = decode_frame(next(iter(streamer.frames(95e6, max_frames=1))))
        assert on_station.reference_dbfs > between.reference_dbfs + 20.0

    def test_timestamps_advance(self, streamer: SpectrumStreamer) -> None:
        frames = [decode_frame(frame) for frame in streamer.frames(98e6, max_frames=3)]
        assert frames[0].timestamp_ms <= frames[-1].timestamp_ms

    def test_viewers_are_counted_and_released(self, streamer: SpectrumStreamer) -> None:
        feed = streamer.frames(98e6)
        next(feed)
        assert streamer.viewers == 1
        feed.close()
        assert streamer.viewers == 0

    def test_a_bin_count_below_two_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least two bins"):
            SpectrumStreamer(device_factory=demo_receiver, bins=1)

    def test_a_non_positive_frame_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="frame rate must be positive"):
            SpectrumStreamer(device_factory=demo_receiver, frame_rate=0.0)

    def test_fewer_samples_than_bins_is_refused(self) -> None:
        # There would be nothing to average, so each bin would be one noisy FFT point.
        with pytest.raises(ValueError, match="at least the bin count"):
            SpectrumStreamer(device_factory=demo_receiver, bins=4096, samples_per_frame=1024)

    def test_a_narrower_frame_still_covers_the_window(self) -> None:
        # Averaging down rather than decimating: a decimated spectrum misses narrow signals
        # entirely, which on a display looks like a station flickering in and out.
        narrow = SpectrumStreamer(device_factory=demo_receiver, bins=128, frame_rate=200.0)
        frame = decode_frame(next(iter(narrow.frames(98e6, max_frames=1))))
        assert frame.bins == 128
        assert frame.span_hz == 2_400_000.0


class TestOverTheWebSocket:
    def test_frames_arrive_as_binary(self) -> None:
        app = create_app(device_factory=lambda _spec: demo_receiver())
        with (
            TestClient(app) as client,
            client.websocket_connect(f"{API_PREFIX}/ws/spectrum?freq_hz=98000000") as socket,
        ):
            frames = [decode_frame(socket.receive_bytes()) for _ in range(3)]
        assert all(frame.bins == 1024 for frame in frames)
        assert all(frame.center_freq_hz == 98e6 for frame in frames)

    def test_the_frequency_and_rate_can_be_chosen(self) -> None:
        app = create_app(device_factory=lambda _spec: demo_receiver())
        with (
            TestClient(app) as client,
            client.websocket_connect(
                f"{API_PREFIX}/ws/spectrum?freq_hz=101700000&sample_rate_hz=1024000"
            ) as socket,
        ):
            frame = decode_frame(socket.receive_bytes())
        assert frame.center_freq_hz == 101.7e6
        assert frame.span_hz == 1_024_000.0

    def test_the_configuration_is_published(self) -> None:
        # So a client knows the frame layout without hard-coding it.
        app = create_app(device_factory=lambda _spec: demo_receiver())
        with TestClient(app) as client:
            body = client.get(f"{API_PREFIX}/spectrum").json()
        assert body["bins"] == 1024
        assert body["frame_header_bytes"] == FRAME_HEADER_SIZE
        assert body["range_db"] == DEFAULT_RANGE_DB
