"""Tests for the Station model and the second pass that identifies stations.

The scan itself is tested in tests/core. What is checked here is the part that only a
demodulator can establish -- whether a station is stereo -- and the honesty of the result: a
station that could not be examined must say so rather than claim to be mono.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openwave.core.scanner import ScanSettings
from openwave.core.signal_detector import ChannelMeasurement
from openwave.radio.demo import DEMO_FM_STATIONS, demo_receiver
from openwave.radio.station import Station
from openwave.radio.station_detector import (
    IdentifySettings,
    examine_station,
    identify_stations,
    scan_fm,
    station_from_measurement,
)
from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.device import TuningRange
from openwave.sdr.mock import MockSdrDevice

QUICK = ScanSettings(samples_per_segment=1 << 14, settling_samples=0, fft_size=2048)
QUICK_IDENTIFY = IdentifySettings(samples=1 << 18, settling_samples=0)


def measurement(freq_hz: float = 98e6, snr_db: float = 40.0) -> ChannelMeasurement:
    return ChannelMeasurement(
        freq_hz=freq_hz,
        power_dbfs=-80.0 + snr_db,
        noise_dbfs=-80.0,
        bandwidth_hz=200_000.0,
    )


class TestStationModel:
    def test_it_reports_megahertz_because_that_is_how_fm_is_spoken(self) -> None:
        station = Station(freq_hz=98_500_000.0, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3)
        assert station.freq_mhz == pytest.approx(98.5)

    def test_it_is_immutable(self) -> None:
        station = Station(freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3)
        with pytest.raises(ValidationError):
            station.freq_hz = 99e6  # type: ignore[misc]

    @pytest.mark.parametrize("freq_hz", [0.0, -98e6])
    def test_a_non_positive_frequency_is_refused(self, freq_hz: float) -> None:
        with pytest.raises(ValidationError):
            Station(freq_hz=freq_hz, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3)

    def test_an_rds_name_of_nothing_but_padding_reads_as_no_name(self) -> None:
        # RDS pads names to eight characters with spaces, and a transmitter sending only
        # padding should not appear as a station called "        ".
        station = Station(
            freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3, name="        "
        )
        assert station.name is None

    def test_an_rds_name_is_trimmed(self) -> None:
        station = Station(
            freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3, name=" CITY FM "
        )
        assert station.name == "CITY FM"

    def test_unchecked_stereo_is_not_the_same_as_mono(self) -> None:
        # A sweep measures power only. Reporting an unexamined station as mono would be a
        # claim the scan never made.
        unchecked = Station(freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3)
        assert unchecked.stereo is None
        assert not unchecked.is_stereo

    def test_the_label_falls_back_to_the_frequency(self) -> None:
        station = Station(freq_hz=98_500_000.0, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3)
        assert station.label == "98.5 MHz"
        named = station.model_copy(update={"name": "CITY FM"})
        assert named.label == "CITY FM"

    def test_it_reads_sensibly_when_printed(self) -> None:
        station = Station(
            freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3, stereo=True
        )
        printed = str(station)
        assert "98.000 MHz" in printed
        assert "stereo" in printed

    def test_it_serialises_for_the_api(self) -> None:
        # The same model the API returns in v0.5 and the Angular client is generated from.
        station = Station(
            freq_hz=98e6, power_dbfs=-25.0, snr_db=40.0, bandwidth_hz=200e3, stereo=True
        )
        payload = station.model_dump()
        assert payload["freq_hz"] == 98e6
        assert payload["stereo"] is True
        assert payload["name"] is None


class TestStationFromMeasurement:
    def test_it_carries_the_measurement_across(self) -> None:
        station = station_from_measurement(measurement(98.6e6, 35.0))
        assert station.freq_hz == 98.6e6
        assert station.snr_db == pytest.approx(35.0)
        assert station.bandwidth_hz == 200e3

    def test_stereo_is_left_unknown_unless_given(self) -> None:
        assert station_from_measurement(measurement()).stereo is None
        assert station_from_measurement(measurement(), stereo=False).stereo is False


class TestExamineStation:
    def test_a_stereo_transmission_is_recognised(self) -> None:
        device = MockSdrDevice(
            sources=[
                SyntheticFmStation(freq_hz=98e6, power_dbfs=-20.0, stereo=True, right_tone_hz=400.0)
            ],
            noise_floor_dbfs=-80.0,
        )
        with device:
            device.set_sample_rate(2.4e6)
            station = examine_station(device, measurement(98e6), settings=QUICK_IDENTIFY)
        assert station.stereo is True
        assert station.pilot_level_db is not None
        assert station.pilot_level_db > 30.0

    def test_a_mono_transmission_is_recognised(self) -> None:
        device = MockSdrDevice(
            sources=[SyntheticFmStation(freq_hz=98e6, power_dbfs=-20.0)],
            noise_floor_dbfs=-80.0,
        )
        with device:
            device.set_sample_rate(2.4e6)
            station = examine_station(device, measurement(98e6), settings=QUICK_IDENTIFY)
        assert station.stereo is False

    def test_the_station_is_not_placed_at_the_centre_of_the_window(self) -> None:
        # A direct-conversion tuner leaves a spike of its own at the exact centre of its
        # window, so tuning a station there lands it on the one spot the receiver measures
        # worst. Real receivers offset-tune, and so does this.
        device = MockSdrDevice(
            sources=[SyntheticFmStation(freq_hz=98e6, power_dbfs=-20.0)],
            noise_floor_dbfs=-80.0,
        )
        with device:
            device.set_sample_rate(2.4e6)
            examine_station(device, measurement(98e6), settings=QUICK_IDENTIFY)
            assert device.center_freq_hz != 98e6
            assert abs(device.center_freq_hz - 98e6) == pytest.approx(250e3)

    def test_it_offsets_the_other_way_near_the_edge_of_the_tuning_range(self) -> None:
        device = MockSdrDevice(
            sources=[SyntheticFmStation(freq_hz=98e6, power_dbfs=-20.0)],
            tuning_range=TuningRange(min_hz=98e6, max_hz=110e6),
            noise_floor_dbfs=-80.0,
        )
        with device:
            device.set_sample_rate(2.4e6)
            station = examine_station(device, measurement(98e6), settings=QUICK_IDENTIFY)
            assert device.center_freq_hz == pytest.approx(98e6 + 250e3)
        assert station.stereo is not None

    def test_a_station_that_cannot_be_reached_is_reported_with_stereo_unknown(self) -> None:
        # Giving up on the whole scan because one station in thirty could not be examined
        # would be worse than reporting thirty stations and being honest about one unknown.
        device = MockSdrDevice(tuning_range=TuningRange(min_hz=98e6, max_hz=98e6))
        with device:
            device.set_sample_rate(2.4e6)
            station = examine_station(device, measurement(98e6), settings=QUICK_IDENTIFY)
        assert station.stereo is None
        assert station.freq_hz == 98e6

    def test_too_few_samples_to_judge_leaves_stereo_unknown(self) -> None:
        device = MockSdrDevice(
            sources=[SyntheticFmStation(freq_hz=98e6, power_dbfs=-20.0)],
            noise_floor_dbfs=-80.0,
        )
        with device:
            device.set_sample_rate(2.4e6)
            station = examine_station(
                device,
                measurement(98e6),
                settings=IdentifySettings(samples=2048, settling_samples=0),
            )
        assert station.stereo is None


class TestIdentifySettingsValidation:
    def test_a_non_positive_sample_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="samples must be positive"):
            IdentifySettings(samples=0)

    def test_negative_settling_is_refused(self) -> None:
        with pytest.raises(ValueError, match="settling_samples"):
            IdentifySettings(settling_samples=-1)


class TestIdentifyStations:
    def test_results_come_back_in_frequency_order(self) -> None:
        device = demo_receiver()
        detections = [measurement(101.7e6), measurement(88.1e6), measurement(98.0e6)]
        with device:
            device.set_sample_rate(2.4e6)
            stations = identify_stations(device, detections, settings=QUICK_IDENTIFY)
        assert [s.freq_mhz for s in stations] == [88.1, 98.0, 101.7]

    def test_progress_is_reported_once_per_station(self) -> None:
        device = demo_receiver()
        detections = [measurement(88.1e6), measurement(98.0e6)]
        seen: list[tuple[int, int, float]] = []
        with device:
            device.set_sample_rate(2.4e6)
            identify_stations(
                device,
                detections,
                settings=QUICK_IDENTIFY,
                progress=lambda i, total, hz: seen.append((i, total, hz)),
            )
        assert seen == [(0, 2, 88.1e6), (1, 2, 98.0e6)]

    def test_no_detections_gives_no_stations(self) -> None:
        device = demo_receiver()
        with device:
            device.set_sample_rate(2.4e6)
            assert identify_stations(device, [], settings=QUICK_IDENTIFY) == ()


class TestScanFm:
    def test_the_demonstration_band_is_recovered_exactly(self) -> None:
        # The whole v0.1 feature, end to end: every station found at the right frequency, with
        # the right stereo flag, and nothing invented.
        result = scan_fm(demo_receiver(), scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        expected = {round(station.freq_hz): station.stereo for station in DEMO_FM_STATIONS}
        found = {round(station.freq_hz): station.stereo for station in result.stations}
        assert found == expected

    def test_a_weak_station_beside_a_strong_one_is_not_lost(self) -> None:
        # 98.6 MHz is 600 kHz from 98.0 MHz and 23 dB weaker, which is the arrangement that
        # catches a scanner reporting its neighbour's spill instead of the station itself.
        result = scan_fm(demo_receiver(), scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        frequencies = [station.freq_mhz for station in result.stations]
        assert 98.0 in frequencies
        assert 98.6 in frequencies

    def test_skipping_identification_leaves_stereo_unknown(self) -> None:
        result = scan_fm(demo_receiver(), scan_settings=QUICK, identify=False)
        assert result.stations
        assert all(station.stereo is None for station in result.stations)

    def test_it_counts_the_stereo_stations(self) -> None:
        result = scan_fm(demo_receiver(), scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        expected_stereo = sum(1 for station in DEMO_FM_STATIONS if station.stereo)
        assert result.stereo_count == expected_stereo
        assert result.station_count == len(DEMO_FM_STATIONS)

    def test_it_leaves_the_device_as_it_found_it(self) -> None:
        device = demo_receiver()
        assert not device.is_open
        scan_fm(device, scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        assert not device.is_open

    def test_an_empty_band_gives_no_stations(self) -> None:
        empty = MockSdrDevice(noise_floor_dbfs=-70.0)
        result = scan_fm(empty, scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        assert result.stations == ()
        assert result.station_count == 0

    def test_it_summarises_itself_for_a_terminal(self) -> None:
        result = scan_fm(demo_receiver(), scan_settings=QUICK, identify_settings=QUICK_IDENTIFY)
        summary = str(result)
        assert "8 stations" in summary
        assert "in stereo" in summary

    def test_both_progress_callbacks_are_used(self) -> None:
        sweeps: list[int] = []
        identifies: list[int] = []
        scan_fm(
            demo_receiver(),
            scan_settings=QUICK,
            identify_settings=QUICK_IDENTIFY,
            sweep_progress=lambda i, total, segment: sweeps.append(i),
            identify_progress=lambda i, total, hz: identifies.append(i),
        )
        assert len(sweeps) > 1
        assert len(identifies) == len(DEMO_FM_STATIONS)
