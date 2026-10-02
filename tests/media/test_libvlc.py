"""Tests for playback through libVLC.

Unlike the SDR drivers, this path can be verified for real: libVLC runs on an ordinary desktop
and needs no special hardware. These tests therefore do start libVLC, hand it a live stream and
check that it decodes it -- with ``--aout=dummy`` so no audio device is needed.

They are marked ``media`` and skipped when libVLC is absent, so the main test suite stays
runnable with nothing installed. The CI workflow has a job that installs libVLC and runs them.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from openwave.media.libvlc import (
    LibVlcPlayer,
    PlaybackUnavailableError,
    libvlc_version,
    playback_available,
)
from openwave.media.stream import ByteStream
from openwave.media.wav import pcm_bytes

SAMPLE_RATE = 48_000

requires_libvlc = pytest.mark.skipif(
    not playback_available(), reason="libVLC and python-vlc are not installed"
)


def tone_bytes(duration_s: float = 0.2, frequency_hz: float = 440.0) -> bytes:
    """PCM bytes for a tone, as the demodulator would produce."""
    samples = int(SAMPLE_RATE * duration_s)
    t = np.arange(samples) / SAMPLE_RATE
    wave = (0.3 * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)
    return pcm_bytes(wave, wave)


def wait_until(predicate: object, *, timeout_s: float = 5.0) -> bool:
    """Poll until ``predicate`` is true, or give up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.05)
    return False


class TestAvailability:
    def test_availability_is_reported_rather_than_raised(self) -> None:
        # Device discovery and a capability listing both ask this question, and neither should
        # have to handle an exception to get an answer.
        assert isinstance(playback_available(), bool)

    def test_the_version_is_none_when_playback_is_unavailable(self) -> None:
        assert (libvlc_version() is None) == (not playback_available())

    @requires_libvlc
    def test_the_version_is_reported_when_available(self) -> None:
        version = libvlc_version()
        assert version is not None
        assert version[0].isdigit()


@requires_libvlc
@pytest.mark.media
class TestPlayback:
    def test_libvlc_reads_the_stream_through_the_callbacks(self) -> None:
        # The claim worth testing here: libVLC accepts a byte stream supplied through callbacks
        # and reads it. Consuming every byte is the proof -- nothing else drains the buffer.
        #
        # Reaching the Playing state needs a continuous supply, which is a separate test: given
        # a short burst and then silence, libVLC consumes it all and waits in Opening for more.
        stream = ByteStream()
        stream.write(tone_bytes(0.5))
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            assert wait_until(lambda: stream.pending == 0)
            assert "Error" not in player.state
        finally:
            player.stop()

    def test_a_finite_burst_plays_when_the_stream_is_closed(self) -> None:
        # Closing tells libVLC the length is known, which is what lets it leave Opening.
        stream = ByteStream()
        stream.write(tone_bytes(0.5))
        stream.close()
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            assert wait_until(lambda: player.is_playing or "Ended" in player.state)
        finally:
            player.stop()

    def test_a_producer_that_stops_without_closing_does_not_hang_the_player(self) -> None:
        # The deadlock this prevents: libVLC's thread waiting inside the read callback for
        # audio that will never arrive, while stop() waits for that same thread.
        stream = ByteStream()
        stream.write(tone_bytes(0.1))
        player = LibVlcPlayer(
            stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy", read_timeout_s=30.0
        )
        player.play()
        time.sleep(0.3)
        started = time.monotonic()
        player.stop()
        assert time.monotonic() - started < 5.0

    def test_it_plays_a_stream_that_is_still_being_written(self) -> None:
        stream = ByteStream()
        stop = threading.Event()

        def produce() -> None:
            while not stop.is_set():
                try:
                    stream.write(tone_bytes(0.05))
                except Exception:  # noqa: BLE001 - the stream closing is the normal ending
                    return
                time.sleep(0.02)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            assert wait_until(lambda: player.is_playing)
            time.sleep(0.5)
            assert player.is_playing
        finally:
            player.stop()
            stop.set()
            stream.close()
            producer.join(timeout=2.0)

    def test_it_reaches_the_end_when_the_stream_closes(self) -> None:
        # How a finite broadcast, or a stopped receiver, is meant to end.
        stream = ByteStream()
        stream.write(tone_bytes(0.3))
        stream.close()
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            assert wait_until(lambda: "Ended" in player.state, timeout_s=10.0)
        finally:
            player.stop()

    def test_stopping_releases_everything(self) -> None:
        stream = ByteStream()
        stream.write(tone_bytes())
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        player.play()
        wait_until(lambda: player.is_playing)
        player.stop()
        assert not player.is_playing
        assert player.state == "stopped"

    def test_stopping_twice_is_harmless(self) -> None:
        stream = ByteStream()
        stream.write(tone_bytes())
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        player.play()
        player.stop()
        player.stop()

    def test_playing_twice_does_not_start_a_second_player(self) -> None:
        stream = ByteStream()
        stream.write(tone_bytes())
        stream.close()
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            player.play()
            assert wait_until(lambda: player.is_playing or "Ended" in player.state)
        finally:
            player.stop()

    def test_it_works_as_a_context_manager(self) -> None:
        stream = ByteStream()
        stream.write(tone_bytes())
        stream.close()
        with LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, audio_output="dummy") as player:
            assert wait_until(lambda: player.is_playing or "Ended" in player.state)
        assert not player.is_playing

    def test_a_player_that_was_never_started_reports_stopped(self) -> None:
        player = LibVlcPlayer(ByteStream(), sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        assert player.state == "stopped"
        assert not player.is_playing

    def test_mono_streams_play_too(self) -> None:
        samples = int(SAMPLE_RATE * 0.3)
        wave = (0.3 * np.sin(2 * np.pi * 440 * np.arange(samples) / SAMPLE_RATE)).astype(np.float32)
        stream = ByteStream()
        stream.write(pcm_bytes(wave))
        stream.close()
        player = LibVlcPlayer(stream, sample_rate_hz=SAMPLE_RATE, channels=1, audio_output="dummy")
        try:
            player.play()
            assert wait_until(lambda: player.is_playing or "Ended" in player.state)
        finally:
            player.stop()

    def test_a_callback_that_fails_does_not_bring_the_process_down(self) -> None:
        # An exception crossing a ctypes callback boundary terminates the interpreter rather
        # than propagating, so the callbacks swallow everything. This test passing at all --
        # the process still being alive to report it -- is the assertion.
        class Exploding(ByteStream):
            def read(self, size: int, *, timeout_s: float | None = None) -> bytes:
                raise RuntimeError("the demodulator fell over")

        player = LibVlcPlayer(Exploding(), sample_rate_hz=SAMPLE_RATE, audio_output="dummy")
        try:
            player.play()
            time.sleep(0.5)
        finally:
            player.stop()


class TestWithoutLibVlc:
    def test_the_error_says_what_to_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "vlc", None)
        with pytest.raises(PlaybackUnavailableError, match="libvlc-dev"):
            LibVlcPlayer(ByteStream(), sample_rate_hz=SAMPLE_RATE)

    def test_it_mentions_the_optional_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "vlc", None)
        with pytest.raises(PlaybackUnavailableError, match=r"openwave\[media\]"):
            LibVlcPlayer(ByteStream(), sample_rate_hz=SAMPLE_RATE)
