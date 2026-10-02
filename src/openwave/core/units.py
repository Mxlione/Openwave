"""Conversions between amplitude, power and decibels.

Decibels are everywhere in this codebase and the factor is easy to get wrong: amplitude ratios
use 20·log₁₀, power ratios use 10·log₁₀. These helpers exist so that factor is written once.

**dBFS, not dBm.** OpenWave reports levels in dB relative to full scale, where full scale is an
amplitude of 1.0. A consumer SDR has no calibrated power reference — an RTL-SDR's reading depends
on its gain setting, its tuner chip, the cable and the antenna — so an absolute dBm figure would
be invented precision. Comparisons between stations, and signal-to-noise ratios, are meaningful;
absolute power is not, unless a particular receiver provides a calibrated measurement.
"""

from __future__ import annotations

import numpy as np

#: Amplitude corresponding to 0 dBFS.
FULL_SCALE_AMPLITUDE = 1.0

#: Level reported in place of -∞ dB for a signal of exactly zero amplitude.
SILENCE_DBFS = -300.0


def amplitude_to_dbfs(amplitude: float) -> float:
    """Convert an amplitude to dB relative to full scale.

    >>> amplitude_to_dbfs(1.0)
    0.0
    >>> round(amplitude_to_dbfs(0.5), 2)
    -6.02
    """
    if amplitude < 0:
        raise ValueError(f"amplitude must not be negative, got {amplitude}")
    if amplitude == 0:
        return SILENCE_DBFS
    return 20.0 * float(np.log10(amplitude / FULL_SCALE_AMPLITUDE))


def dbfs_to_amplitude(dbfs: float) -> float:
    """Convert a level in dBFS to an amplitude.

    >>> dbfs_to_amplitude(0.0)
    1.0
    >>> round(dbfs_to_amplitude(-20.0), 3)
    0.1
    """
    return FULL_SCALE_AMPLITUDE * float(10.0 ** (dbfs / 20.0))


def power_to_dbfs(power: float) -> float:
    """Convert a mean power to dB relative to full-scale power.

    >>> power_to_dbfs(1.0)
    0.0
    >>> round(power_to_dbfs(0.01), 2)
    -20.0
    """
    if power < 0:
        raise ValueError(f"power must not be negative, got {power}")
    if power == 0:
        return SILENCE_DBFS
    return 10.0 * float(np.log10(power))


def dbfs_to_power(dbfs: float) -> float:
    """Convert a level in dBFS to a mean power.

    >>> dbfs_to_power(0.0)
    1.0
    >>> round(dbfs_to_power(-30.0), 6)
    0.001
    """
    return float(10.0 ** (dbfs / 10.0))


def db_ratio(numerator: float, denominator: float) -> float:
    """Express a power ratio in dB, for example a signal-to-noise ratio.

    >>> round(db_ratio(100.0, 1.0), 1)
    20.0
    """
    if numerator < 0 or denominator <= 0:
        raise ValueError(f"invalid power ratio: {numerator} / {denominator}")
    if numerator == 0:
        return SILENCE_DBFS
    return 10.0 * float(np.log10(numerator / denominator))


def format_frequency(freq_hz: float) -> str:
    """Format a frequency for display, choosing a sensible unit.

    >>> format_frequency(98_000_000)
    '98.000 MHz'
    >>> format_frequency(19_000)
    '19.000 kHz'
    >>> format_frequency(1_200_000_000)
    '1.200 GHz'
    """
    magnitude = abs(freq_hz)
    if magnitude >= 1e9:
        return f"{freq_hz / 1e9:.3f} GHz"
    if magnitude >= 1e6:
        return f"{freq_hz / 1e6:.3f} MHz"
    if magnitude >= 1e3:
        return f"{freq_hz / 1e3:.3f} kHz"
    return f"{freq_hz:.1f} Hz"
