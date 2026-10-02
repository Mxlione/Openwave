"""Tests for the decibel conversions.

These are one-line functions, but they are the functions that decide whether a signal is
reported 6 dB or 3 dB away from the truth, so the factor of two between amplitude and power is
pinned down explicitly.
"""

from __future__ import annotations

import math

import pytest

from openwave.core.units import (
    SILENCE_DBFS,
    amplitude_to_dbfs,
    db_ratio,
    dbfs_to_amplitude,
    dbfs_to_power,
    format_frequency,
    power_to_dbfs,
)


class TestAmplitudeAndDbfs:
    def test_full_scale_is_zero_dbfs(self) -> None:
        assert amplitude_to_dbfs(1.0) == 0.0
        assert dbfs_to_amplitude(0.0) == 1.0

    def test_halving_the_amplitude_costs_six_db(self) -> None:
        assert amplitude_to_dbfs(0.5) == pytest.approx(-6.0206, abs=1e-4)

    def test_twenty_db_down_is_a_tenth_of_the_amplitude(self) -> None:
        assert dbfs_to_amplitude(-20.0) == pytest.approx(0.1)

    @pytest.mark.parametrize("dbfs", [0.0, -3.0, -20.0, -47.5, -120.0])
    def test_round_trip(self, dbfs: float) -> None:
        assert amplitude_to_dbfs(dbfs_to_amplitude(dbfs)) == pytest.approx(dbfs)

    def test_silence_reports_a_finite_floor_rather_than_negative_infinity(self) -> None:
        assert amplitude_to_dbfs(0.0) == SILENCE_DBFS
        assert math.isfinite(amplitude_to_dbfs(0.0))

    def test_negative_amplitude_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            amplitude_to_dbfs(-0.5)


class TestPowerAndDbfs:
    def test_unit_power_is_zero_dbfs(self) -> None:
        assert power_to_dbfs(1.0) == 0.0
        assert dbfs_to_power(0.0) == 1.0

    def test_halving_the_power_costs_three_db(self) -> None:
        assert power_to_dbfs(0.5) == pytest.approx(-3.0103, abs=1e-4)

    def test_power_uses_half_the_factor_of_amplitude(self) -> None:
        # The whole point of having both: an amplitude of 0.1 is -20 dBFS, but a power of 0.1
        # is -10 dBFS. Confusing the two is a factor-of-two error in every level reported.
        assert amplitude_to_dbfs(0.1) == pytest.approx(-20.0)
        assert power_to_dbfs(0.1) == pytest.approx(-10.0)

    def test_amplitude_squared_is_power(self) -> None:
        amplitude = 0.25
        assert power_to_dbfs(amplitude**2) == pytest.approx(amplitude_to_dbfs(amplitude))

    @pytest.mark.parametrize("dbfs", [0.0, -10.0, -30.0, -70.0])
    def test_round_trip(self, dbfs: float) -> None:
        assert power_to_dbfs(dbfs_to_power(dbfs)) == pytest.approx(dbfs)

    def test_negative_power_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            power_to_dbfs(-1.0)


class TestDbRatio:
    def test_a_hundredfold_power_ratio_is_twenty_db(self) -> None:
        assert db_ratio(100.0, 1.0) == pytest.approx(20.0)

    def test_equal_powers_give_zero_db(self) -> None:
        assert db_ratio(4.2, 4.2) == pytest.approx(0.0)

    def test_signal_weaker_than_noise_gives_a_negative_ratio(self) -> None:
        assert db_ratio(1.0, 10.0) == pytest.approx(-10.0)

    def test_zero_denominator_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid power ratio"):
            db_ratio(1.0, 0.0)


class TestFormatFrequency:
    @pytest.mark.parametrize(
        ("freq_hz", "expected"),
        [
            (98_000_000, "98.000 MHz"),
            (87_500_000, "87.500 MHz"),
            (19_000, "19.000 kHz"),
            (1_200_000_000, "1.200 GHz"),
            (440, "440.0 Hz"),
        ],
    )
    def test_chooses_a_readable_unit(self, freq_hz: float, expected: str) -> None:
        assert format_frequency(freq_hz) == expected

    def test_an_offset_below_the_centre_keeps_its_sign(self) -> None:
        # Offsets from a centre frequency are routinely negative, and the unit is chosen from
        # the magnitude so that -200 kHz does not come out as a tiny fraction of a megahertz.
        assert format_frequency(-200_000) == "-200.000 kHz"
        assert format_frequency(-2_000_000) == "-2.000 MHz"
