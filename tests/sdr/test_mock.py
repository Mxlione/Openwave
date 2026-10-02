"""Tests for the simulated receivers.

Two things are being checked. That the device contract holds -- state, validation and the right
exception for each kind of failure -- and that the simulation behaves like a receiver: signals
inside the window appear, signals outside it do not, the noise floor is where it was asked to
be, and the whole thing is reproducible.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from scipy import signal

from openwave.core.units import SILENCE_DBFS, dbfs_to_power, power_to_dbfs
from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.device import TS_PACKET_SIZE, SignalQuality
from openwave.sdr.errors import (
    DeviceBusyError,
    DeviceNotFoundError,
    DeviceNotOpenError,
    NoLockError,
    TuningFailedError,
    UnsupportedGainError,
    UnsupportedSampleRateError,
)
from openwave.sdr.mock import MockDvbDevice, MockSdrDevice, SyntheticMux

SAMPLE_RATE_HZ = 2_400_000.0
CENTER_FREQ_HZ = 99_000_000.0


def tuned_device(**kwargs: object) -> MockSdrDevice:
    """An open device, tuned and ready to read."""
    device = MockSdrDevice(**kwargs)  # type: ignore[arg-type]
    device.open()
    device.set_sample_rate(SAMPLE_RATE_HZ)
    device.set_center_freq(CENTER_FREQ_HZ)
    return device


def measured_power_dbfs(samples: np.ndarray) -> float:
    """Mean power of ``samples``, in dBFS."""
    return power_to_dbfs(float(np.mean(np.abs(samples) ** 2)))


class TestLifecycle:
    def test_a_new_device_is_closed(self) -> None:
        assert not MockSdrDevice().is_open

    def test_opening_and_closing(self) -> None:
        device = MockSdrDevice()
        device.open()
        assert device.is_open
        device.close()
        assert not device.is_open

    def test_opening_twice_is_harmless(self) -> None:
        device = MockSdrDevice()
        device.open()
        device.open()
        assert device.is_open

    def test_closing_a_closed_device_is_harmless(self) -> None:
        MockSdrDevice().close()

    def test_the_context_manager_releases_the_device(self) -> None:
        device = MockSdrDevice()
        with device as entered:
            assert entered is device
            assert device.is_open
        assert not device.is_open

    def test_the_context_manager_releases_the_device_even_on_failure(self) -> None:
        device = MockSdrDevice()
        with pytest.raises(RuntimeError), device:
            raise RuntimeError("the scan blew up")
        assert not device.is_open

    def test_an_absent_device_cannot_be_opened(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="absent"):
            MockSdrDevice(present=False).open()

    def test_a_device_claimed_elsewhere_cannot_be_opened(self) -> None:
        with pytest.raises(DeviceBusyError, match="another process"):
            MockSdrDevice(busy=True).open()


class TestOperationsRequireAnOpenDevice:
    @pytest.mark.parametrize(
        "operation",
        [
            lambda d: d.set_center_freq(98e6),
            lambda d: d.set_sample_rate(2.4e6),
            lambda d: d.set_gain(20.0),
            lambda d: d.read_samples(16),
        ],
        ids=["set_center_freq", "set_sample_rate", "set_gain", "read_samples"],
    )
    def test_closed_device_refuses(self, operation: object) -> None:
        device = MockSdrDevice()
        with pytest.raises(DeviceNotOpenError, match="not open"):
            operation(device)  # type: ignore[operator]


class TestTuning:
    def test_tuning_updates_the_reported_frequency(self) -> None:
        device = tuned_device()
        device.set_center_freq(104_300_000.0)
        assert device.center_freq_hz == 104_300_000.0

    @pytest.mark.parametrize("freq_hz", [1e6, 2e9])
    def test_a_frequency_outside_the_range_is_refused(self, freq_hz: float) -> None:
        device = MockSdrDevice()
        device.open()
        with pytest.raises(TuningFailedError, match="outside the tuning range"):
            device.set_center_freq(freq_hz)

    def test_the_error_names_the_range_it_could_not_reach(self) -> None:
        device = MockSdrDevice()
        device.open()
        with pytest.raises(TuningFailedError, match=re.escape("24.0-1766.0 MHz")):
            device.set_center_freq(2e9)


class TestSampleRate:
    def test_any_positive_rate_is_accepted_by_default(self) -> None:
        device = MockSdrDevice()
        device.open()
        device.set_sample_rate(1_024_000.0)
        assert device.sample_rate_hz == 1_024_000.0

    @pytest.mark.parametrize("rate_hz", [0.0, -1.0])
    def test_a_non_positive_rate_is_refused(self, rate_hz: float) -> None:
        device = MockSdrDevice()
        device.open()
        with pytest.raises(UnsupportedSampleRateError, match="must be positive"):
            device.set_sample_rate(rate_hz)

    def test_a_device_with_discrete_rates_refuses_anything_else(self) -> None:
        device = MockSdrDevice(supported_sample_rates_hz=(1.024e6, 2.048e6))
        device.open()
        device.set_sample_rate(2.048e6)
        with pytest.raises(UnsupportedSampleRateError, match="does not support"):
            device.set_sample_rate(2.4e6)

    def test_the_error_lists_what_is_on_offer(self) -> None:
        device = MockSdrDevice(supported_sample_rates_hz=(1.024e6, 2.048e6))
        device.open()
        with pytest.raises(UnsupportedSampleRateError, match=re.escape("1.024, 2.048")):
            device.set_sample_rate(3e6)

    def test_reading_before_setting_a_rate_says_so(self) -> None:
        device = MockSdrDevice()
        device.open()
        with pytest.raises(UnsupportedSampleRateError, match="has not been set"):
            device.read_samples(16)

    def test_usable_bandwidth_is_narrower_than_the_sample_rate(self) -> None:
        # The edges of the window are shaped by the anti-aliasing filter, so the scanner must
        # not believe them. This is what makes it overlap its segments.
        device = tuned_device()
        assert device.usable_bandwidth_hz < SAMPLE_RATE_HZ
        assert device.usable_bandwidth_hz == pytest.approx(SAMPLE_RATE_HZ * 0.8)


class TestGain:
    def test_auto_is_the_default(self) -> None:
        assert MockSdrDevice().gain == "auto"

    def test_a_gain_in_range_is_accepted(self) -> None:
        device = MockSdrDevice()
        device.open()
        device.set_gain(28.0)
        assert device.gain == 28.0

    @pytest.mark.parametrize("gain_db", [-1.0, 60.0])
    def test_a_gain_out_of_range_is_refused(self, gain_db: float) -> None:
        device = MockSdrDevice()
        device.open()
        with pytest.raises(UnsupportedGainError, match="must be between"):
            device.set_gain(gain_db)

    def test_gain_amplifies_the_samples(self) -> None:
        station = SyntheticFmStation(freq_hz=CENTER_FREQ_HZ, power_dbfs=-40.0)
        quiet = tuned_device(sources=[station], noise_floor_dbfs=-90.0)
        loud = tuned_device(sources=[station], noise_floor_dbfs=-90.0)
        loud.set_gain(20.0)

        gained = measured_power_dbfs(loud.read_samples(4096))
        plain = measured_power_dbfs(quiet.read_samples(4096))
        assert gained - plain == pytest.approx(20.0, abs=0.1)


class TestReadingSamples:
    def test_returns_exactly_what_was_asked_for(self) -> None:
        samples = tuned_device().read_samples(4096)
        assert samples.shape == (4096,)
        assert samples.dtype == np.complex64

    @pytest.mark.parametrize("count", [0, -8])
    def test_a_non_positive_count_is_refused(self, count: int) -> None:
        with pytest.raises(ValueError, match="sample count must be positive"):
            tuned_device().read_samples(count)

    def test_the_timeline_advances_with_every_read(self) -> None:
        device = tuned_device()
        assert device.sample_index == 0
        device.read_samples(1000)
        device.read_samples(24)
        assert device.sample_index == 1024

    def test_successive_reads_are_not_the_same_samples(self) -> None:
        # Time passes between reads, so a receiver must not hand back the same block twice.
        device = tuned_device(sources=[SyntheticFmStation(freq_hz=98.5e6)])
        first = device.read_samples(2048)
        second = device.read_samples(2048)
        assert not np.array_equal(first, second)


class TestReproducibility:
    def test_chunking_does_not_change_the_result(self) -> None:
        # One read of 4096 must be bit-for-bit identical to reads of 1000 and 3096, noise
        # included. This is what makes a failing run reproducible from a seed and an offset.
        sources = [SyntheticFmStation(freq_hz=98.6e6, power_dbfs=-20.0, stereo=True)]
        device = tuned_device(sources=sources, noise_floor_dbfs=-70.0)
        whole = device.read_samples(4096)
        device.reset_timeline()
        joined = np.concatenate([device.read_samples(1000), device.read_samples(3096)])
        np.testing.assert_array_equal(whole, joined)

    def test_resetting_the_timeline_reproduces_the_run(self) -> None:
        device = tuned_device(sources=[SyntheticFmStation(freq_hz=98.6e6)])
        first = device.read_samples(2048)
        device.reset_timeline()
        np.testing.assert_array_equal(device.read_samples(2048), first)

    def test_two_devices_with_the_same_seed_agree(self) -> None:
        sources = [SyntheticFmStation(freq_hz=98.6e6)]
        left = tuned_device(sources=sources, seed=1234)
        right = tuned_device(sources=sources, seed=1234)
        np.testing.assert_array_equal(left.read_samples(2048), right.read_samples(2048))

    def test_different_seeds_give_different_noise(self) -> None:
        left = tuned_device(seed=1)
        right = tuned_device(seed=2)
        assert not np.array_equal(left.read_samples(2048), right.read_samples(2048))

    def test_asking_for_randomness_still_reports_a_usable_seed(self) -> None:
        # A surprising result from a random run has to be reproducible, so the device resolves
        # None to a concrete seed and tells you what it picked.
        device = MockSdrDevice(seed=None)
        assert isinstance(device.seed, int)
        replica = tuned_device(seed=device.seed)
        device.open()
        device.set_sample_rate(SAMPLE_RATE_HZ)
        device.set_center_freq(CENTER_FREQ_HZ)
        np.testing.assert_array_equal(device.read_samples(1024), replica.read_samples(1024))


class TestNoiseFloor:
    @pytest.mark.parametrize("noise_floor_dbfs", [-50.0, -70.0, -90.0])
    def test_the_noise_lands_where_it_was_asked_to(self, noise_floor_dbfs: float) -> None:
        device = tuned_device(noise_floor_dbfs=noise_floor_dbfs)
        measured = measured_power_dbfs(device.read_samples(1 << 16))
        assert measured == pytest.approx(noise_floor_dbfs, abs=0.1)

    def test_the_power_is_split_evenly_between_the_two_components(self) -> None:
        device = tuned_device(noise_floor_dbfs=-40.0)
        samples = device.read_samples(1 << 16)
        expected_variance = dbfs_to_power(-40.0) / 2.0
        assert float(np.var(samples.real)) == pytest.approx(expected_variance, rel=0.05)
        assert float(np.var(samples.imag)) == pytest.approx(expected_variance, rel=0.05)

    def test_the_silence_sentinel_gives_a_band_with_no_noise_at_all(self) -> None:
        device = tuned_device(noise_floor_dbfs=SILENCE_DBFS)
        assert np.all(device.read_samples(256) == 0)

    def test_a_merely_very_low_floor_still_has_noise_in_it(self) -> None:
        # Only the sentinel is special. A floor of -200 dBFS is a real, if absurd, noise level.
        device = tuned_device(noise_floor_dbfs=-200.0)
        samples = device.read_samples(256)
        assert not np.all(samples == 0)
        assert measured_power_dbfs(samples) == pytest.approx(-200.0, abs=1.0)

    def test_a_noise_floor_above_full_scale_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not exceed full scale"):
            MockSdrDevice(noise_floor_dbfs=6.0)


class TestTheWindow:
    def test_a_station_inside_the_window_is_received(self) -> None:
        station = SyntheticFmStation(freq_hz=99.2e6, power_dbfs=-20.0)
        device = tuned_device(sources=[station], noise_floor_dbfs=-70.0)
        assert measured_power_dbfs(device.read_samples(1 << 15)) > -25.0

    def test_a_station_outside_the_window_is_not(self) -> None:
        # 105 MHz is 6 MHz from the 99 MHz centre, far outside a 2.4 MHz window.
        station = SyntheticFmStation(freq_hz=105.0e6, power_dbfs=-20.0)
        device = tuned_device(sources=[station], noise_floor_dbfs=-70.0)
        assert measured_power_dbfs(device.read_samples(1 << 15)) == pytest.approx(-70.0, abs=0.2)

    def test_a_station_at_the_edge_of_the_window_is_attenuated(self) -> None:
        # The usable fraction is 0.8, so with a 2.4 MHz window everything beyond 960 kHz from
        # the centre is in the filter skirt.
        middle = SyntheticFmStation(freq_hz=99.2e6, power_dbfs=-20.0)
        edge = SyntheticFmStation(freq_hz=100.15e6, power_dbfs=-20.0)
        quiet = {"noise_floor_dbfs": -120.0}

        middle_power = measured_power_dbfs(
            tuned_device(sources=[middle], **quiet).read_samples(1 << 15)
        )
        edge_power = measured_power_dbfs(
            tuned_device(sources=[edge], **quiet).read_samples(1 << 15)
        )
        assert middle_power - edge_power > 5.0

    def test_several_stations_add_up(self) -> None:
        sources = [
            SyntheticFmStation(freq_hz=98.4e6, power_dbfs=-20.0),
            SyntheticFmStation(freq_hz=99.0e6, power_dbfs=-20.0),
        ]
        device = tuned_device(sources=sources, noise_floor_dbfs=-120.0)
        # Two equal uncorrelated carriers carry twice the power of one: 3 dB more.
        assert measured_power_dbfs(device.read_samples(1 << 15)) == pytest.approx(-17.0, abs=0.3)

    def test_each_station_appears_at_its_own_frequency(self) -> None:
        sources = [
            SyntheticFmStation(freq_hz=98.2e6, power_dbfs=-20.0, deviation_hz=100.0),
            SyntheticFmStation(freq_hz=99.6e6, power_dbfs=-20.0, deviation_hz=100.0),
        ]
        device = tuned_device(sources=sources, noise_floor_dbfs=-90.0)
        samples = device.read_samples(1 << 16)

        freqs, psd = signal.welch(
            samples, fs=SAMPLE_RATE_HZ, nperseg=4096, return_onesided=False, detrend=False
        )
        found = {
            round(float(freqs[index] + CENTER_FREQ_HZ) / 1e5) / 10.0
            for index in signal.find_peaks(
                10 * np.log10(psd), height=np.median(10 * np.log10(psd)) + 20
            )[0]
        }
        assert found == {98.2, 99.6}


class TestWidebandSnr:
    def test_it_is_the_difference_between_the_station_and_the_floor(self) -> None:
        station = SyntheticFmStation(freq_hz=98.6e6, power_dbfs=-25.0)
        device = MockSdrDevice(sources=[station], noise_floor_dbfs=-70.0)
        assert device.wideband_snr_db(station) == pytest.approx(45.0)

    def test_a_source_with_no_level_is_refused(self) -> None:
        class Flat:
            freq_hz = 98e6

            def samples(self, **_: object) -> np.ndarray:
                return np.zeros(1, dtype=np.complex64)

        device = MockSdrDevice()
        with pytest.raises(TypeError, match="does not report a power level"):
            device.wideband_snr_db(Flat())  # type: ignore[arg-type]


class TestDeviceInfo:
    def test_it_names_the_driver_and_counts_the_transmissions(self) -> None:
        info = MockSdrDevice(sources=[SyntheticFmStation(freq_hz=98e6)]).info
        assert info.driver == "mock"
        assert "1 transmission" in info.label

    def test_it_reads_sensibly_when_printed(self) -> None:
        assert str(MockSdrDevice(index=2).info).startswith("mock:2 ")


# -- The simulated DVB-T tuner -------------------------------------------------------------------


def transport_stream(packets: int = 10, *, fill: int = 0x47) -> bytes:
    """A block of TS-shaped bytes. Not valid MPEG, just the right size and shape."""
    return bytes([fill] + [0x00] * (TS_PACKET_SIZE - 1)) * packets


MUX_FREQ_HZ = 498_000_000.0


def dvb_device(**kwargs: object) -> MockDvbDevice:
    """An open tuner carrying one multiplex at 498 MHz."""
    defaults: dict[str, object] = {
        "muxes": [
            SyntheticMux(
                freq_hz=MUX_FREQ_HZ,
                transport_stream=transport_stream(),
                snr_db=22.0,
                ber=1e-6,
                label="MUX 1",
            )
        ]
    }
    defaults.update(kwargs)
    device = MockDvbDevice(**defaults)  # type: ignore[arg-type]
    device.open()
    return device


class TestSyntheticMux:
    def test_it_needs_a_whole_number_of_packets(self) -> None:
        with pytest.raises(ValueError, match="not a whole number"):
            SyntheticMux(freq_hz=MUX_FREQ_HZ, transport_stream=b"\x47" * 100)

    def test_it_needs_some_bytes(self) -> None:
        with pytest.raises(ValueError, match="no transport stream"):
            SyntheticMux(freq_hz=MUX_FREQ_HZ, transport_stream=b"")

    def test_a_bit_error_rate_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            SyntheticMux(freq_hz=MUX_FREQ_HZ, transport_stream=transport_stream(), ber=1.5)


class TestDvbLifecycle:
    def test_an_absent_tuner_cannot_be_opened(self) -> None:
        with pytest.raises(DeviceNotFoundError, match="absent"):
            MockDvbDevice(present=False).open()

    def test_a_tuner_claimed_elsewhere_cannot_be_opened(self) -> None:
        with pytest.raises(DeviceBusyError, match="another process"):
            MockDvbDevice(busy=True).open()

    def test_closing_forgets_the_lock(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        assert device.wait_for_lock()
        device.close()
        device.open()
        assert not device.signal_quality().locked

    @pytest.mark.parametrize(
        "operation",
        [
            lambda d: d.tune(498e6, 8e6),
            lambda d: d.wait_for_lock(),
            lambda d: d.signal_quality(),
            lambda d: d.read_ts(188),
        ],
        ids=["tune", "wait_for_lock", "signal_quality", "read_ts"],
    )
    def test_a_closed_tuner_refuses_everything(self, operation: object) -> None:
        with pytest.raises(DeviceNotOpenError, match="not open"):
            operation(MockDvbDevice())  # type: ignore[operator]


class TestDvbTuning:
    def test_tuning_to_a_multiplex_locks(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        assert device.wait_for_lock()

    def test_an_empty_channel_does_not_lock(self) -> None:
        # The expected outcome for most of a scan, so it reports failure rather than raising.
        device = dvb_device()
        device.tune(474_000_000.0, 8e6)
        assert not device.wait_for_lock()

    def test_a_small_tuning_error_is_pulled_in(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ + 100e3, 8e6)
        assert device.wait_for_lock()

    def test_a_large_tuning_error_is_not(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ + 1e6, 8e6)
        assert not device.wait_for_lock()

    def test_the_wrong_bandwidth_does_not_lock(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 7e6)
        assert not device.wait_for_lock()

    def test_a_frequency_outside_the_range_is_refused(self) -> None:
        with pytest.raises(TuningFailedError, match="outside the tuning range"):
            dvb_device().tune(2e9, 8e6)

    def test_a_non_positive_bandwidth_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bandwidth must be positive"):
            dvb_device().tune(MUX_FREQ_HZ, 0.0)

    def test_an_impatient_scan_misses_a_slow_lock(self) -> None:
        device = dvb_device(lock_delay_s=1.5)
        device.tune(MUX_FREQ_HZ, 8e6)
        assert not device.wait_for_lock(timeout_s=0.2)
        assert device.wait_for_lock(timeout_s=2.0)

    def test_a_negative_timeout_is_refused(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        with pytest.raises(ValueError, match="must not be negative"):
            device.wait_for_lock(timeout_s=-1.0)


class TestDvbSignalQuality:
    def test_a_locked_tuner_reports_what_the_multiplex_specifies(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        quality = device.signal_quality()
        assert quality == SignalQuality(locked=True, snr_db=22.0, strength_dbm=-45.0, ber=1e-6)

    def test_an_unlocked_tuner_reports_nothing_measurable(self) -> None:
        device = dvb_device()
        device.tune(474_000_000.0, 8e6)
        device.wait_for_lock()
        quality = device.signal_quality()
        assert not quality.locked
        assert quality.snr_db is None


class TestDvbReading:
    def test_reading_without_lock_is_an_error(self) -> None:
        device = dvb_device()
        device.tune(474_000_000.0, 8e6)
        with pytest.raises(NoLockError, match="no lock"):
            device.read_ts(TS_PACKET_SIZE)

    def test_it_returns_the_bytes_of_the_multiplex(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        data = device.read_ts(TS_PACKET_SIZE * 4)
        assert len(data) == TS_PACKET_SIZE * 4
        assert data == transport_stream(4)

    def test_successive_reads_walk_through_the_stream(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        first = device.read_ts(TS_PACKET_SIZE * 3)
        second = device.read_ts(TS_PACKET_SIZE * 3)
        assert first + second == transport_stream(6)

    def test_the_stream_loops_when_it_runs_out(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        data = device.read_ts(TS_PACKET_SIZE * 25)
        assert len(data) == TS_PACKET_SIZE * 25
        assert data == transport_stream(25)

    def test_retuning_restarts_the_stream(self) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        first = device.read_ts(TS_PACKET_SIZE * 2)
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        assert device.read_ts(TS_PACKET_SIZE * 2) == first

    @pytest.mark.parametrize("size", [0, -188, 100, 189])
    def test_a_size_that_is_not_whole_packets_is_refused(self, size: int) -> None:
        device = dvb_device()
        device.tune(MUX_FREQ_HZ, 8e6)
        device.wait_for_lock()
        with pytest.raises(ValueError, match=re.escape("multiple of 188")):
            device.read_ts(size)


class TestDvbDeviceInfo:
    def test_it_names_the_driver_and_counts_the_multiplexes(self) -> None:
        info = dvb_device().info
        assert info.driver == "mockdvb"
        assert "1 multiplex" in info.label

    def test_several_multiplexes_are_pluralised(self) -> None:
        muxes = [
            SyntheticMux(freq_hz=498e6, transport_stream=transport_stream()),
            SyntheticMux(freq_hz=506e6, transport_stream=transport_stream()),
        ]
        assert "2 multiplexes" in MockDvbDevice(muxes=muxes).info.label
