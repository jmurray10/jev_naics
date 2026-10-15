"""The versioned API: /v1 routes, the old paths as deprecated aliases, CORS for the web origin only,
no page, and the committed OpenAPI matches the code."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_naics.api.app import app, get_hybrid_decider, get_searcher, get_spelling
from test_api import _notes
from test_search_pick import VET, Jev, searcher

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"


@pytest.fixture
def client():
    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    app.dependency_overrides[get_spelling] = lambda: _notes([])
    app.dependency_overrides[get_hybrid_decider] = lambda: Jev()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_classify_is_versioned(client) -> None:
    body = client.post("/v1/classify", json={"description": "Vet clinic.", "mode": "hybrid"}).json()
    assert (body["gate"], body["code"]) == ("auto_act", "541940")


def test_unversioned_paths_still_work_and_are_deprecated(client) -> None:
    assert client.post("/classify", json={"description": "Vet clinic.", "mode": "hybrid"}).status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/classify"]["post"].get("deprecated") is True
    assert not spec["paths"]["/v1/classify"]["post"].get("deprecated")


def test_cors_allows_only_the_web_origin(client) -> None:
    ok = client.options("/v1/health", headers={"Origin": "http://127.0.0.1:8097",
                                               "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:8097"
    other = client.options("/v1/health", headers={"Origin": "https://evil.example",
                                                  "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers


def test_the_api_serves_no_page(client) -> None:
    assert client.get("/").status_code == 404


def test_the_committed_openapi_matches_the_code(client) -> None:
    assert json.loads(OPENAPI.read_text(encoding="utf-8")) == client.get("/openapi.json").json(), \
        "regenerate: python -m jev_naics --openapi > openapi.json"
