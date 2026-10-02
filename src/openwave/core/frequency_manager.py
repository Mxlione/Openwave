"""Band plans, and slicing a band into windows a receiver can actually see.

A receiver sees a window a couple of megahertz wide. The FM broadcast band is twenty. Scanning
therefore means tuning to a sequence of frequencies and stitching the results together, and the
awkward part is the edges: the outer parts of a window are shaped by the receiver's
anti-aliasing filter and must not be believed.

:func:`plan_segments` works that out. Given a band plan and what the receiver can do, it returns
the frequencies to tune to and, for each, exactly which channels that window is responsible for
measuring. Every channel is assigned to exactly one segment, so nothing is measured twice and
nothing is missed.

This module knows nothing about modulation. The FM plan lives in :mod:`openwave.radio.band` and
the television plans will live in :mod:`openwave.tv`.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field

from openwave.core.units import format_frequency


@dataclass(frozen=True, slots=True)
class BandPlan:
    """A broadcast band and the grid of channels within it.

    Attributes:
        name: What to call this band in output meant for people.
        start_hz: Centre frequency of the lowest channel.
        end_hz: Centre frequency of the highest channel.
        channel_spacing_hz: Distance between adjacent channels on the grid.
        channel_bandwidth_hz: Bandwidth one channel occupies. Usually wider than the spacing,
            because broadcast grids are designed so that neighbouring channels are not both in
            use in the same place.
    """

    name: str
    start_hz: float
    end_hz: float
    channel_spacing_hz: float
    channel_bandwidth_hz: float

    def __post_init__(self) -> None:
        if self.start_hz <= 0:
            raise ValueError(f"band must start above zero, got {self.start_hz} Hz")
        if self.end_hz < self.start_hz:
            raise ValueError(f"band is inverted: {self.start_hz} Hz to {self.end_hz} Hz")
        if self.channel_spacing_hz <= 0:
            raise ValueError(f"channel spacing must be positive, got {self.channel_spacing_hz}")
        if self.channel_bandwidth_hz <= 0:
            raise ValueError(f"channel bandwidth must be positive, got {self.channel_bandwidth_hz}")

    @property
    def width_hz(self) -> float:
        """Distance from the lowest channel to the highest."""
        return self.end_hz - self.start_hz

    @property
    def channel_count(self) -> int:
        """How many channels the grid holds."""
        return math.floor(self.width_hz / self.channel_spacing_hz) + 1

    def channels(self) -> Iterator[float]:
        """Every channel centre frequency, lowest first.

        Computed from the index rather than by repeated addition, so the thousandth channel is
        not off by a thousand rounding errors.
        """
        for index in range(self.channel_count):
            yield self.start_hz + index * self.channel_spacing_hz

    def contains(self, freq_hz: float) -> bool:
        """Whether ``freq_hz`` lies within the band."""
        return self.start_hz <= freq_hz <= self.end_hz

    def nearest_channel(self, freq_hz: float) -> float:
        """The channel on the grid closest to ``freq_hz``.

        Used to turn a measured frequency back into a channel, since a real transmitter sits on
        the grid and a measurement does not.

        Raises:
            ValueError: if ``freq_hz`` is outside the band.
        """
        if not self.contains(freq_hz):
            raise ValueError(
                f"{format_frequency(freq_hz)} is outside the {self.name} band "
                f"({format_frequency(self.start_hz)} to {format_frequency(self.end_hz)})"
            )
        index = round((freq_hz - self.start_hz) / self.channel_spacing_hz)
        return self.start_hz + index * self.channel_spacing_hz

    def is_on_grid(self, freq_hz: float, *, tolerance_hz: float = 1.0) -> bool:
        """Whether ``freq_hz`` sits on the channel grid, within ``tolerance_hz``."""
        if not self.contains(freq_hz):
            return False
        return abs(freq_hz - self.nearest_channel(freq_hz)) <= tolerance_hz

    def __str__(self) -> str:
        return (
            f"{self.name} ({format_frequency(self.start_hz)} to "
            f"{format_frequency(self.end_hz)}, {self.channel_count} channels)"
        )


@dataclass(frozen=True, slots=True)
class Segment:
    """One tuning position in a sweep, and the channels it is responsible for.

    Attributes:
        center_freq_hz: Where to tune the receiver.
        sample_rate_hz: Rate to sample at.
        channel_bandwidth_hz: Bandwidth to integrate when measuring each channel. Carried here
            rather than inferred later, because inferring it from the channel spacing happens
            to work for FM, where a channel is twice its spacing, and would be wrong by a
            factor of two for DVB-T, where a channel is exactly its spacing.
        usable_start_hz: Lower edge of the part of this window worth believing.
        usable_end_hz: Upper edge of the same.
        channels: Channel centre frequencies this segment measures. Every channel in the band
            belongs to exactly one segment, so results can be concatenated without
            deduplication.
    """

    center_freq_hz: float
    sample_rate_hz: float
    channel_bandwidth_hz: float
    usable_start_hz: float
    usable_end_hz: float
    channels: tuple[float, ...] = field(default_factory=tuple)

    @property
    def usable_bandwidth_hz(self) -> float:
        """Width of the trustworthy part of this window."""
        return self.usable_end_hz - self.usable_start_hz

    def covers(self, freq_hz: float) -> bool:
        """Whether ``freq_hz`` falls in the trustworthy part of this window."""
        return self.usable_start_hz <= freq_hz <= self.usable_end_hz

    def offset_of(self, freq_hz: float) -> float:
        """How far ``freq_hz`` sits from the centre of this window, signed."""
        return freq_hz - self.center_freq_hz

    def __str__(self) -> str:
        return (
            f"{format_frequency(self.center_freq_hz)} "
            f"({format_frequency(self.sample_rate_hz)} sample rate, "
            f"{len(self.channels)} channels)"
        )


def plan_segments(
    plan: BandPlan,
    *,
    sample_rate_hz: float,
    usable_fraction: float,
) -> tuple[Segment, ...]:
    """Work out the tuning positions needed to measure every channel in ``plan``.

    A window of ``sample_rate_hz`` is only trustworthy across ``usable_fraction`` of its width,
    and a channel can only be measured when its *whole* bandwidth sits inside that trustworthy
    part. So the distance a single window advances the sweep is the usable width minus one
    channel bandwidth -- not the usable width, and certainly not the sample rate. Getting this
    wrong is how a scanner ends up quietly missing a station every few megahertz.

    Args:
        plan: The band to cover.
        sample_rate_hz: Rate the receiver will sample at.
        usable_fraction: Fraction of the window to believe, from
            :attr:`~openwave.sdr.device.SdrDevice.usable_fraction`.

    Returns:
        Segments in ascending frequency order, together covering every channel exactly once.

    Raises:
        ValueError: if the arguments are nonsensical, or if the window is too narrow to fit a
            whole channel and so could never measure one.
    """
    if sample_rate_hz <= 0:
        raise ValueError(f"sample rate must be positive, got {sample_rate_hz}")
    if not 0.0 < usable_fraction <= 1.0:
        raise ValueError(f"usable fraction must be in (0, 1], got {usable_fraction}")

    usable_bandwidth_hz = sample_rate_hz * usable_fraction
    stride_hz = usable_bandwidth_hz - plan.channel_bandwidth_hz
    if stride_hz <= 0:
        raise ValueError(
            f"a {format_frequency(sample_rate_hz)} window is too narrow to measure a "
            f"{format_frequency(plan.channel_bandwidth_hz)} channel: only "
            f"{format_frequency(usable_bandwidth_hz)} of it is usable. Sample at more than "
            f"{format_frequency(plan.channel_bandwidth_hz / usable_fraction)}."
        )

    count = max(1, math.ceil(plan.width_hz / stride_hz))
    half_usable_hz = usable_bandwidth_hz / 2.0
    centers = [plan.start_hz + stride_hz * (index + 0.5) for index in range(count)]

    # Assign each channel to the segment whose centre is nearest, which keeps every measurement
    # as far from a window edge as it can be.
    assignments: list[list[float]] = [[] for _ in centers]
    for channel_hz in plan.channels():
        best = min(range(count), key=lambda index: abs(centers[index] - channel_hz))
        assignments[best].append(channel_hz)

    return tuple(
        Segment(
            center_freq_hz=center,
            sample_rate_hz=sample_rate_hz,
            channel_bandwidth_hz=plan.channel_bandwidth_hz,
            usable_start_hz=center - half_usable_hz,
            usable_end_hz=center + half_usable_hz,
            channels=tuple(channels),
        )
        for center, channels in zip(centers, assignments, strict=True)
        if channels
    )


def total_channels(segments: Iterator[Segment] | tuple[Segment, ...]) -> int:
    """How many channels a set of segments covers in total."""
    return sum(len(segment.channels) for segment in segments)
