"""Reading a multiplex's tables out of its transport stream.

A scan tunes a channel, reads a few hundred kilobytes of transport stream, and needs to know
what is in it. That means collecting four tables, and they cannot all be collected at once:
the PMTs live on PIDs that only the PAT can reveal, so the parser has to read the PAT first and
then start listening to PIDs it did not know about a moment earlier.

:class:`MuxParser` does that, and says when it has enough. Knowing when to stop matters: a
multiplex repeats its tables several times a second, so a scan that reads a fixed amount wastes
most of its time, and one that reads until a timeout wastes all of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from openwave.tv.psi import (
    NIT_ACTUAL_TABLE_ID,
    PAT_TABLE_ID,
    PMT_TABLE_ID,
    SDT_ACTUAL_TABLE_ID,
    Section,
    SectionAssembler,
)
from openwave.tv.tables import Nit, Pat, Pmt, Sdt, parse_nit, parse_pat, parse_pmt, parse_sdt
from openwave.tv.ts import NIT_PID, PAT_PID, SDT_PID, TsPacket, iter_packets

#: PIDs a parser listens to before it knows anything about the multiplex.
BASE_PIDS: Final = frozenset({PAT_PID, SDT_PID, NIT_PID})


@dataclass(frozen=True, slots=True)
class MuxTables:
    """The tables collected from one multiplex.

    Attributes:
        pat: Which programmes exist, and where their descriptions are.
        sdt: What the services are called.
        nit: The network, its frequencies and its channel numbers.
        pmts: Programme number to what that programme is made of.
        sections_seen: How many sections were reassembled.
        sections_rejected: How many were dropped for a failed CRC or a bad length. A steady
            trickle means the multiplex is being received badly, which is worth saying rather
            than quietly showing half a channel list.
    """

    pat: Pat | None = None
    sdt: Sdt | None = None
    nit: Nit | None = None
    pmts: dict[int, Pmt] = field(default_factory=dict)
    sections_seen: int = 0
    sections_rejected: int = 0

    @property
    def has_services(self) -> bool:
        """Whether enough arrived to list the channels."""
        return self.pat is not None and self.sdt is not None

    @property
    def is_complete(self) -> bool:
        """Whether every table a scan wants has arrived.

        The NIT is not required: it carries the channel numbers, which are a convenience, and
        plenty of multiplexes send it rarely or not at all. Waiting for one would mean a scan
        that never finishes on a perfectly good signal.
        """
        if self.pat is None or self.sdt is None:
            return False
        return all(number in self.pmts for number in self.pat.programmes)

    @property
    def missing(self) -> tuple[str, ...]:
        """What has not arrived yet, for a diagnostic."""
        absent: list[str] = []
        if self.pat is None:
            absent.append("PAT")
        if self.sdt is None:
            absent.append("SDT")
        if self.nit is None:
            absent.append("NIT")
        if self.pat is not None:
            for number in sorted(self.pat.programmes):
                if number not in self.pmts:
                    absent.append(f"PMT for programme {number}")
        return tuple(absent)

    def __str__(self) -> str:
        present = [
            name
            for name, table in (("PAT", self.pat), ("SDT", self.sdt), ("NIT", self.nit))
            if table is not None
        ]
        return f"{', '.join(present) or 'nothing'} and {len(self.pmts)} PMTs"


@dataclass
class MuxParser:
    """Collects a multiplex's tables from its transport stream.

    Example::

        parser = MuxParser()
        while not parser.is_complete:
            parser.feed(device.read_ts(188 * 1000))
        tables = parser.tables

    Attributes:
        version_changes: How many times a table was seen with a new version number. A multiplex
            whose tables keep changing is being reconfigured, or two transmitters are being
            received at once.
    """

    version_changes: int = 0
    _assembler: SectionAssembler = field(default_factory=SectionAssembler, repr=False)
    _pat: Pat | None = field(default=None, repr=False)
    _sdt: Sdt | None = field(default=None, repr=False)
    _nit: Nit | None = field(default=None, repr=False)
    _pmts: dict[int, Pmt] = field(default_factory=dict, repr=False)
    _versions: dict[tuple[int, int], int] = field(default_factory=dict, repr=False)
    _pmt_pids: set[int] = field(default_factory=set, repr=False)
    _sections: int = field(default=0, repr=False)

    # -- Feeding ------------------------------------------------------------------------------

    def feed(self, stream: bytes) -> None:
        """Take a block of transport stream.

        Malformed packets are skipped rather than raising: a stream off the air contains some,
        and stopping at the first would mean never reading a multiplex with one bad packet in it.
        """
        for packet in iter_packets(stream, skip_malformed=True):
            self.feed_packet(packet)

    def feed_packet(self, packet: TsPacket) -> None:
        """Take one packet."""
        if packet.pid not in BASE_PIDS and packet.pid not in self._pmt_pids:
            return
        for section in self._assembler.feed(packet):
            self._accept(section, packet.pid)

    def _accept(self, section: Section, pid: int) -> None:
        """Take one reassembled section, if it is a table worth reading."""
        self._sections += 1
        if not section.current:
            # Describes a change still to come, not what is on the air now.
            return

        key = (section.table_id, section.extension)
        previous = self._versions.get(key)
        if previous is not None and previous != section.version:
            self.version_changes += 1
        self._versions[key] = section.version

        if pid == PAT_PID and section.table_id == PAT_TABLE_ID:
            self._pat = parse_pat(section)
            # The PAT is what reveals where the PMTs are, so new PIDs start being listened to
            # here rather than at the start.
            self._pmt_pids.update(self._pat.programmes.values())
        elif pid == SDT_PID and section.table_id == SDT_ACTUAL_TABLE_ID:
            self._merge_sdt(parse_sdt(section))
        elif pid == NIT_PID and section.table_id == NIT_ACTUAL_TABLE_ID:
            self._merge_nit(parse_nit(section))
        elif pid in self._pmt_pids and section.table_id == PMT_TABLE_ID:
            pmt = parse_pmt(section)
            self._pmts[pmt.programme_number] = pmt

    def _merge_sdt(self, sdt: Sdt) -> None:
        """Add an SDT section's services to what is already known.

        A large multiplex splits its SDT across several sections, so each one carries only some
        of the services. Replacing rather than merging would leave a channel list containing
        only whichever section arrived last.
        """
        if self._sdt is None:
            self._sdt = sdt
            return
        services = {entry.service_id: entry for entry in self._sdt.services}
        services.update({entry.service_id: entry for entry in sdt.services})
        self._sdt = Sdt(
            transport_stream_id=sdt.transport_stream_id,
            original_network_id=sdt.original_network_id,
            services=tuple(sorted(services.values(), key=lambda entry: entry.service_id)),
        )

    def _merge_nit(self, nit: Nit) -> None:
        """Add a NIT section's multiplexes to what is already known."""
        if self._nit is None:
            self._nit = nit
            return
        entries = {entry.transport_stream_id: entry for entry in self._nit.transport_streams}
        entries.update({entry.transport_stream_id: entry for entry in nit.transport_streams})
        self._nit = Nit(
            network_id=nit.network_id,
            network_name=nit.network_name or self._nit.network_name,
            transport_streams=tuple(
                sorted(entries.values(), key=lambda entry: entry.transport_stream_id)
            ),
        )

    # -- Results ------------------------------------------------------------------------------

    @property
    def tables(self) -> MuxTables:
        """Everything collected so far."""
        return MuxTables(
            pat=self._pat,
            sdt=self._sdt,
            nit=self._nit,
            pmts=dict(self._pmts),
            sections_seen=self._sections,
            sections_rejected=self._assembler.rejected,
        )

    @property
    def is_complete(self) -> bool:
        """Whether every table a scan wants has arrived."""
        return self.tables.is_complete

    @property
    def has_services(self) -> bool:
        """Whether enough arrived to list the channels."""
        return self.tables.has_services

    @property
    def pmt_pids(self) -> frozenset[int]:
        """PIDs the parser has learnt carry PMTs."""
        return frozenset(self._pmt_pids)

    def reset(self) -> None:
        """Forget everything, for retuning to another multiplex."""
        self._assembler.reset()
        self._pat = None
        self._sdt = None
        self._nit = None
        self._pmts.clear()
        self._versions.clear()
        self._pmt_pids.clear()
        self._sections = 0
        self.version_changes = 0


def parse_mux(stream: bytes) -> MuxTables:
    """Read every table out of a complete transport stream.

    A convenience for a stream already in memory, such as a recorded capture. A scan uses
    :class:`MuxParser` directly, because it wants to stop reading as soon as it has enough.
    """
    parser = MuxParser()
    parser.feed(stream)
    return parser.tables
