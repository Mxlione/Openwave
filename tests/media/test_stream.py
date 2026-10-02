"""Tests for the byte buffer between the demodulator and the player.

Two threads sharing a buffer is where a program hangs. The tests that matter here are the ones
about shutdown: closing the stream has to release whatever is waiting on it, from either side,
or a stop command never returns.
"""

from __future__ import annotations

import threading
import time

import pytest

from openwave.media.stream import ByteStream, StreamClosedError


class TestBasicUse:
    def test_what_goes_in_comes_out(self) -> None:
        stream = ByteStream()
        stream.write(b"hello")
        assert stream.read(5) == b"hello"

    def test_a_read_can_span_several_writes(self) -> None:
        stream = ByteStream()
        stream.write(b"abc")
        stream.write(b"def")
        assert stream.read(6) == b"abcdef"

    def test_a_write_can_span_several_reads(self) -> None:
        stream = ByteStream()
        stream.write(b"abcdef")
        assert stream.read(2) == b"ab"
        assert stream.read(2) == b"cd"
        assert stream.read(2) == b"ef"

    def test_a_short_read_returns_what_is_there(self) -> None:
        # A player starved of audio would rather have some of it now than all of it late.
        stream = ByteStream()
        stream.write(b"abc")
        assert stream.read(100) == b"abc"

    def test_writing_nothing_does_nothing(self) -> None:
        stream = ByteStream()
        assert stream.write(b"") == 0
        assert stream.pending == 0

    def test_it_tracks_what_is_buffered(self) -> None:
        stream = ByteStream()
        stream.write(b"abcde")
        assert stream.pending == 5
        stream.read(2)
        assert stream.pending == 3

    def test_a_non_positive_read_is_refused(self) -> None:
        with pytest.raises(ValueError, match="read size must be positive"):
            ByteStream().read(0)

    def test_a_non_positive_capacity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="capacity must be positive"):
            ByteStream(0)

    def test_it_reads_sensibly_when_printed(self) -> None:
        stream = ByteStream(capacity_bytes=100)
        stream.write(b"abc")
        assert "pending=3" in repr(stream)
        assert "capacity=100" in repr(stream)


class TestBounds:
    def test_a_write_that_does_not_fit_waits(self) -> None:
        # An unbounded buffer would hide a producer running faster than the consumer by growing
        # until memory ran out, and would add latency in the meantime. Making the producer wait
        # is the right behaviour for live audio.
        stream = ByteStream(capacity_bytes=4)
        stream.write(b"abcd")
        assert stream.write(b"ef", timeout_s=0.05) == 0
        assert stream.pending == 4

    def test_a_write_proceeds_once_the_consumer_catches_up(self) -> None:
        stream = ByteStream(capacity_bytes=4)
        stream.write(b"abcd")

        def drain() -> None:
            time.sleep(0.05)
            stream.read(4)

        threading.Thread(target=drain, daemon=True).start()
        assert stream.write(b"efgh", timeout_s=2.0) == 4

    def test_a_non_blocking_write_takes_what_fits_and_counts_the_rest(self) -> None:
        stream = ByteStream(capacity_bytes=4)
        assert stream.write(b"abcdef", block=False) == 4
        assert stream.dropped == 2

    def test_a_non_blocking_write_to_a_full_buffer_drops_everything(self) -> None:
        stream = ByteStream(capacity_bytes=4)
        stream.write(b"abcd")
        assert stream.write(b"ef", block=False) == 0
        assert stream.dropped == 2


class TestShutdown:
    def test_a_closed_stream_refuses_writes(self) -> None:
        stream = ByteStream()
        stream.close()
        with pytest.raises(StreamClosedError, match="closed stream"):
            stream.write(b"abc")

    def test_closing_leaves_buffered_audio_readable(self) -> None:
        # So a consumer finishes what was already produced instead of cutting off mid-word.
        stream = ByteStream()
        stream.write(b"abc")
        stream.close()
        assert stream.read(3) == b"abc"

    def test_a_drained_closed_stream_reads_empty(self) -> None:
        # Which is how a consumer learns to stop, rather than waiting forever.
        stream = ByteStream()
        stream.close()
        assert stream.read(10) == b""

    def test_closing_releases_a_waiting_reader(self) -> None:
        stream = ByteStream()
        released = threading.Event()

        def consume() -> None:
            stream.read(10)
            released.set()

        threading.Thread(target=consume, daemon=True).start()
        time.sleep(0.05)
        assert not released.is_set()
        stream.close()
        assert released.wait(timeout=2.0)

    def test_closing_releases_a_waiting_writer(self) -> None:
        # The failure this prevents: a stop command hanging behind a producer that is waiting
        # for room in a buffer nobody is draining any more.
        stream = ByteStream(capacity_bytes=4)
        stream.write(b"abcd")
        outcome: list[str] = []

        def produce() -> None:
            try:
                stream.write(b"efgh")
            except StreamClosedError:
                outcome.append("released")

        thread = threading.Thread(target=produce, daemon=True)
        thread.start()
        time.sleep(0.05)
        assert not outcome
        stream.close()
        thread.join(timeout=2.0)
        assert outcome == ["released"]

    def test_closing_twice_is_harmless(self) -> None:
        stream = ByteStream()
        stream.close()
        stream.close()
        assert stream.closed

    def test_it_works_as_a_context_manager(self) -> None:
        with ByteStream() as stream:
            stream.write(b"abc")
        assert stream.closed

    def test_clearing_discards_what_is_buffered(self) -> None:
        stream = ByteStream()
        stream.write(b"stale audio")
        stream.clear()
        assert stream.pending == 0


class TestConcurrency:
    def test_a_producer_and_a_consumer_transfer_everything_in_order(self) -> None:
        stream = ByteStream(capacity_bytes=1024)
        blocks = 200
        payload = bytes(range(256))
        received = bytearray()

        def produce() -> None:
            for _ in range(blocks):
                stream.write(payload)
            stream.close()

        thread = threading.Thread(target=produce, daemon=True)
        thread.start()
        while True:
            chunk = stream.read(777, timeout_s=5.0)
            if not chunk:
                break
            received.extend(chunk)
        thread.join(timeout=5.0)

        assert len(received) == blocks * len(payload)
        assert bytes(received) == payload * blocks
