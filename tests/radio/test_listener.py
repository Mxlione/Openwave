"""Tests for continuous listening.

A producer thread feeding a player is where a program hangs or produces audio with a tick in it.
These tests check both: that the audio is continuous across block joins, and that starting and
stopping are reliable in every order and after every kind of failure.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from scipy import signal as scipy_signal

from openwave.media.stream import ByteStream
from openwave.radio.listener import FmListener, ListenSettings
from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.device import IqSamples, TuningRange
from openwave.sdr.errors import DeviceNotFoundError
from openwave.sdr.mock import MockSdrDevice

FREQ_HZ = 98_000_000.0
TONE_HZ = 1000.0

# Small blocks keep the tests quick while still exercising several joins.
QUICK = ListenSettings(block_samples=1 << 16, settling_samples=0)


def station(**overrides: object) -> SyntheticFmStation:
    fields: dict[str, object] = {
        "freq_hz": FREQ_HZ,
        "power_dbfs": -20.0,
        "left_tone_hz": TONE_HZ,
        "deviation_hz": 50_000.0,
    }
    fields.update(overrides)
    return SyntheticFmStation(**fields)  # type: ignore[arg-type]


def receiver(*stations: SyntheticFmStation, **overrides: object) -> MockSdrDevice:
    fields: dict[str, object] = {"sources": stations or (station(),), "noise_floor_dbfs": -85.0}
    fields.update(overrides)
    return MockSdrDevice(**fields)  # type: ignore[arg-type]


def collect(listener: FmListener, *, blocks: int = 4, timeout_s: float = 20.0) -> bytes:
    """Read until the listener has produced ``blocks`` blocks, or time runs out."""
    pieces: list[bytes] = []
    deadline = time.monotonic() + timeout_s
    while len(pieces) < blocks and time.monotonic() < deadline:
        chunk = listener.stream.read(1 << 20, timeout_s=1.0)
        if not chunk:
            break
        pieces.append(chunk)
    return b"".join(pieces)


def left_channel(pcm: bytes) -> np.ndarray:
    """The left channel of interleaved 16-bit stereo PCM, as floats."""
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64)[0::2] / 32768.0


class TestSettingsValidation:
    @pytest.mark.parametrize("block_samples", [0, -1])
    def test_a_non_positive_block_is_refused(self, block_samples: int) -> None:
        with pytest.raises(ValueError, match="block_samples must be positive"):
            ListenSettings(block_samples=block_samples)

    def test_a_negative_overlap_is_refused(self) -> None:
        with pytest.raises(ValueError, match="overlap_samples must not be negative"):
            ListenSettings(overlap_samples=-1)

    def test_an_overlap_as_large_as_the_block_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be smaller than block_samples"):
            ListenSettings(block_samples=1024, overlap_samples=1024)

    def test_a_non_positive_audio_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="audio_rate_hz must be positive"):
            ListenSettings(audio_rate_hz=0)


class TestLifecycle:
    def test_it_produces_audio(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            assert len(collect(listener, blocks=2)) > 0
        assert listener.error is None

    def test_it_opens_and_closes_a_device_it_was_given_closed(self) -> None:
        device = receiver()
        listener = FmListener(device, FREQ_HZ, settings=QUICK)
        assert not device.is_open
        with listener:
            assert device.is_open
            collect(listener, blocks=1)
        assert not device.is_open

    def test_it_leaves_an_already_open_device_open(self) -> None:
        device = receiver()
        device.open()
        device.set_sample_rate(2.4e6)
        listener = FmListener(device, FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=1)
        assert device.is_open

    def test_starting_twice_is_harmless(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        try:
            listener.start()
            listener.start()
            assert listener.is_running
        finally:
            listener.stop()

    def test_stopping_twice_is_harmless(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        listener.start()
        listener.stop()
        listener.stop()
        assert not listener.is_running

    def test_stopping_a_listener_that_never_started_is_harmless(self) -> None:
        FmListener(receiver(), FREQ_HZ, settings=QUICK).stop()

    def test_stopping_closes_the_stream(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        listener.start()
        listener.stop()
        assert listener.stream.closed

    def test_stopping_returns_promptly_even_with_nobody_draining(self) -> None:
        # The deadlock this prevents: the producer blocked waiting for room in a buffer that
        # nothing is reading, while stop() waits for the producer.
        listener = FmListener(
            receiver(),
            FREQ_HZ,
            settings=QUICK,
            stream=ByteStream(capacity_bytes=4096),
        )
        listener.start()
        time.sleep(0.5)
        started = time.monotonic()
        listener.stop()
        assert time.monotonic() - started < 5.0
        assert not listener.is_running

    def test_a_device_that_will_not_open_fails_at_start(self) -> None:
        listener = FmListener(receiver(present=False), FREQ_HZ, settings=QUICK)
        with pytest.raises(DeviceNotFoundError):
            listener.start()


class TestTuning:
    def test_the_station_is_not_placed_at_the_centre_of_the_window(self) -> None:
        # Tuning the station to zero hertz would land it on the tuner's own DC spike.
        device = receiver()
        listener = FmListener(device, FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=1)
            assert device.center_freq_hz != FREQ_HZ
            assert abs(device.center_freq_hz - FREQ_HZ) == pytest.approx(250e3)

    def test_it_offsets_the_other_way_near_the_edge_of_the_tuning_range(self) -> None:
        device = receiver(tuning_range=TuningRange(min_hz=FREQ_HZ, max_hz=110e6))
        listener = FmListener(device, FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=1)
            assert device.center_freq_hz == pytest.approx(FREQ_HZ + 250e3)

    def test_it_reports_which_station_it_is_on(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        assert listener.freq_hz == FREQ_HZ
        assert "98.000 MHz" in repr(listener)


class TestAudioQuality:
    def test_the_transmitted_tone_comes_out(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            left = left_channel(collect(listener, blocks=4))

        freqs, psd = scipy_signal.welch(
            left, fs=listener.audio_rate_hz, nperseg=min(8192, len(left)), detrend="constant"
        )
        assert float(freqs[int(np.argmax(psd))]) == pytest.approx(TONE_HZ, abs=20.0)

    def test_the_audio_is_continuous_across_block_joins(self) -> None:
        # The artefact this guards against: demodulating blocks independently leaves a jump at
        # every join, which is an audible tick several times a second. The overlap removes it.
        # A tick shows up as a sample-to-sample step far larger than a smooth tone can make.
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            left = left_channel(collect(listener, blocks=5))

        steps = np.abs(np.diff(left))
        typical = float(np.median(steps))
        assert typical > 0
        # Allow a factor of three over the median: a 1 kHz tone's own largest step is about
        # 1.5 times its median, so three leaves room for noise without admitting a tick.
        assert float(np.percentile(steps, 99.9)) < 3.0 * typical

    def test_the_level_does_not_jump_between_blocks(self) -> None:
        # Scaling each block by its own loudest sample would make the volume breathe in time
        # with the blocks. The level comes from the deviation instead.
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            pieces = [listener.stream.read(1 << 20, timeout_s=2.0) for _ in range(4)]

        levels = [float(np.sqrt(np.mean(left_channel(piece) ** 2))) for piece in pieces if piece]
        assert len(levels) >= 3
        assert max(levels) / min(levels) < 1.2

    def test_the_audio_stays_within_full_scale(self) -> None:
        listener = FmListener(receiver(station(deviation_hz=75_000.0)), FREQ_HZ, settings=QUICK)
        with listener:
            left = left_channel(collect(listener, blocks=3))
        assert np.max(np.abs(left)) <= 1.0

    def test_a_stereo_station_is_recognised(self) -> None:
        listener = FmListener(
            receiver(station(stereo=True, right_tone_hz=400.0)), FREQ_HZ, settings=QUICK
        )
        with listener:
            collect(listener, blocks=2)
            assert listener.is_stereo is True

    def test_a_mono_station_is_recognised(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=2)
            assert listener.is_stereo is False

    def test_stereo_is_unknown_before_the_first_block(self) -> None:
        assert FmListener(receiver(), FREQ_HZ, settings=QUICK).is_stereo is None


class TestFailureHandling:
    def test_a_receiver_that_fails_mid_stream_is_reported(self) -> None:
        # A producer thread dying silently would leave a player waiting for audio forever, so
        # the reason is kept for the caller to show.
        class FailsAfterTwoBlocks(MockSdrDevice):
            def __init__(self) -> None:
                super().__init__(sources=[station()], noise_floor_dbfs=-85.0)
                self.reads = 0

            def _do_read_samples(self, count: int) -> IqSamples:
                self.reads += 1
                if self.reads > 2:
                    raise DeviceNotFoundError("the dongle was unplugged")
                return super()._do_read_samples(count)

        listener = FmListener(FailsAfterTwoBlocks(), FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=6, timeout_s=10.0)
            deadline = time.monotonic() + 5.0
            while listener.error is None and time.monotonic() < deadline:
                time.sleep(0.05)

        assert isinstance(listener.error, DeviceNotFoundError)
        assert "unplugged" in str(listener.error)

    def test_a_failing_receiver_closes_the_stream_so_a_reader_is_released(self) -> None:
        class AlwaysFails(MockSdrDevice):
            def _do_read_samples(self, count: int) -> IqSamples:
                raise DeviceNotFoundError("nothing there")

        listener = FmListener(AlwaysFails(), FREQ_HZ, settings=QUICK)
        listener.start()
        try:
            # Returns empty rather than waiting forever, which is how the player learns to stop.
            assert listener.stream.read(1024, timeout_s=5.0) == b""
        finally:
            listener.stop()
        assert listener.error is not None

    def test_blocks_are_counted(self) -> None:
        listener = FmListener(receiver(), FREQ_HZ, settings=QUICK)
        with listener:
            collect(listener, blocks=3)
            assert listener.blocks_produced >= 3
