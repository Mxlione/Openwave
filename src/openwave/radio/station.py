"""The Station model.

A Pydantic model rather than a plain dataclass, because this is what the API in v0.5 returns and
what the Angular client's types will be generated from. Defining it once, here, is what keeps
the interface from drifting away from the backend.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openwave.core.units import format_frequency
from openwave.radio.rds.groups import ProgrammeType


class Station(BaseModel):
    """One FM broadcast station, as found by a scan.

    Levels are in dBFS, relative to full scale. A consumer SDR has no calibrated power
    reference, so comparisons between stations and signal-to-noise ratios are meaningful while
    absolute power is not. See :mod:`openwave.core.units`.
    """

    model_config = ConfigDict(frozen=True)

    freq_hz: float = Field(gt=0.0, description="Carrier frequency in hertz")
    power_dbfs: float = Field(description="Channel power relative to full scale")
    snr_db: float = Field(description="How far the channel stands above the noise floor")
    bandwidth_hz: float = Field(gt=0.0, description="Channel bandwidth the power was measured over")

    stereo: bool | None = Field(
        default=None,
        description=(
            "Whether a stereo pilot was found. None means it was not checked: a band scan "
            "measures power only, and deciding stereo needs the station demodulated."
        ),
    )
    pilot_level_db: float | None = Field(
        default=None,
        description="How far the 19 kHz pilot stood above the multiplex around it, when checked",
    )
    name: str | None = Field(
        default=None,
        max_length=64,
        description="Station name, from the RDS programme service field.",
    )
    pi: int | None = Field(
        default=None,
        ge=0,
        le=0xFFFF,
        description=(
            "RDS programme identification: a 16-bit code identifying the station within its "
            "country. Transmitters carrying the same programme share it, which is how a "
            "receiver follows a station across frequencies."
        ),
    )
    pty: int | None = Field(
        default=None,
        ge=0,
        le=31,
        description=(
            "RDS programme type, as the raw five-bit number. Europe and North America assign "
            "the same bits to different genres, so the number is kept rather than a label."
        ),
    )
    radio_text: str | None = Field(
        default=None,
        max_length=64,
        description="RDS RadioText: a longer free-text field, often the programme or track.",
    )

    @field_validator("name", "radio_text")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        """Trim RDS text, and treat an all-blank value as absent.

        RDS pads names to eight characters with spaces, and a transmitter sending nothing but
        padding should read as "no name" rather than as a station called "        ".
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def freq_mhz(self) -> float:
        """Carrier frequency in megahertz, which is how FM frequencies are spoken."""
        return self.freq_hz / 1e6

    @property
    def is_stereo(self) -> bool:
        """Whether this station is known to be stereo. Unchecked counts as not stereo."""
        return self.stereo is True

    @property
    def label(self) -> str:
        """The best available name for display: the RDS name, else the frequency."""
        return self.name or f"{self.freq_mhz:.1f} MHz"

    @property
    def programme_type(self) -> ProgrammeType | None:
        """The programme type as a European assignment, if one was received."""
        return None if self.pty is None else ProgrammeType(self.pty)

    def __str__(self) -> str:
        parts = [format_frequency(self.freq_hz), f"{self.snr_db:+.1f} dB"]
        if self.stereo is not None:
            parts.append("stereo" if self.stereo else "mono")
        if self.name:
            parts.append(self.name)
        return "  ".join(parts)
