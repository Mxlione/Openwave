"""Tests for PSI sections and their reassembly.

The CRC and the length field are where a parser gets attacked. A section's length comes off the
air, and a reassembler that believes it can be made to allocate without limit or to read past
the end of its buffer. These tests feed it those cases.
"""

from __future__ import annotations

import pytest

from openwave.tv.psi import (
    CRC_SIZE,
    MAX_SECTION_SIZE,
    PAT_TABLE_ID,
    SDT_ACTUAL_TABLE_ID,
    Section,
    SectionAssembler,
    SectionError,
    build_section,
    crc32_mpeg,
    pack_section,
    parse_section,
)
from openwave.tv.ts import TS_PACKET_SIZE, build_packet, iter_packets, null_packet, parse_packet


class TestCrc:
    def test_an_empty_input_gives_all_ones(self) -> None:
        # The MPEG CRC starts from all ones and does not invert its result, unlike the CRC-32
        # of zip files. Using the familiar one rejects every real transmission.
        assert crc32_mpeg(b"") == 0xFFFFFFFF

    def test_it_differs_from_the_common_crc32(self) -> None:
        import zlib

        assert crc32_mpeg(b"OpenWave") != zlib.crc32(b"OpenWave")

    def test_it_fits_in_thirty_two_bits(self) -> None:
        assert 0 <= crc32_mpeg(b"OpenWave" * 100) <= 0xFFFFFFFF

    def test_changing_one_bit_changes_the_result(self) -> None:
        assert crc32_mpeg(b"\x00") != crc32_mpeg(b"\x01")

    def test_data_followed_by_its_own_crc_always_gives_the_same_remainder(self) -> None:
        # The property that makes the check work: appending the CRC leaves a constant
        # remainder, whatever the data was. A receiver can therefore verify a section without
        # separating its body from its CRC first.
        import struct

        remainders = {
            crc32_mpeg(body + struct.pack(">I", crc32_mpeg(body)))
            for body in (b"", b"\x00", b"OpenWave", bytes(range(100)))
        }
        assert len(remainders) == 1


class TestSectionRoundTrip:
    def test_a_section_survives(self) -> None:
        built = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00", extension=0x1234, version=5)
        section = parse_section(built)
        assert section.table_id == PAT_TABLE_ID
        assert section.payload == b"\x00\x01\xe1\x00"
        assert section.extension == 0x1234
        assert section.version == 5
        assert section.current

    def test_an_empty_payload_is_allowed(self) -> None:
        assert parse_section(build_section(PAT_TABLE_ID, b"")).payload == b""

    def test_the_largest_allowed_section_survives(self) -> None:
        payload = b"x" * (MAX_SECTION_SIZE - 3 - 5 - CRC_SIZE)
        assert parse_section(build_section(0x42, payload)).payload == payload

    def test_a_multi_part_table_records_its_position(self) -> None:
        built = build_section(0x42, b"x", section_number=2, last_section_number=4)
        section = parse_section(built)
        assert section.section_number == 2
        assert section.last_section_number == 4
        assert not section.is_complete_table

    def test_a_section_describing_a_future_change_is_marked(self) -> None:
        # A receiver must not act on a table that says it is not yet in force.
        assert not parse_section(build_section(0x42, b"x", current=False)).current

    def test_it_reads_sensibly_when_printed(self) -> None:
        printed = str(parse_section(build_section(0x42, b"xyz", extension=0xABCD, version=3)))
        assert "0x42" in printed
        assert "0xabcd" in printed
        assert "v3" in printed


class TestMalformedSections:
    def test_a_corrupted_crc_is_rejected_not_repaired(self) -> None:
        # There is no error correction at this layer, so a failed CRC means the contents are
        # unknown, not nearly right. A corrupted table would describe channels that are not
        # there.
        built = bytearray(build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00"))
        built[-1] ^= 0xFF
        with pytest.raises(SectionError, match="CRC mismatch"):
            parse_section(bytes(built))

    def test_a_corrupted_payload_is_caught_by_the_crc(self) -> None:
        built = bytearray(build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00"))
        built[8] ^= 0xFF
        with pytest.raises(SectionError, match="CRC mismatch"):
            parse_section(bytes(built))

    def test_a_truncated_section_is_refused(self) -> None:
        built = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00")
        with pytest.raises(SectionError, match="only"):
            parse_section(built[:-2])

    def test_a_section_shorter_than_a_header_is_refused(self) -> None:
        with pytest.raises(SectionError, match="at least 3 bytes"):
            parse_section(b"\x00")

    def test_a_length_beyond_what_the_standard_allows_is_refused(self) -> None:
        # The limit is what makes reassembly safe: without it, a transmitter decides how much
        # memory a receiver allocates.
        data = bytearray(b"\x42\xff\xff")
        data.extend(b"\x00" * 5000)
        with pytest.raises(SectionError, match="more than the 4096"):
            parse_section(bytes(data))

    def test_building_a_section_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="more than the 4096"):
            build_section(0x42, b"x" * MAX_SECTION_SIZE)

    def test_a_version_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 5 bits"):
            build_section(0x42, b"x", version=32)


class TestReassembly:
    def test_a_section_in_one_packet_is_recovered(self) -> None:
        section = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00", extension=0x1234)
        assembler = SectionAssembler()
        recovered = [
            found
            for packet in iter_packets(pack_section(0, section))
            for found in assembler.feed(packet)
        ]
        assert len(recovered) == 1
        assert recovered[0].extension == 0x1234

    def test_a_section_spanning_packets_is_recovered(self) -> None:
        payload = bytes(range(256)) * 3
        section = build_section(SDT_ACTUAL_TABLE_ID, payload)
        stream = pack_section(0x11, section)
        assert len(stream) // TS_PACKET_SIZE > 1

        assembler = SectionAssembler()
        recovered = [f for packet in iter_packets(stream) for f in assembler.feed(packet)]
        assert len(recovered) == 1
        assert recovered[0].payload == payload

    def test_two_short_sections_sharing_a_packet_are_both_recovered(self) -> None:
        first = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00", extension=1)
        second = build_section(PAT_TABLE_ID, b"\x00\x02\xe1\x01", extension=2)
        packet = build_packet(0x0000, b"\x00" + first + second, payload_start=True)

        assembler = SectionAssembler()
        recovered = assembler.feed(parse_packet(packet))
        assert [section.extension for section in recovered] == [1, 2]

    def test_padding_after_a_section_is_ignored(self) -> None:
        # A real stream fills the rest of a packet with 0xFF, which is not the start of a table.
        section = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00")
        payload = b"\x00" + section + b"\xff" * 20
        assembler = SectionAssembler()
        assert len(assembler.feed(parse_packet(build_packet(0, payload, payload_start=True)))) == 1

    def test_null_packets_are_ignored(self) -> None:
        assembler = SectionAssembler()
        assert assembler.feed(parse_packet(null_packet())) == []

    def test_a_scrambled_packet_is_ignored(self) -> None:
        assembler = SectionAssembler()
        packet = parse_packet(build_packet(0x11, b"\x00\x42", payload_start=True, scrambled=True))
        assert assembler.feed(packet) == []

    def test_a_pointer_running_past_the_payload_is_rejected(self) -> None:
        # The pointer comes off the air, so it is checked before being used as an index.
        packet = parse_packet(build_packet(0x0000, b"\xff" + b"\x00" * 10, payload_start=True))
        assembler = SectionAssembler()
        assert assembler.feed(packet) == []
        assert assembler.rejected == 1

    def test_a_section_with_a_bad_crc_is_counted_as_rejected(self) -> None:
        # Worth counting: a multiplex with a steady trickle of these is being received badly,
        # which is worth telling a user rather than silently showing half a channel list.
        broken = bytearray(build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00"))
        broken[-1] ^= 0xFF
        assembler = SectionAssembler()
        recovered = [
            f
            for packet in iter_packets(pack_section(0, bytes(broken)))
            for f in assembler.feed(packet)
        ]
        assert recovered == []
        assert assembler.rejected == 1

    def test_a_new_section_abandons_an_unfinished_one(self) -> None:
        # Which is what happens when packets are lost: the next payload-start flag means the
        # previous section will never be completed.
        long_section = build_section(0x42, bytes(400))
        stream = pack_section(0x11, long_section)
        first_packet = parse_packet(stream[:TS_PACKET_SIZE])

        assembler = SectionAssembler()
        assembler.feed(first_packet)
        assert assembler.pending == 1

        short = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00")
        recovered = assembler.feed(
            parse_packet(build_packet(0x11, b"\x00" + short, payload_start=True))
        )
        assert len(recovered) == 1

    def test_resetting_forgets_everything(self) -> None:
        long_section = build_section(0x42, bytes(400))
        stream = pack_section(0x11, long_section)
        assembler = SectionAssembler()
        assembler.feed(parse_packet(stream[:TS_PACKET_SIZE]))
        assembler.reset()
        assert assembler.pending == 0
        assert assembler.rejected == 0


class TestPacking:
    def test_a_short_section_takes_one_packet(self) -> None:
        section = build_section(PAT_TABLE_ID, b"\x00\x01\xe1\x00")
        assert len(pack_section(0, section)) == TS_PACKET_SIZE

    def test_a_long_section_takes_as_many_as_it_needs(self) -> None:
        section = build_section(0x42, bytes(600))
        stream = pack_section(0x11, section)
        assert len(stream) % TS_PACKET_SIZE == 0
        assert len(stream) // TS_PACKET_SIZE >= 4

    def test_only_the_first_packet_starts_a_section(self) -> None:
        stream = pack_section(0x11, build_section(0x42, bytes(600)))
        starts = [packet.payload_start for packet in iter_packets(stream)]
        assert starts[0]
        assert not any(starts[1:])

    def test_continuity_counters_advance(self) -> None:
        stream = pack_section(0x11, build_section(0x42, bytes(600)), continuity_start=5)
        counters = [packet.continuity_counter for packet in iter_packets(stream)]
        assert counters[0] == 5
        assert counters == [(5 + index) % 16 for index in range(len(counters))]


def test_a_section_model_can_be_built_directly() -> None:
    section = Section(table_id=0x42, payload=b"x", extension=7)
    assert section.table_id == 0x42
    assert section.is_complete_table
