"""Tests for remembered stations and channels.

The behaviour that matters is identity: what counts as the same favourite, and what happens when
the same one is added twice. A list that quietly holds a station twice is the kind of bug nobody
can explain afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openwave.api.app import API_PREFIX, create_app
from openwave.api.favourites import (
    MAX_FAVOURITES,
    Favourite,
    FavouriteKind,
    FavouriteStore,
)
from openwave.radio.demo import demo_receiver


def station(name: str = "CITY FM", freq_hz: float = 101.7e6) -> Favourite:
    return Favourite(kind=FavouriteKind.STATION, name=name, freq_hz=freq_hz)


def channel(name: str = "OpenWave One", freq_hz: float = 498e6, service_id: int = 1) -> Favourite:
    return Favourite(kind=FavouriteKind.CHANNEL, name=name, freq_hz=freq_hz, service_id=service_id)


@pytest.fixture
def store(tmp_path: Path) -> FavouriteStore:
    """A store writing to a temporary file rather than the user's real list."""
    return FavouriteStore(tmp_path / "favourites.json")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """An API whose favourites go to a temporary file."""
    app = create_app(
        device_factory=lambda _spec: demo_receiver(),
        favourite_store=FavouriteStore(tmp_path / "favourites.json"),
    )
    return TestClient(app)


class TestIdentity:
    def test_a_station_is_identified_by_its_frequency(self, store: FavouriteStore) -> None:
        store.add(station(name="One", freq_hz=101.7e6))
        store.add(station(name="Two", freq_hz=101.7e6))
        assert len(store.all()) == 1
        assert store.all()[0].name == "Two"

    def test_a_small_frequency_difference_is_the_same_station(self, store: FavouriteStore) -> None:
        # A receiver reports where it was tuned, so the same station found by two scans can
        # differ by a few hertz. A list that then held it twice would be inexplicable.
        store.add(station(freq_hz=101_700_000.0))
        store.add(station(freq_hz=101_700_120.0))
        assert len(store.all()) == 1

    def test_a_genuinely_different_frequency_is_a_different_station(
        self, store: FavouriteStore
    ) -> None:
        store.add(station(freq_hz=101.7e6))
        store.add(station(freq_hz=101.8e6))
        assert len(store.all()) == 2

    def test_a_channel_is_identified_by_its_multiplex_and_service(
        self, store: FavouriteStore
    ) -> None:
        # Several services share one frequency, so the frequency alone cannot identify them.
        store.add(channel(name="One", service_id=1))
        store.add(channel(name="Two", service_id=2))
        assert len(store.all()) == 2

    def test_a_station_and_a_channel_at_the_same_frequency_are_different(
        self, store: FavouriteStore
    ) -> None:
        store.add(station(freq_hz=498e6))
        store.add(channel(freq_hz=498e6, service_id=0))
        assert len(store.all()) == 2


class TestStore:
    def test_it_starts_empty(self, store: FavouriteStore) -> None:
        assert store.all() == []

    def test_favourites_come_back_in_frequency_order(self, store: FavouriteStore) -> None:
        store.add(station(name="High", freq_hz=107.9e6))
        store.add(station(name="Low", freq_hz=88.1e6))
        assert [item.name for item in store.all()] == ["Low", "High"]

    def test_they_can_be_filtered_by_kind(self, store: FavouriteStore) -> None:
        store.add(station())
        store.add(channel())
        assert len(store.all(FavouriteKind.STATION)) == 1
        assert len(store.all(FavouriteKind.CHANNEL)) == 1

    def test_membership_can_be_tested(self, store: FavouriteStore) -> None:
        store.add(station())
        assert store.contains(station())
        assert not store.contains(station(freq_hz=98e6))

    def test_removing_reports_whether_it_was_there(self, store: FavouriteStore) -> None:
        store.add(station())
        assert store.remove(station())
        assert not store.remove(station())

    def test_clearing_forgets_everything(self, store: FavouriteStore) -> None:
        store.add(station())
        store.add(channel())
        store.clear()
        assert store.all() == []

    def test_a_full_list_refuses_another(self, store: FavouriteStore) -> None:
        # A limit exists so a misbehaving client cannot grow the file without bound.
        for index in range(MAX_FAVOURITES):
            store.add(station(name=f"S{index}", freq_hz=88e6 + index * 1e5))
        with pytest.raises(ValueError, match="at most"):
            store.add(station(name="One too many", freq_hz=500e6))

    def test_a_full_list_still_accepts_a_replacement(self, store: FavouriteStore) -> None:
        # Replacing is not growing, so renaming a station must still work on a full list.
        for index in range(MAX_FAVOURITES):
            store.add(station(name=f"S{index}", freq_hz=88e6 + index * 1e5))
        store.add(station(name="Renamed", freq_hz=88e6))
        assert store.all()[0].name == "Renamed"

    def test_a_name_is_required(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Favourite(kind=FavouriteKind.STATION, name="", freq_hz=98e6)

    def test_a_non_positive_frequency_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Favourite(kind=FavouriteKind.STATION, name="X", freq_hz=0.0)


class TestPersistence:
    def test_it_survives_being_reopened(self, tmp_path: Path) -> None:
        path = tmp_path / "favourites.json"
        first = FavouriteStore(path)
        first.add(station())
        first.add(channel())

        second = FavouriteStore(path)
        assert [item.name for item in second.all()] == ["CITY FM", "OpenWave One"]

    def test_the_file_is_readable_json(self, tmp_path: Path) -> None:
        path = tmp_path / "favourites.json"
        store = FavouriteStore(path)
        store.add(station())
        entries = json.loads(path.read_text())
        assert isinstance(entries, list)
        assert entries[0]["name"] == "CITY FM"

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert FavouriteStore(tmp_path / "absent.json").all() == []

    def test_a_corrupt_file_does_not_stop_the_server_starting(self, tmp_path: Path) -> None:
        # Losing a favourites list is a nuisance; refusing to start is a fault.
        path = tmp_path / "favourites.json"
        path.write_text("{not json at all")
        assert FavouriteStore(path).all() == []

    def test_a_file_that_is_not_a_list_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "favourites.json"
        path.write_text('{"station": "CITY FM"}')
        assert FavouriteStore(path).all() == []

    def test_one_bad_entry_does_not_lose_the_others(self, tmp_path: Path) -> None:
        # The file is editable by hand and survives upgrades, so it can easily hold something
        # this version does not expect.
        path = tmp_path / "favourites.json"
        path.write_text(
            json.dumps(
                [
                    {"kind": "station", "name": "Good", "freq_hz": 98e6},
                    {"kind": "nonsense", "name": "Bad", "freq_hz": -1},
                    {"kind": "station", "name": "Also good", "freq_hz": 101.7e6},
                ]
            )
        )
        names = [item.name for item in FavouriteStore(path).all()]
        assert names == ["Good", "Also good"]

    def test_nothing_is_written_when_autosave_is_off(self, tmp_path: Path) -> None:
        path = tmp_path / "favourites.json"
        store = FavouriteStore(path, autosave=False)
        store.add(station())
        assert not path.exists()
        store.save()
        assert path.is_file()

    def test_the_directory_is_created_if_it_does_not_exist(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "favourites.json"
        FavouriteStore(path).add(station())
        assert path.is_file()

    def test_no_temporary_files_are_left_behind(self, tmp_path: Path) -> None:
        # The write goes to a temporary file and is then moved into place, so that a crash
        # partway through leaves the previous list rather than half of the new one.
        store = FavouriteStore(tmp_path / "favourites.json")
        store.add(station())
        assert not list(tmp_path.glob("*.tmp"))


class TestRoutes:
    def test_the_list_starts_empty(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/favourites").json() == []

    def test_a_favourite_can_be_added(self, client: TestClient) -> None:
        response = client.post(
            f"{API_PREFIX}/favourites",
            json={"kind": "station", "name": "CITY FM", "freq_hz": 101700000.0},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "CITY FM"
        assert len(client.get(f"{API_PREFIX}/favourites").json()) == 1

    def test_adding_the_same_one_twice_replaces_it(self, client: TestClient) -> None:
        for name in ("First name", "Second name"):
            client.post(
                f"{API_PREFIX}/favourites",
                json={"kind": "station", "name": name, "freq_hz": 101700000.0},
            )
        listed = client.get(f"{API_PREFIX}/favourites").json()
        assert len(listed) == 1
        assert listed[0]["name"] == "Second name"

    def test_they_can_be_filtered_by_kind(self, client: TestClient) -> None:
        client.post(
            f"{API_PREFIX}/favourites",
            json={"kind": "station", "name": "CITY FM", "freq_hz": 101700000.0},
        )
        client.post(
            f"{API_PREFIX}/favourites",
            json={
                "kind": "channel",
                "name": "OpenWave One",
                "freq_hz": 498000000.0,
                "service_id": 1,
            },
        )
        stations = client.get(f"{API_PREFIX}/favourites?kind=station").json()
        assert [item["name"] for item in stations] == ["CITY FM"]

    def test_a_favourite_can_be_removed(self, client: TestClient) -> None:
        body = {"kind": "station", "name": "CITY FM", "freq_hz": 101700000.0}
        client.post(f"{API_PREFIX}/favourites", json=body)
        response = client.request("DELETE", f"{API_PREFIX}/favourites", json=body)
        assert response.status_code == 204
        assert client.get(f"{API_PREFIX}/favourites").json() == []

    def test_removing_something_absent_is_not_an_error(self, client: TestClient) -> None:
        # A client that sends the same request twice should end up with the state it asked for
        # rather than an error to handle.
        body = {"kind": "station", "name": "Never added", "freq_hz": 98000000.0}
        assert client.request("DELETE", f"{API_PREFIX}/favourites", json=body).status_code == 204

    def test_an_invalid_favourite_is_refused(self, client: TestClient) -> None:
        response = client.post(
            f"{API_PREFIX}/favourites", json={"kind": "station", "name": "", "freq_hz": -1}
        )
        assert response.status_code == 422

    def test_the_routes_are_in_the_schema(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert f"{API_PREFIX}/favourites" in paths
        assert {"get", "post", "delete"} <= set(paths[f"{API_PREFIX}/favourites"])
