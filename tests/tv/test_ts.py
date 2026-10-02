"""Tests for the transport stream packet.

A transport stream arrives off the air, from whoever is transmitting, through a demodulator that
may have failed to repair it. So as well as the ordinary round trips, these tests feed the parser
the things a hostile or broken stream would contain: lengths that run past the end of a packet,
sync bytes in the wrong place, and streams that are not a whole number of packets.
"""

from __future__ import annotations

import pytest

from openwave.tv.ts import (
    CONTINUITY_MODULO,
    MAX_PID,
    NULL_PID,
    SYNC_BYTE,
    TS_HEADER_SIZE,
    TS_PACKET_SIZE,
    TransportStreamError,
    TsPacket,
    build_packet,
    continuity_gap,
    find_sync,
    iter_packets,
    null_packet,
    parse_packet,
)


class TestConstants:
    def test_a_packet_is_the_size_the_standard_fixes(self) -> None:
        # 188 bytes, from ISO/IEC 13818-1. Worth asserting rather than trusting nobody will
        # ever "tidy" it to 184 or 204.
        assert TS_PACKET_SIZE == 188
        assert SYNC_BYTE == 0x47
        assert MAX_PID == 0x1FFF


class TestRoundTrip:
    def test_a_payload_survives(self) -> None:
        packet = build_packet(0x0100, b"hello", payload_start=True, continuity_counter=3)
        assert len(packet) == TS_PACKET_SIZE
        decoded = parse_packet(packet)
        assert decoded.pid == 0x0100
        assert decoded.payload == b"hello"
        assert decoded.payload_start
        assert decoded.continuity_counter == 3

    def test_a_full_payload_needs_no_padding(self) -> None:
        payload = bytes(range(TS_PACKET_SIZE - TS_HEADER_SIZE))
        decoded = parse_packet(build_packet(0x0100, payload))
        assert decoded.payload == payload

    def test_a_short_payload_is_padded_with_an_adaptation_field(self) -> None:
        # Padding with arbitrary bytes instead would make the next section start in the wrong
        # place, which a reassembler cannot detect.
        decoded = parse_packet(build_packet(0x0100, b"ab"))
        assert decoded.payload == b"ab"

    def test_one_byte_of_padding_is_handled(self) -> None:
        # The awkward case: an adaptation field of length L takes L+1 bytes, so one byte of
        # padding can only be the length byte with a length of zero.
        payload = bytes(TS_PACKET_SIZE - TS_HEADER_SIZE - 1)
        assert parse_packet(build_packet(0x0100, payload)).payload == payload

    def test_a_null_packet_carries_nothing(self) -> None:
        decoded = parse_packet(null_packet())
        assert decoded.pid == NULL_PID
        assert decoded.is_null
        assert decoded.payload == b""

    def test_a_scrambled_packet_is_marked(self) -> None:
        decoded = parse_packet(build_packet(0x0100, b"secret", scrambled=True))
        assert decoded.scrambled
        assert not decoded.is_usable

    @pytest.mark.parametrize("pid", [0x0000, 0x0001, 0x0100, 0x1FFE, 0x1FFF])
    def test_every_pid_survives(self, pid: int) -> None:
        assert parse_packet(build_packet(pid, b"x")).pid == pid

    @pytest.mark.parametrize("counter", range(CONTINUITY_MODULO))
    def test_every_continuity_counter_survives(self, counter: int) -> None:
        decoded = parse_packet(build_packet(0x0100, b"x", continuity_counter=counter))
        assert decoded.continuity_counter == counter


class TestMalformedInput:
    @pytest.mark.parametrize("size", [0, 100, 187, 189, 376])
    def test_the_wrong_length_is_refused(self, size: int) -> None:
        with pytest.raises(TransportStreamError, match="is 188 bytes"):
            parse_packet(b"\x47" + b"\x00" * (size - 1) if size else b"")

    def test_a_missing_sync_byte_is_refused_with_a_hint(self) -> None:
        data = bytearray(build_packet(0x0100, b"x"))
        data[0] = 0x00
        with pytest.raises(TransportStreamError, match="not aligned"):
            parse_packet(bytes(data))

    def test_an_adaptation_field_running_past_the_packet_is_refused(self) -> None:
        # The length comes off the air. A parser that believes it reads into the next packet.
        data = bytearray(build_packet(0x0100, b"x"))
        data[3] = (data[3] & 0x0F) | 0x30  # adaptation field present
        data[4] = 200  # longer than the packet
        with pytest.raises(TransportStreamError, match="does not fit"):
            parse_packet(bytes(data))

    def test_a_payload_too_large_to_build_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not fit in a packet"):
            build_packet(0x0100, b"x" * 200)

    def test_a_pid_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 13 bits"):
            build_packet(0x2000, b"x")

    def test_a_continuity_counter_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be 0 to 15"):
            build_packet(0x0100, b"x", continuity_counter=16)


class TestPacketModel:
    def test_a_pid_outside_thirteen_bits_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 13 bits"):
            TsPacket(pid=0x2000)

    def test_a_flagged_error_makes_a_packet_unusable(self) -> None:
        # The demodulator could not repair it, so its contents are unknown rather than nearly
        # right. Parsing them would describe a multiplex that does not exist.
        assert not TsPacket(pid=0x100, payload=b"x", error=True).is_usable

    def test_an_empty_payload_is_unusable(self) -> None:
        assert not TsPacket(pid=0x100).is_usable

    def test_it_reads_sensibly_when_printed(self) -> None:
        printed = str(TsPacket(pid=0x100, payload=b"x" * 5, payload_start=True))
        assert "0x0100" in printed
        assert "5B" in printed


class TestIteration:
    def test_a_stream_of_packets_is_walked(self) -> None:
        stream = b"".join(build_packet(0x100 + index, b"x") for index in range(5))
        assert [packet.pid for packet in iter_packets(stream)] == [0x100 + i for i in range(5)]

    def test_a_stream_that_is_not_whole_packets_is_refused(self) -> None:
        with pytest.raises(TransportStreamError, match="whole number"):
            list(iter_packets(build_packet(0x100, b"x")[:-1]))

    def test_a_corrupt_packet_is_skipped_by_default(self) -> None:
        # A stream off the air contains some, and stopping at the first would mean never
        # reading a multiplex with one bad packet in it.
        good = build_packet(0x100, b"x")
        bad = bytearray(good)
        bad[0] = 0x00
        stream = good + bytes(bad) + good
        assert len(list(iter_packets(stream))) == 2

    def test_a_corrupt_packet_can_be_made_to_raise(self) -> None:
        bad = bytearray(build_packet(0x100, b"x"))
        bad[0] = 0x00
        with pytest.raises(TransportStreamError):
            list(iter_packets(bytes(bad), skip_malformed=False))


class TestFindSync:
    def test_an_aligned_stream_starts_at_zero(self) -> None:
        stream = build_packet(0x100, b"x") * 6
        assert find_sync(stream) == 0

    def test_an_offset_stream_is_found(self) -> None:
        # A reader joining a stream partway through lands mid-packet.
        stream = b"\xaa\xbb\xcc" + build_packet(0x100, b"x") * 6
        assert find_sync(stream) == 3

    def test_a_stray_sync_byte_is_not_mistaken_for_alignment(self) -> None:
        # 0x47 occurs in payload data too, which is why a candidate is confirmed by checking
        # that it recurs every 188 bytes.
        stream = b"\x47" + b"\x00" * 50 + build_packet(0x100, b"x") * 6
        assert find_sync(stream) == 51

    def test_a_stream_with_no_alignment_reports_none(self) -> None:
        assert find_sync(b"\x00" * 2000) is None

    def test_too_few_confirmations_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one confirmation"):
            find_sync(b"", confirmations=0)


class TestContinuity:
    def test_the_expected_next_packet_shows_no_gap(self) -> None:
        assert continuity_gap(3, 4) == 0

    def test_the_counter_wrapping_shows_no_gap(self) -> None:
        # Four bits, so it wraps every sixteen packets. Treating that as a loss of fifteen
        # would report constant packet loss on a perfect stream.
        assert continuity_gap(15, 0) == 0

    def test_missing_packets_are_counted(self) -> None:
        assert continuity_gap(3, 7) == 3

    def test_a_gap_across_the_wrap_is_counted(self) -> None:
        assert continuity_gap(14, 2) == 3
