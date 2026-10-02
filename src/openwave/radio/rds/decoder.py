"""Recovering RDS from a demodulated multiplex.

The input is the multiplex that comes out of :func:`~openwave.radio.fm_demodulator.\
quadrature_demodulate`; the output is what the station says about itself. The stages:

1. **Isolate the subcarrier.** Band-pass 57 kHz +/- 2.4 kHz, which is all RDS is allowed.
2. **Bring it down to zero.** Mix with a complex oscillator at 57 kHz and low-pass, leaving the
   data as a complex signal pointing in one direction.
3. **Find which direction.** The subcarrier is sent with its carrier suppressed, so nothing in
   the signal says what its phase is. It is recovered blind, from the data itself.
4. **Recover the bit clock.** Match-filter against the biphase symbol shape and find the
   sampling instant that gives the strongest output.
5. **Undo the differential encoding**, which also resolves the half-turn of phase ambiguity
   left over from step 3.
6. **Find the block boundaries** using the offset words, assemble groups, and hand them to
   :class:`~openwave.radio.rds.groups.RdsReceiver`.

**Recovering the phase without a carrier.** A suppressed-carrier signal arrives rotated by an
unknown angle, and the usual fix is to lock onto a pilot: the standard requires the subcarrier to
be the third harmonic of the 19 kHz stereo pilot, so cubing the pilot gives a reference. That
works only for stereo stations, and a mono station can carry RDS perfectly well. So the phase is
recovered from the data instead. The data is real, so its samples lie along a line through the
origin; squaring them removes the sign, and the average of the squares points along twice the
angle. Half of that argument is the angle, give or take a half turn -- which the differential
encoding was there to absorb.

**What this does not do.** The bit clock is estimated once over the whole signal, not tracked. A
real transmitter's clock drifts against the receiver's, so a long recording needs a timing loop
that follows it. For the second or two a scan spends on each station, a single estimate is
enough, and the alternative is harder to get right blind.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy import signal as scipy_signal

from openwave.radio.constants import RDS_BITRATE, RDS_SUBCARRIER_HZ
from openwave.radio.rds.blocks import (
    BLOCK_BITS,
    Block,
    BlockOffset,
    decode_block,
    syndrome,
)
from openwave.radio.rds.groups import (
    GROUP_BLOCKS,
    Group,
    RdsData,
    RdsReceiver,
    decode_group,
)

#: Samples per bit used for timing recovery.
#:
#: A biphase symbol changes sign halfway through a bit, so the clock has to be placed to within
#: a fraction of a half-bit. Sixteen samples per bit puts the quantisation at a sixteenth of a
#: bit, well inside what the matched filter tolerates, and 1187.5 x 16 is exactly 19 kHz.
SAMPLES_PER_BIT: Final = 16

#: Sample rate the bitstream is recovered at, in Hz.
RDS_WORK_RATE_HZ: Final = RDS_BITRATE * SAMPLES_PER_BIT

#: Half-width of the band-pass isolating the subcarrier, in Hz. The width RDS is allocated.
RDS_BANDWIDTH_HZ: Final = 2_400.0

#: Bits in one group.
GROUP_BITS: Final = BLOCK_BITS * GROUP_BLOCKS


@dataclass(frozen=True, slots=True)
class RdsBitstream:
    """The bits recovered from a subcarrier, before they mean anything.

    Attributes:
        bits: The differentially decoded data bits.
        eye_opening: How cleanly the bits separated, as the mean magnitude of the matched
            filter at the sampling instants divided by its standard deviation. Around 3 or more
            is a confident decode; near 1 means the decoder is guessing, which is what a weak
            or absent subcarrier looks like.
        clock_phase: Which of the :data:`SAMPLES_PER_BIT` sampling instants was chosen.
    """

    bits: tuple[int, ...]
    eye_opening: float
    clock_phase: int

    def __len__(self) -> int:
        return len(self.bits)

    @property
    def is_plausible(self) -> bool:
        """Whether there is enough here to be worth trying to decode."""
        return len(self.bits) >= GROUP_BITS and self.eye_opening > 1.2


def isolate_subcarrier(
    mpx: npt.NDArray[np.float64], *, sample_rate_hz: float
) -> npt.NDArray[np.complex128]:
    """Band-pass the RDS subcarrier and bring it down to zero frequency.

    Returns the data as a complex signal, still rotated by the subcarrier's unknown phase.
    """
    if sample_rate_hz <= 2 * (RDS_SUBCARRIER_HZ + RDS_BANDWIDTH_HZ):
        raise ValueError(
            f"a sample rate of {sample_rate_hz} Hz cannot represent the RDS subcarrier at "
            f"{RDS_SUBCARRIER_HZ} Hz; sample above "
            f"{2 * (RDS_SUBCARRIER_HZ + RDS_BANDWIDTH_HZ)} Hz"
        )
    if mpx.size < 2:
        raise ValueError(f"need samples to work with, got {mpx.size}")

    nyquist_hz = sample_rate_hz / 2.0
    numtaps, beta = scipy_signal.kaiserord(50.0, RDS_BANDWIDTH_HZ / nyquist_hz)
    numtaps = min(int(numtaps) | 1, max(31, (mpx.size // 4) | 1))
    taps = scipy_signal.firwin(
        numtaps,
        [
            (RDS_SUBCARRIER_HZ - RDS_BANDWIDTH_HZ) / nyquist_hz,
            (RDS_SUBCARRIER_HZ + RDS_BANDWIDTH_HZ) / nyquist_hz,
        ],
        window=("kaiser", beta),
        pass_zero=False,
    )
    isolated = scipy_signal.filtfilt(taps, 1.0, mpx)

    n = np.arange(isolated.size, dtype=np.float64)
    mixed = isolated * np.exp(-2j * np.pi * RDS_SUBCARRIER_HZ * n / sample_rate_hz)

    # Low-pass after mixing, to drop the image the mixing put at twice the subcarrier.
    low_numtaps, low_beta = scipy_signal.kaiserord(50.0, RDS_BANDWIDTH_HZ / nyquist_hz)
    low_numtaps = min(int(low_numtaps) | 1, max(31, (mixed.size // 4) | 1))
    low_taps = scipy_signal.firwin(
        low_numtaps, RDS_BANDWIDTH_HZ / nyquist_hz, window=("kaiser", low_beta)
    )
    baseband: npt.NDArray[np.complex128] = scipy_signal.filtfilt(low_taps, 1.0, mixed).astype(
        np.complex128
    )
    return baseband


def recover_phase(baseband: npt.NDArray[np.complex128]) -> float:
    """Estimate the subcarrier's phase from the data itself, in radians.

    The data is real, so its samples lie along a line through the origin at the unknown phase.
    Squaring a sample doubles its angle and discards its sign, so the average of the squares
    points along twice the phase. The result is ambiguous by half a turn, which the differential
    encoding resolves.
    """
    if baseband.size == 0:
        raise ValueError("no samples to estimate a phase from")
    mean_square = complex(np.mean(baseband**2))
    return 0.5 * float(np.angle(mean_square))


def project(
    baseband: npt.NDArray[np.complex128], *, phase_rad: float | None = None
) -> npt.NDArray[np.float64]:
    """Rotate a complex baseband onto the real axis and take the real part."""
    angle = recover_phase(baseband) if phase_rad is None else phase_rad
    projected: npt.NDArray[np.float64] = np.real(baseband * np.exp(-1j * angle)).astype(np.float64)
    return projected


def recover_bits(mpx: npt.NDArray[np.float64], *, sample_rate_hz: float) -> RdsBitstream:
    """Recover the RDS bitstream from a demodulated multiplex."""
    baseband = isolate_subcarrier(mpx, sample_rate_hz=sample_rate_hz)
    data = project(baseband)

    resampled = _resample(data, from_rate_hz=sample_rate_hz, to_rate_hz=RDS_WORK_RATE_HZ)
    if resampled.size < SAMPLES_PER_BIT * GROUP_BITS:
        return RdsBitstream(bits=(), eye_opening=0.0, clock_phase=0)

    # The biphase symbol is positive for the first half of a bit and negative for the second,
    # so correlating against that shape turns each bit into one strong sample.
    half = SAMPLES_PER_BIT // 2
    template = np.concatenate([np.ones(half), -np.ones(half)]) / SAMPLES_PER_BIT
    matched = np.convolve(resampled, template[::-1], mode="same")

    phase, eye = _best_clock_phase(matched)
    symbols = matched[phase::SAMPLES_PER_BIT]
    raw = (symbols > 0).astype(np.int8)

    # Differential decoding: the data is the change between consecutive symbols, which is what
    # makes the half-turn phase ambiguity harmless.
    bits = np.bitwise_xor(raw[1:], raw[:-1])
    return RdsBitstream(bits=tuple(int(bit) for bit in bits), eye_opening=eye, clock_phase=phase)


def _best_clock_phase(matched: npt.NDArray[np.float64]) -> tuple[int, float]:
    """Choose the sampling instant within the bit, and report how open the eye is.

    The right instant is the one where the matched filter output is consistently largest. The
    ratio of its mean magnitude to its spread says how confident the result is: a clean
    subcarrier gives well-separated samples, and noise gives a cloud around zero.
    """
    best_phase = 0
    best_score = -np.inf
    best_eye = 0.0
    for phase in range(SAMPLES_PER_BIT):
        samples = matched[phase::SAMPLES_PER_BIT]
        if samples.size < 2:
            continue
        magnitude = np.abs(samples)
        mean = float(np.mean(magnitude))
        spread = float(np.std(magnitude))
        if mean > best_score:
            best_score = mean
            best_phase = phase
            best_eye = mean / spread if spread > 0 else float("inf")
    return best_phase, best_eye


def find_groups(bits: tuple[int, ...]) -> list[Group]:
    """Find block boundaries in a bitstream and assemble the groups.

    RDS has no preamble: the bits run continuously with nothing marking where a block starts.
    The offset words are the only signpost.

    **Synchronisation is acquired on undamaged blocks only, and kept with repair allowed.** That
    split matters more than it looks. The check word repairs a burst of up to five bits, which
    means roughly a third of all possible syndromes map to some correctable error -- so a
    decoder willing to repair while *searching* will "repair" random noise into a plausible
    block, four times in a row, and report a group that was never transmitted. Measured on a
    clean synthetic signal, searching with repair enabled produced 22 groups, every one of them
    invented, with the station's identity changing in each. Requiring four consecutive clean
    syndromes to acquire, then allowing repair while tracking, finds the real groups.
    """
    if len(bits) < GROUP_BITS:
        return []

    values = _window_values(bits)
    groups: list[Group] = []
    limit = len(bits) - GROUP_BITS
    position = 0
    synchronised = False

    while position <= limit:
        if not synchronised:
            if not _is_clean_group(values, position):
                position += 1
                continue
            synchronised = True

        group = _group_at(values, position)
        if group is None:
            # Lost it. Start searching again from the next bit rather than trusting the stride.
            synchronised = False
            position += 1
            continue

        groups.append(group)
        position += GROUP_BITS

    return groups


def _is_clean_group(values: list[int], position: int) -> bool:
    """Whether four undamaged blocks with the right offsets start at ``position``.

    This is the test that acquires synchronisation. A random 26 bits matches a particular offset
    word about once in a thousand, so four in a row at the right spacing is a sound signpost.
    """
    if position + 3 * BLOCK_BITS >= len(values):
        return False
    for index, allowed in enumerate(_GROUP_OFFSET_CANDIDATES):
        found = syndrome(values[position + index * BLOCK_BITS])
        if not any(int(option) == found for option in allowed):
            return False
    return True


def _window_values(bits: tuple[int, ...]) -> list[int]:
    """The 26-bit value starting at each position in the bitstream."""
    if len(bits) < BLOCK_BITS:
        return []
    mask = (1 << BLOCK_BITS) - 1
    window = 0
    for bit in bits[:BLOCK_BITS]:
        window = ((window << 1) | (bit & 1)) & mask
    values = [window]
    for bit in bits[BLOCK_BITS:]:
        window = ((window << 1) | (bit & 1)) & mask
        values.append(window)
    return values


#: The offset words that may appear at each position in a group.
_GROUP_OFFSET_CANDIDATES: Final = (
    (BlockOffset.A,),
    (BlockOffset.B,),
    (BlockOffset.C, BlockOffset.C_PRIME),
    (BlockOffset.D,),
)


def _group_at(values: list[int], position: int) -> Group | None:
    """Read a group at a position already known to be a block boundary.

    Repair is allowed here, because by this point the boundary is established and a damaged
    block is a damaged block rather than a coincidence. Refusing to repair would mean a
    station's name never appearing on a signal that is merely imperfect.
    """
    if position + 3 * BLOCK_BITS >= len(values):
        return None

    blocks: list[Block] = []
    for index, allowed in enumerate(_GROUP_OFFSET_CANDIDATES):
        raw = values[position + index * BLOCK_BITS]
        found = syndrome(raw)
        offset = next((option for option in allowed if int(option) == found), None)
        if offset is not None:
            decoded = decode_block(raw, offset)
        else:
            decoded = next(
                (
                    repaired
                    for option in allowed
                    if (repaired := decode_block(raw, option)) is not None
                ),
                None,
            )
        if decoded is None:
            return None
        blocks.append(decoded)

    return decode_group(tuple(blocks))


def decode_rds(mpx: npt.NDArray[np.float64], *, sample_rate_hz: float) -> RdsData:
    """Decode everything RDS says about a station, from a demodulated multiplex.

    Returns an empty :class:`~openwave.radio.rds.groups.RdsData` when there is no usable
    subcarrier, which is the normal result for a station that does not carry RDS.
    """
    bitstream = recover_bits(mpx, sample_rate_hz=sample_rate_hz)
    if not bitstream.is_plausible:
        return RdsData()

    receiver = RdsReceiver()
    for group in find_groups(bitstream.bits):
        receiver.accept(group)
    return receiver.data


def _resample(
    signal: npt.NDArray[np.float64], *, from_rate_hz: float, to_rate_hz: float
) -> npt.NDArray[np.float64]:
    """Resample by a rational factor."""
    if from_rate_hz == to_rate_hz:
        return signal
    ratio = Fraction(to_rate_hz / from_rate_hz).limit_denominator(4000)
    resampled: npt.NDArray[np.float64] = scipy_signal.resample_poly(
        signal, ratio.numerator, ratio.denominator
    ).astype(np.float64)
    return resampled
