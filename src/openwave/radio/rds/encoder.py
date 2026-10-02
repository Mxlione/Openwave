"""Building an RDS signal that does not exist.

With no receiver available, this is how the RDS decoder is tested: encode a known station name,
run it through the decoder, and compare. A decoder tested only against its own output proves
little, so this module is written from the standard rather than from the decoder, and the two
share nothing but the block definitions.

The chain, following IEC 62106:

1. **Groups to blocks.** Each 16-bit information word gets a check word with its offset mixed
   in, giving 26 bits. Four blocks make a 104-bit group.
2. **Differential encoding.** Each bit is sent as "same as the last one" or "different", which
   is what makes the decoder immune to the subcarrier being recovered upside down -- and it will
   be, half the time, because a suppressed-carrier signal carries no clue about its own polarity.
3. **Biphase coding.** Each data bit becomes a symbol that rises and falls within one bit
   period. The result has no energy at zero frequency, which matters because the subcarrier sits
   only 57 kHz from a multiplex that already occupies everything below it.
4. **Shaping and modulation.** The symbols are filtered and multiplied onto 57 kHz with the
   carrier suppressed.

What it does not model: the exact RDS data-shaping filter, which is specified as a particular
cosine response. A plain low-pass stands in for it. That makes the synthetic signal slightly
cleaner than a real one -- a difference only hardware can measure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy import signal as scipy_signal

from openwave.radio.constants import RDS_BITRATE, RDS_SUBCARRIER_HZ
from openwave.radio.rds.blocks import BLOCK_BITS, BlockOffset, encode_block
from openwave.radio.rds.groups import (
    GROUP_BLOCKS,
    PS_LENGTH,
    RADIOTEXT_LENGTH,
    RADIOTEXT_TERMINATOR,
    GroupVersion,
)

#: Bits in one group.
GROUP_BITS: Final = BLOCK_BITS * GROUP_BLOCKS

#: Samples per bit in the symbol shaping, before resampling to the output rate.
#:
#: A biphase symbol changes sign in the middle of a bit, so at least two are needed; more make
#: the shaping filter's job easier. Eight is comfortable and keeps the arrays small.
SYMBOL_OVERSAMPLING: Final = 8

#: Rate the data signal is built and shaped at, in Hz.
#:
#: The signal is band-limited to about 2.4 kHz, so it needs nothing like a megahertz to
#: represent -- and shaping it at the output rate is ruinously expensive. A filter with a
#: 1.2 kHz transition at a 1.2 MHz Nyquist needs some 3000 taps, and running that over the
#: 1.9 million samples of one RDS period took 5.1 seconds. The same filter at 38 kHz needs
#: 47 taps. Building and shaping here, then resampling up, is far cheaper for a signal that is
#: identical where it matters.
#:
#: 38 kHz is 32 samples per bit, which places the mid-bit transition finely enough that the
#: resampling has nothing to smear.
SHAPING_RATE_HZ: Final = RDS_BITRATE * 32


@dataclass(frozen=True, slots=True)
class RdsProgramme:
    """What a synthetic station transmits over RDS.

    Attributes:
        pi: Programme identification, the station's 16-bit code.
        programme_service: Station name. Padded or truncated to eight characters, as the
            standard requires.
        pty: Programme type.
        traffic_programme: Whether the station carries traffic announcements.
        radio_text: RadioText message, up to 64 characters. ``None`` sends no RadioText.
    """

    pi: int = 0x1234
    programme_service: str = "OPENWAVE"
    pty: int = 10
    traffic_programme: bool = False
    radio_text: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.pi <= 0xFFFF:
            raise ValueError(f"programme identification must fit in 16 bits, got {self.pi}")
        if not 0 <= self.pty <= 0x1F:
            raise ValueError(f"programme type must fit in 5 bits, got {self.pty}")
        if self.radio_text is not None and len(self.radio_text) > RADIOTEXT_LENGTH:
            raise ValueError(
                f"RadioText must be at most {RADIOTEXT_LENGTH} characters, "
                f"got {len(self.radio_text)}"
            )

    @property
    def padded_name(self) -> str:
        """The station name as eight characters, as it goes on the air."""
        return self.programme_service[:PS_LENGTH].ljust(PS_LENGTH)


def programme_service_groups(programme: RdsProgramme) -> list[tuple[int, int, int, int]]:
    """The four group 0A groups that carry a station name.

    Two characters per group, with a segment number saying which pair, so a receiver can
    assemble them whatever order they arrive in.
    """
    name = programme.padded_name
    groups: list[tuple[int, int, int, int]] = []
    for segment in range(PS_LENGTH // 2):
        second = _block_two(programme, group_type=0, version=GroupVersion.A, low_bits=segment)
        # Group 0A's third block carries alternative frequencies, which OpenWave does not use.
        # 0xE0E0 is the standard's "no alternative frequency" filler.
        third = 0xE0E0
        pair = name[segment * 2 : segment * 2 + 2]
        fourth = (ord(pair[0]) << 8) | ord(pair[1])
        groups.append((programme.pi, second, third, fourth))
    return groups


def radio_text_groups(programme: RdsProgramme) -> list[tuple[int, int, int, int]]:
    """The group 2A groups that carry a RadioText message.

    Four characters per group. A carriage return marks the end when the message is shorter than
    the full 64 characters, so a receiver knows not to wait for the rest.
    """
    if programme.radio_text is None:
        return []

    text = programme.radio_text
    if len(text) < RADIOTEXT_LENGTH:
        text = text + chr(RADIOTEXT_TERMINATOR)
    text = text.ljust(((len(text) + 3) // 4) * 4)

    groups: list[tuple[int, int, int, int]] = []
    for segment in range(len(text) // 4):
        chunk = text[segment * 4 : segment * 4 + 4]
        second = _block_two(programme, group_type=2, version=GroupVersion.A, low_bits=segment & 0xF)
        third = (ord(chunk[0]) << 8) | ord(chunk[1])
        fourth = (ord(chunk[2]) << 8) | ord(chunk[3])
        groups.append((programme.pi, second, third, fourth))
    return groups


def _block_two(
    programme: RdsProgramme, *, group_type: int, version: GroupVersion, low_bits: int
) -> int:
    """Assemble the second block, which every group type shares the layout of.

    Four bits of group type, one of version, one traffic-programme flag, five of programme
    type, and five the group type decides the meaning of.
    """
    return (
        ((group_type & 0xF) << 12)
        | ((int(version) & 0x1) << 11)
        | ((1 if programme.traffic_programme else 0) << 10)
        | ((programme.pty & 0x1F) << 5)
        | (low_bits & 0x1F)
    )


def group_sequence(programme: RdsProgramme) -> list[tuple[int, int, int, int]]:
    """The groups a station repeats, in the order it sends them.

    Name groups come first and are interleaved with RadioText, because a real station sends the
    name often -- it is what a listener sees -- and the text more sparsely.
    """
    name_groups = programme_service_groups(programme)
    text_groups = radio_text_groups(programme)
    if not text_groups:
        return name_groups

    sequence: list[tuple[int, int, int, int]] = []
    for index, name_group in enumerate(name_groups):
        sequence.append(name_group)
        if index < len(text_groups):
            sequence.append(text_groups[index])
    sequence.extend(text_groups[len(name_groups) :])
    return sequence


def group_bits(information: Sequence[int]) -> list[int]:
    """Turn four information words into the 104 bits of a group, check words included."""
    if len(information) != GROUP_BLOCKS:
        raise ValueError(f"a group needs {GROUP_BLOCKS} information words, got {len(information)}")

    version_b = bool((information[1] >> 11) & 0x1)
    offsets = (
        BlockOffset.A,
        BlockOffset.B,
        BlockOffset.C_PRIME if version_b else BlockOffset.C,
        BlockOffset.D,
    )
    bits: list[int] = []
    for word, offset in zip(information, offsets, strict=True):
        block = encode_block(word, offset)
        bits.extend((block >> shift) & 1 for shift in range(BLOCK_BITS - 1, -1, -1))
    return bits


def differential_encode(bits: Iterable[int], *, initial: int = 0) -> list[int]:
    """Encode bits as changes rather than values.

    Each output bit is the previous output exclusive-ored with the input. The decoder recovers
    the data by comparing consecutive bits, which makes it immune to the subcarrier being
    recovered with the opposite polarity -- and it will be, half the time, because a
    suppressed-carrier signal carries nothing that says which way up it is.

    >>> differential_encode([1, 0, 0, 1])
    [1, 1, 1, 0]
    """
    previous = initial
    encoded: list[int] = []
    for bit in bits:
        previous ^= bit & 1
        encoded.append(previous)
    return encoded


def biphase_symbols(bits: Sequence[int], *, oversampling: int = SYMBOL_OVERSAMPLING) -> np.ndarray:
    """Turn bits into biphase symbols: each bit rises then falls within its own period.

    A 1 goes positive then negative, a 0 the other way. The result has no average value, so the
    modulated signal puts nothing at the subcarrier frequency itself -- which matters, because
    57 kHz is close to a multiplex that already fills everything below it.
    """
    if oversampling < 2 or oversampling % 2:
        raise ValueError(f"oversampling must be an even number of at least 2, got {oversampling}")

    half = oversampling // 2
    symbol = np.concatenate([np.ones(half), -np.ones(half)])
    waveform = np.empty(len(bits) * oversampling, dtype=np.float64)
    for index, bit in enumerate(bits):
        sign = 1.0 if bit else -1.0
        waveform[index * oversampling : (index + 1) * oversampling] = sign * symbol
    return waveform


def rds_subcarrier(
    programme: RdsProgramme,
    *,
    sample_rate_hz: float,
    duration_s: float,
    repeats: int | None = None,
) -> npt.NDArray[np.float64]:
    """Generate the RDS part of a multiplex: data on a suppressed carrier at 57 kHz.

    The group sequence repeats for as long as asked, which is what a real station does -- a
    receiver that tunes in halfway through must still be able to assemble a name.

    Args:
        programme: What to transmit.
        sample_rate_hz: Rate of the multiplex this will be added to.
        duration_s: How much signal to generate.
        repeats: How many times to send the group sequence, or ``None`` to fill the duration.

    Returns:
        A real-valued signal at the given sample rate, scaled to a peak of 1.
    """
    if sample_rate_hz <= 2 * RDS_SUBCARRIER_HZ:
        raise ValueError(
            f"a sample rate of {sample_rate_hz} Hz cannot carry a {RDS_SUBCARRIER_HZ} Hz subcarrier"
        )
    if duration_s <= 0:
        raise ValueError(f"duration must be positive, got {duration_s}")

    sequence = group_sequence(programme)
    if not sequence:
        raise ValueError("the programme has nothing to transmit")

    bits_needed = int(np.ceil(duration_s * RDS_BITRATE)) + GROUP_BITS
    groups_needed = max(1, int(np.ceil(bits_needed / GROUP_BITS)))
    if repeats is not None:
        groups_needed = max(1, repeats * len(sequence))

    raw_bits: list[int] = []
    for index in range(groups_needed):
        raw_bits.extend(group_bits(sequence[index % len(sequence)]))

    encoded = differential_encode(raw_bits)
    samples_wanted = round(duration_s * sample_rate_hz)

    # Build and shape at a modest rate, then resample up. See SHAPING_RATE_HZ.
    shaping_count = int(np.ceil(duration_s * SHAPING_RATE_HZ)) + 2
    shaped = _biphase_at_rate(encoded, sample_rate_hz=SHAPING_RATE_HZ, count=shaping_count)
    shaped = _shape(shaped, sample_rate_hz=SHAPING_RATE_HZ)
    baseband = _resample(shaped, from_rate_hz=SHAPING_RATE_HZ, to_rate_hz=sample_rate_hz)
    if baseband.size < samples_wanted:
        baseband = np.pad(baseband, (0, samples_wanted - baseband.size))
    baseband = baseband[:samples_wanted]

    t = np.arange(samples_wanted, dtype=np.float64) / sample_rate_hz
    modulated: npt.NDArray[np.float64] = baseband * np.cos(2.0 * np.pi * RDS_SUBCARRIER_HZ * t)
    peak = float(np.max(np.abs(modulated)))
    if peak > 0:
        modulated = modulated / peak
    return modulated


def _biphase_at_rate(
    bits: Sequence[int], *, sample_rate_hz: float, count: int
) -> npt.NDArray[np.float64]:
    """Build the biphase waveform directly at ``sample_rate_hz``.

    Each output sample is placed by working out which bit it falls in and whether it is in the
    first or second half of that bit. A 1 is positive then negative, a 0 the other way.
    """
    if not bits:
        raise ValueError("no bits to transmit")

    samples_per_bit = sample_rate_hz / RDS_BITRATE
    position = np.arange(count, dtype=np.float64) / samples_per_bit
    bit_index = np.minimum(position.astype(np.int64), len(bits) - 1)
    second_half = (position - np.floor(position)) >= 0.5

    values = np.asarray(bits, dtype=np.float64) * 2.0 - 1.0
    waveform: npt.NDArray[np.float64] = values[bit_index] * np.where(second_half, -1.0, 1.0)
    return waveform


def _resample(
    signal: npt.NDArray[np.float64], *, from_rate_hz: float, to_rate_hz: float
) -> npt.NDArray[np.float64]:
    """Resample by a rational factor."""
    if from_rate_hz == to_rate_hz:
        return signal
    ratio = Fraction(to_rate_hz / from_rate_hz).limit_denominator(1000)
    resampled: npt.NDArray[np.float64] = scipy_signal.resample_poly(
        signal, ratio.numerator, ratio.denominator
    ).astype(np.float64)
    return resampled


def _shape(signal: npt.NDArray[np.float64], *, sample_rate_hz: float) -> npt.NDArray[np.float64]:
    """Limit the bandwidth of the data signal.

    The standard specifies a particular cosine response; a low-pass at twice the bit rate stands
    in for it. That keeps the signal inside the 57 kHz +/- 2.4 kHz the subcarrier is allowed,
    which is what the decoder's own filter expects to find.
    """
    nyquist_hz = sample_rate_hz / 2.0
    cutoff_hz = 2.0 * RDS_BITRATE
    if cutoff_hz >= nyquist_hz:
        return signal
    numtaps, beta = scipy_signal.kaiserord(50.0, (cutoff_hz / 2.0) / nyquist_hz)
    numtaps = min(int(numtaps) | 1, max(31, (signal.size // 4) | 1))
    taps = scipy_signal.firwin(numtaps, cutoff_hz / nyquist_hz, window=("kaiser", beta))
    shaped: npt.NDArray[np.float64] = scipy_signal.filtfilt(taps, 1.0, signal).astype(np.float64)
    return shaped


@dataclass(frozen=True, slots=True)
class RdsBaseband:
    """One repeat of a station's RDS subcarrier, with its integral precomputed.

    **Why a cached period rather than a stream.** The FM synthesiser in
    :mod:`openwave.radio.synthesis` is exactly reproducible: the samples it produces depend only
    on a sample index, never on how many times it has been called, which is what lets a failing
    test be reproduced from a seed and an offset. RDS data is not a sum of sinusoids, so it has
    no closed form to evaluate at an arbitrary index -- but it *is* periodic, because a station
    repeats its group sequence. So one period is generated once, and any sample index is
    answered by looking into it.

    The FM phase needs the signal's integral as well as the signal, so that is precomputed too.
    An index beyond the first period adds a whole number of period integrals, which is exact
    because the integral is additive.

    One approximation: a period is rounded to a whole number of samples. A sequence of seven
    groups at 1187.5 bits per second is 0.613 s, which at 2.4 MS/s is 1 470 821.05 samples, so
    rounding shifts the simulated bit clock by under a part per million -- far better than a real
    transmitter and far below anything a decoder notices.
    """

    samples: npt.NDArray[np.float64]
    """One period of the subcarrier, scaled to a peak of 1."""

    integral: npt.NDArray[np.float64]
    """Cumulative integral of :attr:`samples`, in sample-seconds."""

    period_integral: float
    """The integral across one whole period, added once per period for a later index."""

    sample_rate_hz: float

    @property
    def period_samples(self) -> int:
        """Length of one period, in samples."""
        return int(self.samples.size)

    def phase_at(self, indices: npt.NDArray[np.int64]) -> npt.NDArray[np.float64]:
        """The integral of the subcarrier from time zero to each of ``indices``.

        Exact for any index, however large, because the integral over a whole period is added
        once per completed period.
        """
        period = self.period_samples
        cycles = indices // period
        within = indices % period
        result: npt.NDArray[np.float64] = (
            self.integral[within] + cycles.astype(np.float64) * self.period_integral
        )
        return result

    def value_at(self, indices: npt.NDArray[np.int64]) -> npt.NDArray[np.float64]:
        """The subcarrier's value at each of ``indices``."""
        values: npt.NDArray[np.float64] = self.samples[indices % self.period_samples]
        return values


def rds_baseband_period(programme: RdsProgramme, *, sample_rate_hz: float) -> RdsBaseband:
    """Generate one repeat of a station's RDS subcarrier, with its integral.

    The result is the whole group sequence exactly once, so looking into it modulo its length
    reproduces what the station transmits forever.
    """
    sequence = group_sequence(programme)
    if not sequence:
        raise ValueError("the programme has nothing to transmit")

    period_s = len(sequence) * GROUP_BITS / RDS_BITRATE
    signal = rds_subcarrier(
        programme,
        sample_rate_hz=sample_rate_hz,
        duration_s=period_s,
        repeats=1,
    )
    # The cumulative integral, by the trapezium rule at a one-sample step.
    integral = np.cumsum(signal) / sample_rate_hz
    return RdsBaseband(
        samples=signal,
        integral=integral,
        period_integral=float(integral[-1]) if integral.size else 0.0,
        sample_rate_hz=sample_rate_hz,
    )
