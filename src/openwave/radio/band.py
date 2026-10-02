"""The FM broadcast band plan.

Separate from :mod:`openwave.radio.constants`, which holds plain numbers, because a band plan is
a thing the scanner is handed rather than a figure to look up.
"""

from __future__ import annotations

from typing import Final

from openwave.core.frequency_manager import BandPlan
from openwave.radio.constants import (
    FM_BAND_END_HZ,
    FM_BAND_START_HZ,
    FM_CHANNEL_BANDWIDTH_HZ,
    FM_CHANNEL_SPACING_HZ,
)

#: The FM broadcast band, on the 100 kHz grid.
#:
#: The 100 kHz raster is a superset of the grids actually in use: ITU Region 1 assigns
#: frequencies on 100 kHz, and Region 2 uses odd 200 kHz steps, which fall on it too. Scanning
#: the finer grid costs a little time and finds stations under either plan, which matters for a
#: project that cannot test against any particular country's transmitters.
FM_BAND_PLAN: Final = BandPlan(
    name="FM broadcast",
    start_hz=FM_BAND_START_HZ,
    end_hz=FM_BAND_END_HZ,
    channel_spacing_hz=FM_CHANNEL_SPACING_HZ,
    channel_bandwidth_hz=FM_CHANNEL_BANDWIDTH_HZ,
)

#: The Japanese FM band, which sits below the one used everywhere else.
FM_BAND_PLAN_JAPAN: Final = BandPlan(
    name="FM broadcast (Japan)",
    start_hz=76_000_000.0,
    end_hz=95_000_000.0,
    channel_spacing_hz=100_000.0,
    channel_bandwidth_hz=FM_CHANNEL_BANDWIDTH_HZ,
)

#: Band plans by name, for selecting one from a command line or an API request.
FM_BAND_PLANS: Final = {
    "fm": FM_BAND_PLAN,
    "fm-japan": FM_BAND_PLAN_JAPAN,
}
