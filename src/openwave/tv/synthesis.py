"""Building a transport stream that does not exist.

With no DVB-T tuner available, this is how the television side of OpenWave is tested: describe a
multiplex, build the stream a transmitter would send, and check the parser recovers the
description. Written from the standards rather than from the parser, so that a round trip proves
something.

What it builds is a real transport stream: 188-byte packets, PSI sections with correct CRCs,
tables repeating at a plausible rate, and null packets padding it to a constant size. What it
does not build is any actual video or audio -- the payload PIDs carry filler. A channel scan
never looks at them, and generating real elementary streams would be a video encoder.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Final

from openwave.tv.psi import (
    NIT_ACTUAL_TABLE_ID,
    PAT_TABLE_ID,
    PMT_TABLE_ID,
    SDT_ACTUAL_TABLE_ID,
    build_section,
    pack_section,
)
from openwave.tv.tables import (
    LOGICAL_CHANNEL_DESCRIPTOR_TAG,
    NETWORK_NAME_DESCRIPTOR_TAG,
    SERVICE_DESCRIPTOR_TAG,
    SERVICE_LIST_DESCRIPTOR_TAG,
    TERRESTRIAL_DELIVERY_DESCRIPTOR_TAG,
    ServiceType,
)
from openwave.tv.ts import NIT_PID, PAT_PID, SDT_PID, null_packet

#: PID of the first programme's PMT. Later programmes take the next PIDs up.
FIRST_PMT_PID: Final = 0x0100

#: PID of the first programme's video stream.
FIRST_VIDEO_PID: Final = 0x0200

#: PID of the first programme's audio stream.
FIRST_AUDIO_PID: Final = 0x0300

#: Stream type for H.264 video.
H264_STREAM_TYPE: Final = 0x1B

#: Stream type for AAC audio in ADTS framing.
AAC_STREAM_TYPE: Final = 0x0F

#: How many null packets separate one repeat of the tables from the next.
#:
#: A real multiplex sends its PSI tables a few times a second amid a flood of video. The ratio
#: here is far smaller, because a test wants a channel list from a short stream rather than a
#: realistic bit rate -- but it is not zero, because a parser that only works on back-to-back
#: tables would fail on anything real.
DEFAULT_PADDING_PACKETS: Final = 8


@dataclass(frozen=True, slots=True)
class SyntheticService:
    """One service to put in a synthetic multiplex.

    Attributes:
        service_id: Identifier, which is also the programme number in the PAT.
        name: What the service is called.
        provider: Who runs it.
        service_type: What kind of service, from :class:`~openwave.tv.tables.ServiceType`.
        logical_channel: The number a viewer would type, published in the NIT.
        scrambled: Whether to mark the service as access-controlled. OpenWave reports this and
            goes no further, so a scan should list it and say so.
        running: Whether the service is on the air.
    """

    service_id: int
    name: str
    provider: str = "OpenWave"
    service_type: int = int(ServiceType.DIGITAL_TELEVISION)
    logical_channel: int | None = None
    scrambled: bool = False
    running: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.service_id <= 0xFFFF:
            raise ValueError(f"service identifier must fit in 16 bits, got {self.service_id}")
        if not self.name:
            raise ValueError("a service needs a name")
        if self.logical_channel is not None and not 0 <= self.logical_channel <= 0x3FF:
            raise ValueError(
                f"a logical channel number must fit in 10 bits, got {self.logical_channel}"
            )

    @property
    def carries_video(self) -> bool:
        """Whether this service should be given a video stream."""
        try:
            return ServiceType(self.service_type).is_television
        except ValueError:
            return True


@dataclass(frozen=True, slots=True)
class SyntheticMultiplex:
    """A whole multiplex to build.

    Attributes:
        transport_stream_id: Identifier of the multiplex.
        services: What it carries.
        network_id: Identifier of the network it belongs to.
        network_name: What the network is called.
        centre_frequency_hz: The frequency the NIT says this multiplex is on.
        bandwidth_hz: Channel bandwidth the NIT reports.
        original_network_id: Network the multiplex originated on.
    """

    transport_stream_id: int = 0x1000
    services: tuple[SyntheticService, ...] = ()
    network_id: int = 0x2000
    network_name: str = "OpenWave Network"
    centre_frequency_hz: float = 498_000_000.0
    bandwidth_hz: float = 8e6
    original_network_id: int = 0x3000

    def __post_init__(self) -> None:
        if not self.services:
            raise ValueError("a multiplex needs at least one service")
        identifiers = [service.service_id for service in self.services]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("two services share a service identifier")

    def pmt_pid(self, index: int) -> int:
        """PID of the PMT describing the service at ``index``."""
        return FIRST_PMT_PID + index

    def video_pid(self, index: int) -> int:
        """PID of the video stream of the service at ``index``."""
        return FIRST_VIDEO_PID + index

    def audio_pid(self, index: int) -> int:
        """PID of the audio stream of the service at ``index``."""
        return FIRST_AUDIO_PID + index


def build_pat(multiplex: SyntheticMultiplex, *, version: int = 0) -> bytes:
    """Build the Program Association Table section."""
    payload = bytearray()
    # Programme number zero points at the Network Information Table.
    payload += struct.pack(">HH", 0x0000, 0xE000 | NIT_PID)
    for index, service in enumerate(multiplex.services):
        payload += struct.pack(">HH", service.service_id, 0xE000 | multiplex.pmt_pid(index))
    return build_section(
        PAT_TABLE_ID,
        bytes(payload),
        extension=multiplex.transport_stream_id,
        version=version,
    )


def build_pmt(multiplex: SyntheticMultiplex, index: int, *, version: int = 0) -> bytes:
    """Build the Program Map Table section for one service."""
    service = multiplex.services[index]
    payload = bytearray()
    payload += struct.pack(">H", 0xE000 | multiplex.video_pid(index))  # PCR PID
    payload += struct.pack(">H", 0xF000)  # no programme-level descriptors

    if service.carries_video:
        payload += struct.pack(
            ">BHH", H264_STREAM_TYPE, 0xE000 | multiplex.video_pid(index), 0xF000
        )
    payload += struct.pack(">BHH", AAC_STREAM_TYPE, 0xE000 | multiplex.audio_pid(index), 0xF000)

    return build_section(
        PMT_TABLE_ID, bytes(payload), extension=service.service_id, version=version
    )


def build_sdt(multiplex: SyntheticMultiplex, *, version: int = 0) -> bytes:
    """Build the Service Description Table section, which carries the names."""
    payload = bytearray()
    payload += struct.pack(">H", multiplex.original_network_id)
    payload += bytes([0xFF])  # reserved

    for service in multiplex.services:
        descriptor_body = bytearray()
        descriptor_body += bytes([service.service_type])
        provider = service.provider.encode("latin-1", errors="replace")
        descriptor_body += bytes([len(provider)]) + provider
        name = service.name.encode("latin-1", errors="replace")
        descriptor_body += bytes([len(name)]) + name
        descriptor = bytes([SERVICE_DESCRIPTOR_TAG, len(descriptor_body)]) + bytes(descriptor_body)

        running_status = 4 if service.running else 1
        flags = (running_status << 5) | (0x10 if service.scrambled else 0x00)
        payload += struct.pack(">H", service.service_id)
        payload += bytes([0xFC])  # reserved, with both EIT flags clear
        payload += bytes([flags | ((len(descriptor) >> 8) & 0x0F), len(descriptor) & 0xFF])
        payload += descriptor

    return build_section(
        SDT_ACTUAL_TABLE_ID,
        bytes(payload),
        extension=multiplex.transport_stream_id,
        version=version,
    )


def build_nit(multiplex: SyntheticMultiplex, *, version: int = 0) -> bytes:
    """Build the Network Information Table section, with the frequency and channel numbers."""
    name = multiplex.network_name.encode("latin-1", errors="replace")
    network_descriptors = bytes([NETWORK_NAME_DESCRIPTOR_TAG, len(name)]) + name

    transport_descriptors = bytearray()

    # Terrestrial delivery: the frequency is in units of 10 Hz.
    bandwidth_code = {8e6: 0, 7e6: 1, 6e6: 2, 5e6: 3}.get(multiplex.bandwidth_hz, 0)
    delivery = bytearray()
    delivery += struct.pack(">I", round(multiplex.centre_frequency_hz / 10.0))
    delivery += bytes([(bandwidth_code << 5) | 0x1F])
    delivery += bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])  # the rest, unused here
    transport_descriptors += bytes([TERRESTRIAL_DELIVERY_DESCRIPTOR_TAG, len(delivery)]) + bytes(
        delivery
    )

    service_list = bytearray()
    for service in multiplex.services:
        service_list += struct.pack(">HB", service.service_id, service.service_type)
    transport_descriptors += bytes([SERVICE_LIST_DESCRIPTOR_TAG, len(service_list)]) + bytes(
        service_list
    )

    numbered = [s for s in multiplex.services if s.logical_channel is not None]
    if numbered:
        channel_list = bytearray()
        for service in numbered:
            assert service.logical_channel is not None
            channel_list += struct.pack(">HH", service.service_id, 0xFC00 | service.logical_channel)
        transport_descriptors += bytes([LOGICAL_CHANNEL_DESCRIPTOR_TAG, len(channel_list)]) + bytes(
            channel_list
        )

    transport_loop = bytearray()
    transport_loop += struct.pack(
        ">HHH",
        multiplex.transport_stream_id,
        multiplex.original_network_id,
        0xF000 | len(transport_descriptors),
    )
    transport_loop += bytes(transport_descriptors)

    payload = bytearray()
    payload += struct.pack(">H", 0xF000 | len(network_descriptors))
    payload += network_descriptors
    payload += struct.pack(">H", 0xF000 | len(transport_loop))
    payload += bytes(transport_loop)

    return build_section(
        NIT_ACTUAL_TABLE_ID,
        bytes(payload),
        extension=multiplex.network_id,
        version=version,
    )


@dataclass
class _Counters:
    """Continuity counters, one per PID, which a real stream increments per packet."""

    values: dict[int, int] = field(default_factory=dict)

    def next(self, pid: int, step: int = 1) -> int:
        """The current counter for a PID, advancing it by ``step``."""
        current = self.values.get(pid, 0)
        self.values[pid] = (current + step) % 16
        return current


def build_transport_stream(
    multiplex: SyntheticMultiplex,
    *,
    repeats: int = 4,
    padding_packets: int = DEFAULT_PADDING_PACKETS,
    version: int = 0,
) -> bytes:
    """Build a transport stream carrying ``multiplex``.

    The tables repeat, because a receiver tuning in partway through has to be able to read them.
    Null packets separate the repeats, so a parser that only works on back-to-back tables fails
    here rather than on real hardware.

    Args:
        multiplex: What to transmit.
        repeats: How many times to send the full set of tables.
        padding_packets: Null packets between one repeat and the next.
        version: Table version number.
    """
    if repeats < 1:
        raise ValueError(f"a stream needs at least one repeat of its tables, got {repeats}")
    if padding_packets < 0:
        raise ValueError(f"padding must not be negative, got {padding_packets}")

    counters = _Counters()
    stream = bytearray()

    for _ in range(repeats):
        for pid, section in _table_cycle(multiplex, version=version):
            packets = pack_section(pid, section, continuity_start=counters.values.get(pid, 0))
            counters.next(pid, step=len(packets) // 188)
            stream += packets

        for _ in range(padding_packets):
            stream += null_packet(counters.next(0x1FFF))

    return bytes(stream)


def _table_cycle(multiplex: SyntheticMultiplex, *, version: int) -> list[tuple[int, bytes]]:
    """One full set of tables, each with the PID it belongs on."""
    tables: list[tuple[int, bytes]] = [
        (PAT_PID, build_pat(multiplex, version=version)),
        (SDT_PID, build_sdt(multiplex, version=version)),
        (NIT_PID, build_nit(multiplex, version=version)),
    ]
    tables.extend(
        (multiplex.pmt_pid(index), build_pmt(multiplex, index, version=version))
        for index in range(len(multiplex.services))
    )
    return tables


def demo_multiplex(
    *, centre_frequency_hz: float = 498_000_000.0, transport_stream_id: int = 0x1000
) -> SyntheticMultiplex:
    """A plausible multiplex, for trying a TV scan without a tuner.

    The spread is deliberate: television and radio together, a scrambled service that a scan
    must list without pretending it can be watched, and logical channel numbers that are not in
    the order the services appear.
    """
    return SyntheticMultiplex(
        transport_stream_id=transport_stream_id,
        centre_frequency_hz=centre_frequency_hz,
        services=(
            SyntheticService(service_id=0x0001, name="OpenWave One", logical_channel=1),
            SyntheticService(service_id=0x0002, name="OpenWave Two", logical_channel=2),
            SyntheticService(
                service_id=0x0003,
                name="OpenWave HD",
                service_type=int(ServiceType.ADVANCED_CODEC_HD_TELEVISION),
                logical_channel=101,
            ),
            SyntheticService(
                service_id=0x0004,
                name="OpenWave Radio",
                service_type=int(ServiceType.DIGITAL_RADIO),
                logical_channel=701,
            ),
            SyntheticService(
                service_id=0x0005,
                name="Premium Sport",
                provider="Somebody Else",
                logical_channel=401,
                scrambled=True,
            ),
        ),
    )
