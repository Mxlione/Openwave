"""FM broadcast constants.

The numbers in this module come from the FM broadcasting standards (ITU-R BS.450 for the
transmission system, IEC 62106 for RDS). They are the same everywhere FM broadcasting exists,
apart from the channel raster and the de-emphasis time constant, which differ between ITU
regions — those are noted where they appear.
"""

from __future__ import annotations

from typing import Final

# -- The band ------------------------------------------------------------------------------------

#: Lowest frequency of the FM broadcast band, in Hz.
FM_BAND_START_HZ: Final = 87_500_000.0

#: Highest frequency of the FM broadcast band, in Hz.
FM_BAND_END_HZ: Final = 108_000_000.0

#: Channel spacing in ITU Region 1 (Europe, Africa, Middle East), in Hz.
#:
#: Region 2 (the Americas) uses a 200 kHz raster with odd-numbered frequencies, and Japan places
#: its band at 76-95 MHz. The 100 kHz raster is a superset of the Region 1 and Region 2 grids, so
#: scanning on it finds stations in both.
FM_CHANNEL_SPACING_HZ: Final = 100_000.0

#: Nominal bandwidth occupied by one FM broadcast channel, in Hz.
#:
#: Carson's rule gives 2·(75 kHz deviation + 53 kHz highest baseband frequency) ≈ 256 kHz for the
#: full multiplex, but the energy outside ±100 kHz is low enough that 200 kHz is the figure used
#: for channel planning.
FM_CHANNEL_BANDWIDTH_HZ: Final = 200_000.0

# -- Modulation ----------------------------------------------------------------------------------

#: Maximum frequency deviation of the carrier, in Hz.
FM_MAX_DEVIATION_HZ: Final = 75_000.0

#: Highest audio frequency carried, in Hz.
FM_AUDIO_BANDWIDTH_HZ: Final = 15_000.0

#: Pre-emphasis / de-emphasis time constant in ITU Region 1, in seconds.
#:
#: Region 2 and Japan use 75 µs. Applying the wrong one does not break the audio, it just makes
#: it sound dull or harsh.
FM_DEEMPHASIS_TAU_S: Final = 50e-6

# -- The stereo multiplex ------------------------------------------------------------------------

#: Stereo pilot tone frequency, in Hz. Its presence is what marks a transmission as stereo.
STEREO_PILOT_HZ: Final = 19_000.0

#: Stereo difference-signal subcarrier frequency, in Hz. Exactly twice the pilot.
STEREO_SUBCARRIER_HZ: Final = 38_000.0

#: Share of total deviation allocated to the pilot tone, as a fraction.
STEREO_PILOT_DEVIATION_SHARE: Final = 0.10

#: Share of total deviation allocated to the sum and to the difference signal, each.
STEREO_AUDIO_DEVIATION_SHARE: Final = 0.45

# -- RDS -----------------------------------------------------------------------------------------

#: RDS subcarrier frequency, in Hz. The third harmonic of the stereo pilot.
RDS_SUBCARRIER_HZ: Final = 57_000.0

#: RDS bit rate, in bits per second. Exactly the subcarrier divided by 48.
RDS_BITRATE: Final = 1187.5

#: Share of total deviation allocated to the RDS subcarrier, as a fraction.
RDS_DEVIATION_SHARE: Final = 0.02

#: Highest frequency present in the stereo multiplex, in Hz.
#:
#: Set by the upper edge of the RDS subcarrier: 57 kHz + half of its ±2.4 kHz spectrum.
FM_MPX_BANDWIDTH_HZ: Final = 60_000.0
