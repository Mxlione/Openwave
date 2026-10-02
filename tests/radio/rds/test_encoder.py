"""Tests for the synthetic RDS transmitter.

The decoder is tested against this encoder, so the encoder has to be right independently --
otherwise the two could agree on something the standard never said. These tests check it against
the specification: the group layouts, the differential encoding, and the shape of the spectrum it
produces.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal as scipy_signal

from openwave.radio.constants import RDS_BITRATE, RDS_SUBCARRIER_HZ
from openwave.radio.rds.blocks import BLOCK_BITS, BlockOffset, syndrome
from openwave.radio.rds.encoder import (
    GROUP_BITS,
    RdsProgramme,
    biphase_symbols,
    differential_encode,
    group_bits,
    group_sequence,
    programme_service_groups,
    radio_text_groups,
    rds_baseband_period,
    rds_subcarrier,
)
from openwave.radio.rds.groups import RADIOTEXT_LENGTH

#: A rate that is an exact multiple of the bit rate and well above what the subcarrier needs.
RATE_HZ = RDS_BITRATE * 128


class TestProgrammeValidation:
    def test_a_programme_identification_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 16 bits"):
            RdsProgramme(pi=0x1_0000)

    def test_a_programme_type_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 5 bits"):
            RdsProgramme(pty=32)

    def test_radio_text_longer_than_the_standard_allows_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most 64 characters"):
            RdsProgramme(radio_text="x" * (RADIOTEXT_LENGTH + 1))

    def test_a_name_is_padded_to_the_eight_characters_rds_sends(self) -> None:
        assert RdsProgramme(programme_service="BBC").padded_name == "BBC     "

    def test_a_name_too_long_is_truncated_to_what_rds_allows(self) -> None:
        # Eight characters is all there is room for. Truncating is what a real transmitter does.
        assert RdsProgramme(programme_service="VERY LONG NAME").padded_name == "VERY LON"


class TestProgrammeServiceGroups:
    def test_a_name_takes_four_groups_of_two_characters(self) -> None:
        groups = programme_service_groups(RdsProgramme(programme_service="OPENWAVE"))
        assert len(groups) == 4

    def test_each_group_carries_its_pair_and_says_which_pair_it_is(self) -> None:
        groups = programme_service_groups(
            RdsProgramme(pi=0xF201, programme_service="OPENWAVE", pty=10)
        )
        for segment, (pi, second, _third, fourth) in enumerate(groups):
            assert pi == 0xF201
            assert (second >> 12) & 0xF == 0, "should be group type 0"
            assert (second >> 11) & 0x1 == 0, "should be version A"
            assert (second >> 5) & 0x1F == 10, "should carry the programme type"
            assert second & 0x3 == segment, "should say which pair this is"
            expected = "OPENWAVE"[segment * 2 : segment * 2 + 2]
            assert chr(fourth >> 8) + chr(fourth & 0xFF) == expected

    def test_the_traffic_programme_flag_is_carried(self) -> None:
        groups = programme_service_groups(RdsProgramme(traffic_programme=True))
        assert all((second >> 10) & 0x1 for _pi, second, _third, _fourth in groups)


class TestRadioTextGroups:
    def test_a_station_without_radio_text_sends_none(self) -> None:
        assert radio_text_groups(RdsProgramme(radio_text=None)) == []

    def test_four_characters_go_in_each_group(self) -> None:
        groups = radio_text_groups(RdsProgramme(radio_text="Open source radio!!!"))
        assert len(groups) == 6  # 20 characters plus a terminator, rounded up to a multiple of 4
        for segment, (_pi, second, _third, _fourth) in enumerate(groups):
            assert (second >> 12) & 0xF == 2, "should be group type 2"
            assert second & 0xF == segment

    def test_a_short_message_is_terminated(self) -> None:
        # Otherwise a receiver waits for 64 characters that will never come, and shows the
        # message padded out with whatever was there before.
        from openwave.radio.rds.groups import RADIOTEXT_TERMINATOR

        groups = radio_text_groups(RdsProgramme(radio_text="Hi"))
        characters = []
        for _pi, _second, third, fourth in groups:
            characters.extend([third >> 8, third & 0xFF, fourth >> 8, fourth & 0xFF])
        assert RADIOTEXT_TERMINATOR in characters


class TestGroupSequence:
    def test_a_station_with_only_a_name_sends_only_name_groups(self) -> None:
        sequence = group_sequence(RdsProgramme(radio_text=None))
        assert len(sequence) == 4
        assert all((second >> 12) & 0xF == 0 for _pi, second, _t, _f in sequence)

    def test_name_and_text_groups_are_interleaved(self) -> None:
        # A real station sends its name often, because that is what a listener sees.
        sequence = group_sequence(RdsProgramme(radio_text="Open source radio"))
        types = [(second >> 12) & 0xF for _pi, second, _t, _f in sequence]
        assert 0 in types
        assert 2 in types
        assert types[0] == 0

    def test_every_group_in_the_sequence_appears(self) -> None:
        programme = RdsProgramme(radio_text="Open source radio")
        sequence = group_sequence(programme)
        expected = len(programme_service_groups(programme)) + len(radio_text_groups(programme))
        assert len(sequence) == expected


class TestGroupBits:
    def test_a_group_is_a_hundred_and_four_bits(self) -> None:
        bits = group_bits((0x1234, 0x0140, 0xE0E0, 0x4F50))
        assert len(bits) == GROUP_BITS == 104

    def test_each_block_carries_the_offset_its_position_requires(self) -> None:
        # Which is what lets a receiver find the block boundaries at all.
        bits = group_bits((0x1234, 0x0140, 0xE0E0, 0x4F50))
        expected = (BlockOffset.A, BlockOffset.B, BlockOffset.C, BlockOffset.D)
        for index, offset in enumerate(expected):
            block = int(
                "".join(str(b) for b in bits[index * BLOCK_BITS : (index + 1) * BLOCK_BITS]), 2
            )
            assert syndrome(block) == int(offset)

    def test_a_version_b_group_uses_the_prime_offset(self) -> None:
        second = 0x0140 | (1 << 11)
        bits = group_bits((0x1234, second, 0x1234, 0x4F50))
        third = int("".join(str(b) for b in bits[2 * BLOCK_BITS : 3 * BLOCK_BITS]), 2)
        assert syndrome(third) == int(BlockOffset.C_PRIME)

    def test_the_wrong_number_of_words_is_refused(self) -> None:
        with pytest.raises(ValueError, match="needs 4 information words"):
            group_bits((0x1234, 0x0140))


class TestDifferentialEncoding:
    def test_each_bit_is_sent_as_a_change(self) -> None:
        assert differential_encode([1, 0, 0, 1]) == [1, 1, 1, 0]

    def test_it_reverses_by_comparing_consecutive_bits(self) -> None:
        # The property that makes the decoder immune to the subcarrier arriving upside down,
        # which it will, half the time.
        original = [1, 0, 1, 1, 0, 0, 1, 0]
        encoded = differential_encode(original)
        recovered = [a ^ b for a, b in zip(encoded[1:], encoded[:-1], strict=True)]
        assert recovered == original[1:]

    def test_starting_from_the_opposite_state_gives_the_inverse(self) -> None:
        bits = [1, 0, 1, 1, 0]
        normal = differential_encode(bits, initial=0)
        inverted = differential_encode(bits, initial=1)
        assert all(a != b for a, b in zip(normal, inverted, strict=True))


class TestBiphaseSymbols:
    def test_a_symbol_rises_then_falls_within_one_bit(self) -> None:
        symbols = biphase_symbols([1], oversampling=8)
        assert list(symbols) == [1, 1, 1, 1, -1, -1, -1, -1]

    def test_a_zero_is_the_other_way_round(self) -> None:
        symbols = biphase_symbols([0], oversampling=8)
        assert list(symbols) == [-1, -1, -1, -1, 1, 1, 1, 1]

    def test_every_symbol_averages_to_zero(self) -> None:
        # Which is what keeps the modulated signal from putting energy at the subcarrier
        # frequency, where it would sit right beside a multiplex that is already full.
        symbols = biphase_symbols([1, 0, 1, 1, 0, 0], oversampling=8)
        assert float(np.mean(symbols)) == pytest.approx(0.0)

    @pytest.mark.parametrize("oversampling", [0, 1, 3, 7])
    def test_an_oversampling_that_cannot_hold_a_symbol_is_refused(self, oversampling: int) -> None:
        with pytest.raises(ValueError, match="even number of at least 2"):
            biphase_symbols([1], oversampling=oversampling)


class TestSubcarrier:
    def test_it_produces_the_requested_duration(self) -> None:
        signal = rds_subcarrier(RdsProgramme(), sample_rate_hz=RATE_HZ, duration_s=0.5)
        assert signal.size == round(0.5 * RATE_HZ)

    def test_it_is_scaled_to_full_scale(self) -> None:
        signal = rds_subcarrier(RdsProgramme(), sample_rate_hz=RATE_HZ, duration_s=0.3)
        assert float(np.max(np.abs(signal))) == pytest.approx(1.0)

    def test_the_energy_sits_in_the_band_rds_is_allocated(self) -> None:
        signal = rds_subcarrier(RdsProgramme(), sample_rate_hz=RATE_HZ, duration_s=1.0)
        freqs, psd = scipy_signal.welch(signal, fs=RATE_HZ, nperseg=4096)
        in_band = np.abs(freqs - RDS_SUBCARRIER_HZ) <= 2_400.0
        assert float(np.sum(psd[in_band]) / np.sum(psd)) > 0.9

    def test_the_carrier_itself_is_suppressed(self) -> None:
        # Biphase coding puts a null where the carrier would be, which is the point of using
        # it: 57 kHz is close to a multiplex that already occupies everything below it.
        signal = rds_subcarrier(RdsProgramme(), sample_rate_hz=RATE_HZ, duration_s=2.0)
        freqs, psd = scipy_signal.welch(signal, fs=RATE_HZ, nperseg=1 << 15)

        def level_at(target_hz: float) -> float:
            return float(psd[int(np.argmin(np.abs(freqs - target_hz)))])

        carrier = level_at(RDS_SUBCARRIER_HZ)
        sideband = level_at(RDS_SUBCARRIER_HZ + RDS_BITRATE)
        assert 10 * np.log10(sideband / carrier) > 20.0

    def test_a_sample_rate_too_low_for_the_subcarrier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot carry"):
            rds_subcarrier(RdsProgramme(), sample_rate_hz=100_000.0, duration_s=0.5)

    def test_a_non_positive_duration_is_refused(self) -> None:
        with pytest.raises(ValueError, match="duration must be positive"):
            rds_subcarrier(RdsProgramme(), sample_rate_hz=RATE_HZ, duration_s=0.0)


class TestBasebandPeriod:
    def test_it_covers_the_whole_group_sequence_exactly_once(self) -> None:
        programme = RdsProgramme(radio_text="Open source radio")
        baseband = rds_baseband_period(programme, sample_rate_hz=RATE_HZ)
        expected = len(group_sequence(programme)) * GROUP_BITS / RDS_BITRATE
        assert baseband.period_samples == pytest.approx(expected * RATE_HZ, rel=1e-3)

    def test_the_integral_is_exact_beyond_the_first_period(self) -> None:
        # The property that keeps the FM synthesiser exactly reproducible: the same sample
        # index must always give the same phase, however the reads are chunked.
        baseband = rds_baseband_period(RdsProgramme(), sample_rate_hz=RATE_HZ)
        period = baseband.period_samples
        indices = np.array([5, period + 5, 2 * period + 5], dtype=np.int64)
        phases = baseband.phase_at(indices)
        steps = np.diff(phases)
        assert float(np.std(steps)) < 1e-9

    def test_a_value_repeats_once_per_period(self) -> None:
        baseband = rds_baseband_period(RdsProgramme(), sample_rate_hz=RATE_HZ)
        period = baseband.period_samples
        indices = np.array([100, period + 100, 2 * period + 100], dtype=np.int64)
        values = baseband.value_at(indices)
        assert float(np.std(values)) == pytest.approx(0.0, abs=1e-12)
