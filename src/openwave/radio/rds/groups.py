"""What RDS blocks mean.

Four blocks make a group, and the group's type says what the last two blocks carry. OpenWave
reads the types that answer the question a scan asks -- what is this station called? -- and
records the rest without interpreting it:

===========  ==========================================================================
Group        Carries
===========  ==========================================================================
0A, 0B       The programme service name: eight characters, two per group
2A, 2B       RadioText: a longer free-text field, 64 characters in 2A and 32 in 2B
any          Programme identification, programme type and the traffic-programme flag,
             which live in the first two blocks of every group whatever its type
===========  ==========================================================================

**Why a name arrives in pieces.** The programme service name is sent two characters at a time,
with a segment number saying which pair. A receiver that has heard only some of the segments
holds an incomplete name, and showing it would spell a station's name wrongly on screen for the
second or two before the rest arrives. :class:`RdsReceiver` therefore reports a name only once
every segment has been received.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final

from openwave.radio.rds.blocks import Block, BlockOffset

#: Blocks in one group.
GROUP_BLOCKS: Final = 4

#: Characters in a programme service name.
PS_LENGTH: Final = 8

#: Characters in a RadioText message sent by version A groups.
RADIOTEXT_LENGTH: Final = 64

#: Characters in a RadioText message sent by version B groups.
RADIOTEXT_LENGTH_B: Final = 32

#: Character RDS uses to mark the end of a RadioText message.
RADIOTEXT_TERMINATOR: Final = 0x0D


class GroupVersion(IntEnum):
    """Which variant of a group type was sent.

    Version B repeats the programme identification in the third block instead of using it for
    group data, which costs payload and buys a second chance at the station's identity.
    """

    A = 0
    B = 1

    def __str__(self) -> str:
        return self.name


class ProgrammeType(IntEnum):
    """Programme type, the five-bit genre field.

    The names here are the European RDS assignments from IEC 62106. North American RBDS uses the
    same five bits for a different list, so a receiver has to know which continent it is on to
    label them correctly -- which is why :attr:`Group.pty` keeps the raw number and this
    enumeration is only applied for display.
    """

    NONE = 0
    NEWS = 1
    CURRENT_AFFAIRS = 2
    INFORMATION = 3
    SPORT = 4
    EDUCATION = 5
    DRAMA = 6
    CULTURE = 7
    SCIENCE = 8
    VARIED = 9
    POP_MUSIC = 10
    ROCK_MUSIC = 11
    EASY_LISTENING = 12
    LIGHT_CLASSICAL = 13
    SERIOUS_CLASSICAL = 14
    OTHER_MUSIC = 15
    WEATHER = 16
    FINANCE = 17
    CHILDREN = 18
    SOCIAL_AFFAIRS = 19
    RELIGION = 20
    PHONE_IN = 21
    TRAVEL = 22
    LEISURE = 23
    JAZZ_MUSIC = 24
    COUNTRY_MUSIC = 25
    NATIONAL_MUSIC = 26
    OLDIES_MUSIC = 27
    FOLK_MUSIC = 28
    DOCUMENTARY = 29
    ALARM_TEST = 30
    ALARM = 31

    @property
    def label(self) -> str:
        """A readable form, for a station list."""
        return self.name.replace("_", " ").title()


@dataclass(frozen=True, slots=True)
class Group:
    """One decoded RDS group.

    Attributes:
        pi: Programme identification, a 16-bit code unique to a station in its country. The
            nearest thing RDS has to an identifier; two transmitters carrying the same programme
            share it, which is how a receiver follows a station across frequencies.
        group_type: Which of the sixteen group types this is.
        version: A or B.
        traffic_programme: Whether the station carries traffic announcements.
        pty: Programme type, as the raw five-bit number. See :class:`ProgrammeType`.
        blocks: The four blocks, for a caller that wants a field this layer does not read.
        corrected_bits: Total bits repaired across the group.
    """

    pi: int
    group_type: int
    version: GroupVersion
    traffic_programme: bool
    pty: int
    blocks: tuple[Block, ...]
    corrected_bits: int = 0

    @property
    def programme_type(self) -> ProgrammeType:
        """The programme type as a European assignment."""
        return ProgrammeType(self.pty)

    @property
    def name(self) -> str:
        """How the standard writes this group type, such as ``"0A"``."""
        return f"{self.group_type}{self.version}"

    @property
    def is_clean(self) -> bool:
        """Whether every block decoded without correction."""
        return self.corrected_bits == 0

    def __str__(self) -> str:
        return f"{self.name} PI={self.pi:04X} PTY={self.pty}"


def decode_group(blocks: tuple[Block, ...]) -> Group | None:
    """Assemble four decoded blocks into a group.

    Returns ``None`` if the blocks are not a well-formed group, which includes the case where
    the sequence of offset words is wrong -- a receiver that has lost synchronisation sees that
    before it sees anything else.
    """
    if len(blocks) != GROUP_BLOCKS:
        return None
    expected = (
        (BlockOffset.A,),
        (BlockOffset.B,),
        (BlockOffset.C, BlockOffset.C_PRIME),
        (BlockOffset.D,),
    )
    if any(block.offset not in allowed for block, allowed in zip(blocks, expected, strict=True)):
        return None

    second = blocks[1].information
    version = GroupVersion((second >> 11) & 0x1)
    # A version B group repeats the programme identification in the third block, and its offset
    # word has to agree: C' marks version B, C marks version A.
    expected_third = BlockOffset.C_PRIME if version is GroupVersion.B else BlockOffset.C
    if blocks[2].offset is not expected_third:
        return None

    return Group(
        pi=blocks[0].information,
        group_type=(second >> 12) & 0xF,
        version=version,
        traffic_programme=bool((second >> 10) & 0x1),
        pty=(second >> 5) & 0x1F,
        blocks=blocks,
        corrected_bits=sum(block.corrected_bits for block in blocks),
    )


def radio_text_char(code: int) -> str:
    """Decode one RadioText character, keeping the end-of-message marker.

    Separate from :func:`rds_text` because that maps everything outside printable ASCII to a
    space -- which would include the carriage return marking where a message ends. A short
    message would then never be trimmed, and would appear padded out with the remains of
    whatever the station sent before it.
    """
    if code == RADIOTEXT_TERMINATOR:
        return chr(RADIOTEXT_TERMINATOR)
    return rds_text(code)


def rds_text(code: int) -> str:
    """Decode one RDS character.

    RDS defines its own character sets. The default one matches ASCII across the printable
    range, which covers the station names and RadioText of every station using the Latin
    alphabet. Anything outside that range becomes a space rather than a replacement glyph,
    because a name is going on a display and a run of question marks reads as a fault.

    The accented and non-Latin parts of the RDS tables are not implemented, so a station whose
    name uses them loses those characters. Adding the tables is self-contained work.

    >>> rds_text(0x41)
    'A'
    >>> rds_text(0x00)
    ' '
    """
    if 0x20 <= code <= 0x7E:
        return chr(code)
    return " "


@dataclass(frozen=True, slots=True)
class RdsData:
    """What has been learnt about a station from its RDS.

    Every field is optional, because RDS arrives a fragment at a time and a station may not send
    everything. ``None`` means not yet received, which is different from empty.
    """

    pi: int | None = None
    pty: int | None = None
    traffic_programme: bool | None = None
    programme_service: str | None = None
    """The station name, once every segment of it has arrived."""

    radio_text: str | None = None
    """The RadioText message, once every segment of it has arrived."""

    groups_decoded: int = 0
    groups_corrected: int = 0
    """How many groups needed error correction, as a measure of reception quality."""

    @property
    def has_name(self) -> bool:
        """Whether a complete station name is available."""
        return bool(self.programme_service)

    @property
    def programme_type(self) -> ProgrammeType | None:
        """The programme type as a European assignment, if one has been received."""
        return None if self.pty is None else ProgrammeType(self.pty)

    def __str__(self) -> str:
        parts = []
        if self.programme_service:
            parts.append(self.programme_service)
        if self.pi is not None:
            parts.append(f"PI={self.pi:04X}")
        if self.pty is not None:
            parts.append(ProgrammeType(self.pty).label)
        return "  ".join(parts) if parts else "no RDS data"


@dataclass
class RdsReceiver:
    """Accumulates groups into the station's identity.

    Fragments are held until a field is complete, then published. The station name is eight
    characters sent two at a time, so a receiver that published every fragment would spell the
    name wrongly for a second or two each time.

    Example::

        receiver = RdsReceiver()
        for group in groups:
            receiver.accept(group)
        print(receiver.data.programme_service)
    """

    _ps_segments: dict[int, str] = field(default_factory=dict, repr=False)
    _rt_segments: dict[int, str] = field(default_factory=dict, repr=False)
    _rt_flag: int | None = field(default=None, repr=False)
    _pi: int | None = field(default=None, repr=False)
    _pty: int | None = field(default=None, repr=False)
    _tp: bool | None = field(default=None, repr=False)
    _groups: int = field(default=0, repr=False)
    _corrected: int = field(default=0, repr=False)

    def accept(self, group: Group) -> None:
        """Take one group into account."""
        self._groups += 1
        if not group.is_clean:
            self._corrected += 1
        self._pi = group.pi
        self._pty = group.pty
        self._tp = group.traffic_programme

        if group.group_type == 0:
            self._accept_programme_service(group)
        elif group.group_type == 2:
            self._accept_radio_text(group)

    def _accept_programme_service(self, group: Group) -> None:
        """Group 0A or 0B: two characters of the station name, with their position."""
        segment = group.blocks[1].information & 0x3
        pair = group.blocks[3].information
        self._ps_segments[segment] = rds_text(pair >> 8) + rds_text(pair & 0xFF)

    def _accept_radio_text(self, group: Group) -> None:
        """Group 2A or 2B: four or two characters of RadioText.

        The text A/B flag toggles when the station changes the message. Everything collected so
        far is then discarded: keeping it would splice half of one message onto half of another.
        """
        second = group.blocks[1].information
        flag = (second >> 4) & 0x1
        if self._rt_flag is not None and flag != self._rt_flag:
            self._rt_segments.clear()
        self._rt_flag = flag

        segment = second & 0xF
        if group.version is GroupVersion.A:
            third, fourth = group.blocks[2].information, group.blocks[3].information
            self._rt_segments[segment] = "".join(
                radio_text_char(code)
                for code in (third >> 8, third & 0xFF, fourth >> 8, fourth & 0xFF)
            )
        else:
            fourth = group.blocks[3].information
            self._rt_segments[segment] = radio_text_char(fourth >> 8) + radio_text_char(
                fourth & 0xFF
            )

    @property
    def data(self) -> RdsData:
        """Everything learnt so far."""
        return RdsData(
            pi=self._pi,
            pty=self._pty,
            traffic_programme=self._tp,
            programme_service=self._assembled_name(),
            radio_text=self._assembled_text(),
            groups_decoded=self._groups,
            groups_corrected=self._corrected,
        )

    def _assembled_name(self) -> str | None:
        """The station name, or ``None`` until every segment has arrived."""
        needed = PS_LENGTH // 2
        if len(self._ps_segments) < needed:
            return None
        name = "".join(self._ps_segments[index] for index in range(needed))
        return name.strip() or None

    def _assembled_text(self) -> str | None:
        """The RadioText message, as far as it has arrived.

        RadioText is reported incomplete, unlike the station name. Messages are long, often
        changing, and frequently shorter than the full 64 characters -- waiting for every
        segment of a message that will never fill them would mean never showing any. A carriage
        return marks the end, and everything after it is padding.
        """
        if not self._rt_segments:
            return None
        highest = max(self._rt_segments)
        pieces = [self._rt_segments.get(index, "") for index in range(highest + 1)]
        text = "".join(pieces)
        terminator = text.find(chr(RADIOTEXT_TERMINATOR))
        if terminator >= 0:
            text = text[:terminator]
        return text.rstrip() or None

    def reset(self) -> None:
        """Forget everything, for retuning to another station."""
        self._ps_segments.clear()
        self._rt_segments.clear()
        self._rt_flag = None
        self._pi = None
        self._pty = None
        self._tp = None
        self._groups = 0
        self._corrected = 0
