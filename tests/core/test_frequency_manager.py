"""Tests for band plans and sweep planning.

The invariant that matters is coverage: every channel in a band must be measured by exactly one
window, and must sit far enough inside that window's trustworthy part for its whole bandwidth to
be measurable. A planner that is subtly wrong here does not fail loudly -- it quietly misses a
station every few megahertz.
"""

from __future__ import annotations

import re

import pytest

from openwave.core.frequency_manager import (
    BandPlan,
    Segment,
    plan_segments,
    total_channels,
)
from openwave.radio.band import FM_BAND_PLAN

#: A small band, so a test can list what it expects.
TINY_PLAN = BandPlan(
    name="tiny",
    start_hz=100_000_000.0,
    end_hz=100_000_000.0 + 4 * 100_000.0,
    channel_spacing_hz=100_000.0,
    channel_bandwidth_hz=200_000.0,
)


class TestBandPlanValidation:
    def test_a_band_must_start_above_zero(self) -> None:
        with pytest.raises(ValueError, match="must start above zero"):
            BandPlan(
                name="bad",
                start_hz=0.0,
                end_hz=1e6,
                channel_spacing_hz=1e5,
                channel_bandwidth_hz=2e5,
            )

    def test_an_inverted_band_is_refused(self) -> None:
        with pytest.raises(ValueError, match="inverted"):
            BandPlan(
                name="bad",
                start_hz=108e6,
                end_hz=87.5e6,
                channel_spacing_hz=1e5,
                channel_bandwidth_hz=2e5,
            )

    @pytest.mark.parametrize("spacing", [0.0, -1e5])
    def test_a_non_positive_spacing_is_refused(self, spacing: float) -> None:
        with pytest.raises(ValueError, match="spacing must be positive"):
            BandPlan(
                name="bad",
                start_hz=87.5e6,
                end_hz=108e6,
                channel_spacing_hz=spacing,
                channel_bandwidth_hz=2e5,
            )

    def test_a_single_channel_band_is_allowed(self) -> None:
        plan = BandPlan(
            name="one",
            start_hz=98e6,
            end_hz=98e6,
            channel_spacing_hz=1e5,
            channel_bandwidth_hz=2e5,
        )
        assert plan.channel_count == 1
        assert list(plan.channels()) == [98e6]


class TestChannelGrid:
    def test_the_fm_band_holds_the_expected_number_of_channels(self) -> None:
        # 87.5 to 108.0 MHz on a 100 kHz grid, inclusive of both ends.
        assert FM_BAND_PLAN.channel_count == 206

    def test_the_grid_starts_and_ends_on_the_band_edges(self) -> None:
        channels = list(FM_BAND_PLAN.channels())
        assert channels[0] == pytest.approx(87.5e6)
        assert channels[-1] == pytest.approx(108.0e6)

    def test_channels_are_evenly_spaced_without_accumulated_error(self) -> None:
        # Computed from the index rather than by repeated addition, so the two hundredth
        # channel is not two hundred rounding errors away from where it belongs.
        channels = list(FM_BAND_PLAN.channels())
        for index, channel in enumerate(channels):
            assert channel == pytest.approx(87.5e6 + index * 100_000.0, abs=1e-6)

    def test_it_knows_what_it_contains(self) -> None:
        assert FM_BAND_PLAN.contains(98e6)
        assert not FM_BAND_PLAN.contains(87.4e6)
        assert not FM_BAND_PLAN.contains(108.1e6)

    @pytest.mark.parametrize(
        ("measured_hz", "expected_hz"),
        [
            (98_000_000.0, 98_000_000.0),
            (98_020_000.0, 98_000_000.0),
            (98_060_000.0, 98_100_000.0),
            (87_510_000.0, 87_500_000.0),
        ],
    )
    def test_a_measured_frequency_snaps_to_the_grid(
        self, measured_hz: float, expected_hz: float
    ) -> None:
        # A real transmitter sits on the grid; a measurement does not.
        assert FM_BAND_PLAN.nearest_channel(measured_hz) == pytest.approx(expected_hz)

    def test_snapping_outside_the_band_is_refused(self) -> None:
        with pytest.raises(ValueError, match="outside the FM broadcast band"):
            FM_BAND_PLAN.nearest_channel(70e6)

    def test_it_recognises_a_frequency_on_the_grid(self) -> None:
        assert FM_BAND_PLAN.is_on_grid(98_000_000.0)
        assert not FM_BAND_PLAN.is_on_grid(98_050_000.0)
        assert not FM_BAND_PLAN.is_on_grid(70e6)

    def test_it_reads_sensibly_when_printed(self) -> None:
        assert str(FM_BAND_PLAN) == ("FM broadcast (87.500 MHz to 108.000 MHz, 206 channels)")


class TestSegmentPlanning:
    @pytest.mark.parametrize("sample_rate_hz", [1_024_000.0, 2_048_000.0, 2_400_000.0])
    def test_every_channel_is_measured_exactly_once(self, sample_rate_hz: float) -> None:
        # The invariant the whole sweep depends on.
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=sample_rate_hz, usable_fraction=0.8)
        assigned = [channel for segment in segments for channel in segment.channels]
        assert sorted(assigned) == list(FM_BAND_PLAN.channels())
        assert len(assigned) == len(set(assigned))
        assert total_channels(segments) == FM_BAND_PLAN.channel_count

    @pytest.mark.parametrize("sample_rate_hz", [1_024_000.0, 2_400_000.0])
    def test_each_channel_fits_wholly_inside_its_window(self, sample_rate_hz: float) -> None:
        # Measuring channel power means integrating across the channel's full bandwidth, so a
        # channel straddling the edge of a window would read low and be reported as weak.
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=sample_rate_hz, usable_fraction=0.8)
        half_channel = FM_BAND_PLAN.channel_bandwidth_hz / 2.0
        for segment in segments:
            for channel in segment.channels:
                assert segment.usable_start_hz <= channel - half_channel + 1e-6
                assert channel + half_channel <= segment.usable_end_hz + 1e-6

    def test_segments_come_back_in_ascending_order(self) -> None:
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)
        centers = [segment.center_freq_hz for segment in segments]
        assert centers == sorted(centers)

    def test_a_faster_rate_needs_fewer_tuning_positions(self) -> None:
        slow = plan_segments(FM_BAND_PLAN, sample_rate_hz=1.024e6, usable_fraction=0.8)
        fast = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)
        assert len(fast) < len(slow)

    def test_each_segment_carries_the_channel_bandwidth_to_measure(self) -> None:
        # Carried rather than inferred: deriving it from the channel spacing happens to work
        # for FM, where a channel is twice its spacing, and is wrong by a factor of two for
        # DVB-T, where a channel is exactly its spacing.
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)
        for segment in segments:
            assert segment.channel_bandwidth_hz == FM_BAND_PLAN.channel_bandwidth_hz

    def test_the_usable_width_follows_the_fraction(self) -> None:
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)
        for segment in segments:
            assert segment.usable_bandwidth_hz == pytest.approx(2.4e6 * 0.8)

    def test_a_window_too_narrow_for_a_channel_is_refused_with_advice(self) -> None:
        # 200 kHz of channel in 150 kHz of usable window is impossible, and saying so beats
        # returning segments that can never measure anything.
        with pytest.raises(ValueError, match="too narrow to measure"):
            plan_segments(FM_BAND_PLAN, sample_rate_hz=187_500.0, usable_fraction=0.8)

    def test_the_refusal_says_how_fast_to_sample_instead(self) -> None:
        with pytest.raises(ValueError, match=re.escape("Sample at more than 250.000 kHz")):
            plan_segments(FM_BAND_PLAN, sample_rate_hz=187_500.0, usable_fraction=0.8)

    @pytest.mark.parametrize("fraction", [0.0, -0.5, 1.5])
    def test_a_nonsensical_usable_fraction_is_refused(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="usable fraction"):
            plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=fraction)

    def test_a_non_positive_sample_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample rate must be positive"):
            plan_segments(FM_BAND_PLAN, sample_rate_hz=0.0, usable_fraction=0.8)

    def test_a_band_narrower_than_one_window_takes_a_single_segment(self) -> None:
        segments = plan_segments(TINY_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)
        assert len(segments) == 1
        assert segments[0].channels == tuple(TINY_PLAN.channels())

    def test_a_usable_fraction_of_one_is_allowed(self) -> None:
        # A receiver that reports its whole window as trustworthy is unusual but not invalid.
        segments = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=1.0)
        assert total_channels(segments) == FM_BAND_PLAN.channel_count


class TestSegment:
    def test_it_knows_what_it_covers(self) -> None:
        segment = Segment(
            center_freq_hz=98e6,
            sample_rate_hz=2.4e6,
            channel_bandwidth_hz=200e3,
            usable_start_hz=97.04e6,
            usable_end_hz=98.96e6,
        )
        assert segment.covers(98e6)
        assert segment.covers(97.04e6)
        assert not segment.covers(99e6)

    def test_it_reports_offsets_from_its_centre(self) -> None:
        segment = Segment(
            center_freq_hz=98e6,
            sample_rate_hz=2.4e6,
            channel_bandwidth_hz=200e3,
            usable_start_hz=97.04e6,
            usable_end_hz=98.96e6,
        )
        assert segment.offset_of(98.3e6) == pytest.approx(300e3)
        assert segment.offset_of(97.5e6) == pytest.approx(-500e3)

    def test_it_reads_sensibly_when_printed(self) -> None:
        segment = plan_segments(FM_BAND_PLAN, sample_rate_hz=2.4e6, usable_fraction=0.8)[0]
        assert "MHz" in str(segment)
        assert "channels" in str(segment)
