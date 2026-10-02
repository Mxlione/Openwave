"""Running scans as background jobs.

A scan takes seconds for FM and up to a minute for television, which is far too long for an HTTP
request to wait on. So the API starts a scan, hands back an identifier, and lets the caller
collect the result or watch the progress afterwards.

The scanning itself is ordinary blocking code that spends its time in NumPy, so it runs in a
worker thread rather than on the event loop. A thread is the right shape here: NumPy releases
the interpreter lock for the work that matters, and the alternative -- rewriting the signal
processing to yield -- would make it harder to read for no benefit.

**One scan at a time, per receiver.** A receiver cannot be tuned to two places at once, so a
second scan while one is running is refused rather than queued. Queuing would leave somebody
waiting for a scan they did not ask for.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from openwave.api.models import (
    Band,
    FmScanResults,
    MultiplexSummary,
    ScanDetail,
    ScanProgress,
    ScanRequest,
    ScanState,
    ScanSummary,
    TvScanResults,
)
from openwave.core.scanner import ScanSettings
from openwave.core.signal_detector import DEFAULT_DETECTION_THRESHOLD_DB
from openwave.radio.band import FM_BAND_PLANS
from openwave.radio.station_detector import FmScanResult, IdentifySettings, scan_fm
from openwave.sdr.device_manager import open_device, open_dvb_device
from openwave.sdr.errors import DeviceError
from openwave.tv.band import TELEVISION_PLANS
from openwave.tv.dvb_scanner import (
    DEFAULT_LOCK_TIMEOUT_S,
    DvbScanResult,
    DvbScanSettings,
    scan_dvb,
)

#: How many finished scans to remember.
#:
#: Enough that a client can come back to a result after a page reload, few enough that a server
#: left running for a week does not accumulate a megabyte of station lists.
DEFAULT_HISTORY: Final = 20


class ScanBusyError(RuntimeError):
    """A scan is already running on this receiver.

    A receiver cannot be tuned to two places at once, and queuing the request would leave
    somebody waiting on a scan they did not ask for.
    """


class ScanNotFoundError(KeyError):
    """No scan with that identifier, or it has been forgotten."""


@dataclass
class ScanJob:
    """One scan, running or finished."""

    id: str
    band: Band
    request: ScanRequest
    state: ScanState = ScanState.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    progress: ScanProgress | None = None
    error: str | None = None
    fm_result: FmScanResult | None = field(default=None, repr=False)
    tv_result: DvbScanResult | None = field(default=None, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def is_cancelled(self) -> bool:
        """Whether the scan has been asked to stop."""
        return self._cancelled.is_set()

    def cancel(self) -> None:
        """Ask the scan to stop at the next opportunity.

        A scan in progress is left to finish the segment it is on rather than being killed: a
        receiver abandoned mid-read is left in a state the next caller has to clear up.
        """
        self._cancelled.set()

    def summary(self) -> ScanSummary:
        """The job without its results."""
        return ScanSummary(
            id=self.id,
            band=self.band,
            state=self.state,
            started_at=self.started_at,
            finished_at=self.finished_at,
            progress=self.progress,
            error=self.error,
        )

    def detail(self) -> ScanDetail:
        """The job with its results."""
        return ScanDetail(
            **self.summary().model_dump(),
            fm=_fm_results(self.fm_result) if self.fm_result else None,
            tv=_tv_results(self.tv_result) if self.tv_result else None,
        )


def _fm_results(result: FmScanResult) -> FmScanResults:
    """Convert an FM scan's result into the API's shape."""
    return FmScanResults(
        plan=result.scan.plan.name,
        sample_rate_hz=result.scan.sample_rate_hz,
        channels_measured=result.scan.channels_measured,
        coverage=result.scan.coverage,
        complete=result.scan.is_complete,
        stations=result.stations,
    )


def _tv_results(result: DvbScanResult) -> TvScanResults:
    """Convert a television scan's result into the API's shape."""
    return TvScanResults(
        plan=result.plan.name,
        channels_tried=result.channels_tried,
        multiplexes=tuple(
            MultiplexSummary(
                freq_hz=mux.freq_hz,
                channel=mux.channel_number,
                bandwidth_hz=mux.bandwidth_hz,
                transport_stream_id=(
                    mux.tables.pat.transport_stream_id if mux.tables.pat else None
                ),
                network_name=mux.network_name or None,
                snr_db=mux.quality.snr_db,
                services=len(mux.channels),
            )
            for mux in result.muxes
        ),
        channels=result.channels,
        incomplete=tuple(mux.freq_hz for mux in result.incomplete),
    )


class ScanManager:
    """Starts scans, keeps their results, and lets callers watch their progress.

    Example::

        manager = ScanManager(device_spec="mock")
        job = manager.start(ScanRequest(band=Band.FM))
        ...
        detail = manager.detail(job.id)

    Args:
        device_spec: Default receiver for FM scans.
        tuner_spec: Default tuner for television scans.
        device_factory: How to build a receiver from a specification. Replaceable so that a
            test, or the demonstration mode, can supply a populated simulator.
        tuner_factory: The same for tuners.
        history: How many finished scans to remember.
    """

    def __init__(
        self,
        *,
        device_spec: str = "mock",
        tuner_spec: str = "mockdvb",
        device_factory: Callable[[str], Any] | None = None,
        tuner_factory: Callable[[str], Any] | None = None,
        history: int = DEFAULT_HISTORY,
    ) -> None:
        if history < 1:
            raise ValueError(f"history must be at least one scan, got {history}")

        self._device_spec = device_spec
        self._tuner_spec = tuner_spec
        self._open_device = device_factory or open_device
        self._open_tuner = tuner_factory or open_dvb_device
        self._history = history

        self._jobs: dict[str, ScanJob] = {}
        self._order: list[str] = []
        self._running: str | None = None
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openwave-scan")
        self._futures: dict[str, Future[None]] = {}
        self._listeners: dict[str, list[Callable[[ScanSummary], None]]] = {}

    # -- Starting and stopping ----------------------------------------------------------------

    def start(self, request: ScanRequest) -> ScanJob:
        """Start a scan.

        Raises:
            ScanBusyError: if a scan is already running.
        """
        with self._lock:
            if self._running is not None:
                raise ScanBusyError(
                    f"a {self._jobs[self._running].band.value} scan is already running. A "
                    "receiver cannot be tuned to two places at once."
                )
            job = ScanJob(id=uuid.uuid4().hex[:12], band=request.band, request=request)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._running = job.id
            self._prune()

        self._futures[job.id] = self._executor.submit(self._run, job)
        return job

    def cancel(self, scan_id: str) -> ScanJob:
        """Ask a scan to stop.

        Raises:
            ScanNotFoundError: if there is no such scan.
        """
        job = self.job(scan_id)
        if not job.state.is_finished:
            job.cancel()
        return job

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting scans and let any running one finish."""
        for job in list(self._jobs.values()):
            if not job.state.is_finished:
                job.cancel()
        self._executor.shutdown(wait=wait)

    # -- Looking at jobs ----------------------------------------------------------------------

    def job(self, scan_id: str) -> ScanJob:
        """One job.

        Raises:
            ScanNotFoundError: if there is no such scan.
        """
        with self._lock:
            job = self._jobs.get(scan_id)
        if job is None:
            raise ScanNotFoundError(
                f"no scan {scan_id!r}. It may have finished long enough ago to be forgotten: "
                f"the last {self._history} are kept."
            )
        return job

    def summary(self, scan_id: str) -> ScanSummary:
        """One job without its results."""
        return self.job(scan_id).summary()

    def detail(self, scan_id: str) -> ScanDetail:
        """One job with its results."""
        return self.job(scan_id).detail()

    def summaries(self) -> list[ScanSummary]:
        """Every remembered job, newest first."""
        with self._lock:
            jobs = [self._jobs[scan_id] for scan_id in reversed(self._order)]
        return [job.summary() for job in jobs]

    @property
    def is_busy(self) -> bool:
        """Whether a scan is running."""
        with self._lock:
            return self._running is not None

    def latest(self, band: Band) -> ScanJob | None:
        """The most recent completed scan of a band, which is what a station list shows."""
        with self._lock:
            jobs = [self._jobs[scan_id] for scan_id in reversed(self._order)]
        return next(
            (job for job in jobs if job.band is band and job.state is ScanState.COMPLETE),
            None,
        )

    # -- Watching progress --------------------------------------------------------------------

    def listen(self, scan_id: str, callback: Callable[[ScanSummary], None]) -> Callable[[], None]:
        """Call ``callback`` whenever a scan's progress changes.

        Returns a function that stops the subscription. Used by the progress WebSocket.
        """
        self.job(scan_id)  # raises if unknown
        with self._lock:
            self._listeners.setdefault(scan_id, []).append(callback)

        def stop() -> None:
            with self._lock:
                listeners = self._listeners.get(scan_id, [])
                if callback in listeners:
                    listeners.remove(callback)

        return stop

    def _publish(self, job: ScanJob) -> None:
        """Tell anybody watching that a job has changed.

        A listener that raises is dropped rather than allowed to stop the scan: a client whose
        connection has gone away must not be able to kill the work it was watching.
        """
        with self._lock:
            listeners = list(self._listeners.get(job.id, []))
        summary = job.summary()
        for callback in listeners:
            try:
                callback(summary)
            except Exception:  # noqa: BLE001 - a dead client must not stop the scan
                with self._lock:
                    remaining = self._listeners.get(job.id, [])
                    if callback in remaining:
                        remaining.remove(callback)

    # -- The work -----------------------------------------------------------------------------

    def _run(self, job: ScanJob) -> None:
        """Run one scan in the worker thread."""
        job.state = ScanState.RUNNING
        job.progress = ScanProgress(stage="Starting", done=0, total=0)
        self._publish(job)

        try:
            if job.band is Band.FM:
                job.fm_result = self._run_fm(job)
            else:
                job.tv_result = self._run_tv(job)
            job.state = ScanState.CANCELLED if job.is_cancelled else ScanState.COMPLETE
        except DeviceError as error:
            job.state = ScanState.FAILED
            job.error = f"{type(error).__name__}: {error}"
        except ValueError as error:
            job.state = ScanState.FAILED
            job.error = str(error)
        except Exception as error:  # noqa: BLE001 - a silent worker death strands the client
            job.state = ScanState.FAILED
            job.error = f"{type(error).__name__}: {error}"
        finally:
            job.finished_at = datetime.now(UTC)
            with self._lock:
                if self._running == job.id:
                    self._running = None
            self._publish(job)

    def _run_fm(self, job: ScanJob) -> FmScanResult:
        """Sweep the FM band."""
        request = job.request
        plan_name = (request.plan or "fm").lower()
        plan = FM_BAND_PLANS.get(plan_name)
        if plan is None:
            raise ValueError(
                f"unknown FM band plan {plan_name!r}; available plans are "
                f"{', '.join(FM_BAND_PLANS)}"
            )

        settings = ScanSettings(
            sample_rate_hz=request.sample_rate_hz,
            threshold_db=(
                request.threshold_db
                if request.threshold_db is not None
                else DEFAULT_DETECTION_THRESHOLD_DB
            ),
        )
        identify_settings = IdentifySettings(rds=request.rds)

        def on_sweep(index: int, total: int, segment: Any) -> None:
            self._advance(job, "Sweeping the band", index, total, segment.center_freq_hz)

        def on_identify(index: int, total: int, freq_hz: float) -> None:
            stage = "Reading RDS" if request.rds else "Checking stereo"
            self._advance(job, stage, index, total, freq_hz)

        with self._open_device(request.device or self._device_spec) as receiver:
            return scan_fm(
                receiver,
                plan=plan,
                scan_settings=settings,
                identify_settings=identify_settings,
                identify=request.identify,
                sweep_progress=on_sweep,
                identify_progress=on_identify,
            )

    def _run_tv(self, job: ScanJob) -> DvbScanResult:
        """Sweep a television band."""
        request = job.request
        plan_name = (request.plan or "uhf").lower()
        plan = TELEVISION_PLANS.get(plan_name)
        if plan is None:
            raise ValueError(
                f"unknown television band plan {plan_name!r}; available plans are "
                f"{', '.join(TELEVISION_PLANS)}"
            )

        settings = DvbScanSettings(
            include_scrambled=request.include_scrambled,
            lock_timeout_s=(
                request.lock_timeout_s
                if request.lock_timeout_s is not None
                else DEFAULT_LOCK_TIMEOUT_S
            ),
        )

        def on_channel(index: int, total: int, freq_hz: float) -> None:
            self._advance(job, "Tuning channels", index, total, freq_hz)

        with self._open_tuner(request.device or self._tuner_spec) as tuner:
            return scan_dvb(tuner, plan, settings=settings, progress=on_channel)

    def _advance(
        self, job: ScanJob, stage: str, done: int, total: int, frequency_hz: float | None
    ) -> None:
        """Record progress and tell anybody watching."""
        job.progress = ScanProgress(stage=stage, done=done, total=total, frequency_hz=frequency_hz)
        self._publish(job)

    def _prune(self) -> None:
        """Forget the oldest finished jobs. The lock must already be held."""
        while len(self._order) > self._history:
            oldest = next(
                (scan_id for scan_id in self._order if self._jobs[scan_id].state.is_finished),
                None,
            )
            if oldest is None:
                return
            self._order.remove(oldest)
            self._jobs.pop(oldest, None)
            self._listeners.pop(oldest, None)
            self._futures.pop(oldest, None)
