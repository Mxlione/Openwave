"""What the API sends and receives.

Everything crossing the boundary is a Pydantic model, so the OpenAPI schema is generated from
the same definitions the code uses and the Angular client can be generated from that. Defining
the shapes once is what keeps the interface from drifting away from the backend.

:class:`~openwave.radio.station.Station` and :class:`~openwave.tv.channel.Channel` are reused as
they are rather than copied into API-specific versions. A second definition of a station would be
a second thing to keep in step.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openwave.radio.station import Station
from openwave.tv.channel import Channel


class Band(StrEnum):
    """Which band a scan covers."""

    FM = "fm"
    TV = "tv"


class ScanState(StrEnum):
    """Where a scan has got to.

    A scan takes seconds for FM and up to a minute for television, which is far too long for an
    HTTP request to wait on. So a scan is a job: it is started, it progresses, and its result is
    collected afterwards.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_finished(self) -> bool:
        """Whether this scan will not change again."""
        return self in {ScanState.COMPLETE, ScanState.FAILED, ScanState.CANCELLED}


class Capabilities(BaseModel):
    """What this installation can actually do.

    Worth asking before offering a button that cannot work: playback needs libVLC, the RTL-SDR
    driver needs its optional extra, and a scan needs a receiver.
    """

    model_config = ConfigDict(frozen=True)

    version: str = Field(description="OpenWave version")
    playback: bool = Field(description="Whether audio can be played, which needs libVLC")
    libvlc_version: str | None = Field(default=None, description="libVLC version, if present")
    rtlsdr: bool = Field(description="Whether the RTL-SDR driver's dependency is installed")
    receivers: int = Field(ge=0, description="How many IQ receivers were found")
    tuners: int = Field(ge=0, description="How many DVB tuners were found")


class DeviceSummary(BaseModel):
    """One receiver or tuner, as a device picker needs it."""

    model_config = ConfigDict(frozen=True)

    spec: str = Field(description="What to pass as the device, such as 'rtlsdr:1'")
    driver: str
    index: int
    label: str
    serial: str | None = None
    kind: Literal["receiver", "tuner"] = Field(
        description="Whether this produces IQ samples or a demodulated transport stream"
    )
    validated_on_hardware: bool = Field(
        description=(
            "Whether this driver has ever been run against the hardware it targets. False for "
            "the real receivers: the maintainer owns none, so they are written from the vendor "
            "interfaces and unverified. A client should say so rather than imply otherwise."
        )
    )


class ScanRequest(BaseModel):
    """What to scan, and how thoroughly."""

    band: Band = Field(default=Band.FM, description="Which band to sweep")
    device: str | None = Field(
        default=None,
        description=("Receiver or tuner to use. Defaults to whatever the server was started with."),
    )
    plan: str | None = Field(
        default=None,
        description="Band plan: 'fm' or 'fm-japan' for radio, 'uhf' or 'vhf' for television",
    )
    threshold_db: float | None = Field(
        default=None,
        description="Signal-to-noise ratio for an FM channel to count as occupied",
    )
    sample_rate_hz: float | None = Field(
        default=None, gt=0.0, description="Sample rate for an FM scan"
    )
    identify: bool = Field(
        default=True,
        description="Whether to demodulate each FM station to decide stereo",
    )
    rds: bool = Field(
        default=True,
        description=(
            "Whether to read RDS, which is what fills in station names. It needs about a "
            "second of signal per station and so dominates how long a scan takes."
        ),
    )
    lock_timeout_s: float | None = Field(
        default=None,
        ge=0.0,
        description="Seconds to give a DVB demodulator to lock onto each channel",
    )
    include_scrambled: bool = Field(
        default=True, description="Whether to list television services that are encrypted"
    )


class ScanProgress(BaseModel):
    """How far a scan has got."""

    model_config = ConfigDict(frozen=True)

    stage: str = Field(description="What the scan is doing, in words a person can read")
    done: int = Field(ge=0, description="Steps completed")
    total: int = Field(ge=0, description="Steps in this stage, or zero if not yet known")
    frequency_hz: float | None = Field(
        default=None, description="Where the receiver is at this moment"
    )

    @property
    def fraction(self) -> float:
        """How far through, from 0.0 to 1.0. Zero when the total is not yet known."""
        return self.done / self.total if self.total else 0.0


class ScanSummary(BaseModel):
    """A scan without its results, for a listing."""

    model_config = ConfigDict(frozen=True)

    id: str
    band: Band
    state: ScanState
    started_at: datetime
    finished_at: datetime | None = None
    progress: ScanProgress | None = None
    error: str | None = Field(default=None, description="Why the scan failed, when it did")

    @property
    def duration_s(self) -> float | None:
        """How long the scan took, if it has finished."""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class FmScanResults(BaseModel):
    """What an FM scan found."""

    model_config = ConfigDict(frozen=True)

    plan: str
    sample_rate_hz: float
    channels_measured: int = Field(ge=0)
    coverage: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of the band the receiver could reach. Below one when replaying a "
            "capture, which holds a single window of spectrum."
        ),
    )
    complete: bool
    stations: tuple[Station, ...] = ()


class TvScanResults(BaseModel):
    """What a television scan found."""

    model_config = ConfigDict(frozen=True)

    plan: str
    channels_tried: int = Field(ge=0)
    multiplexes: tuple[MultiplexSummary, ...] = ()
    channels: tuple[Channel, ...] = ()
    incomplete: tuple[float, ...] = Field(
        default=(),
        description=(
            "Frequencies that locked but whose tables never arrived. Reported separately "
            "because the signal being present and unreadable usually means marginal reception."
        ),
    )


class MultiplexSummary(BaseModel):
    """One multiplex a television scan locked onto."""

    model_config = ConfigDict(frozen=True)

    freq_hz: float
    channel: int | None = Field(default=None, description="Channel number on the plan scanned")
    bandwidth_hz: float
    transport_stream_id: int | None = None
    network_name: str | None = None
    snr_db: float | None = None
    services: int = Field(ge=0)


class ScanDetail(ScanSummary):
    """A scan with its results."""

    fm: FmScanResults | None = None
    tv: TvScanResults | None = None


class ErrorResponse(BaseModel):
    """What the API returns when something goes wrong.

    The message is meant to be shown to a person, because the most common failures here are
    things a person can fix: a dongle not plugged in, a driver not installed, a band plan that
    does not exist.
    """

    model_config = ConfigDict(frozen=True)

    error: str = Field(description="The kind of problem, as an exception name")
    detail: str = Field(description="What went wrong, in words a person can act on")


# Pydantic needs the forward reference in TvScanResults resolved once MultiplexSummary exists.
TvScanResults.model_rebuild()
