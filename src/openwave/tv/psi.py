"""PSI sections: the tables that describe what a multiplex carries.

A multiplex is mostly video and audio, but a small part of it describes the rest. Those
descriptions are *sections*, and they arrive in the payloads of ordinary transport stream
packets, on reserved PIDs. A section can be larger than a packet, so it is spread across
several; several short sections can share one packet. :class:`SectionAssembler` puts them back
together.

A section's header gives a length, a version and a position in a sequence, and ends with a
CRC-32. The CRC matters more here than elsewhere: a corrupted section would describe a multiplex
that does not exist, and a receiver would show channels that are not there.

**Reassembly is where a parser gets attacked.** The length field says how long the section is,
and it comes off the air. A reassembler that allocates what the length claims can be made to
allocate four kilobytes per packet for ever; one that copies without checking reads past the end
of its buffer. Both are guarded here, and a section whose CRC fails is dropped rather than
repaired -- there is no error correction at this layer, so a failed CRC means the contents are
unknown, not nearly right.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Final

from openwave.tv.ts import TS_HEADER_SIZE, TS_PACKET_SIZE, TsPacket, build_packet

#: Bytes of header before a long section's payload, counting from the table identifier.
LONG_SECTION_HEADER_SIZE: Final = 8

#: Bytes of header before a short section's payload.
SHORT_SECTION_HEADER_SIZE: Final = 3

#: Bytes of CRC at the end of a long section.
CRC_SIZE: Final = 4

#: Largest a section may be, including its header and CRC. Fixed by ISO/IEC 13818-1.
#:
#: The limit is what makes reassembly safe: a stream claiming a longer section is malformed, and
#: refusing it costs nothing, where believing it would let a transmitter decide how much memory
#: a receiver allocates.
MAX_SECTION_SIZE: Final = 4096

#: Generator polynomial of the MPEG-2 CRC-32, as used by every PSI table.
CRC32_POLYNOMIAL: Final = 0x04C11DB7

#: Table identifiers OpenWave reads.
PAT_TABLE_ID: Final = 0x00
PMT_TABLE_ID: Final = 0x02
NIT_ACTUAL_TABLE_ID: Final = 0x40
NIT_OTHER_TABLE_ID: Final = 0x41
SDT_ACTUAL_TABLE_ID: Final = 0x42
SDT_OTHER_TABLE_ID: Final = 0x46

#: Byte that marks an unused run at the end of a packet's section area.
STUFFING_BYTE: Final = 0xFF


class SectionError(ValueError):
    """A PSI section is malformed."""


def crc32_mpeg(data: bytes) -> int:
    """The CRC-32 every PSI table ends with.

    Not the CRC-32 of zip files or Ethernet: MPEG uses the same polynomial but feeds the bits in
    the other order, starts from all ones, and does not invert the result. Using the familiar
    one instead rejects every real transmission.

    >>> f"{crc32_mpeg(b''):08X}"
    'FFFFFFFF'
    """
    register = 0xFFFFFFFF
    for byte in data:
        register ^= byte << 24
        for _ in range(8):
            if register & 0x80000000:
                register = ((register << 1) ^ CRC32_POLYNOMIAL) & 0xFFFFFFFF
            else:
                register = (register << 1) & 0xFFFFFFFF
    return register


@dataclass(frozen=True, slots=True)
class Section:
    """One reassembled PSI section.

    Attributes:
        table_id: Which kind of table this is.
        payload: The bytes between the header and the CRC, which the table's own parser reads.
        extension: The table identifier extension, whose meaning depends on the table: the
            transport stream identifier in a PAT, the programme number in a PMT, the network
            identifier in a NIT.
        version: Increments when the table's contents change, so a receiver knows to re-read it.
        current: Whether this version is in force now, or describes a change still to come.
        section_number: Position in a table split across several sections.
        last_section_number: The highest section number of this table.
        long: Whether the section carried the long header, and so an extension and a CRC.
    """

    table_id: int
    payload: bytes
    extension: int = 0
    version: int = 0
    current: bool = True
    section_number: int = 0
    last_section_number: int = 0
    long: bool = True

    @property
    def is_complete_table(self) -> bool:
        """Whether this section is the whole table rather than one part of several."""
        return self.last_section_number == 0

    def __str__(self) -> str:
        position = (
            ""
            if self.is_complete_table
            else f" {self.section_number + 1}/{self.last_section_number + 1}"
        )
        return (
            f"table {self.table_id:#04x} ext {self.extension:#06x} "
            f"v{self.version}{position} {len(self.payload)}B"
        )


def parse_section(data: bytes) -> Section:
    """Decode one complete section, checking its CRC.

    Raises:
        SectionError: if the section is truncated, longer than the standard allows, or its CRC
            does not match.
    """
    if len(data) < SHORT_SECTION_HEADER_SIZE:
        raise SectionError(f"a section needs at least {SHORT_SECTION_HEADER_SIZE} bytes")

    table_id = data[0]
    long_form = bool(data[1] & 0x80)
    section_length = ((data[1] & 0x0F) << 8) | data[2]
    total = SHORT_SECTION_HEADER_SIZE + section_length

    if total > MAX_SECTION_SIZE:
        raise SectionError(
            f"section claims {total} bytes, more than the {MAX_SECTION_SIZE} the standard allows"
        )
    if len(data) < total:
        raise SectionError(f"section claims {total} bytes but only {len(data)} are present")

    body = data[:total]
    if not long_form:
        return Section(
            table_id=table_id,
            payload=body[SHORT_SECTION_HEADER_SIZE:],
            long=False,
        )

    if section_length < LONG_SECTION_HEADER_SIZE - SHORT_SECTION_HEADER_SIZE + CRC_SIZE:
        raise SectionError(
            f"a long section needs room for its header and CRC; it claims {section_length} bytes"
        )

    expected = crc32_mpeg(body[:-CRC_SIZE])
    found = struct.unpack(">I", body[-CRC_SIZE:])[0]
    if expected != found:
        raise SectionError(
            f"CRC mismatch in table {table_id:#04x}: computed {expected:#010x}, "
            f"the section says {found:#010x}"
        )

    extension = struct.unpack(">H", body[3:5])[0]
    version = (body[5] >> 1) & 0x1F
    current = bool(body[5] & 0x01)
    return Section(
        table_id=table_id,
        payload=body[LONG_SECTION_HEADER_SIZE:-CRC_SIZE],
        extension=extension,
        version=version,
        current=current,
        section_number=body[6],
        last_section_number=body[7],
        long=True,
    )


def build_section(
    table_id: int,
    payload: bytes,
    *,
    extension: int = 0,
    version: int = 0,
    current: bool = True,
    section_number: int = 0,
    last_section_number: int = 0,
) -> bytes:
    """Build one long-form section, CRC included.

    Raises:
        ValueError: if the result would exceed what the standard allows.
    """
    if not 0 <= table_id <= 0xFF:
        raise ValueError(f"table identifier must be one byte, got {table_id}")
    if not 0 <= extension <= 0xFFFF:
        raise ValueError(f"extension must fit in 16 bits, got {extension}")
    if not 0 <= version <= 0x1F:
        raise ValueError(f"version must fit in 5 bits, got {version}")

    section_length = LONG_SECTION_HEADER_SIZE - SHORT_SECTION_HEADER_SIZE + len(payload) + CRC_SIZE
    total = SHORT_SECTION_HEADER_SIZE + section_length
    if total > MAX_SECTION_SIZE:
        raise ValueError(
            f"section would be {total} bytes, more than the {MAX_SECTION_SIZE} allowed"
        )

    header = bytes(
        [
            table_id,
            0x80 | 0x30 | ((section_length >> 8) & 0x0F),
            section_length & 0xFF,
            (extension >> 8) & 0xFF,
            extension & 0xFF,
            0xC0 | (version << 1) | (1 if current else 0),
            section_number,
            last_section_number,
        ]
    )
    body = header + payload
    return body + struct.pack(">I", crc32_mpeg(body))


@dataclass
class SectionAssembler:
    """Collects transport stream payloads into complete sections.

    A section can span packets, and several short sections can share one. The packet that starts
    a section sets its payload-start flag and begins with a pointer saying how far into the
    payload the section starts -- the bytes before it belong to the section that ended in the
    previous packet.

    Example::

        assembler = SectionAssembler()
        for packet in iter_packets(stream):
            for section in assembler.feed(packet):
                ...

    Attributes:
        rejected: Sections dropped for a failed CRC or a malformed length. A multiplex with a
            steady trickle of these is one being received badly, which is worth telling a user
            rather than silently showing them half a channel list.
    """

    rejected: int = 0
    _buffers: dict[int, bytearray] = field(default_factory=dict, repr=False)
    _wanted: dict[int, int] = field(default_factory=dict, repr=False)

    def feed(self, packet: TsPacket) -> list[Section]:
        """Take one packet and return whatever sections it completed."""
        if not packet.is_usable or packet.is_null:
            return []

        payload = packet.payload
        completed: list[Section] = []

        if packet.payload_start:
            pointer = payload[0]
            # The pointer is attacker-controlled, so it is checked before being used as an index.
            if 1 + pointer > len(payload):
                self.rejected += 1
                self._discard(packet.pid)
                return []
            tail = payload[1 : 1 + pointer]
            if tail:
                completed.extend(self._continue(packet.pid, tail, finish=True))
            self._discard(packet.pid)
            completed.extend(self._start(packet.pid, payload[1 + pointer :]))
        else:
            completed.extend(self._continue(packet.pid, payload))

        return completed

    def _start(self, pid: int, data: bytes) -> list[Section]:
        """Begin one or more sections at the start of a packet's section area."""
        completed: list[Section] = []
        offset = 0
        while offset < len(data):
            if data[offset] == STUFFING_BYTE:
                break  # the rest of the packet is padding
            remaining = data[offset:]
            if len(remaining) < SHORT_SECTION_HEADER_SIZE:
                # Not enough to know how long the section is; keep it for the next packet.
                self._buffers[pid] = bytearray(remaining)
                self._wanted[pid] = 0
                break

            length = ((remaining[1] & 0x0F) << 8) | remaining[2]
            total = SHORT_SECTION_HEADER_SIZE + length
            if total > MAX_SECTION_SIZE:
                self.rejected += 1
                break

            if total <= len(remaining):
                section = self._decode(remaining[:total])
                if section is not None:
                    completed.append(section)
                offset += total
                continue

            self._buffers[pid] = bytearray(remaining)
            self._wanted[pid] = total
            break
        return completed

    def _continue(self, pid: int, data: bytes, *, finish: bool = False) -> list[Section]:
        """Add the continuation of a section already under way."""
        buffer = self._buffers.get(pid)
        if buffer is None:
            return []

        buffer.extend(data)
        wanted = self._wanted.get(pid, 0)

        if wanted == 0 and len(buffer) >= SHORT_SECTION_HEADER_SIZE:
            wanted = SHORT_SECTION_HEADER_SIZE + (((buffer[1] & 0x0F) << 8) | buffer[2])
            if wanted > MAX_SECTION_SIZE:
                self.rejected += 1
                self._discard(pid)
                return []
            self._wanted[pid] = wanted

        if wanted and len(buffer) >= wanted:
            section = self._decode(bytes(buffer[:wanted]))
            self._discard(pid)
            return [section] if section is not None else []

        if finish:
            # The packet said this section ended here, but it is short. Something was lost.
            self.rejected += 1
            self._discard(pid)
        return []

    def _decode(self, data: bytes) -> Section | None:
        """Parse a complete section, counting a rejection rather than raising."""
        try:
            return parse_section(data)
        except SectionError:
            self.rejected += 1
            return None

    def _discard(self, pid: int) -> None:
        """Forget any part-assembled section on this PID."""
        self._buffers.pop(pid, None)
        self._wanted.pop(pid, None)

    @property
    def pending(self) -> int:
        """How many sections are part-assembled, waiting for more packets."""
        return len(self._buffers)

    def reset(self) -> None:
        """Forget everything, for retuning to another multiplex."""
        self._buffers.clear()
        self._wanted.clear()
        self.rejected = 0


def pack_section(pid: int, section: bytes, *, continuity_start: int = 0) -> bytes:
    """Split a section across as many packets as it needs.

    The first packet sets the payload-start flag and begins with a pointer of zero, meaning the
    section starts immediately.
    """
    available = TS_PACKET_SIZE - TS_HEADER_SIZE
    packets: list[bytes] = []
    remaining = b"\x00" + section  # the pointer field
    counter = continuity_start
    first = True

    while remaining:
        chunk = remaining[:available]
        remaining = remaining[available:]
        packets.append(
            build_packet(
                pid,
                chunk,
                payload_start=first,
                continuity_counter=counter % 16,
            )
        )
        counter += 1
        first = False
    return b"".join(packets)
