"""Tests for serving the built interface alongside the API.

One process serving both means no second port and no cross-origin rules, but it puts a
catch-all mount next to the API routes -- and a catch-all that catches too much is a mistake
that shows up a long way from its cause.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from openwave.api.app import API_PREFIX, INTERFACE_ROOT, create_app
from openwave.radio.demo import demo_receiver

built = pytest.mark.skipif(
    not (INTERFACE_ROOT / "index.html").is_file(),
    reason="the interface has not been built; run npm run build in frontend/",
)


@pytest.fixture
def client() -> TestClient:
    app = create_app(device_factory=lambda _spec: demo_receiver())
    return TestClient(app)


class TestApiComesFirst:
    def test_the_api_still_answers(self, client: TestClient) -> None:
        # The mount is at "/", so registering it before the routes would swallow all of them.
        assert client.get(f"{API_PREFIX}/health").status_code == 200

    def test_an_unknown_api_route_is_a_json_404(self, client: TestClient) -> None:
        # Not a page of HTML. Handing a client HTML to parse as JSON turns a typo into a
        # baffling error a long way from its cause.
        response = client.get(f"{API_PREFIX}/nosuchroute")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    def test_an_unknown_scan_is_still_a_json_404(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/scans/nosuchscan")
        assert response.status_code == 404
        assert json.loads(response.text)["error"] == "ScanNotFound"


@built
class TestInterfaceIsServed:
    def test_the_root_serves_the_application(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "<app-root>" in response.text

    @pytest.mark.parametrize("path", ["/stations", "/channels", "/spectrum"])
    def test_a_browser_route_serves_the_application(self, client: TestClient, path: str) -> None:
        # These are routes the browser resolves once the application has loaded, not files.
        # Somebody who bookmarks one, or reloads the page, asks the server for it directly.
        response = client.get(path)
        assert response.status_code == 200
        assert "<app-root>" in response.text

    def test_a_real_asset_is_served_with_its_own_type(self, client: TestClient) -> None:
        stylesheet = next(INTERFACE_ROOT.glob("styles-*.css"), None)
        assert stylesheet is not None, "the build produced no stylesheet"
        response = client.get(f"/{stylesheet.name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")

    def test_a_post_to_a_route_that_does_not_exist_is_not_a_page(self, client: TestClient) -> None:
        # A POST to a missing route is a mistake, not a page somebody is trying to open.
        assert client.post("/nosuchpage").status_code in (404, 405)


class TestWithoutABuild:
    def test_the_api_works_whether_or_not_the_interface_is_built(self, client: TestClient) -> None:
        # A backend contributor should not have to install Node to run the API.
        assert client.get(f"{API_PREFIX}/health").status_code == 200
        assert client.get(f"{API_PREFIX}/devices").status_code == 200
