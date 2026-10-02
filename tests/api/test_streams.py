"""Tests for the audio streamer.

The endless case is tested here rather than over HTTP: a generator can be closed, whereas an
endless HTTP response cannot be completed and leaves a test client waiting.
"""

from __future__ import annotations

import io
import re
import struct
import threading
import wave
from collections.abc import Iterator

import pytest

from openwave.api.streams import AudioStreamer, StreamBusyError
from openwave.radio.demo import demo_receiver


@pytest.fixture
def streamer() -> Iterator[AudioStreamer]:
    """A streamer wired to the invented band."""
    instance = AudioStreamer(device_factory=demo_receiver)
    try:
        yield instance
    finally:
        instance.stop()


class TestEndlessStreaming:
    def test_it_keeps_producing_audio(self, streamer: AudioStreamer) -> None:
        stream = streamer.stream(88_100_000.0)
        collected = bytearray()
        for index, chunk in enumerate(stream):
            collected.extend(chunk)
            if index >= 8:
                break
        stream.close()
        assert len(collected) > 48_000  # more than a quarter-second of stereo audio

    def test_the_header_comes_first(self, streamer: AudioStreamer) -> None:
        stream = streamer.stream(88_100_000.0)
        header = next(stream)
        stream.close()
        assert header[:4] == b"RIFF"
        assert header[8:12] == b"WAVE"

    def test_an_endless_stream_does_not_state_a_length(self, streamer: AudioStreamer) -> None:
        # It cannot: the broadcast has not finished. Every player reads the maximum value as
        # "keep going until the stream stops".
        stream = streamer.stream(88_100_000.0)
        header = next(stream)
        stream.close()
        assert struct.unpack_from("<I", header, 40)[0] == 0xFFFFFFFF

    def test_closing_the_generator_releases_the_receiver(self, streamer: AudioStreamer) -> None:
        stream = streamer.stream(88_100_000.0)
        next(stream)
        assert streamer.listener_count == 1
        assert streamer.frequency_hz == 88_100_000.0

        stream.close()
        assert streamer.listener_count == 0
        assert streamer.frequency_hz is None

    def test_no_threads_are_left_behind(self, streamer: AudioStreamer) -> None:
        before = {thread.name for thread in threading.enumerate()}
        stream = streamer.stream(88_100_000.0)
        next(stream)
        stream.close()
        after = {thread.name for thread in threading.enumerate()}
        assert not {name for name in after - before if "openwave-listen" in name}


class TestFiniteClips:
    def test_a_clip_states_its_length(self, streamer: AudioStreamer) -> None:
        # Which is what lets a player show a duration rather than an endless progress bar.
        data = b"".join(streamer.stream(88_100_000.0, max_seconds=0.3))
        with wave.open(io.BytesIO(data)) as reader:
            assert reader.getnframes() == pytest.approx(0.3 * 48_000, rel=0.05)

    def test_a_clip_ends_on_its_own(self, streamer: AudioStreamer) -> None:
        chunks = list(streamer.stream(88_100_000.0, max_seconds=0.2))
        assert chunks
        assert streamer.listener_count == 0

    def test_a_non_positive_duration_is_refused(self, streamer: AudioStreamer) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            next(streamer.stream(88_100_000.0, max_seconds=0.0))


class TestSharingAndContention:
    def test_two_clients_can_share_one_frequency(self, streamer: AudioStreamer) -> None:
        # They read from the same buffer, so the receiver is tuned once.
        first = streamer.stream(88_100_000.0)
        next(first)
        second = streamer.stream(88_100_000.0)
        next(second)
        assert streamer.listener_count == 2
        first.close()
        assert streamer.listener_count == 1
        second.close()
        assert streamer.listener_count == 0

    def test_a_second_frequency_is_refused_rather_than_stealing_the_radio(
        self, streamer: AudioStreamer
    ) -> None:
        first = streamer.stream(88_100_000.0)
        next(first)
        try:
            with pytest.raises(StreamBusyError, match=re.escape("already streaming 88.1 MHz")):
                next(streamer.stream(98_000_000.0))
        finally:
            first.close()

    def test_a_different_frequency_works_once_the_first_has_stopped(
        self, streamer: AudioStreamer
    ) -> None:
        first = streamer.stream(88_100_000.0)
        next(first)
        first.close()

        second = streamer.stream(101_700_000.0)
        next(second)
        assert streamer.frequency_hz == 101_700_000.0
        second.close()

    def test_stopping_everything_releases_the_receiver(self, streamer: AudioStreamer) -> None:
        stream = streamer.stream(88_100_000.0)
        next(stream)
        streamer.stop()
        assert streamer.frequency_hz is None
        assert streamer.listener_count == 0
