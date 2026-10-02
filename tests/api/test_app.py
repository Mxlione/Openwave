"""Tests for the HTTP API.

The API is what the Angular client and anything else talks to, so these tests pin down the
contract: the shapes, the status codes, and the behaviour that matters when something is wrong.
"""

from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openwave.api.app import API_PREFIX, create_app
from openwave.sdr.errors import DeviceNotFoundError
from openwave.tv.demo import DEMO_MULTIPLEXES

from .conftest import FAST_FM, FAST_TV, wait_for_scan


class TestHealth:
    def test_it_reports_the_version(self, client: TestClient) -> None:
        body = client.get(f"{API_PREFIX}/health").json()
        assert body["version"]

    def test_it_reports_what_the_installation_can_do(self, client: TestClient) -> None:
        # Worth asking before offering a button that cannot work.
        body = client.get(f"{API_PREFIX}/health").json()
        assert isinstance(body["playback"], bool)
        assert isinstance(body["rtlsdr"], bool)
        assert body["receivers"] >= 1
        assert body["tuners"] >= 1

    def test_the_libvlc_version_matches_whether_playback_works(self, client: TestClient) -> None:
        body = client.get(f"{API_PREFIX}/health").json()
        assert (body["libvlc_version"] is None) == (not body["playback"])


class TestDevices:
    def test_receivers_and_tuners_are_both_listed(self, client: TestClient) -> None:
        kinds = {device["kind"] for device in client.get(f"{API_PREFIX}/devices").json()}
        assert kinds == {"receiver", "tuner"}

    def test_each_device_says_what_to_pass_as_its_specification(self, client: TestClient) -> None:
        for device in client.get(f"{API_PREFIX}/devices").json():
            assert device["spec"] == f"{device['driver']}:{device['index']}"

    def test_the_simulators_are_marked_as_validated(self, client: TestClient) -> None:
        # They are the only drivers that have actually been run against what they target.
        simulated = [
            device
            for device in client.get(f"{API_PREFIX}/devices").json()
            if device["driver"].startswith("mock")
        ]
        assert simulated
        assert all(device["validated_on_hardware"] for device in simulated)


class TestScanLifecycle:
    def test_starting_a_scan_returns_at_once(self, client: TestClient) -> None:
        # A sweep takes seconds to a minute, which is far too long for a request to wait on.
        response = client.post(f"{API_PREFIX}/scans", json=FAST_FM)
        assert response.status_code == 202
        body = response.json()
        assert body["id"]
        assert body["state"] in ("pending", "running")

    def test_a_scan_completes_and_reports_its_results(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        detail = wait_for_scan(client, started["id"])
        assert detail["state"] == "complete", detail["error"]
        assert detail["fm"] is not None
        assert len(detail["fm"]["stations"]) == 8
        assert detail["fm"]["complete"] is True

    def test_a_television_scan_reports_its_multiplexes(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json=FAST_TV).json()
        detail = wait_for_scan(client, started["id"])
        assert detail["state"] == "complete", detail["error"]
        assert detail["tv"] is not None
        assert len(detail["tv"]["multiplexes"]) == len(DEMO_MULTIPLEXES)
        assert len(detail["tv"]["channels"]) == 9

    def test_scans_are_listed_newest_first(self, client: TestClient) -> None:
        first = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        wait_for_scan(client, first["id"])
        second = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        wait_for_scan(client, second["id"])

        listed = [scan["id"] for scan in client.get(f"{API_PREFIX}/scans").json()]
        assert listed.index(second["id"]) < listed.index(first["id"])

    def test_a_second_scan_while_one_runs_is_refused(self, client: TestClient) -> None:
        # A receiver cannot be tuned to two places at once, and queuing would leave somebody
        # waiting on a scan they did not ask for.
        first = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        try:
            response = client.post(f"{API_PREFIX}/scans", json=FAST_FM)
            assert response.status_code == 409
            assert "already running" in response.json()["detail"]
        finally:
            client.delete(f"{API_PREFIX}/scans/{first['id']}")
            wait_for_scan(client, first["id"])

    def test_a_scan_can_be_cancelled(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        response = client.delete(f"{API_PREFIX}/scans/{started['id']}")
        assert response.status_code == 200
        detail = wait_for_scan(client, started["id"])
        assert detail["state"] in ("cancelled", "complete")

    def test_an_unknown_scan_is_a_not_found(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/scans/nosuchscan")
        assert response.status_code == 404
        assert response.json()["error"] == "ScanNotFound"

    def test_the_error_says_that_old_scans_are_forgotten(self, client: TestClient) -> None:
        # Which is the likely reason a client's identifier stopped working.
        detail = client.get(f"{API_PREFIX}/scans/nosuchscan").json()["detail"]
        assert "forgotten" in detail


class TestScanOptions:
    def test_an_unknown_band_plan_fails_with_the_valid_ones(self, client: TestClient) -> None:
        started = client.post(
            f"{API_PREFIX}/scans", json={"band": "fm", "plan": "shortwave", "rds": False}
        ).json()
        detail = wait_for_scan(client, started["id"])
        assert detail["state"] == "failed"
        assert "available plans" in (detail["error"] or "")

    def test_a_higher_threshold_finds_fewer_stations(self, client: TestClient) -> None:
        lenient = client.post(f"{API_PREFIX}/scans", json={**FAST_FM, "threshold_db": 10.0}).json()
        found_lenient = wait_for_scan(client, lenient["id"])["fm"]["stations"]

        strict = client.post(f"{API_PREFIX}/scans", json={**FAST_FM, "threshold_db": 45.0}).json()
        found_strict = wait_for_scan(client, strict["id"])["fm"]["stations"]

        assert len(found_strict) < len(found_lenient)

    def test_skipping_identification_leaves_stereo_unknown(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json={"band": "fm", "identify": False}).json()
        stations = wait_for_scan(client, started["id"])["fm"]["stations"]
        assert stations
        assert all(station["stereo"] is None for station in stations)

    def test_reading_rds_fills_in_station_names(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json={"band": "fm", "rds": True}).json()
        stations = wait_for_scan(client, started["id"])["fm"]["stations"]
        assert any(station["name"] for station in stations)

    def test_scrambled_television_services_can_be_left_out(self, client: TestClient) -> None:
        started = client.post(
            f"{API_PREFIX}/scans", json={**FAST_TV, "include_scrambled": False}
        ).json()
        channels = wait_for_scan(client, started["id"])["tv"]["channels"]
        assert not any(channel["scrambled"] for channel in channels)


class TestResults:
    def test_stations_are_empty_before_any_scan(self, client: TestClient) -> None:
        # Which is different from a scan that found nothing. The scan list says which.
        assert client.get(f"{API_PREFIX}/stations").json() == []

    def test_stations_reflect_the_latest_completed_scan(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        wait_for_scan(client, started["id"])
        stations = client.get(f"{API_PREFIX}/stations").json()
        assert len(stations) == 8
        assert {"freq_hz", "snr_db", "stereo", "name"} <= set(stations[0])

    def test_channels_are_empty_before_any_television_scan(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/channels").json() == []

    def test_channels_reflect_the_latest_television_scan(self, client: TestClient) -> None:
        started = client.post(f"{API_PREFIX}/scans", json=FAST_TV).json()
        wait_for_scan(client, started["id"])
        channels = client.get(f"{API_PREFIX}/channels").json()
        assert len(channels) == 9
        assert {"name", "service_id", "logical_channel", "scrambled"} <= set(channels[0])


class TestStreams:
    def test_nothing_is_streaming_to_begin_with(self, client: TestClient) -> None:
        body = client.get(f"{API_PREFIX}/streams").json()
        assert body["frequency_hz"] is None
        assert body["listeners"] == 0

    def test_a_bounded_clip_is_a_playable_wav(self, client: TestClient) -> None:
        # Bounded rather than endless: an endless response cannot be completed, and the point
        # of this test is the format rather than the duration. The endless case is covered in
        # test_streams.py, against the streamer itself.
        response = client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0.3")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")

        with wave.open(io.BytesIO(response.content)) as reader:
            assert reader.getnchannels() == 2
            assert reader.getframerate() == 48_000
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == pytest.approx(0.3 * 48_000, rel=0.05)

    def test_the_clip_carries_the_station_that_was_asked_for(self, client: TestClient) -> None:
        # 88.1 MHz in the demonstration band transmits 1 kHz on the left and 400 Hz on the
        # right, so the audio can be checked rather than merely counted.
        import numpy as np
        from scipy.signal import welch

        response = client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0.5")
        with wave.open(io.BytesIO(response.content)) as reader:
            frames = reader.readframes(reader.getnframes())
            rate = reader.getframerate()

        samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
        left, right = samples[0::2], samples[1::2]

        def level(channel: np.ndarray, target_hz: float) -> float:
            freqs, psd = welch(channel, fs=rate, nperseg=min(8192, len(channel)))
            return float(psd[int(np.argmin(np.abs(freqs - target_hz)))])

        assert level(left, 1000.0) > 10 * level(left, 400.0)
        assert level(right, 400.0) > 10 * level(right, 1000.0)

    def test_the_frequency_asked_for_comes_back_in_a_header(self, client: TestClient) -> None:
        # So a client can tell it got the station it wanted.
        response = client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0.1")
        assert response.headers["x-openwave-frequency-hz"] == "88100000.0"

    def test_the_stream_is_not_cached(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0.1")
        assert "no-store" in response.headers["cache-control"]

    def test_a_non_positive_frequency_is_refused(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/streams/fm/0").status_code == 400

    def test_a_non_positive_duration_is_refused(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0")
        assert response.status_code == 400
        assert "must be positive" in response.json()["detail"]

    def test_the_stream_is_released_when_the_clip_ends(self, client: TestClient) -> None:
        # Otherwise the receiver stays claimed and the next request is refused.
        client.get(f"{API_PREFIX}/streams/fm/88100000?seconds=0.1")
        body = client.get(f"{API_PREFIX}/streams").json()
        assert body["frequency_hz"] is None
        assert body["listeners"] == 0


class TestProgressWebSocket:
    def test_progress_is_pushed_until_the_scan_finishes(self, client: TestClient) -> None:
        # The alternative is a client polling every few hundred milliseconds to learn that a
        # sweep has moved on by one channel.
        started = client.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
        states: list[str] = []
        stages: set[str] = set()

        with client.websocket_connect(f"{API_PREFIX}/ws/scans/{started['id']}") as socket:
            for _ in range(200):
                update = socket.receive_json()
                states.append(update["state"])
                if update["progress"]:
                    stages.add(update["progress"]["stage"])
                if update["state"] in ("complete", "failed", "cancelled"):
                    break

        assert states[-1] == "complete"
        assert "Sweeping the band" in stages

    def test_an_unknown_scan_closes_the_socket(self, client: TestClient) -> None:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"{API_PREFIX}/ws/scans/nosuchscan") as socket,
        ):
            socket.receive_json()


class TestSchema:
    def test_the_schema_is_generated(self, client: TestClient) -> None:
        # The Angular client is generated from this, which is what keeps the two in step.
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"] == "OpenWave"
        assert schema["components"]["schemas"]

    def test_every_route_sits_behind_the_version_prefix(self, client: TestClient) -> None:
        # A breaking change has to be something a client can detect rather than discover at
        # run time.
        paths = client.get("/openapi.json").json()["paths"]
        assert paths
        assert all(path.startswith(API_PREFIX) for path in paths)

    def test_the_documented_routes_are_all_present(self, client: TestClient) -> None:
        paths = set(client.get("/openapi.json").json()["paths"])
        assert {
            f"{API_PREFIX}/health",
            f"{API_PREFIX}/devices",
            f"{API_PREFIX}/scans",
            f"{API_PREFIX}/scans/{{scan_id}}",
            f"{API_PREFIX}/stations",
            f"{API_PREFIX}/channels",
            f"{API_PREFIX}/streams/fm/{{freq_hz}}",
        } <= paths


class TestFailures:
    def test_a_receiver_that_cannot_be_opened_fails_the_scan(self) -> None:
        def refuse(_spec: str) -> object:
            raise DeviceNotFoundError("no dongle here")

        app = create_app(device_factory=refuse)  # type: ignore[arg-type]
        with TestClient(app) as failing:
            started = failing.post(f"{API_PREFIX}/scans", json=FAST_FM).json()
            detail = wait_for_scan(failing, started["id"])
            assert detail["state"] == "failed"
            assert "no dongle here" in (detail["error"] or "")
