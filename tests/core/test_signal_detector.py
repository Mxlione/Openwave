"""Tests for the channel-power detector.

These are the tests that decide whether OpenWave reports stations where they are. They work the
way the whole project works: inject a transmission at a known frequency and strength, then
assert the detector finds exactly that.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwave.core.signal_detector import (
    DEFAULT_NOISE_PERCENTILE,
    ChannelMeasurement,
    Spectrum,
    measure_channels,
    power_spectrum,
    select_occupied,
)
from openwave.core.units import SILENCE_DBFS, dbfs_to_power, power_to_dbfs
from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.mock import MockSdrDevice

SAMPLE_RATE_HZ = 2_400_000.0
CENTER_FREQ_HZ = 99_000_000.0
CHANNEL_BANDWIDTH_HZ = 200_000.0


def capture(
    stations: list[SyntheticFmStation],
    *,
    noise_floor_dbfs: float = -70.0,
    count: int = 1 << 17,
) -> np.ndarray:
    """Samples of a simulated band."""
    device = MockSdrDevice(sources=stations, noise_floor_dbfs=noise_floor_dbfs)
    with device:
        device.set_sample_rate(SAMPLE_RATE_HZ)
        device.set_center_freq(CENTER_FREQ_HZ)
        return device.read_samples(count)


def spectrum_of(stations: list[SyntheticFmStation], *, noise_floor_dbfs: float = -70.0) -> Spectrum:
    return power_spectrum(
        capture(stations, noise_floor_dbfs=noise_floor_dbfs),
        sample_rate_hz=SAMPLE_RATE_HZ,
        center_freq_hz=CENTER_FREQ_HZ,
    )


def measurement(freq_hz: float, snr_db: float) -> ChannelMeasurement:
    """A measurement with a chosen signal-to-noise ratio, for testing the selection logic."""
    return ChannelMeasurement(
        freq_hz=freq_hz,
        power_dbfs=-80.0 + snr_db,
        noise_dbfs=-80.0,
        bandwidth_hz=CHANNEL_BANDWIDTH_HZ,
    )


class TestSpectrumShape:
    def test_frequencies_are_absolute_not_offsets(self) -> None:
        # Absolute frequencies mean measurements from different segments of a sweep can be
        # compared without the caller tracking which window each came from.
        spectrum = spectrum_of([])
        assert spectrum.start_hz == pytest.approx(CENTER_FREQ_HZ - SAMPLE_RATE_HZ / 2, rel=1e-3)
        assert spectrum.end_hz == pytest.approx(CENTER_FREQ_HZ + SAMPLE_RATE_HZ / 2, rel=1e-3)

    def test_frequencies_are_ascending(self) -> None:
        # welch returns FFT order, with the negative frequencies last. Leaving them that way
        # would make every band-power mask wrong.
        spectrum = spectrum_of([])
        assert np.all(np.diff(spectrum.freqs_hz) > 0)

    def test_the_resolution_matches_the_fft_size(self) -> None:
        spectrum = spectrum_of([])
        assert spectrum.resolution_hz == pytest.approx(SAMPLE_RATE_HZ / 4096, rel=1e-6)

    def test_too_few_samples_for_the_fft_is_refused(self) -> None:
        with pytest.raises(ValueError, match="need at least 4096 samples"):
            power_spectrum(
                np.zeros(100, dtype=np.complex64),
                sample_rate_hz=SAMPLE_RATE_HZ,
                center_freq_hz=CENTER_FREQ_HZ,
            )

    def test_mismatched_arrays_are_refused(self) -> None:
        with pytest.raises(ValueError, match="3 frequencies but 2 values"):
            Spectrum(freqs_hz=np.zeros(3), psd=np.zeros(2))


class TestNoiseFloorEstimate:
    @pytest.mark.parametrize("noise_floor_dbfs", [-60.0, -70.0, -80.0])
    def test_it_recovers_the_noise_floor_of_an_empty_band(self, noise_floor_dbfs: float) -> None:
        spectrum = spectrum_of([], noise_floor_dbfs=noise_floor_dbfs)
        # The floor is spread across the whole window, so the density times the width is the
        # total power that went in.
        density = spectrum.noise_density()
        total = density * SAMPLE_RATE_HZ
        assert power_to_dbfs(total) == pytest.approx(noise_floor_dbfs, abs=0.6)

    def test_the_lower_quartile_survives_a_crowded_window_where_the_median_does_not(
        self,
    ) -> None:
        # The justification for the default. Eight stations every 300 kHz fill about two
        # thirds of a 2.4 MHz window, at which point the median is measuring stations.
        crowded = [
            SyntheticFmStation(freq_hz=98.0e6 + index * 300e3, power_dbfs=-25.0)
            for index in range(8)
        ]
        spectrum = spectrum_of(crowded, noise_floor_dbfs=-70.0)
        true_density = dbfs_to_power(-70.0) / SAMPLE_RATE_HZ

        quartile_error = 10 * np.log10(spectrum.noise_density(25.0) / true_density)
        median_error = 10 * np.log10(spectrum.noise_density(50.0) / true_density)

        assert abs(quartile_error) < 2.0
        assert median_error > 30.0

    @pytest.mark.parametrize("percentile", [0.0, 100.0, -1.0])
    def test_a_percentile_outside_the_range_is_refused(self, percentile: float) -> None:
        with pytest.raises(ValueError, match="percentile must be in"):
            spectrum_of([]).noise_density(percentile)


class TestBandPower:
    def test_an_empty_channel_reads_as_the_noise_floor(self) -> None:
        spectrum = spectrum_of([], noise_floor_dbfs=-70.0)
        power = spectrum.band_power(CENTER_FREQ_HZ, CHANNEL_BANDWIDTH_HZ)
        expected = dbfs_to_power(-70.0) * CHANNEL_BANDWIDTH_HZ / SAMPLE_RATE_HZ
        assert power_to_dbfs(power) == pytest.approx(power_to_dbfs(expected), abs=0.6)

    def test_a_wider_channel_collects_more_noise(self) -> None:
        spectrum = spectrum_of([], noise_floor_dbfs=-70.0)
        narrow = spectrum.band_power(CENTER_FREQ_HZ, 100e3)
        wide = spectrum.band_power(CENTER_FREQ_HZ, 400e3)
        # Four times the width is four times the noise, which is 6 dB.
        assert 10 * np.log10(wide / narrow) == pytest.approx(6.0, abs=0.8)

    def test_a_channel_outside_the_spectrum_is_refused(self) -> None:
        with pytest.raises(ValueError, match="lies outside this spectrum"):
            spectrum_of([]).band_power(120e6, CHANNEL_BANDWIDTH_HZ)

    def test_a_non_positive_bandwidth_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bandwidth must be positive"):
            spectrum_of([]).band_power(CENTER_FREQ_HZ, 0.0)

    def test_it_knows_whether_a_whole_channel_fits(self) -> None:
        spectrum = spectrum_of([])
        assert spectrum.contains_channel(CENTER_FREQ_HZ, CHANNEL_BANDWIDTH_HZ)
        assert not spectrum.contains_channel(spectrum.end_hz, CHANNEL_BANDWIDTH_HZ)


class TestMeasureChannels:
    def test_a_station_reads_far_above_an_empty_channel(self) -> None:
        station = SyntheticFmStation(freq_hz=99.0e6, power_dbfs=-25.0)
        spectrum = spectrum_of([station])
        measured = {
            m.freq_hz: m
            for m in measure_channels(spectrum, [98.0e6, 99.0e6], bandwidth_hz=CHANNEL_BANDWIDTH_HZ)
        }
        assert measured[99.0e6].snr_db > 40.0
        assert measured[98.0e6].snr_db < 3.0

    @pytest.mark.parametrize("power_dbfs", [-20.0, -30.0, -40.0, -50.0])
    def test_the_measured_ratio_tracks_the_transmitted_power(self, power_dbfs: float) -> None:
        # The measurement has to be linear in the station's power, or a station list cannot be
        # ranked by strength. The absolute offset depends on how much of the window's noise a
        # 200 kHz channel collects, which is why this checks the slope and not the intercept.
        station = SyntheticFmStation(freq_hz=99.0e6, power_dbfs=power_dbfs)
        measured = measure_channels(
            spectrum_of([station], noise_floor_dbfs=-80.0),
            [99.0e6],
            bandwidth_hz=CHANNEL_BANDWIDTH_HZ,
        )
        # Noise of -80 dBFS over 2.4 MHz, collected in 200 kHz, is -80 - 10*log10(12).
        channel_noise_dbfs = -80.0 - 10 * np.log10(SAMPLE_RATE_HZ / CHANNEL_BANDWIDTH_HZ)
        assert measured[0].snr_db == pytest.approx(power_dbfs - channel_noise_dbfs, abs=1.5)

    def test_channels_that_do_not_fit_are_skipped_rather_than_measured_badly(self) -> None:
        # A channel straddling the edge of a window reads low, and a weak reading reported as
        # a weak station is worse than no reading at all.
        spectrum = spectrum_of([])
        measured = measure_channels(
            spectrum,
            [spectrum.start_hz, CENTER_FREQ_HZ, spectrum.end_hz],
            bandwidth_hz=CHANNEL_BANDWIDTH_HZ,
        )
        assert [m.freq_hz for m in measured] == [CENTER_FREQ_HZ]

    def test_measurements_come_back_in_the_order_the_channels_were_given(self) -> None:
        channels = [99.4e6, 98.6e6, 99.0e6]
        measured = measure_channels(spectrum_of([]), channels, bandwidth_hz=CHANNEL_BANDWIDTH_HZ)
        assert [m.freq_hz for m in measured] == channels

    def test_the_noise_is_integrated_over_the_same_bins_as_the_signal(self) -> None:
        # Deriving the noise from a bin count computed from the bandwidth, rather than from the
        # bins actually summed, puts a fraction of a dB of bias into every channel.
        spectrum = spectrum_of([], noise_floor_dbfs=-70.0)
        measured = measure_channels(spectrum, [CENTER_FREQ_HZ], bandwidth_hz=CHANNEL_BANDWIDTH_HZ)
        assert measured[0].snr_db == pytest.approx(0.0, abs=1.5)

    def test_a_non_positive_bandwidth_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bandwidth must be positive"):
            measure_channels(spectrum_of([]), [CENTER_FREQ_HZ], bandwidth_hz=-1.0)

    def test_silence_reports_a_floor_rather_than_negative_infinity(self) -> None:
        spectrum = Spectrum(
            freqs_hz=np.linspace(98e6, 99e6, 128), psd=np.zeros(128, dtype=np.float64)
        )
        measured = measure_channels(spectrum, [98.5e6], bandwidth_hz=100e3)
        assert measured[0].power_dbfs == SILENCE_DBFS


class TestSelectOccupied:
    def test_nothing_above_the_threshold_gives_nothing(self) -> None:
        quiet = [measurement(98e6, 2.0), measurement(98.1e6, 1.0)]
        assert select_occupied(quiet, threshold_db=10.0) == ()

    def test_an_isolated_station_survives(self) -> None:
        found = select_occupied([measurement(98e6, 40.0)], threshold_db=10.0)
        assert [m.freq_hz for m in found] == [98e6]

    def test_spill_into_the_neighbouring_channels_is_suppressed(self) -> None:
        # One transmitter puts three channels above the threshold. Reporting all three would
        # turn one station into three.
        spill = [
            measurement(97.9e6, 35.0),
            measurement(98.0e6, 40.0),
            measurement(98.1e6, 36.0),
        ]
        found = select_occupied(spill, threshold_db=10.0, merge_window_hz=200e3)
        assert [m.freq_hz for m in found] == [98.0e6]

    def test_a_station_is_not_lost_to_a_neighbour_that_is_itself_spill(self) -> None:
        # The bug this guards against, found by measuring against the simulator. With
        # suppression applied against every candidate rather than against accepted detections,
        # the real station at 98.0 lost to the spill channel at 98.2, which was then itself
        # dropped in favour of the real station at 98.3. One transmitter eliminated by another
        # that did not exist.
        measurements = [
            measurement(98.0e6, 52.2),  # real
            measurement(98.1e6, 49.2),  # spill
            measurement(98.2e6, 52.5),  # spill, and stronger than the real station at 98.0
            measurement(98.3e6, 55.6),  # real
            measurement(98.4e6, 52.6),  # spill
        ]
        found = select_occupied(measurements, threshold_db=10.0, merge_window_hz=200e3)
        assert [m.freq_hz for m in found] == [98.0e6, 98.3e6]

    def test_stations_further_apart_than_the_window_both_survive(self) -> None:
        apart = [measurement(98.0e6, 40.0), measurement(98.3e6, 38.0)]
        found = select_occupied(apart, threshold_db=10.0, merge_window_hz=200e3)
        assert len(found) == 2

    def test_equally_strong_neighbours_give_one_detection_not_none(self) -> None:
        tied = [measurement(98.0e6, 40.0), measurement(98.1e6, 40.0)]
        found = select_occupied(tied, threshold_db=10.0, merge_window_hz=200e3)
        assert [m.freq_hz for m in found] == [98.0e6]

    def test_the_result_does_not_depend_on_the_input_order(self) -> None:
        measurements = [
            measurement(98.0e6, 52.2),
            measurement(98.2e6, 52.5),
            measurement(98.3e6, 55.6),
        ]
        forwards = select_occupied(measurements, threshold_db=10.0, merge_window_hz=200e3)
        backwards = select_occupied(
            list(reversed(measurements)), threshold_db=10.0, merge_window_hz=200e3
        )
        assert [m.freq_hz for m in forwards] == [m.freq_hz for m in backwards]

    def test_results_come_back_in_ascending_frequency_order(self) -> None:
        scattered = [
            measurement(101.0e6, 30.0),
            measurement(98.0e6, 40.0),
            measurement(99.5e6, 35.0),
        ]
        found = select_occupied(scattered, threshold_db=10.0)
        assert [m.freq_hz for m in found] == [98.0e6, 99.5e6, 101.0e6]

    def test_the_merge_window_defaults_to_the_channel_bandwidth(self) -> None:
        spill = [measurement(98.0e6, 40.0), measurement(98.1e6, 38.0)]
        assert len(select_occupied(spill, threshold_db=10.0)) == 1

    def test_a_higher_threshold_finds_fewer_stations(self) -> None:
        mixed = [measurement(98.0e6, 40.0), measurement(99.0e6, 12.0)]
        assert len(select_occupied(mixed, threshold_db=10.0)) == 2
        assert len(select_occupied(mixed, threshold_db=20.0)) == 1


class TestEndToEnd:
    def test_a_simulated_band_is_recovered_exactly(self) -> None:
        # Six stations inside one window, spread over 30 dB of strength, including a weak one
        # 600 kHz from a strong one.
        truth = {
            98.0e6: -22.0,
            98.6e6: -45.0,
            99.0e6: -20.0,
            99.5e6: -35.0,
            99.9e6: -30.0,
        }
        stations = [SyntheticFmStation(freq_hz=hz, power_dbfs=power) for hz, power in truth.items()]
        spectrum = spectrum_of(stations, noise_floor_dbfs=-70.0)
        channels = [98.0e6 + index * 100e3 for index in range(20)]
        measured = measure_channels(
            spectrum,
            channels,
            bandwidth_hz=CHANNEL_BANDWIDTH_HZ,
            noise_percentile=DEFAULT_NOISE_PERCENTILE,
        )
        found = select_occupied(measured, threshold_db=10.0)

        assert [round(m.freq_hz) for m in found] == [round(hz) for hz in sorted(truth)]

    def test_an_empty_band_yields_nothing(self) -> None:
        spectrum = spectrum_of([], noise_floor_dbfs=-70.0)
        channels = [98.0e6 + index * 100e3 for index in range(20)]
        measured = measure_channels(spectrum, channels, bandwidth_hz=CHANNEL_BANDWIDTH_HZ)
        assert select_occupied(measured, threshold_db=10.0) == ()
