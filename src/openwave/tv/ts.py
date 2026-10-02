"""The MPEG transport stream packet.

Digital television is carried in packets of exactly 188 bytes, fixed by ISO/IEC 13818-1. Each
begins with the byte 0x47 and a header saying which stream it belongs to; the rest is payload.
A multiplex is nothing but millions of these, interleaved.

The header, in the four bytes after the sync byte:

===================================  =====  ============================================
Field                                Bits   Meaning
===================================  =====  ============================================
``transport_error_indicator``            1  The demodulator could not repair this packet
``payload_unit_start_indicator``         1  A new table or frame starts in this payload
``transport_priority``                   1  Rarely used
``pid``                                 13  Which stream this packet belongs to
``transport_scrambling_control``         2  Non-zero means encrypted
``adaptation_field_control``             2  Whether an adaptation field is present
``continuity_counter``                   4  Increments per packet, for spotting losses
===================================  =====  ============================================

**Everything here treats its input as hostile.** A transport stream arrives off the air, from
whoever is transmitting, through a demodulator that may have failed to repair it. A parser that
trusts a length field in it is a parser that can be made to read past the end of a buffer or
allocate until memory runs out. Every field is range-checked, and a malformed packet is reported
rather than worked around.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

#: Size of one transport stream packet, in bytes.
TS_PACKET_SIZE: Final = 188

#: The byte every packet starts with.
SYNC_BYTE: Final = 0x47

#: Bytes of header before the payload, when there is no adaptation field.
TS_HEADER_SIZE: Final = 4

#: PID of the Program Association Table, which lists the services in a multiplex.
PAT_PID: Final = 0x0000

#: PID of the Conditional Access Table.
CAT_PID: Final = 0x0001

#: PID of the Network Information Table, which describes the network and its frequencies.
NIT_PID: Final = 0x0010

#: PID of the Service Description Table, which carries service names.
SDT_PID: Final = 0x0011

#: PID of the Event Information Table, the programme guide.
EIT_PID: Final = 0x0012

#: PID reserved for null packets, used as padding to keep a multiplex at a constant rate.
NULL_PID: Final = 0x1FFF

#: Highest valid PID.
MAX_PID: Final = 0x1FFF

#: Highest value of the continuity counter, which wraps.
CONTINUITY_MODULO: Final = 16


class TransportStreamError(ValueError):
    """A transport stream is malformed beyond interpretation."""


@dataclass(frozen=True, slots=True)
class TsPacket:
    """One decoded transport stream packet.

    Attributes:
        pid: Which stream this packet belongs to.
        payload: The bytes after the header and any adaptation field. Empty when the packet
            carries only an adaptation field, which is how a stream inserts timing information.
        payload_start: Whether a new table section or frame starts in this payload.
        continuity_counter: The packet's position in its stream's cycle of sixteen.
        error: Whether the demodulator flagged this packet as unrepairable. Its contents are
            not to be trusted.
        scrambled: Whether the payload is encrypted. OpenWave reports this and goes no
            further; see docs/legal.md.
        discontinuity: Whether the stream signalled a deliberate break in continuity, so a
            counter jump here is expected rather than a lost packet.
    """

    pid: int
    payload: bytes = b""
    payload_start: bool = False
    continuity_counter: int = 0
    error: bool = False
    scrambled: bool = False
    discontinuity: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.pid <= MAX_PID:
            raise ValueError(f"PID must fit in 13 bits, got {self.pid}")
        if not 0 <= self.continuity_counter < CONTINUITY_MODULO:
            raise ValueError(
                f"continuity counter must be 0 to {CONTINUITY_MODULO - 1}, "
                f"got {self.continuity_counter}"
            )

    @property
    def is_null(self) -> bool:
        """Whether this is a padding packet, which a parser skips."""
        return self.pid == NULL_PID

    @property
    def is_usable(self) -> bool:
        """Whether the payload is worth parsing."""
        return bool(self.payload) and not self.error and not self.scrambled

    def __str__(self) -> str:
        flags = "".join(
            (
                "S" if self.payload_start else "-",
                "E" if self.error else "-",
                "X" if self.scrambled else "-",
            )
        )
        return f"PID {self.pid:#06x} cc={self.continuity_counter:2d} {flags} {len(self.payload)}B"


def parse_packet(data: bytes) -> TsPacket:
    """Decode one 188-byte packet.

    Raises:
        TransportStreamError: if the data is the wrong length, does not start with the sync
            byte, or has an adaptation field longer than the packet.
    """
    if len(data) != TS_PACKET_SIZE:
        raise TransportStreamError(f"a packet is {TS_PACKET_SIZE} bytes, got {len(data)}")
    if data[0] != SYNC_BYTE:
        raise TransportStreamError(
            f"packet starts with {data[0]:#04x}, not the sync byte {SYNC_BYTE:#04x}; "
            "the stream is probably not aligned"
        )

    error = bool(data[1] & 0x80)
    payload_start = bool(data[1] & 0x40)
    pid = ((data[1] & 0x1F) << 8) | data[2]
    scrambling = (data[3] >> 6) & 0x03
    adaptation_control = (data[3] >> 4) & 0x03
    continuity = data[3] & 0x0F

    offset = TS_HEADER_SIZE
    discontinuity = False
    if adaptation_control in (0b10, 0b11):
        if offset >= TS_PACKET_SIZE:
            raise TransportStreamError("packet claims an adaptation field but has no room for it")
        adaptation_length = data[offset]
        offset += 1
        # The length is attacker-controlled. A stream can claim an adaptation field that runs
        # past the end of the packet, and a parser that believes it reads into the next one.
        if offset + adaptation_length > TS_PACKET_SIZE:
            raise TransportStreamError(
                f"adaptation field of {adaptation_length} bytes does not fit in a "
                f"{TS_PACKET_SIZE}-byte packet"
            )
        if adaptation_length > 0:
            discontinuity = bool(data[offset] & 0x80)
        offset += adaptation_length

    payload = b"" if adaptation_control == 0b10 else data[offset:]
    return TsPacket(
        pid=pid,
        payload=payload,
        payload_start=payload_start,
        continuity_counter=continuity,
        error=error,
        scrambled=scrambling != 0,
        discontinuity=discontinuity,
    )


def build_packet(
    pid: int,
    payload: bytes = b"",
    *,
    payload_start: bool = False,
    continuity_counter: int = 0,
    scrambled: bool = False,
    stuff_to_full_size: bool = True,
) -> bytes:
    """Build one 188-byte packet.

    A payload shorter than the space available is padded with an adaptation field, which is how
    a real stream fills a packet it has nothing more to put in. Padding with arbitrary bytes
    instead would make the next section start in the wrong place.

    Raises:
        ValueError: if the payload cannot fit in a packet.
    """
    if not 0 <= pid <= MAX_PID:
        raise ValueError(f"PID must fit in 13 bits, got {pid}")
    if not 0 <= continuity_counter < CONTINUITY_MODULO:
        raise ValueError(f"continuity counter must be 0 to 15, got {continuity_counter}")

    available = TS_PACKET_SIZE - TS_HEADER_SIZE
    if len(payload) > available:
        raise ValueError(
            f"payload of {len(payload)} bytes does not fit in a packet, which has room "
            f"for {available}"
        )

    padding = available - len(payload)
    if padding and stuff_to_full_size:
        adaptation_control = 0b11
        # An adaptation field of length L occupies L+1 bytes: the length byte and L after it.
        # One byte of padding can only be the length byte itself, with a length of zero.
        adaptation = bytes([padding - 1]) + (
            bytes([0x00]) + b"\xff" * (padding - 2) if padding >= 2 else b""
        )
    else:
        adaptation_control = 0b01
        adaptation = b""

    header = bytes(
        [
            SYNC_BYTE,
            ((0x40 if payload_start else 0x00) | ((pid >> 8) & 0x1F)),
            pid & 0xFF,
            ((0x80 if scrambled else 0x00) | (adaptation_control << 4) | continuity_counter),
        ]
    )
    packet = header + adaptation + payload
    if len(packet) != TS_PACKET_SIZE:
        if stuff_to_full_size:
            raise AssertionError(f"built a {len(packet)}-byte packet, expected {TS_PACKET_SIZE}")
        packet = packet + b"\xff" * (TS_PACKET_SIZE - len(packet))
    return packet


def null_packet(continuity_counter: int = 0) -> bytes:
    """A padding packet, which keeps a multiplex at a constant bit rate."""
    return build_packet(NULL_PID, b"", continuity_counter=continuity_counter)


def iter_packets(stream: bytes, *, skip_malformed: bool = True) -> Iterator[TsPacket]:
    """Walk a transport stream, packet by packet.

    Args:
        stream: The bytes to read. Must be a whole number of packets.
        skip_malformed: Whether to pass over a packet that will not parse. A real stream off the
            air contains some, and stopping at the first would mean never reading a multiplex
            with a single corrupted packet in it.

    Raises:
        TransportStreamError: if the stream is not a whole number of packets, or if
            ``skip_malformed`` is false and a packet will not parse.
    """
    if len(stream) % TS_PACKET_SIZE != 0:
        raise TransportStreamError(
            f"a transport stream is a whole number of {TS_PACKET_SIZE}-byte packets; "
            f"got {len(stream)} bytes, which is {len(stream) % TS_PACKET_SIZE} too many"
        )

    for start in range(0, len(stream), TS_PACKET_SIZE):
        chunk = stream[start : start + TS_PACKET_SIZE]
        try:
            yield parse_packet(chunk)
        except TransportStreamError:
            if not skip_malformed:
                raise


def find_sync(stream: bytes, *, confirmations: int = 5) -> int | None:
    """Find where the packets start in an unaligned stream.

    A reader that joins a stream partway through lands in the middle of a packet. The sync byte
    is the only marker, and 0x47 occurs in payload data too, so a candidate is confirmed by
    checking that the byte recurs every 188 bytes a few times over.

    Returns the offset of the first packet, or ``None`` if no alignment holds up.
    """
    if confirmations < 1:
        raise ValueError(f"need at least one confirmation, got {confirmations}")

    for offset in range(min(TS_PACKET_SIZE, len(stream))):
        positions = [offset + index * TS_PACKET_SIZE for index in range(confirmations)]
        if positions[-1] >= len(stream):
            break
        if all(stream[position] == SYNC_BYTE for position in positions):
            return offset
    return None


def continuity_gap(previous: int, current: int) -> int:
    """How many packets went missing between two continuity counters.

    The counter is four bits and wraps, so the gap is modular. Zero means the expected next
    packet; a repeat of the same counter means a duplicate, which the standard allows.

    >>> continuity_gap(3, 4)
    0
    >>> continuity_gap(15, 0)
    0
    >>> continuity_gap(3, 7)
    3
    """
    return (current - previous - 1) % CONTINUITY_MODULO
