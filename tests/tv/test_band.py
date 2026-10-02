"""Tests for the television channel plans.

The grid formulae are checked against frequencies that can be looked up independently, because a
plan that is off by one channel puts every transmitter in the wrong place and nothing else in the
codebase would notice.
"""

from __future__ import annotations

import pytest

from openwave.tv.band import (
    TELEVISION_PLANS,
    UHF_PLAN,
    UHF_PLAN_EXTENDED,
    VHF_BAND_III_PLAN,
    TelevisionChannelPlan,
)


class TestKnownFrequencies:
    @pytest.mark.parametrize(
        ("channel", "expected_mhz"),
        [(21, 474.0), (22, 482.0), (24, 498.0), (30, 546.0), (48, 690.0)],
    )
    def test_uhf_channels_are_where_they_should_be(self, channel: int, expected_mhz: float) -> None:
        assert UHF_PLAN.frequency_of(channel) == pytest.approx(expected_mhz * 1e6)

    @pytest.mark.parametrize(("channel", "expected_mhz"), [(5, 177.5), (6, 184.5), (12, 226.5)])
    def test_vhf_channels_are_where_they_should_be(self, channel: int, expected_mhz: float) -> None:
        assert VHF_BAND_III_PLAN.frequency_of(channel) == pytest.approx(expected_mhz * 1e6)

    def test_uhf_channels_are_eight_megahertz_apart(self) -> None:
        assert UHF_PLAN.spacing_hz == 8e6
        assert UHF_PLAN.bandwidth_hz == 8e6

    def test_vhf_channels_are_seven_megahertz_apart(self) -> None:
        assert VHF_BAND_III_PLAN.spacing_hz == 7e6

    def test_the_default_uhf_plan_stops_below_the_mobile_bands(self) -> None:
        # Everything above 694 MHz went to mobile networks across most of the world, so a scan
        # that still swept it would spend a quarter of its time listening to telephones.
        assert UHF_PLAN.frequency_of(UHF_PLAN.last_channel) <= 694e6

    def test_the_extended_plan_reaches_the_old_upper_channels(self) -> None:
        assert UHF_PLAN_EXTENDED.last_channel == 69
        assert UHF_PLAN_EXTENDED.frequency_of(69) == pytest.approx(858e6)


class TestChannelLookup:
    def test_a_frequency_on_the_grid_maps_back_to_its_channel(self) -> None:
        assert UHF_PLAN.channel_of(498e6) == 24

    def test_a_frequency_slightly_off_still_maps(self) -> None:
        # A tuner reports where it is tuned, not where the grid says, so a little slack is
        # needed or a found multiplex would lose its channel number.
        assert UHF_PLAN.channel_of(498.5e6) == 24

    def test_a_frequency_nowhere_near_the_grid_gives_nothing(self) -> None:
        # Reporting the nearest channel anyway would mislabel a transmitter.
        assert UHF_PLAN.channel_of(100e6) is None

    def test_a_frequency_below_the_plan_gives_nothing(self) -> None:
        # Channel 21 is the lowest, at 474 MHz, so anything a channel below it is off the plan.
        assert UHF_PLAN.channel_of(466e6) is None

    def test_a_frequency_above_the_plan_gives_nothing(self) -> None:
        assert UHF_PLAN.channel_of(700e6) is None

    def test_every_frequency_in_the_plan_maps_back(self) -> None:
        for channel in UHF_PLAN.channels:
            assert UHF_PLAN.channel_of(UHF_PLAN.frequency_of(channel)) == channel


class TestPlanShape:
    def test_the_channel_count_matches_the_range(self) -> None:
        assert UHF_PLAN.channel_count == 28
        assert VHF_BAND_III_PLAN.channel_count == 8

    def test_frequencies_come_back_in_order(self) -> None:
        frequencies = UHF_PLAN.frequencies()
        assert len(frequencies) == UHF_PLAN.channel_count
        assert list(frequencies) == sorted(frequencies)

    def test_a_channel_outside_the_plan_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not in the UHF plan"):
            UHF_PLAN.frequency_of(20)

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="inverted"):
            TelevisionChannelPlan(
                name="bad", first_channel=48, last_channel=21, offset_hz=306e6, spacing_hz=8e6
            )

    def test_a_non_positive_spacing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="spacing must be positive"):
            TelevisionChannelPlan(
                name="bad", first_channel=21, last_channel=48, offset_hz=306e6, spacing_hz=0.0
            )

    def test_it_converts_to_the_generic_band_plan(self) -> None:
        plan = UHF_PLAN.as_band_plan()
        assert plan.start_hz == UHF_PLAN.frequency_of(21)
        assert plan.end_hz == UHF_PLAN.frequency_of(48)
        assert plan.channel_count == UHF_PLAN.channel_count

    def test_it_reads_sensibly_when_printed(self) -> None:
        printed = str(UHF_PLAN)
        assert "21-48" in printed
        assert "474.000 MHz" in printed


class TestPlanRegistry:
    def test_every_named_plan_can_be_looked_up(self) -> None:
        for name, plan in TELEVISION_PLANS.items():
            assert isinstance(plan, TelevisionChannelPlan), name

    def test_the_default_names_are_present(self) -> None:
        assert set(TELEVISION_PLANS) >= {"uhf", "vhf"}
