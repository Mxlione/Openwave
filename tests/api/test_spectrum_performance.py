"""A performance budget for the spectrum feed.

A live display is the one part of OpenWave where being slow makes it wrong: a waterfall that
falls behind is showing the past, and a frame rate that drops below about ten a second stops
reading as motion. So the budget is a test rather than a note in a document, and a change that
makes the feed slower fails here.

**The numbers are deliberately loose.** A test runner is a shared machine with other work on it,
and a budget tight enough to be interesting is a budget that fails for reasons nothing to do
with the code. These are set at roughly three times the measured cost, which is wide enough not
to flake and narrow enough to catch the kind of regression that matters -- an accidental copy of
every frame, or an FFT that grew by a factor of ten.

Marked ``slow`` because they measure rather than assert, and a quick run does not need them.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from openwave.api.spectrum import (
    DEFAULT_BINS,
    DEFAULT_FRAME_RATE,
    FRAME_HEADER_SIZE,
    SpectrumStreamer,
    decode_frame,
    encode_frame,
)
from openwave.radio.demo import demo_receiver

pytestmark = pytest.mark.slow

#: Frames to measure over. Enough to average out one slow moment on a busy machine.
SAMPLE_FRAMES = 30

#: Longest a frame may take to produce, in seconds.
#:
#: The feed aims for twenty frames a second, so 50 ms is the budget it has to fit in. Three
#: times that leaves room for a loaded runner while still failing if producing a frame becomes
#: as expensive as producing three.
FRAME_BUDGET_S = 3.0 / DEFAULT_FRAME_RATE

#: Longest encoding one frame may take, in seconds.
#:
#: Encoding is quantisation and a struct pack, so it should be well under a millisecond. A
#: budget of one millisecond catches an accidental per-bin Python loop, which is the realistic
#: way this gets slow.
ENCODE_BUDGET_S = 1e-3

#: Most bytes a frame may take on the wire.
#:
#: The whole reason for a binary format. A frame that grows past this has probably gone back to
#: something textual, which costs ten times the bandwidth and a parse in the browser.
WIRE_BUDGET_BYTES = FRAME_HEADER_SIZE + DEFAULT_BINS + 16


def levels(count: int = DEFAULT_BINS) -> np.ndarray:
    """A plausible spectrum: a noise floor with one station in it."""
    values = np.full(count, -75.0, dtype=np.float64)
    values[count // 2 - 20 : count // 2 + 20] = -25.0
    return values


class TestFrameCost:
    def test_a_frame_fits_the_wire_budget(self) -> None:
        frame = encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6)
        assert len(frame) <= WIRE_BUDGET_BYTES

    def test_the_feed_fits_in_a_modest_connection(self) -> None:
        # Twenty frames a second has to stay small enough to leave running, including over a
        # phone's connection to a receiver in another room.
        frame = encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6)
        per_second = len(frame) * DEFAULT_FRAME_RATE
        assert per_second < 32_000, f"{per_second / 1024:.1f} kB/s is more than intended"

    def test_encoding_is_fast_enough_to_be_irrelevant(self) -> None:
        spectrum = levels()
        started = time.perf_counter()
        for _ in range(100):
            encode_frame(spectrum, center_freq_hz=98e6, span_hz=2.4e6)
        each = (time.perf_counter() - started) / 100
        assert each < ENCODE_BUDGET_S, f"encoding took {each * 1e6:.0f} µs per frame"

    def test_decoding_is_fast_enough_to_be_irrelevant(self) -> None:
        frame = encode_frame(levels(), center_freq_hz=98e6, span_hz=2.4e6)
        started = time.perf_counter()
        for _ in range(100):
            decode_frame(frame)
        each = (time.perf_counter() - started) / 100
        assert each < ENCODE_BUDGET_S, f"decoding took {each * 1e6:.0f} µs per frame"


class TestFeedThroughput:
    def test_frames_are_produced_fast_enough_for_the_intended_rate(self) -> None:
        # The measurement that matters: can the receiver be read and a spectrum taken inside
        # the time between frames? If not, the display falls behind whatever it is told to aim
        # for, and a waterfall showing the past is worse than one showing less.
        streamer = SpectrumStreamer(device_factory=demo_receiver, frame_rate=1000.0)

        started = time.perf_counter()
        produced = sum(1 for _ in streamer.frames(98e6, max_frames=SAMPLE_FRAMES))
        elapsed = time.perf_counter() - started

        assert produced == SAMPLE_FRAMES
        each = elapsed / produced
        assert each < FRAME_BUDGET_S, (
            f"a frame took {each * 1000:.1f} ms, and the budget is {FRAME_BUDGET_S * 1000:.0f} ms"
        )

    def test_the_feed_paces_itself_rather_than_running_flat_out(self) -> None:
        # A display cannot use more than its refresh rate, and the spare time is better left to
        # whatever else is running -- a scan, or the audio the same receiver is feeding.
        streamer = SpectrumStreamer(device_factory=demo_receiver, frame_rate=10.0)

        started = time.perf_counter()
        list(streamer.frames(98e6, max_frames=5))
        elapsed = time.perf_counter() - started

        # Five frames at ten a second cannot arrive in much less than four intervals.
        assert elapsed > 0.3, f"five frames took {elapsed:.2f} s, which is faster than paced"

    def test_a_narrower_frame_is_not_slower(self) -> None:
        # Averaging down to fewer bins should cost less, not more. A regression here would mean
        # the averaging had become a per-bin loop.
        wide = SpectrumStreamer(device_factory=demo_receiver, bins=1024, frame_rate=1000.0)
        narrow = SpectrumStreamer(device_factory=demo_receiver, bins=128, frame_rate=1000.0)

        def cost(streamer: SpectrumStreamer) -> float:
            started = time.perf_counter()
            list(streamer.frames(98e6, max_frames=10))
            return (time.perf_counter() - started) / 10

        assert cost(narrow) < cost(wide) * 1.5


class TestLatency:
    def test_a_frame_describes_a_recent_moment(self) -> None:
        # The timestamp is what a client uses to tell a stalled feed from a quiet band, so it
        # has to be the time the frame was made rather than the time it was asked for.
        streamer = SpectrumStreamer(device_factory=demo_receiver, frame_rate=1000.0)
        before = time.time() * 1000
        frame = decode_frame(next(iter(streamer.frames(98e6, max_frames=1))))
        after = time.time() * 1000

        assert before - 1 <= frame.timestamp_ms <= after + 1

    def test_the_samples_behind_a_frame_are_a_short_window(self) -> None:
        # A frame averages over the samples it read. Too few and the noise floor shimmers;
        # too many and the display shows an average of the last second rather than now.
        from openwave.api.spectrum import DEFAULT_SAMPLES_PER_FRAME

        window_s = DEFAULT_SAMPLES_PER_FRAME / 2_400_000
        assert 0.005 < window_s < 0.1, f"a frame averages over {window_s * 1000:.0f} ms"
