"""A sample FM band, for trying OpenWave without a receiver.

Running a scanner that finds nothing is a poor way to meet a project, and the simulator carries
nothing unless it is told to. This module provides a plausible band so that ``--demo`` produces
a station list on a machine with no hardware attached.

The stations are invented. The point is a believable spread: strong and weak, stereo and mono,
one near each end of the band, and a weak station close to a strong one -- which is the case
that catches a scanner reporting its neighbour's spill as a station of its own.
"""

from __future__ import annotations

from typing import Final

from openwave.radio.synthesis import SyntheticFmStation
from openwave.sdr.mock import MockSdrDevice

#: Noise floor of the demonstration band, in dBFS. A plausible figure for an indoor antenna.
DEMO_NOISE_FLOOR_DBFS: Final = -70.0

#: An invented but plausible FM band.
DEMO_FM_STATIONS: Final = (
    SyntheticFmStation(
        freq_hz=88_100_000.0, power_dbfs=-20.0, stereo=True, right_tone_hz=400.0, name="OPENWAVE"
    ),
    SyntheticFmStation(freq_hz=89_900_000.0, power_dbfs=-30.0, name="MONO FM"),
    SyntheticFmStation(
        freq_hz=92_400_000.0, power_dbfs=-35.0, stereo=True, right_tone_hz=600.0, name="DISTANT"
    ),
    SyntheticFmStation(freq_hz=98_000_000.0, power_dbfs=-22.0, name="LOCAL 98"),
    # Deliberately close to the strong station above, and much weaker: the arrangement that
    # exposes a scanner reporting spill as a separate station.
    SyntheticFmStation(
        freq_hz=98_600_000.0, power_dbfs=-45.0, stereo=True, right_tone_hz=800.0, name="WEAK ONE"
    ),
    SyntheticFmStation(
        freq_hz=101_700_000.0, power_dbfs=-28.0, stereo=True, right_tone_hz=500.0, name="CITY FM"
    ),
    SyntheticFmStation(freq_hz=104_300_000.0, power_dbfs=-33.0, name="TALK 104"),
    SyntheticFmStation(freq_hz=107_900_000.0, power_dbfs=-30.0, name="BAND EDGE"),
)


def demo_receiver(
    *, noise_floor_dbfs: float = DEMO_NOISE_FLOOR_DBFS, seed: int = 0
) -> MockSdrDevice:
    """A simulated receiver carrying :data:`DEMO_FM_STATIONS`.

    Args:
        noise_floor_dbfs: Integrated noise power across the window.
        seed: Noise seed. Fixed by default, so the demonstration is the same every time.
    """
    return MockSdrDevice(
        sources=DEMO_FM_STATIONS,
        noise_floor_dbfs=noise_floor_dbfs,
        seed=seed,
    )
