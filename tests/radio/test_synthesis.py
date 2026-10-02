"""Tests for the synthetic FM transmitter.

Everything downstream is tested against this generator, so it has to be verified against theory
rather than against itself. The checks here are the ones that would catch a generator that
looks plausible but transmits the wrong thing: constant envelope, the right multiplex
components, the right occupied bandwidth, and exact reproducibility.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

from openwave.core.units import dbfs_to_amplitude
from openwave.radio.constants import (
    FM_MAX_DEVIATION_HZ,
    STEREO_PILOT_HZ,
    STEREO_SUBCARRIER_HZ,
)
from openwave.radio.synthesis import SyntheticFmStation, Tone

SAMPLE_RATE_HZ = 2_400_000.0


def generate(
    station: SyntheticFmStation,
    *,
    count: int = 1 << 16,
    center_freq_hz: float | None = None,
    start_index: int = 0,
    sample_rate_hz: float = SAMPLE_RATE_HZ,
) -> np.ndarray:
    """Generate samples of ``station``, tuned to it exactly unless told otherwise."""
    return station.samples(
        center_freq_hz=station.freq_hz if center_freq_hz is None else center_freq_hz,
        sample_rate_hz=sample_rate_hz,
        start_index=start_index,
        count=count,
    )


class TestTone:
    def test_a_tone_needs_a_positive_frequency(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            Tone(freq_hz=0.0, amplitude=1.0)

    def test_a_negative_amplitude_is_allowed(self) -> None:
        # The difference-channel expansion produces negative amplitudes, which is just a phase
        # shift of pi written more legibly.
        assert Tone(freq_hz=1000.0, amplitude=-0.5).amplitude == -0.5


class TestValidation:
    def test_carrier_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="carrier frequency must be positive"):
            SyntheticFmStation(freq_hz=0.0)

    def test_power_above_full_scale_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not exceed full scale"):
            SyntheticFmStation(freq_hz=98e6, power_dbfs=3.0)

    def test_tones_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="left tone"):
            SyntheticFmStation(freq_hz=98e6, left_tone_hz=0.0)
        with pytest.raises(ValueError, match="right tone"):
            SyntheticFmStation(freq_hz=98e6, right_tone_hz=-1.0)

    def test_deviation_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="deviation must be positive"):
            SyntheticFmStation(freq_hz=98e6, deviation_hz=0.0)

    @pytest.mark.parametrize("count", [0, -1])
    def test_sample_count_must_be_positive(self, count: int) -> None:
        with pytest.raises(ValueError, match="sample count must be positive"):
            generate(SyntheticFmStation(freq_hz=98e6), count=count)

    def test_start_index_must_not_be_negative(self) -> None:
        with pytest.raises(ValueError, match="start index"):
            generate(SyntheticFmStation(freq_hz=98e6), start_index=-1)


class TestMultiplex:
    def test_mono_carries_only_the_audio_tone(self) -> None:
        tones = SyntheticFmStation(freq_hz=98e6, left_tone_hz=1000.0).mpx_tones()
        assert [tone.freq_hz for tone in tones] == [1000.0]
        assert tones[0].amplitude == pytest.approx(1.0)

    def test_amplitudes_are_normalised_so_the_multiplex_cannot_exceed_unity(self) -> None:
        # This is what guarantees the peak deviation never exceeds deviation_hz.
        for station in (
            SyntheticFmStation(freq_hz=98e6),
            SyntheticFmStation(freq_hz=98e6, stereo=True, right_tone_hz=400.0),
        ):
            total = sum(abs(tone.amplitude) for tone in station.mpx_tones())
            assert total == pytest.approx(1.0)

    def test_stereo_carries_the_pilot_and_the_difference_sidebands(self) -> None:
        station = SyntheticFmStation(
            freq_hz=98e6, stereo=True, left_tone_hz=1000.0, right_tone_hz=400.0
        )
        frequencies = {tone.freq_hz for tone in station.mpx_tones()}
        assert STEREO_PILOT_HZ in frequencies
        assert {1000.0, 400.0} <= frequencies
        # The difference signal is double-sideband suppressed-carrier on 38 kHz, so each audio
        # tone appears as a pair either side of the subcarrier, and never at 38 kHz itself.
        assert {
            STEREO_SUBCARRIER_HZ - 1000.0,
            STEREO_SUBCARRIER_HZ + 1000.0,
            STEREO_SUBCARRIER_HZ - 400.0,
            STEREO_SUBCARRIER_HZ + 400.0,
        } <= frequencies
        assert STEREO_SUBCARRIER_HZ not in frequencies

    def test_identical_channels_cancel_the_difference_signal(self) -> None:
        # L and R equal means L-R is silence, so the difference sidebands must cancel exactly.
        station = SyntheticFmStation(
            freq_hz=98e6, stereo=True, left_tone_hz=1000.0, right_tone_hz=1000.0
        )
        by_frequency: dict[float, float] = {}
        for tone in station.mpx_tones():
            by_frequency[tone.freq_hz] = by_frequency.get(tone.freq_hz, 0.0) + tone.amplitude
        for sideband in (STEREO_SUBCARRIER_HZ - 1000.0, STEREO_SUBCARRIER_HZ + 1000.0):
            assert by_frequency[sideband] == pytest.approx(0.0)

    def test_the_pilot_is_weaker_than_the_audio(self) -> None:
        station = SyntheticFmStation(freq_hz=98e6, stereo=True, right_tone_hz=400.0)
        tones = {tone.freq_hz: abs(tone.amplitude) for tone in station.mpx_tones()}
        assert tones[STEREO_PILOT_HZ] < tones[1000.0]


class TestOccupiedBandwidth:
    def test_mono_follows_carsons_rule(self) -> None:
        station = SyntheticFmStation(freq_hz=98e6, left_tone_hz=1000.0)
        assert station.occupied_bandwidth_hz == pytest.approx(2 * (FM_MAX_DEVIATION_HZ + 1000.0))

    def test_stereo_is_wider_because_the_multiplex_reaches_60_khz(self) -> None:
        mono = SyntheticFmStation(freq_hz=98e6)
        stereo = SyntheticFmStation(freq_hz=98e6, stereo=True)
        assert stereo.occupied_bandwidth_hz > mono.occupied_bandwidth_hz


class TestGeneratedSamples:
    def test_shape_and_dtype(self) -> None:
        samples = generate(SyntheticFmStation(freq_hz=98e6), count=1024)
        assert samples.shape == (1024,)
        assert samples.dtype == np.complex64

    @pytest.mark.parametrize("power_dbfs", [-6.0, -20.0, -40.0])
    def test_the_envelope_is_constant_at_the_requested_level(self, power_dbfs: float) -> None:
        # FM carries its information in the phase alone, so the magnitude must not wobble.
        # A varying envelope would mean amplitude modulation had leaked in.
        samples = generate(
            SyntheticFmStation(freq_hz=98e6, power_dbfs=power_dbfs, stereo=True), count=4096
        )
        expected = dbfs_to_amplitude(power_dbfs)
        magnitude = np.abs(samples)
        assert magnitude.mean() == pytest.approx(expected, rel=1e-4)
        assert magnitude.std() < expected * 1e-4

    def test_consecutive_reads_join_up_continuously(self) -> None:
        # This is the property that lets a demodulator be fed in blocks. It holds because the
        # phase is evaluated from the absolute sample index rather than accumulated.
        station = SyntheticFmStation(freq_hz=98e6, stereo=True, right_tone_hz=400.0)
        whole = generate(station, count=4096)
        joined = np.concatenate(
            [
                generate(station, count=1500, start_index=0),
                generate(station, count=2596, start_index=1500),
            ]
        )
        np.testing.assert_array_equal(whole, joined)

    def test_the_same_range_always_gives_the_same_samples(self) -> None:
        station = SyntheticFmStation(freq_hz=98e6, stereo=True)
        first = generate(station, count=2048, start_index=9999)
        second = generate(station, count=2048, start_index=9999)
        np.testing.assert_array_equal(first, second)

    def test_a_narrow_deviation_puts_the_carrier_where_it_belongs(self) -> None:
        # With a small deviation the signal is nearly a bare carrier, so the strongest bin
        # should sit at the offset between the station and the tuned frequency. (A real 75 kHz
        # deviation suppresses its own carrier and spreads the energy to the deviation edges,
        # which is why the scanner integrates channel power instead of picking peaks.)
        center_freq_hz = 98.0e6
        station = SyntheticFmStation(freq_hz=98.3e6, deviation_hz=100.0, left_tone_hz=1000.0)
        samples = generate(station, count=1 << 16, center_freq_hz=center_freq_hz)

        spectrum = np.fft.fftshift(np.abs(np.fft.fft(samples)))
        bins = np.fft.fftshift(np.fft.fftfreq(len(samples), 1.0 / SAMPLE_RATE_HZ))
        peak_offset_hz = bins[int(np.argmax(spectrum))]

        assert peak_offset_hz == pytest.approx(300_000.0, abs=SAMPLE_RATE_HZ / len(samples))

    def test_occupied_bandwidth_matches_what_carsons_rule_predicts(self) -> None:
        # Measure the span holding 98% of the power and compare it with the prediction. The
        # tolerance is loose because Carson's rule is itself an approximation.
        station = SyntheticFmStation(freq_hz=98e6, deviation_hz=FM_MAX_DEVIATION_HZ)
        samples = generate(station, count=1 << 18)
        freqs, psd = signal.welch(
            samples, fs=SAMPLE_RATE_HZ, nperseg=4096, return_onesided=False, detrend=False
        )
        order = np.argsort(freqs)
        freqs, psd = freqs[order], psd[order]

        cumulative = np.cumsum(psd) / np.sum(psd)
        low = float(freqs[int(np.searchsorted(cumulative, 0.01))])
        high = float(freqs[int(np.searchsorted(cumulative, 0.99))])
        measured_bandwidth = high - low

        assert measured_bandwidth == pytest.approx(station.occupied_bandwidth_hz, rel=0.25)

    def test_demodulating_recovers_the_audio_tone(self) -> None:
        # A quadrature demodulator applied to the generated signal must give back the tone that
        # went in. This is the end-to-end check that the phase integral is right: an error in
        # it would shift or distort the recovered tone.
        audio_hz = 1200.0
        station = SyntheticFmStation(freq_hz=98e6, left_tone_hz=audio_hz, deviation_hz=50_000.0)
        samples = generate(station, count=1 << 18)

        decimation = 10
        baseband = signal.decimate(samples, decimation, ftype="fir", zero_phase=True)
        baseband_rate_hz = SAMPLE_RATE_HZ / decimation
        demodulated = np.angle(baseband[1:] * np.conj(baseband[:-1]))

        freqs, psd = signal.welch(
            demodulated, fs=baseband_rate_hz, nperseg=16384, detrend="constant"
        )
        recovered_hz = float(freqs[int(np.argmax(psd))])
        assert recovered_hz == pytest.approx(audio_hz, abs=baseband_rate_hz / 16384)
