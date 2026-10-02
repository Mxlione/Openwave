"""Terrestrial television channel plans.

Digital terrestrial television is transmitted on a numbered grid, and the numbers are what
people and equipment use: a transmitter is "on channel 24", not "on 498 MHz". The two plans
below cover where DVB-T is broadcast.

**UHF**, channels 21 to 48, 8 MHz apart. The grid is ``306 + 8 * channel`` MHz, so channel 21 is
474 MHz and channel 48 is 690 MHz. It used to run to channel 69, but the bands above 694 MHz
were reallocated to mobile networks across most of the world, so a scan that still swept them
would spend a quarter of its time listening to telephones.

**VHF band III**, channels 5 to 12, 7 MHz apart. Used for DVB-T in parts of Europe, Australia
and elsewhere; empty in much of the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from openwave.core.frequency_manager import BandPlan
from openwave.core.units import format_frequency


@dataclass(frozen=True, slots=True)
class TelevisionChannelPlan:
    """A numbered grid of television channels.

    Attributes:
        name: What to call this plan in output meant for people.
        first_channel: Lowest channel number.
        last_channel: Highest channel number.
        offset_hz: Constant in the grid formula, so that channel N is at
            ``offset_hz + spacing_hz * N``.
        spacing_hz: Distance between channels, which for television also equals the bandwidth
            each one occupies.
    """

    name: str
    first_channel: int
    last_channel: int
    offset_hz: float
    spacing_hz: float

    def __post_init__(self) -> None:
        if self.first_channel > self.last_channel:
            raise ValueError(
                f"channel range is inverted: {self.first_channel} to {self.last_channel}"
            )
        if self.spacing_hz <= 0:
            raise ValueError(f"channel spacing must be positive, got {self.spacing_hz}")

    @property
    def channels(self) -> range:
        """Every channel number in the plan."""
        return range(self.first_channel, self.last_channel + 1)

    @property
    def channel_count(self) -> int:
        """How many channels the plan holds."""
        return self.last_channel - self.first_channel + 1

    @property
    def bandwidth_hz(self) -> float:
        """Bandwidth one channel occupies. For television this equals the spacing."""
        return self.spacing_hz

    def frequency_of(self, channel: int) -> float:
        """The centre frequency of a channel number.

        Raises:
            ValueError: if the channel is not in this plan.
        """
        if channel not in self.channels:
            raise ValueError(
                f"channel {channel} is not in the {self.name} plan "
                f"({self.first_channel} to {self.last_channel})"
            )
        return self.offset_hz + self.spacing_hz * channel

    def channel_of(self, freq_hz: float) -> int | None:
        """The channel number nearest a frequency, or ``None`` if it is outside the plan.

        A tuner reports where it is tuned, not where the grid says, so a found multiplex is
        matched to the nearest channel rather than to an exact frequency. On a uniform grid that
        is always within half a channel, so being outside the plan's range is the only way to
        be off it -- which is why there is no separate tolerance check here.
        """
        candidate = round((freq_hz - self.offset_hz) / self.spacing_hz)
        if candidate not in self.channels:
            return None
        return int(candidate)

    def frequencies(self) -> tuple[float, ...]:
        """Every centre frequency in the plan, lowest first."""
        return tuple(self.frequency_of(channel) for channel in self.channels)

    def as_band_plan(self) -> BandPlan:
        """The same plan in the form the generic scanner understands."""
        return BandPlan(
            name=self.name,
            start_hz=self.frequency_of(self.first_channel),
            end_hz=self.frequency_of(self.last_channel),
            channel_spacing_hz=self.spacing_hz,
            channel_bandwidth_hz=self.bandwidth_hz,
        )

    def __str__(self) -> str:
        return (
            f"{self.name} (channels {self.first_channel}-{self.last_channel}, "
            f"{format_frequency(self.frequency_of(self.first_channel))} to "
            f"{format_frequency(self.frequency_of(self.last_channel))})"
        )


#: UHF channels 21 to 48, which is where most of the world's DVB-T lives.
UHF_PLAN: Final = TelevisionChannelPlan(
    name="UHF",
    first_channel=21,
    last_channel=48,
    offset_hz=306e6,
    spacing_hz=8e6,
)

#: UHF channels 21 to 69, the plan before the bands above 694 MHz went to mobile networks.
#:
#: Kept because some countries still broadcast there, and because a hardware report from one of
#: them is more useful than a scan that stopped at 690 MHz.
UHF_PLAN_EXTENDED: Final = TelevisionChannelPlan(
    name="UHF (extended)",
    first_channel=21,
    last_channel=69,
    offset_hz=306e6,
    spacing_hz=8e6,
)

#: VHF band III, channels 5 to 12, 7 MHz apart.
VHF_BAND_III_PLAN: Final = TelevisionChannelPlan(
    name="VHF band III",
    first_channel=5,
    last_channel=12,
    offset_hz=142.5e6,
    spacing_hz=7e6,
)

#: Plans by name, for selecting one from a command line or an API request.
TELEVISION_PLANS: Final = {
    "uhf": UHF_PLAN,
    "uhf-extended": UHF_PLAN_EXTENDED,
    "vhf": VHF_BAND_III_PLAN,
}
