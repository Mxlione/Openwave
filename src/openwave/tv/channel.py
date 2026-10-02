"""The Channel model.

A Pydantic model, like :class:`~openwave.radio.station.Station`, because this is what the API
returns in v0.5 and what the Angular client's types are generated from.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from openwave.core.units import format_frequency
from openwave.tv.tables import ServiceType


class ChannelKind(StrEnum):
    """What a channel carries, in the terms a viewer would use."""

    TELEVISION = "television"
    RADIO = "radio"
    DATA = "data"
    OTHER = "other"


class Channel(BaseModel):
    """One digital television or radio service, as found by a scan."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="What the service is called, from the Service Description Table")
    service_id: int = Field(
        gt=0,
        le=0xFFFF,
        description="Identifier within the multiplex, which is also its programme number",
    )
    mux_freq_hz: float = Field(
        gt=0.0, description="Centre frequency of the multiplex carrying this service"
    )
    transport_stream_id: int = Field(
        ge=0, le=0xFFFF, description="Identifier of the multiplex carrying this service"
    )
    service_type: int = Field(
        ge=0,
        le=0xFF,
        description=(
            "Service type from EN 300 468. Kept as a number because the list is long, "
            "country-specific in places, and still growing."
        ),
    )

    logical_channel: int | None = Field(
        default=None,
        ge=0,
        le=0x3FF,
        description=(
            "The number a viewer types. Published by the network in a private descriptor "
            "rather than by the standard, so plenty of multiplexes do not carry it."
        ),
    )
    provider: str | None = Field(default=None, description="Who runs the service")
    scrambled: bool = Field(
        default=False,
        description=(
            "Whether access is controlled. OpenWave reports this and goes no further: "
            "see docs/legal.md."
        ),
    )
    running: bool = Field(default=True, description="Whether the service is on the air now")

    video_pid: int | None = Field(
        default=None, ge=0, le=0x1FFF, description="PID of the video stream, if there is one"
    )
    audio_pid: int | None = Field(
        default=None, ge=0, le=0x1FFF, description="PID of the first audio stream"
    )

    snr_db: float | None = Field(
        default=None, description="Carrier-to-noise ratio the tuner reported for the multiplex"
    )
    strength_dbm: float | None = Field(
        default=None, description="Signal strength the tuner reported, where it provides one"
    )

    @property
    def kind(self) -> ChannelKind:
        """What this channel carries.

        Taken from the service type where it is one OpenWave knows, and from whether the
        programme has a video stream otherwise. The service type is the station's own claim and
        is occasionally wrong; the presence of pictures is a fact.
        """
        try:
            declared = ServiceType(self.service_type)
        except ValueError:
            return ChannelKind.TELEVISION if self.video_pid is not None else ChannelKind.OTHER

        if declared.is_television:
            return ChannelKind.TELEVISION
        if declared.is_radio:
            return ChannelKind.RADIO
        if declared is ServiceType.DATA_BROADCAST:
            return ChannelKind.DATA
        return ChannelKind.OTHER

    @property
    def is_television(self) -> bool:
        """Whether this channel carries pictures."""
        return self.kind is ChannelKind.TELEVISION

    @property
    def is_playable(self) -> bool:
        """Whether OpenWave could play this channel.

        A scrambled service cannot be, and a service with no audio or video stream is not a
        programme. Both are listed, because a channel list that silently omits what it cannot
        play leaves a viewer wondering where a channel went.
        """
        return not self.scrambled and (self.video_pid is not None or self.audio_pid is not None)

    @property
    def label(self) -> str:
        """The best available name for display."""
        if self.logical_channel is not None:
            return f"{self.logical_channel}. {self.name}"
        return self.name

    def __str__(self) -> str:
        parts = [self.label, format_frequency(self.mux_freq_hz), self.kind.value]
        if self.scrambled:
            parts.append("scrambled")
        return "  ".join(parts)
