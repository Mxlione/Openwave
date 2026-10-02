"""Tests for the scan manager's own bookkeeping.

The parts the HTTP tests do not reach: how many finished scans are remembered, what happens to a
listener whose client has gone away, and what "the latest scan" means when several have run.
"""

from __future__ import annotations

import time

import pytest

from openwave.api.models import Band, ScanRequest, ScanState
from openwave.api.scans import (
    ScanBusyError,
    ScanManager,
    ScanNotFoundError,
    ScanSummary,
)
from openwave.radio.demo import demo_receiver
from openwave.sdr.errors import DeviceNotFoundError
from openwave.tv.demo import demo_tuner

FAST_FM = ScanRequest(band=Band.FM, rds=False, identify=False)


def finished(manager: ScanManager, scan_id: str, *, timeout_s: float = 60.0) -> ScanState:
    """Wait for a scan to finish and return its final state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = manager.job(scan_id).state
        if state.is_finished:
            return state
        time.sleep(0.05)
    raise AssertionError(f"scan {scan_id} did not finish within {timeout_s} s")


class TestConstruction:
    def test_a_history_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one scan"):
            ScanManager(history=0)


class TestRunning:
    def test_a_scan_runs_and_completes(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        assert finished(manager, job.id) is ScanState.COMPLETE
        assert manager.job(job.id).fm_result is not None

    def test_the_manager_is_free_again_afterwards(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        finished(manager, job.id)
        assert not manager.is_busy

    def test_a_second_scan_while_one_runs_is_refused(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        try:
            with pytest.raises(ScanBusyError, match="already running"):
                manager.start(FAST_FM)
        finally:
            manager.cancel(job.id)
            finished(manager, job.id)

    def test_the_refusal_explains_why(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        try:
            with pytest.raises(ScanBusyError, match="two places at once"):
                manager.start(FAST_FM)
        finally:
            manager.cancel(job.id)
            finished(manager, job.id)

    def test_an_unknown_scan_is_refused(self, manager: ScanManager) -> None:
        with pytest.raises(ScanNotFoundError, match="no scan"):
            manager.job("nosuchscan")


class TestFailures:
    def test_a_receiver_that_cannot_be_opened_fails_the_scan(self) -> None:
        def refuse(_spec: str) -> object:
            raise DeviceNotFoundError("nothing plugged in")

        manager = ScanManager(device_factory=refuse)  # type: ignore[arg-type]
        try:
            job = manager.start(FAST_FM)
            assert finished(manager, job.id) is ScanState.FAILED
            assert "nothing plugged in" in (manager.job(job.id).error or "")
        finally:
            manager.shutdown(wait=False)

    def test_an_unexpected_failure_is_recorded_rather_than_swallowed(self) -> None:
        # A worker thread that dies silently leaves a client waiting for a result that will
        # never come, so the reason is kept.
        def explode(_spec: str) -> object:
            raise RuntimeError("something nobody anticipated")

        manager = ScanManager(device_factory=explode)  # type: ignore[arg-type]
        try:
            job = manager.start(FAST_FM)
            assert finished(manager, job.id) is ScanState.FAILED
            assert "RuntimeError" in (manager.job(job.id).error or "")
        finally:
            manager.shutdown(wait=False)

    def test_an_unknown_band_plan_fails_with_the_valid_ones(self, manager: ScanManager) -> None:
        job = manager.start(ScanRequest(band=Band.FM, plan="shortwave", rds=False))
        assert finished(manager, job.id) is ScanState.FAILED
        assert "available plans" in (manager.job(job.id).error or "")


class TestCancellation:
    def test_a_cancelled_scan_reports_as_cancelled(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        manager.cancel(job.id)
        state = finished(manager, job.id)
        # The scan finishes the segment it is on rather than being killed, so a very quick scan
        # may complete before the cancellation is noticed.
        assert state in (ScanState.CANCELLED, ScanState.COMPLETE)

    def test_cancelling_a_finished_scan_is_harmless(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        finished(manager, job.id)
        assert manager.cancel(job.id).state is ScanState.COMPLETE

    def test_cancelling_an_unknown_scan_is_refused(self, manager: ScanManager) -> None:
        with pytest.raises(ScanNotFoundError):
            manager.cancel("nosuchscan")


class TestHistory:
    def test_only_the_most_recent_scans_are_kept(self) -> None:
        # A server left running for a week should not accumulate a megabyte of station lists.
        manager = ScanManager(device_factory=lambda _spec: demo_receiver(), history=3)
        try:
            identifiers = []
            for _ in range(5):
                job = manager.start(FAST_FM)
                finished(manager, job.id)
                identifiers.append(job.id)

            assert len(manager.summaries()) == 3
            with pytest.raises(ScanNotFoundError):
                manager.job(identifiers[0])
            assert manager.job(identifiers[-1]).state is ScanState.COMPLETE
        finally:
            manager.shutdown(wait=False)

    def test_summaries_come_back_newest_first(self, manager: ScanManager) -> None:
        first = manager.start(FAST_FM)
        finished(manager, first.id)
        second = manager.start(FAST_FM)
        finished(manager, second.id)
        assert [summary.id for summary in manager.summaries()][:2] == [second.id, first.id]


class TestLatest:
    def test_the_latest_scan_of_a_band_is_found(self, manager: ScanManager) -> None:
        first = manager.start(FAST_FM)
        finished(manager, first.id)
        second = manager.start(FAST_FM)
        finished(manager, second.id)
        latest = manager.latest(Band.FM)
        assert latest is not None
        assert latest.id == second.id

    def test_bands_are_tracked_separately(self, manager: ScanManager) -> None:
        fm = manager.start(FAST_FM)
        finished(manager, fm.id)
        tv = manager.start(ScanRequest(band=Band.TV, lock_timeout_s=0.05))
        finished(manager, tv.id)

        fm_latest = manager.latest(Band.FM)
        tv_latest = manager.latest(Band.TV)
        assert fm_latest is not None and fm_latest.id == fm.id
        assert tv_latest is not None and tv_latest.id == tv.id

    def test_a_failed_scan_does_not_count_as_the_latest(self, manager: ScanManager) -> None:
        # A station list showing the results of a scan that failed would be worse than showing
        # the previous ones.
        good = manager.start(FAST_FM)
        finished(manager, good.id)
        bad = manager.start(ScanRequest(band=Band.FM, plan="nonsense"))
        finished(manager, bad.id)

        latest = manager.latest(Band.FM)
        assert latest is not None
        assert latest.id == good.id

    def test_no_scan_of_a_band_gives_nothing(self, manager: ScanManager) -> None:
        assert manager.latest(Band.TV) is None


class TestListeners:
    def test_progress_is_published(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        updates: list[ScanSummary] = []
        manager.listen(job.id, updates.append)
        finished(manager, job.id)
        assert updates
        assert updates[-1].state.is_finished

    def test_a_listener_can_unsubscribe(self, manager: ScanManager) -> None:
        job = manager.start(FAST_FM)
        updates: list[ScanSummary] = []
        stop = manager.listen(job.id, updates.append)
        stop()
        finished(manager, job.id)
        assert not updates

    def test_a_listener_that_raises_does_not_stop_the_scan(self, manager: ScanManager) -> None:
        # A client whose connection has gone away must not be able to kill the work it was
        # watching.
        def explode(_summary: ScanSummary) -> None:
            raise RuntimeError("the client vanished")

        job = manager.start(FAST_FM)
        manager.listen(job.id, explode)
        assert finished(manager, job.id) is ScanState.COMPLETE

    def test_listening_to_an_unknown_scan_is_refused(self, manager: ScanManager) -> None:
        with pytest.raises(ScanNotFoundError):
            manager.listen("nosuchscan", lambda _summary: None)


class TestTelevisionScans:
    def test_a_television_scan_finds_the_multiplexes(self, manager: ScanManager) -> None:
        job = manager.start(ScanRequest(band=Band.TV, lock_timeout_s=0.05))
        assert finished(manager, job.id) is ScanState.COMPLETE
        result = manager.job(job.id).tv_result
        assert result is not None
        assert result.mux_count == 3

    def test_the_results_convert_to_the_api_shape(self, manager: ScanManager) -> None:
        job = manager.start(ScanRequest(band=Band.TV, lock_timeout_s=0.05))
        finished(manager, job.id)
        detail = manager.detail(job.id)
        assert detail.tv is not None
        assert len(detail.tv.multiplexes) == 3
        assert all(mux.channel is not None for mux in detail.tv.multiplexes)

    def test_an_unknown_television_plan_fails_with_the_valid_ones(
        self, manager: ScanManager
    ) -> None:
        job = manager.start(ScanRequest(band=Band.TV, plan="satellite"))
        assert finished(manager, job.id) is ScanState.FAILED
        assert "available plans" in (manager.job(job.id).error or "")


def test_the_tuner_factory_is_used_for_television(manager: ScanManager) -> None:
    # Which is what lets a test, or the demonstration mode, supply a populated simulator.
    assert demo_tuner().muxes
    job = manager.start(ScanRequest(band=Band.TV, lock_timeout_s=0.05))
    finished(manager, job.id)
    result = manager.job(job.id).tv_result
    assert result is not None
    assert result.channel_count == 9
