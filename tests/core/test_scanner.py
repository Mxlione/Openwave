"""Tests for the band sweep.

The sweep is what drives a receiver, so these tests check both the measurement (does it find the
stations?) and the driving (does it discard settling samples, does it leave the device as it
found it, does it cope with a receiver that cannot reach the whole band?).
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from openwave.core.frequency_manager import BandPlan
from openwave.core.scanner import (
    DEFAULT_SCAN_SAMPLE_RATE_HZ,
    ScanResult,
    ScanSettings,
    choose_sample_rate,
    scan_band,
)
from openwave.radio.band import FM_BAND_PLAN
from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.device import DeviceInfo, Gain, IqSamples, SdrDevice, TuningRange
from openwave.sdr.errors import TuningFailedError
from openwave.sdr.mock import MockSdrDevice

# A fast sweep: few samples per segment, so the tests stay quick.
QUICK = ScanSettings(samples_per_segment=1 << 14, settling_samples=0, fft_size=2048)


def device_with(*stations: SyntheticFmStation, noise_floor_dbfs: float = -70.0) -> MockSdrDevice:
    return MockSdrDevice(sources=stations, noise_floor_dbfs=noise_floor_dbfs)


class TestChooseSampleRate:
    def test_a_continuous_receiver_gets_the_default(self) -> None:
        assert choose_sample_rate(MockSdrDevice()) == DEFAULT_SCAN_SAMPLE_RATE_HZ

    def test_an_explicit_request_is_honoured(self) -> None:
        assert choose_sample_rate(MockSdrDevice(), 1.024e6) == 1.024e6

    def test_the_fastest_usable_rate_is_preferred(self) -> None:
        # Faster means fewer tuning positions, so a sweep is quicker.
        device = MockSdrDevice(supported_sample_rates_hz=(1.024e6, 2.048e6, 2.4e6))
        assert choose_sample_rate(device) == 2.4e6

    def test_rates_beyond_what_usb_sustains_are_not_chosen(self) -> None:
        # An RTL-SDR above 2.4 MS/s drops samples, which a scan reads as a raised noise floor.
        device = MockSdrDevice(supported_sample_rates_hz=(2.4e6, 2.88e6, 3.2e6))
        assert choose_sample_rate(device) == 2.4e6

    def test_a_receiver_offering_only_fast_rates_gets_its_slowest(self) -> None:
        device = MockSdrDevice(supported_sample_rates_hz=(2.88e6, 3.2e6))
        assert choose_sample_rate(device) == 2.88e6


class TestSweepMechanics:
    def test_it_covers_every_channel_in_the_band(self) -> None:
        result = scan_band(device_with(), FM_BAND_PLAN, settings=QUICK)
        assert result.channels_measured == FM_BAND_PLAN.channel_count
        assert result.is_complete
        assert result.coverage == 1.0

    def test_measurements_come_back_in_frequency_order(self) -> None:
        result = scan_band(device_with(), FM_BAND_PLAN, settings=QUICK)
        frequencies = [m.freq_hz for m in result.measurements]
        assert frequencies == sorted(frequencies)

    def test_it_opens_and_closes_a_device_it_was_given_closed(self) -> None:
        device = device_with()
        assert not device.is_open
        scan_band(device, FM_BAND_PLAN, settings=QUICK)
        assert not device.is_open

    def test_it_leaves_an_already_open_device_open(self) -> None:
        device = device_with()
        device.open()
        scan_band(device, FM_BAND_PLAN, settings=QUICK)
        assert device.is_open

    def test_it_closes_the_device_even_when_the_sweep_fails(self) -> None:
        class FailsOnRead(MockSdrDevice):
            def _do_read_samples(self, count: int) -> IqSamples:
                raise OSError("the dongle went away")

        device = FailsOnRead()
        with pytest.raises(OSError, match="went away"):
            scan_band(device, FM_BAND_PLAN, settings=QUICK)
        assert not device.is_open

    def test_progress_is_reported_once_per_segment(self) -> None:
        seen: list[tuple[int, int]] = []
        result = scan_band(
            device_with(),
            FM_BAND_PLAN,
            settings=QUICK,
            progress=lambda index, total, segment: seen.append((index, total)),
        )
        assert len(seen) == len(result.segments)
        assert [index for index, _ in seen] == list(range(len(result.segments)))
        assert {total for _, total in seen} == {len(result.segments)}

    def test_settling_samples_are_read_and_discarded(self) -> None:
        # Real hardware needs a moment after a retune: the PLL settles and the gain control
        # readjusts. Measuring those samples would read the transition, not the band.
        without = device_with()
        scan_band(without, FM_BAND_PLAN, settings=QUICK)
        baseline = without.sample_index

        with_settling = device_with()
        scan_band(
            with_settling,
            FM_BAND_PLAN,
            settings=ScanSettings(
                samples_per_segment=1 << 14, settling_samples=4096, fft_size=2048
            ),
        )
        segments = len(scan_band(device_with(), FM_BAND_PLAN, settings=QUICK).segments)
        assert with_settling.sample_index == baseline + 4096 * segments


class TestDetection:
    def test_a_simulated_band_is_recovered_exactly(self) -> None:
        truth = {88.1e6: -20.0, 98.0e6: -25.0, 101.7e6: -30.0, 107.9e6: -35.0}
        stations = [SyntheticFmStation(freq_hz=hz, power_dbfs=p) for hz, p in truth.items()]
        result = scan_band(device_with(*stations), FM_BAND_PLAN, settings=QUICK)
        assert [round(m.freq_hz) for m in result.detections] == [round(hz) for hz in sorted(truth)]

    def test_an_empty_band_yields_no_detections(self) -> None:
        result = scan_band(device_with(), FM_BAND_PLAN, settings=QUICK)
        assert result.detections == ()

    def test_detections_are_ranked_consistently_with_the_transmitted_power(self) -> None:
        stations = [
            SyntheticFmStation(freq_hz=90.0e6, power_dbfs=-40.0),
            SyntheticFmStation(freq_hz=95.0e6, power_dbfs=-20.0),
            SyntheticFmStation(freq_hz=100.0e6, power_dbfs=-30.0),
        ]
        result = scan_band(device_with(*stations), FM_BAND_PLAN, settings=QUICK)
        by_strength = sorted(result.detections, key=lambda m: -m.snr_db)
        assert [round(m.freq_hz / 1e6) for m in by_strength] == [95, 100, 90]

    def test_a_higher_threshold_rejects_the_weakest(self) -> None:
        stations = [
            SyntheticFmStation(freq_hz=95.0e6, power_dbfs=-20.0),
            SyntheticFmStation(freq_hz=100.0e6, power_dbfs=-58.0),
        ]
        device = device_with(*stations)
        lenient = ScanSettings(
            samples_per_segment=1 << 14, settling_samples=0, fft_size=2048, threshold_db=5.0
        )
        strict = ScanSettings(
            samples_per_segment=1 << 14, settling_samples=0, fft_size=2048, threshold_db=30.0
        )
        assert len(scan_band(device, FM_BAND_PLAN, settings=lenient).detections) == 2
        assert len(scan_band(device, FM_BAND_PLAN, settings=strict).detections) == 1

    def test_one_transmitter_produces_one_detection(self) -> None:
        # A strong station lifts the channels either side of it above the threshold too.
        station = SyntheticFmStation(freq_hz=98.0e6, power_dbfs=-15.0)
        result = scan_band(device_with(station), FM_BAND_PLAN, settings=QUICK)
        assert len(result.detections) == 1


class TestPartialCoverage:
    def test_a_receiver_covering_only_part_of_the_band_reports_what_it_reached(self) -> None:
        # A recorded capture holds one window of spectrum. Refusing to scan it would be worse
        # than scanning it and saying how much of the band was out of reach.
        narrow = MockSdrDevice(
            sources=[SyntheticFmStation(freq_hz=98.0e6, power_dbfs=-20.0)],
            tuning_range=TuningRange(min_hz=97.0e6, max_hz=99.0e6),
        )
        result = scan_band(narrow, FM_BAND_PLAN, settings=QUICK)
        assert not result.is_complete
        assert 0.0 < result.coverage < 1.0
        assert result.skipped_segments
        assert [round(m.freq_hz) for m in result.detections] == [98_000_000]

    def test_a_receiver_that_cannot_reach_the_band_at_all_says_so(self) -> None:
        elsewhere = MockSdrDevice(tuning_range=TuningRange(min_hz=400e6, max_hz=500e6))
        with pytest.raises(TuningFailedError, match="cannot reach any part of"):
            scan_band(elsewhere, FM_BAND_PLAN, settings=QUICK)

    def test_the_refusal_names_the_range_it_has_and_the_range_it_needs(self) -> None:
        elsewhere = MockSdrDevice(tuning_range=TuningRange(min_hz=400e6, max_hz=500e6))
        with pytest.raises(TuningFailedError, match=re.escape("400.000 MHz to 500.000 MHz")):
            scan_band(elsewhere, FM_BAND_PLAN, settings=QUICK)


class TestScanSettingsValidation:
    def test_fewer_samples_than_the_fft_needs_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be at least the FFT size"):
            ScanSettings(samples_per_segment=1024, fft_size=4096)

    def test_a_non_positive_sample_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample rate must be positive"):
            ScanSettings(sample_rate_hz=0.0)

    def test_negative_settling_is_refused(self) -> None:
        with pytest.raises(ValueError, match="settling_samples"):
            ScanSettings(settling_samples=-1)

    def test_an_fft_smaller_than_two_is_refused(self) -> None:
        with pytest.raises(ValueError, match="FFT size"):
            ScanSettings(fft_size=1, samples_per_segment=1)


class TestScanResult:
    def test_an_empty_result_has_no_coverage_rather_than_dividing_by_zero(self) -> None:
        result = ScanResult(plan=FM_BAND_PLAN, sample_rate_hz=2.4e6)
        assert result.coverage == 0.0

    def test_it_summarises_itself_for_a_terminal(self) -> None:
        station = SyntheticFmStation(freq_hz=98.0e6, power_dbfs=-20.0)
        result = scan_band(device_with(station), FM_BAND_PLAN, settings=QUICK)
        summary = str(result)
        assert "1 station " in summary
        assert "206 channels" in summary

    def test_incomplete_coverage_is_stated_in_the_summary(self) -> None:
        narrow = MockSdrDevice(tuning_range=TuningRange(min_hz=97.0e6, max_hz=99.0e6))
        result = scan_band(narrow, FM_BAND_PLAN, settings=QUICK)
        assert "of the band covered" in str(result)


class TestANarrowWindowIsRefused:
    def test_a_sample_rate_too_low_for_the_channel_is_refused(self) -> None:
        class Slow(SdrDevice):
            @property
            def info(self) -> DeviceInfo:
                return DeviceInfo(driver="slow", index=0, label="Slow receiver")

            @property
            def tuning_range(self) -> TuningRange:
                return TuningRange(min_hz=80e6, max_hz=120e6)

            @property
            def supported_sample_rates_hz(self) -> tuple[float, ...]:
                return (150_000.0,)

            def _do_open(self) -> None: ...
            def _do_close(self) -> None: ...
            def _do_set_center_freq(self, freq_hz: float) -> None: ...
            def _do_set_sample_rate(self, rate_hz: float) -> None: ...
            def _do_set_gain(self, gain: Gain) -> None: ...

            def _do_read_samples(self, count: int) -> IqSamples:
                return np.zeros(count, dtype=np.complex64)

        with pytest.raises(ValueError, match="too narrow to measure"):
            scan_band(Slow(), FM_BAND_PLAN, settings=QUICK)


def test_a_custom_band_plan_is_honoured() -> None:
    narrow_band = BandPlan(
        name="two metres",
        start_hz=144_000_000.0,
        end_hz=146_000_000.0,
        channel_spacing_hz=25_000.0,
        channel_bandwidth_hz=50_000.0,
    )
    result = scan_band(device_with(), narrow_band, settings=QUICK)
    assert result.plan is narrow_band
    assert result.channels_measured == narrow_band.channel_count
