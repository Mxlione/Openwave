"""Shared fixtures for the API tests.

Every test runs against the simulator, so the whole suite works with no hardware and in CI.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openwave.api.app import API_PREFIX, create_app
from openwave.api.scans import ScanManager
from openwave.radio.demo import demo_receiver
from openwave.tv.demo import demo_tuner

#: Scans that finish quickly: no RDS, and a lock timeout the simulator never needs.
FAST_FM = {"band": "fm", "rds": False}
FAST_TV = {"band": "tv", "lock_timeout_s": 0.05}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """An API serving the invented band and multiplexes."""
    app = create_app(
        device_factory=lambda _spec: demo_receiver(),
        tuner_factory=lambda _spec: demo_tuner(),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def manager() -> Iterator[ScanManager]:
    """A scan manager wired to the simulator."""
    instance = ScanManager(
        device_factory=lambda _spec: demo_receiver(),
        tuner_factory=lambda _spec: demo_tuner(),
    )
    try:
        yield instance
    finally:
        instance.shutdown(wait=False)


def wait_for_scan(client: TestClient, scan_id: str, *, timeout_s: float = 120.0) -> dict[str, Any]:
    """Poll a scan until it finishes, and return its detail.

    Polling rather than using the WebSocket, because the point of most of these tests is the
    result rather than how progress is delivered.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        detail = client.get(f"{API_PREFIX}/scans/{scan_id}").json()
        if detail["state"] in ("complete", "failed", "cancelled"):
            return detail
        time.sleep(0.1)
    raise AssertionError(f"scan {scan_id} did not finish within {timeout_s} s")
