"""The RDS block: 16 bits of data, 10 bits of protection.

RDS sends its data in blocks of 26 bits: 16 bits of information followed by a 10-bit check word.
Four blocks make a group, and the check word does three jobs at once.

**It detects errors.** The check word is the remainder of the information word divided by the
generator polynomial, which is the usual cyclic redundancy check.

**It finds the block boundaries.** Before the check word is added, an *offset word* is mixed in,
and a different one for each position in the group. A receiver has no other way to tell where a
block starts: the bitstream is continuous, with no preamble and no marker. It slides a 26-bit
window along, and a window whose syndrome equals one of the five offset words is a block --
almost certainly at the right position, because a random 26 bits matches a particular offset
word about once in a thousand.

**It corrects errors.** The code is built to repair a burst of up to five consecutive wrong bits,
which is the shape reception errors actually take: a fade or a click corrupts a run of bits, not
scattered single ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final

#: Bits in one block.
BLOCK_BITS: Final = 26

#: Bits of information in a block.
INFORMATION_BITS: Final = 16

#: Bits of check word in a block.
CHECKWORD_BITS: Final = 10

#: Generator polynomial of the check word, as a bit pattern.
#:
#: ``x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1``, with the leading ``x^10`` in bit 10. Fixed by
#: IEC 62106.
GENERATOR: Final = 0b101_1011_1001

#: Mask for a 10-bit value.
CHECKWORD_MASK: Final = (1 << CHECKWORD_BITS) - 1

#: Mask for a 16-bit value.
INFORMATION_MASK: Final = (1 << INFORMATION_BITS) - 1

#: Mask for a whole block.
BLOCK_MASK: Final = (1 << BLOCK_BITS) - 1

#: Longest run of wrong bits the code is designed to repair.
MAX_CORRECTABLE_BURST: Final = 5


class BlockOffset(IntEnum):
    """The offset word mixed into a block's check word, which identifies its position.

    ``C_PRIME`` marks the third block of a version B group, where it holds the programme
    identification again rather than group data. A receiver distinguishes the two by which
    offset word matches.
    """

    A = 0b00_1111_1100
    B = 0b01_1001_1000
    C = 0b01_0110_1000
    C_PRIME = 0b11_0101_0000
    D = 0b01_1011_0100

    @property
    def label(self) -> str:
        """Short name as the standard writes it."""
        return "C'" if self is BlockOffset.C_PRIME else self.name


#: The offset words in the order they appear in a group.
#:
#: The third block takes ``C`` in a version A group and ``C_PRIME`` in a version B group, so
#: both are candidates at that position.
GROUP_OFFSETS: Final = (
    (BlockOffset.A,),
    (BlockOffset.B,),
    (BlockOffset.C, BlockOffset.C_PRIME),
    (BlockOffset.D,),
)


@dataclass(frozen=True, slots=True)
class Block:
    """One decoded block.

    Attributes:
        information: The 16 bits of payload.
        offset: Which position in the group this block came from.
        corrected_bits: How many bits had to be repaired. Zero for a clean block. Worth keeping:
            a stream that decodes only after heavy correction is one to distrust, and a station
            whose name keeps changing is usually a weak signal rather than a decoder bug.
    """

    information: int
    offset: BlockOffset
    corrected_bits: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.information <= INFORMATION_MASK:
            raise ValueError(
                f"information word must fit in {INFORMATION_BITS} bits, got {self.information}"
            )
        if self.corrected_bits < 0:
            raise ValueError(f"corrected bits must not be negative, got {self.corrected_bits}")

    @property
    def is_clean(self) -> bool:
        """Whether the block decoded without any correction."""
        return self.corrected_bits == 0

    def __str__(self) -> str:
        suffix = "" if self.is_clean else f" ({self.corrected_bits} bits corrected)"
        return f"{self.offset.label}:{self.information:04X}{suffix}"


def checkword(information: int) -> int:
    """The 10-bit check word for an information word, before the offset is mixed in.

    The remainder of ``information * x^10`` divided by the generator polynomial.

    >>> f"{checkword(0x0000):03X}"
    '000'
    """
    if not 0 <= information <= INFORMATION_MASK:
        raise ValueError(f"information word must fit in {INFORMATION_BITS} bits, got {information}")
    return _remainder(information << CHECKWORD_BITS)


def syndrome(block: int) -> int:
    """The syndrome of a received block: its remainder modulo the generator polynomial.

    For an undamaged block the syndrome equals the offset word that was mixed in, because the
    remainder is linear and the information and check word together divide exactly. That is what
    makes the syndrome both an integrity check and a position marker.

    >>> syndrome(encode_block(0x1234, BlockOffset.A)) == BlockOffset.A
    True
    """
    if not 0 <= block <= BLOCK_MASK:
        raise ValueError(f"block must fit in {BLOCK_BITS} bits, got {block}")
    return _remainder(block)


def _remainder(value: int) -> int:
    """Remainder of ``value`` divided by the generator polynomial, over GF(2)."""
    register = value
    for bit in range(BLOCK_BITS - 1, CHECKWORD_BITS - 1, -1):
        if register & (1 << bit):
            register ^= GENERATOR << (bit - CHECKWORD_BITS)
    return register & CHECKWORD_MASK


def encode_block(information: int, offset: BlockOffset) -> int:
    """Build a 26-bit block from an information word and its offset.

    >>> f"{encode_block(0x1234, BlockOffset.A):07X}"
    '048D06A'
    """
    return ((information & INFORMATION_MASK) << CHECKWORD_BITS) | (
        checkword(information) ^ int(offset)
    )


def decode_block(block: int, offset: BlockOffset) -> Block | None:
    """Decode a block known to be at ``offset``, correcting errors where possible.

    Returns ``None`` when the damage is beyond what the code can repair, which is the honest
    answer: a block decoded from an uncorrectable error would put a wrong station name on
    somebody's display.
    """
    if not 0 <= block <= BLOCK_MASK:
        raise ValueError(f"block must fit in {BLOCK_BITS} bits, got {block}")

    difference = syndrome(block) ^ int(offset)
    if difference == 0:
        return Block(information=block >> CHECKWORD_BITS, offset=offset)

    pattern = _ERROR_PATTERNS.get(difference)
    if pattern is None:
        return None
    repaired = block ^ pattern
    return Block(
        information=repaired >> CHECKWORD_BITS,
        offset=offset,
        corrected_bits=pattern.bit_count(),
    )


def correct_block(block: int, offset: BlockOffset) -> tuple[int, int] | None:
    """Repair a block, returning the corrected 26 bits and how many were changed.

    Returns ``None`` if the damage is beyond repair.
    """
    decoded = decode_block(block, offset)
    if decoded is None:
        return None
    return encode_block(decoded.information, offset), decoded.corrected_bits


def matching_offsets(block: int) -> tuple[BlockOffset, ...]:
    """Which offset words this block's syndrome matches exactly.

    Used to find block boundaries in a bitstream. Usually one, occasionally none, and never more
    than one in practice -- the offset words are chosen to be far apart.
    """
    found = syndrome(block)
    return tuple(offset for offset in BlockOffset if int(offset) == found)


def _build_error_patterns() -> dict[int, int]:
    """Map each syndrome to the error it implies, for every burst the code can repair.

    Reception errors come in runs: a fade or a click corrupts consecutive bits rather than
    scattered ones, which is why RDS uses a code built for bursts rather than for isolated bit
    flips. Every burst up to :data:`MAX_CORRECTABLE_BURST` bits long is enumerated here, and
    where two bursts share a syndrome the lighter one wins -- fewer flipped bits is the more
    likely explanation.
    """
    patterns: dict[int, int] = {}
    for start in range(BLOCK_BITS):
        for length in range(1, MAX_CORRECTABLE_BURST + 1):
            if start + length > BLOCK_BITS:
                break
            # A burst of length L has its first and last bits wrong by definition; the ones
            # between may be either way.
            interior_count = max(0, length - 2)
            for interior in range(1 << interior_count):
                pattern = 1 << start
                if length > 1:
                    pattern |= 1 << (start + length - 1)
                    for index in range(interior_count):
                        if interior & (1 << index):
                            pattern |= 1 << (start + 1 + index)
                key = _remainder(pattern)
                if key == 0:
                    continue
                existing = patterns.get(key)
                if existing is None or pattern.bit_count() < existing.bit_count():
                    patterns[key] = pattern
    return patterns


#: Syndrome to error pattern, for every burst the code can repair. Built once at import.
_ERROR_PATTERNS: Final = _build_error_patterns()
