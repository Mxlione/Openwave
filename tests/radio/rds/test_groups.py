"""Tests for what RDS blocks mean, and for assembling fragments into a station's identity."""

from __future__ import annotations

import pytest

from openwave.radio.rds.blocks import Block, BlockOffset
from openwave.radio.rds.groups import (
    PS_LENGTH,
    RADIOTEXT_TERMINATOR,
    Group,
    GroupVersion,
    ProgrammeType,
    RdsData,
    RdsReceiver,
    decode_group,
    rds_text,
)


def blocks(
    first: int, second: int, third: int, fourth: int, *, version_b: bool = False
) -> tuple[Block, ...]:
    """Four decoded blocks with the offsets their positions require."""
    third_offset = BlockOffset.C_PRIME if version_b else BlockOffset.C
    return (
        Block(information=first, offset=BlockOffset.A),
        Block(information=second, offset=BlockOffset.B),
        Block(information=third, offset=third_offset),
        Block(information=fourth, offset=BlockOffset.D),
    )


def second_block(
    *, group_type: int, version_b: bool = False, tp: bool = False, pty: int = 0, low: int = 0
) -> int:
    """Assemble the second block, whose layout every group type shares."""
    return (
        ((group_type & 0xF) << 12)
        | ((1 if version_b else 0) << 11)
        | ((1 if tp else 0) << 10)
        | ((pty & 0x1F) << 5)
        | (low & 0x1F)
    )


def name_group(segment: int, pair: str, *, pi: int = 0x1234, pty: int = 10) -> tuple[Block, ...]:
    """A group 0A carrying two characters of a station name."""
    return blocks(
        pi,
        second_block(group_type=0, pty=pty, low=segment),
        0xE0E0,
        (ord(pair[0]) << 8) | ord(pair[1]),
    )


def text_group(segment: int, chunk: str, *, pi: int = 0x1234, flag: int = 0) -> tuple[Block, ...]:
    """A group 2A carrying four characters of RadioText."""
    return blocks(
        pi,
        second_block(group_type=2, low=(flag << 4) | (segment & 0xF)),
        (ord(chunk[0]) << 8) | ord(chunk[1]),
        (ord(chunk[2]) << 8) | ord(chunk[3]),
    )


class TestDecodeGroup:
    def test_it_reads_the_fields_every_group_shares(self) -> None:
        group = decode_group(
            blocks(0xF201, second_block(group_type=4, tp=True, pty=20), 0x0000, 0x0000)
        )
        assert group is not None
        assert group.pi == 0xF201
        assert group.group_type == 4
        assert group.version is GroupVersion.A
        assert group.traffic_programme is True
        assert group.pty == 20
        assert group.name == "4A"

    def test_a_version_b_group_is_recognised(self) -> None:
        group = decode_group(
            blocks(
                0x1234, second_block(group_type=0, version_b=True), 0x1234, 0x4142, version_b=True
            )
        )
        assert group is not None
        assert group.version is GroupVersion.B
        assert group.name == "0B"

    def test_a_version_flag_disagreeing_with_the_offset_is_rejected(self) -> None:
        # A version B group must carry the C' offset in its third block. If the two disagree,
        # the decoder has lost synchronisation and is reading across a block boundary.
        mismatched = blocks(
            0x1234, second_block(group_type=0, version_b=True), 0x1234, 0x4142, version_b=False
        )
        assert decode_group(mismatched) is None

    def test_offsets_in_the_wrong_order_are_rejected(self) -> None:
        wrong = (
            Block(information=0, offset=BlockOffset.B),
            Block(information=0, offset=BlockOffset.A),
            Block(information=0, offset=BlockOffset.C),
            Block(information=0, offset=BlockOffset.D),
        )
        assert decode_group(wrong) is None

    def test_the_wrong_number_of_blocks_is_rejected(self) -> None:
        assert decode_group(blocks(0, 0, 0, 0)[:3]) is None

    def test_corrections_are_totalled_across_the_group(self) -> None:
        # Worth keeping: a station whose name keeps changing is usually a weak signal rather
        # than a decoder bug, and the correction count is how you tell.
        damaged = (
            Block(information=0x1234, offset=BlockOffset.A, corrected_bits=1),
            Block(information=0, offset=BlockOffset.B, corrected_bits=2),
            Block(information=0, offset=BlockOffset.C),
            Block(information=0, offset=BlockOffset.D, corrected_bits=3),
        )
        group = decode_group(damaged)
        assert group is not None
        assert group.corrected_bits == 6
        assert not group.is_clean


class TestProgrammeType:
    def test_the_assignments_match_the_european_table(self) -> None:
        assert ProgrammeType(0) is ProgrammeType.NONE
        assert ProgrammeType(10) is ProgrammeType.POP_MUSIC
        assert ProgrammeType(31) is ProgrammeType.ALARM

    def test_it_has_a_readable_label(self) -> None:
        assert ProgrammeType.POP_MUSIC.label == "Pop Music"
        assert ProgrammeType.CURRENT_AFFAIRS.label == "Current Affairs"

    def test_all_thirty_two_values_are_defined(self) -> None:
        assert {int(member) for member in ProgrammeType} == set(range(32))


class TestRdsText:
    @pytest.mark.parametrize(("code", "expected"), [(0x41, "A"), (0x20, " "), (0x7E, "~")])
    def test_printable_codes_match_ascii(self, code: int, expected: str) -> None:
        assert rds_text(code) == expected

    @pytest.mark.parametrize("code", [0x00, 0x1F, 0x7F, 0xE0])
    def test_anything_else_becomes_a_space(self, code: int) -> None:
        # A name is going on a display, and a run of replacement glyphs reads as a fault.
        assert rds_text(code) == " "


class TestProgrammeServiceName:
    def test_a_name_appears_only_once_every_segment_has_arrived(self) -> None:
        # Publishing fragments would spell a station's name wrongly on screen for the second
        # or two before the rest arrives.
        receiver = RdsReceiver()
        for segment, pair in enumerate(["OP", "EN", "WA"]):
            receiver.accept(_group(name_group(segment, pair)))
            assert receiver.data.programme_service is None

        receiver.accept(_group(name_group(3, "VE")))
        assert receiver.data.programme_service == "OPENWAVE"

    def test_segments_can_arrive_in_any_order(self) -> None:
        # Which they do: a receiver tunes in partway through the sequence.
        receiver = RdsReceiver()
        for segment, pair in [(2, "WA"), (0, "OP"), (3, "VE"), (1, "EN")]:
            receiver.accept(_group(name_group(segment, pair)))
        assert receiver.data.programme_service == "OPENWAVE"

    def test_a_short_name_is_trimmed_of_its_padding(self) -> None:
        receiver = RdsReceiver()
        for segment, pair in enumerate(["BB", "C ", "  ", "  "]):
            receiver.accept(_group(name_group(segment, pair)))
        assert receiver.data.programme_service == "BBC"

    def test_a_name_of_nothing_but_padding_reads_as_no_name(self) -> None:
        receiver = RdsReceiver()
        for segment in range(PS_LENGTH // 2):
            receiver.accept(_group(name_group(segment, "  ")))
        assert receiver.data.programme_service is None

    def test_a_later_segment_replaces_an_earlier_one(self) -> None:
        receiver = RdsReceiver()
        for segment, pair in enumerate(["OP", "EN", "WA", "VE"]):
            receiver.accept(_group(name_group(segment, pair)))
        receiver.accept(_group(name_group(0, "XX")))
        assert receiver.data.programme_service == "XXENWAVE"


class TestRadioText:
    def test_it_is_assembled_from_its_segments(self) -> None:
        receiver = RdsReceiver()
        for segment, chunk in enumerate(["Open", " sou", "rce ", "radio"[:4]]):
            receiver.accept(_group(text_group(segment, chunk)))
        assert receiver.data.radio_text == "Open source radi"

    def test_it_is_reported_before_every_segment_has_arrived(self) -> None:
        # Unlike a name. Messages are long and often shorter than the full 64 characters, so
        # waiting for every segment would mean never showing any.
        receiver = RdsReceiver()
        receiver.accept(_group(text_group(0, "Open")))
        assert receiver.data.radio_text == "Open"

    def test_a_carriage_return_marks_the_end(self) -> None:
        receiver = RdsReceiver()
        receiver.accept(_group(text_group(0, "Open")))
        receiver.accept(_group(text_group(1, " " + chr(RADIOTEXT_TERMINATOR) + "ZZ")))
        assert receiver.data.radio_text == "Open"

    def test_the_text_flag_toggling_discards_what_was_collected(self) -> None:
        # The flag toggles when the station changes the message. Keeping the old fragments
        # would splice half of one message onto half of another.
        receiver = RdsReceiver()
        receiver.accept(_group(text_group(0, "Old ")))
        receiver.accept(_group(text_group(1, "text")))
        assert receiver.data.radio_text == "Old text"

        receiver.accept(_group(text_group(0, "New!", flag=1)))
        assert receiver.data.radio_text == "New!"


class TestAccumulatedData:
    def test_fields_not_yet_received_are_none_rather_than_empty(self) -> None:
        empty = RdsData()
        assert empty.pi is None
        assert empty.pty is None
        assert not empty.has_name
        assert empty.programme_type is None
        assert str(empty) == "no RDS data"

    def test_identity_fields_come_from_any_group_type(self) -> None:
        receiver = RdsReceiver()
        receiver.accept(_group(blocks(0xF201, second_block(group_type=8, tp=True, pty=20), 0, 0)))
        data = receiver.data
        assert data.pi == 0xF201
        assert data.pty == 20
        assert data.traffic_programme is True
        assert data.programme_type is ProgrammeType.RELIGION

    def test_it_counts_groups_and_repairs(self) -> None:
        receiver = RdsReceiver()
        receiver.accept(_group(name_group(0, "OP")))
        damaged = (
            Block(information=0x1234, offset=BlockOffset.A, corrected_bits=2),
            Block(information=second_block(group_type=0, low=1), offset=BlockOffset.B),
            Block(information=0xE0E0, offset=BlockOffset.C),
            Block(information=0x454E, offset=BlockOffset.D),
        )
        receiver.accept(_group(damaged))
        data = receiver.data
        assert data.groups_decoded == 2
        assert data.groups_corrected == 1

    def test_resetting_forgets_everything(self) -> None:
        receiver = RdsReceiver()
        for segment, pair in enumerate(["OP", "EN", "WA", "VE"]):
            receiver.accept(_group(name_group(segment, pair)))
        receiver.reset()
        assert receiver.data.programme_service is None
        assert receiver.data.pi is None
        assert receiver.data.groups_decoded == 0

    def test_it_summarises_itself_readably(self) -> None:
        receiver = RdsReceiver()
        for segment, pair in enumerate(["CI", "TY", " F", "M "]):
            receiver.accept(_group(name_group(segment, pair, pi=0xF201)))
        printed = str(receiver.data)
        assert "CITY FM" in printed
        assert "F201" in printed
        assert "Pop Music" in printed


def _group(raw: tuple[Block, ...]) -> Group:
    """Decode a tuple of blocks into a group, asserting it is well formed."""
    group = decode_group(raw)
    assert group is not None
    return group
