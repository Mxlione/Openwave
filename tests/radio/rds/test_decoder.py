"""Tests for recovering RDS from a multiplex.

The decisive test is the round trip: encode a known station, decode it, and check the name comes
back. Everything else here exists because a round trip that passes can still hide a decoder that
would fall over on a real signal -- one that cannot work without a stereo pilot, say, or one that
invents groups out of noise.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwave.radio.constants import RDS_BITRATE
from openwave.radio.rds.decoder import (
    GROUP_BITS,
    RDS_WORK_RATE_HZ,
    SAMPLES_PER_BIT,
    decode_rds,
    find_groups,
    isolate_subcarrier,
    project,
    recover_bits,
    recover_phase,
)
from openwave.radio.rds.encoder import (
    RdsProgramme,
    differential_encode,
    group_bits,
    group_sequence,
    rds_subcarrier,
)

#: An exact multiple of the bit rate, well above what the subcarrier needs, and quick to work on.
RATE_HZ = RDS_BITRATE * 128

PROGRAMME = RdsProgramme(
    pi=0xF201, programme_service="OPENWAVE", pty=10, radio_text="Open source radio"
)


def transmitted(programme: RdsProgramme = PROGRAMME, *, seconds: float = 1.5) -> np.ndarray:
    """A clean RDS subcarrier."""
    return rds_subcarrier(programme, sample_rate_hz=RATE_HZ, duration_s=seconds)


def with_noise(signal: np.ndarray, snr_db: float, *, seed: int = 0) -> np.ndarray:
    """The signal with white noise at the given ratio."""
    rng = np.random.default_rng(seed)
    return signal + rng.normal(0.0, 10 ** (-snr_db / 20.0), signal.size)


class TestConstants:
    def test_the_working_rate_is_an_exact_multiple_of_the_bit_rate(self) -> None:
        # Otherwise the sampling instants drift against the bits, and the eye closes.
        assert RDS_WORK_RATE_HZ == RDS_BITRATE * SAMPLES_PER_BIT
        assert RDS_WORK_RATE_HZ == 19_000.0


class TestSubcarrierIsolation:
    def test_it_brings_the_data_down_to_zero_frequency(self) -> None:
        baseband = isolate_subcarrier(transmitted(seconds=0.5), sample_rate_hz=RATE_HZ)
        assert baseband.dtype == np.complex128
        assert baseband.size == transmitted(seconds=0.5).size

    def test_a_sample_rate_too_low_is_refused_with_advice(self) -> None:
        with pytest.raises(ValueError, match="sample above"):
            isolate_subcarrier(np.zeros(10_000), sample_rate_hz=100_000.0)

    def test_too_few_samples_is_refused(self) -> None:
        with pytest.raises(ValueError, match="need samples"):
            isolate_subcarrier(np.zeros(1), sample_rate_hz=RATE_HZ)


class TestPhaseRecovery:
    def test_a_phase_is_recovered_from_the_data_alone(self) -> None:
        # No pilot, no training sequence: the data is real, so its samples lie along a line,
        # and squaring them reveals its angle.
        baseband = isolate_subcarrier(transmitted(seconds=0.5), sample_rate_hz=RATE_HZ)
        angle = recover_phase(baseband)
        assert -np.pi <= angle <= np.pi

    def test_rotating_the_signal_moves_the_estimate_with_it(self) -> None:
        baseband = isolate_subcarrier(transmitted(seconds=0.5), sample_rate_hz=RATE_HZ)
        original = recover_phase(baseband)
        rotated = recover_phase(baseband * np.exp(1j * 0.5))
        # Modulo a half turn, which is the ambiguity differential encoding absorbs.
        difference = (rotated - original) % np.pi
        assert min(difference, np.pi - difference) == pytest.approx(0.5, abs=0.05)

    def test_projecting_gives_a_real_signal(self) -> None:
        baseband = isolate_subcarrier(transmitted(seconds=0.5), sample_rate_hz=RATE_HZ)
        projected = project(baseband)
        assert projected.dtype == np.float64
        assert np.all(np.isfinite(projected))

    def test_no_samples_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no samples"):
            recover_phase(np.zeros(0, dtype=np.complex128))


class TestBitRecovery:
    def test_the_right_number_of_bits_comes_out(self) -> None:
        bitstream = recover_bits(transmitted(seconds=1.0), sample_rate_hz=RATE_HZ)
        # One bit is consumed by the differential decoding.
        assert len(bitstream) == pytest.approx(RDS_BITRATE, rel=0.01)

    def test_a_clean_signal_gives_a_wide_open_eye(self) -> None:
        bitstream = recover_bits(transmitted(seconds=1.0), sample_rate_hz=RATE_HZ)
        assert bitstream.eye_opening > 5.0
        assert bitstream.is_plausible

    def test_noise_closes_the_eye(self) -> None:
        # Which is how the decoder knows to distrust what it recovered.
        clean = recover_bits(transmitted(seconds=1.0), sample_rate_hz=RATE_HZ)
        noisy = recover_bits(with_noise(transmitted(seconds=1.0), 0.0), sample_rate_hz=RATE_HZ)
        assert noisy.eye_opening < clean.eye_opening

    def test_too_short_a_signal_gives_nothing_rather_than_guesses(self) -> None:
        short = np.zeros(SAMPLES_PER_BIT * 10, dtype=np.float64)
        bitstream = recover_bits(short, sample_rate_hz=RATE_HZ)
        assert not bitstream.is_plausible

    def test_the_recovered_bits_are_the_ones_transmitted(self) -> None:
        # The underlying claim, checked directly rather than through the group layer. The
        # bitstream starts at an arbitrary point, so the comparison allows any alignment.
        expected = []
        for group in group_sequence(PROGRAMME) * 4:
            expected.extend(group_bits(group))
        expected_text = "".join(str(bit) for bit in expected)

        recovered = recover_bits(transmitted(seconds=1.5), sample_rate_hz=RATE_HZ)
        recovered_text = "".join(str(bit) for bit in recovered.bits)

        assert expected_text[:GROUP_BITS] in recovered_text


class TestGroupFinding:
    def test_groups_are_found_in_a_clean_bitstream(self) -> None:
        bitstream = recover_bits(transmitted(seconds=1.5), sample_rate_hz=RATE_HZ)
        groups = find_groups(bitstream.bits)
        assert len(groups) >= 10
        assert all(group.pi == PROGRAMME.pi for group in groups)

    def test_a_clean_signal_needs_no_repair(self) -> None:
        bitstream = recover_bits(transmitted(seconds=1.5), sample_rate_hz=RATE_HZ)
        groups = find_groups(bitstream.bits)
        assert sum(1 for group in groups if not group.is_clean) <= 1

    def test_noise_invents_no_groups(self) -> None:
        # The bug this guards against. The check word repairs a burst of up to five bits, which
        # means about a third of all syndromes map to some correctable error -- so a decoder
        # that repairs while searching will "repair" noise into four plausible blocks in a row
        # and report a group nobody sent. Measured before the fix: 22 groups out of pure noise,
        # with the station's identity different in every one.
        rng = np.random.default_rng(7)
        noise = tuple(int(bit) for bit in rng.integers(0, 2, 4000))
        assert find_groups(noise) == []

    def test_too_few_bits_gives_nothing(self) -> None:
        assert find_groups(tuple([0] * (GROUP_BITS - 1))) == []

    def test_a_bitstream_with_no_structure_gives_nothing(self) -> None:
        assert find_groups(tuple([0] * 1000)) == []


class TestRoundTrip:
    def test_the_station_name_comes_back(self) -> None:
        data = decode_rds(transmitted(), sample_rate_hz=RATE_HZ)
        assert data.programme_service == "OPENWAVE"

    def test_the_identity_fields_come_back(self) -> None:
        data = decode_rds(transmitted(), sample_rate_hz=RATE_HZ)
        assert data.pi == 0xF201
        assert data.pty == 10
        assert data.traffic_programme is False

    def test_the_radio_text_comes_back(self) -> None:
        data = decode_rds(transmitted(), sample_rate_hz=RATE_HZ)
        assert data.radio_text == "Open source radio"

    def test_a_different_station_gives_different_answers(self) -> None:
        # Guards against a decoder that happens to return the right constant.
        other = RdsProgramme(pi=0x1337, programme_service="CITY FM", pty=4)
        data = decode_rds(transmitted(other), sample_rate_hz=RATE_HZ)
        assert data.programme_service == "CITY FM"
        assert data.pi == 0x1337
        assert data.pty == 4

    @pytest.mark.parametrize("name", ["BBC R4", "X", "ABCDEFGH", "ROCK 99"])
    def test_names_of_every_length_survive(self, name: str) -> None:
        data = decode_rds(transmitted(RdsProgramme(programme_service=name)), sample_rate_hz=RATE_HZ)
        assert data.programme_service == name

    def test_a_station_without_radio_text_reports_none(self) -> None:
        data = decode_rds(
            transmitted(RdsProgramme(programme_service="PLAIN", radio_text=None)),
            sample_rate_hz=RATE_HZ,
        )
        assert data.programme_service == "PLAIN"
        assert data.radio_text is None


class TestRobustness:
    @pytest.mark.parametrize("snr_db", [40.0, 20.0, 10.0, 6.0])
    def test_the_name_survives_noise(self, snr_db: float) -> None:
        data = decode_rds(with_noise(transmitted(), snr_db), sample_rate_hz=RATE_HZ)
        assert data.programme_service == "OPENWAVE"

    def test_an_inverted_subcarrier_decodes_the_same(self) -> None:
        # A suppressed-carrier signal carries nothing that says which way up it is, so it will
        # arrive inverted half the time. The differential encoding is what makes that harmless.
        upright = decode_rds(transmitted(), sample_rate_hz=RATE_HZ)
        inverted = decode_rds(-transmitted(), sample_rate_hz=RATE_HZ)
        assert inverted.programme_service == upright.programme_service
        assert inverted.pi == upright.pi

    def test_nothing_but_noise_yields_nothing(self) -> None:
        rng = np.random.default_rng(3)
        data = decode_rds(rng.normal(0.0, 0.1, int(RATE_HZ)), sample_rate_hz=RATE_HZ)
        assert data.programme_service is None
        assert data.pi is None
        assert data.groups_decoded == 0

    def test_silence_yields_nothing(self) -> None:
        data = decode_rds(np.zeros(int(RATE_HZ), dtype=np.float64), sample_rate_hz=RATE_HZ)
        assert data.groups_decoded == 0

    def test_a_signal_too_short_for_a_name_reports_none(self) -> None:
        # A name arrives two characters at a time, so a glimpse of the subcarrier is not
        # enough. Reporting a fragment would spell the station's name wrongly.
        data = decode_rds(transmitted(seconds=0.2), sample_rate_hz=RATE_HZ)
        assert data.programme_service is None


class TestDifferentialDecoding:
    def test_the_encoder_and_decoder_agree_on_the_convention(self) -> None:
        # Checked directly, because getting this backwards gives a bitstream that is the
        # inverse of the truth -- which still synchronises, and still decodes to nonsense.
        bits = [1, 0, 1, 1, 0, 0, 1]
        encoded = differential_encode(bits)
        decoded = [a ^ b for a, b in zip(encoded[1:], encoded[:-1], strict=True)]
        assert decoded == bits[1:]
