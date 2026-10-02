"""Tests for the RDS block: its check word, its offsets and its error correction.

The check word does three jobs -- detect errors, mark block positions, and repair damage -- and
each is tested separately, because a bug in one would look like a bug in the others.
"""

from __future__ import annotations

import pytest

from openwave.radio.rds.blocks import (
    BLOCK_BITS,
    CHECKWORD_BITS,
    GENERATOR,
    INFORMATION_BITS,
    MAX_CORRECTABLE_BURST,
    Block,
    BlockOffset,
    checkword,
    correct_block,
    decode_block,
    encode_block,
    matching_offsets,
    syndrome,
)

#: A handful of information words, including the awkward extremes.
WORDS = (0x0000, 0x0001, 0x1234, 0xABCD, 0xFFFF, 0x8000)


class TestConstants:
    def test_a_block_is_sixteen_bits_of_data_and_ten_of_protection(self) -> None:
        assert INFORMATION_BITS + CHECKWORD_BITS == BLOCK_BITS
        assert BLOCK_BITS == 26

    def test_the_generator_is_the_polynomial_the_standard_specifies(self) -> None:
        # x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1, from IEC 62106. Getting a single bit wrong
        # here makes a decoder that rejects every real transmission, so the exponents are
        # written out rather than compared against another way of spelling the same number.
        exponents = {index for index in range(CHECKWORD_BITS + 1) if GENERATOR & (1 << index)}
        assert exponents == {0, 3, 4, 5, 7, 8, 10}

    def test_the_offset_words_are_the_ones_the_standard_specifies(self) -> None:
        assert BlockOffset.A == 0b00_1111_1100
        assert BlockOffset.B == 0b01_1001_1000
        assert BlockOffset.C == 0b01_0110_1000
        assert BlockOffset.C_PRIME == 0b11_0101_0000
        assert BlockOffset.D == 0b01_1011_0100

    def test_the_offsets_are_far_apart(self) -> None:
        # They have to be: the decoder tells one block position from another by which offset
        # matches, so two close together would be confusable under a few bit errors.
        offsets = list(BlockOffset)
        for index, first in enumerate(offsets):
            for second in offsets[index + 1 :]:
                assert (int(first) ^ int(second)).bit_count() >= 2

    def test_the_prime_offset_prints_as_the_standard_writes_it(self) -> None:
        assert BlockOffset.C_PRIME.label == "C'"
        assert BlockOffset.A.label == "A"


class TestCheckword:
    @pytest.mark.parametrize("word", WORDS)
    def test_it_fits_in_ten_bits(self, word: int) -> None:
        assert 0 <= checkword(word) < (1 << CHECKWORD_BITS)

    def test_zero_information_gives_zero_check(self) -> None:
        assert checkword(0x0000) == 0

    def test_it_is_linear(self) -> None:
        # The code is linear over GF(2), which is what makes the syndrome both an integrity
        # check and a position marker. If this fails, nothing else about the scheme works.
        assert checkword(0x1234 ^ 0xABCD) == checkword(0x1234) ^ checkword(0xABCD)

    def test_a_word_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 16 bits"):
            checkword(0x1_0000)


class TestSyndrome:
    @pytest.mark.parametrize("word", WORDS)
    @pytest.mark.parametrize("offset", list(BlockOffset))
    def test_an_undamaged_block_has_its_offset_as_its_syndrome(
        self, word: int, offset: BlockOffset
    ) -> None:
        # The property the whole synchronisation scheme rests on.
        assert syndrome(encode_block(word, offset)) == int(offset)

    def test_a_block_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 26 bits"):
            syndrome(1 << BLOCK_BITS)

    def test_it_identifies_which_offset_a_block_carries(self) -> None:
        for offset in BlockOffset:
            assert matching_offsets(encode_block(0x1234, offset)) == (offset,)

    def test_random_bits_rarely_match_any_offset(self) -> None:
        # About five chances in 1024, which is what makes four matches in a row a sound
        # signpost for a block boundary.
        import random

        rng = random.Random(0)
        matches = sum(1 for _ in range(20_000) if matching_offsets(rng.getrandbits(BLOCK_BITS)))
        assert matches / 20_000 < 0.02


class TestEncodeAndDecode:
    @pytest.mark.parametrize("word", WORDS)
    @pytest.mark.parametrize("offset", list(BlockOffset))
    def test_a_round_trip_is_exact(self, word: int, offset: BlockOffset) -> None:
        decoded = decode_block(encode_block(word, offset), offset)
        assert decoded is not None
        assert decoded.information == word
        assert decoded.offset is offset
        assert decoded.is_clean

    def test_a_block_decoded_against_the_wrong_offset_is_rejected_or_repaired(self) -> None:
        # Decoding against the wrong offset must not quietly give the right answer, or the
        # decoder could not tell block positions apart.
        block = encode_block(0x1234, BlockOffset.A)
        decoded = decode_block(block, BlockOffset.B)
        assert decoded is None or decoded.information != 0x1234

    def test_a_block_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 26 bits"):
            decode_block(1 << BLOCK_BITS, BlockOffset.A)


class TestErrorCorrection:
    @pytest.mark.parametrize("position", range(BLOCK_BITS))
    def test_a_single_wrong_bit_anywhere_is_repaired(self, position: int) -> None:
        block = encode_block(0xABCD, BlockOffset.B)
        decoded = decode_block(block ^ (1 << position), BlockOffset.B)
        assert decoded is not None
        assert decoded.information == 0xABCD
        assert decoded.corrected_bits == 1

    @pytest.mark.parametrize("length", range(1, MAX_CORRECTABLE_BURST + 1))
    def test_a_burst_up_to_five_bits_long_is_repaired(self, length: int) -> None:
        # Reception errors come in runs: a fade or a click corrupts consecutive bits. The code
        # is built for exactly that, and five is the length the standard claims.
        block = encode_block(0x1234, BlockOffset.D)
        for start in range(BLOCK_BITS - length + 1):
            pattern = ((1 << length) - 1) << start
            decoded = decode_block(block ^ pattern, BlockOffset.D)
            assert decoded is not None, f"burst of {length} at {start} was not repaired"
            assert decoded.information == 0x1234
            assert not decoded.is_clean

    def test_damage_beyond_the_code_is_reported_rather_than_guessed(self) -> None:
        # A block "repaired" from uncorrectable damage would put a wrong station name on
        # somebody's display, which is worse than showing none.
        block = encode_block(0x1234, BlockOffset.A)
        wrecked = block ^ 0b1010_1010_1010_1010_1010_1010
        decoded = decode_block(wrecked, BlockOffset.A)
        assert decoded is None or decoded.information != 0x1234

    def test_correcting_returns_the_repaired_bits(self) -> None:
        block = encode_block(0x1234, BlockOffset.A)
        result = correct_block(block ^ 0b111, BlockOffset.A)
        assert result is not None
        repaired, changed = result
        assert repaired == block
        assert changed == 3

    def test_correcting_the_uncorrectable_reports_failure(self) -> None:
        block = encode_block(0x1234, BlockOffset.A)
        assert correct_block(block ^ 0b1010_1010_1010_1010_1010_1010, BlockOffset.A) is None


class TestBlockModel:
    def test_an_information_word_too_large_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must fit in 16 bits"):
            Block(information=0x1_0000, offset=BlockOffset.A)

    def test_negative_corrections_are_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            Block(information=0x1234, offset=BlockOffset.A, corrected_bits=-1)

    def test_it_reads_sensibly_when_printed(self) -> None:
        assert str(Block(information=0x1234, offset=BlockOffset.A)) == "A:1234"
        repaired = Block(information=0x1234, offset=BlockOffset.C_PRIME, corrected_bits=2)
        assert "C':1234" in str(repaired)
        assert "2 bits corrected" in str(repaired)
