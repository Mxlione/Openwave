"""RDS: the data broadcast alongside an FM signal.

RDS (Radio Data System, IEC 62106) carries a few hundred bits per second on a subcarrier at
57 kHz. It is what puts a station's name on a radio's display, and it is the difference between
a scan that reports "98.0 MHz" and one that reports what the station is called.

The package is in four layers, lowest first:

:mod:`~openwave.radio.rds.blocks`
    The error-protected 26-bit block: its check word, how a receiver finds block boundaries in a
    bitstream, and how it corrects the errors radio reception introduces.

:mod:`~openwave.radio.rds.groups`
    What the blocks mean. Programme identification, programme type, the station name and
    RadioText.

:mod:`~openwave.radio.rds.encoder`
    Builds an RDS signal that does not exist. With no receiver available, this is how the
    decoder is tested: encode a known station name, decode it, compare.

:mod:`~openwave.radio.rds.decoder`
    Recovers the bitstream from a demodulated multiplex and assembles it back into groups.
"""

from openwave.radio.rds.blocks import (
    BLOCK_BITS,
    CHECKWORD_BITS,
    INFORMATION_BITS,
    Block,
    BlockOffset,
    checkword,
    correct_block,
    decode_block,
    encode_block,
    syndrome,
)
from openwave.radio.rds.encoder import (
    RdsBaseband,
    RdsProgramme,
    rds_baseband_period,
    rds_subcarrier,
)
from openwave.radio.rds.groups import (
    GROUP_BLOCKS,
    Group,
    GroupVersion,
    ProgrammeType,
    RdsData,
    RdsReceiver,
    decode_group,
    radio_text_char,
    rds_text,
)

__all__ = [
    "BLOCK_BITS",
    "CHECKWORD_BITS",
    "GROUP_BLOCKS",
    "INFORMATION_BITS",
    "Block",
    "BlockOffset",
    "Group",
    "GroupVersion",
    "ProgrammeType",
    "RdsBaseband",
    "RdsData",
    "RdsProgramme",
    "RdsReceiver",
    "checkword",
    "correct_block",
    "decode_block",
    "decode_group",
    "encode_block",
    "radio_text_char",
    "rds_baseband_period",
    "rds_subcarrier",
    "rds_text",
    "syndrome",
]
