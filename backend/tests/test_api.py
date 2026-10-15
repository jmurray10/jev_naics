"""The jev_naics API (JEV-48): classify over full NAICS (search, tree walk, or hybrid), look up codes."""

import pytest
from fastapi.testclient import TestClient
from jev_naics._jev.openapi import SWAGGER_UI, openapi_problems
from jev_naics._jev import ReplayMissing

from jev_naics.api.app import app, get_full_decider
from test_full_naics import Oracle


@pytest.fixture
def client():
    app.dependency_overrides[get_full_decider] = lambda: Oracle()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_swagger_is_complete_searchable_and_in_order() -> None:
    spec = app.openapi()
    assert openapi_problems(spec) == []
    assert app.swagger_ui_parameters == SWAGGER_UI
    assert [t["name"] for t in spec["tags"]] == ["classify", "naics", "service"]


def test_full_classification_returns_the_code_path_and_how_it_got_there(client) -> None:
    body = client.post("/classify", json={"description": "Family dental practice.", "mode": "full"}).json()
    assert (body["gate"], body["code"], body["title"]) == ("auto_act", "621210", "Offices of Dentists")
    assert [step["code"] for step in body["path"]] == ["62", "621", "6212", "62121", "621210"]
    assert body["path"][0]["level"] == "sector" and body["trace"] and body["finalists"]
    assert body["trace"][0]["asked"][0]["top"][0]["code"] == "62"


def test_tree_is_the_same_walk_as_full_under_the_librarys_name_for_it(client) -> None:
    # The library and README call this method "tree"; the API and page call it "full" (JEV-55).
    # The API accepts both, and the response's mode echoes whichever was sent.
    body = client.post("/classify", json={"description": "Family dental practice.", "mode": "tree"}).json()
    assert (body["mode"], body["gate"], body["code"], body["title"]) == \
        ("tree", "auto_act", "621210", "Offices of Dentists")
    full = client.post("/classify", json={"description": "Family dental practice.", "mode": "full"}).json()
    assert full["mode"] == "full"
    skip = {"mode", "seconds"}
    assert {k: v for k, v in body.items() if k not in skip} == {k: v for k, v in full.items() if k not in skip}


def test_a_hold_is_a_normal_result_with_its_reason(client) -> None:
    app.dependency_overrides[get_full_decider] = lambda: Oracle(underspecified=0.9)
    body = client.post("/classify", json={"description": "We help businesses.", "mode": "full"}).json()
    assert (body["gate"], body["code"], body["hold_reason"]) == ("hold", None, "too_vague")


def test_no_recording_and_no_key_is_a_documented_409(client) -> None:
    class NoRecording:
        def judge(self, state, questions):
            raise ReplayMissing("none")

    app.dependency_overrides[get_full_decider] = lambda: NoRecording()
    response = client.post("/classify", json={"description": "Anything.", "mode": "full"})
    assert response.status_code == 409
    assert "409" in app.openapi()["paths"]["/classify"]["post"]["responses"]


@pytest.mark.parametrize("body", [{"description": "  ", "mode": "full"}, {"description": "x", "mode": "all"}])
def test_bad_requests_are_documented_422s(client, body) -> None:
    assert client.post("/classify", json=body).status_code == 422


def test_code_lookup_shows_the_official_node_and_unknown_codes_404(client) -> None:
    node = client.get("/naics/621210").json()
    assert (node["title"], node["level"]) == ("Offices of Dentists", "national industry")
    assert [p["code"] for p in node["path"]] == ["62", "621", "6212", "62121"]
    assert "dental" in node["description"].lower() and node["index_terms"]
    assert client.get("/naics/62").json()["children"]
    assert client.get("/naics/999999").status_code == 404


def test_search_finds_industries_by_index_terms_without_calling_jev(client) -> None:
    hits = client.get("/naics", params={"q": "tattoo"}).json()
    assert hits and hits[0]["code"] == "812199"
    assert client.get("/naics", params={"q": "x"}).status_code == 422


def test_the_eval_list_and_health_are_served(client) -> None:
    evals = client.get("/evals").json()
    assert any(e["id"] == "dental-office" and e["expect_code"] == "621210" for e in evals)
    health = client.get("/health").json()
    assert (health["industries"], health["sectors"]) == (1012, 20)


def test_a_hold_shows_no_path_only_a_diagnostic_one(client) -> None:
    app.dependency_overrides[get_full_decider] = lambda: Oracle(underspecified=0.9)
    body = client.post("/classify", json={"description": "We help businesses.", "mode": "full"}).json()
    assert body["path"] == [] and body["model_path"] and body["code"] is None


# ---- full-text search and search-then-pick (JEV-50) ----------------------------------------------

from jev_naics.api.app import get_search_decider, get_searcher, get_spelling  # noqa: E402
from jev_naics.db import Hit  # noqa: E402
from test_search_pick import Jev, searcher  # noqa: E402


def _no_database():
    def search(text, limit):
        raise RuntimeError("NAICS_DATABASE_URL is missing")

    return search


def _notes(corrections=()):
    def notes(text):
        return list(corrections)

    return notes


def test_search_returns_ranked_industries_and_what_it_did_with_the_words(client) -> None:
    app.dependency_overrides[get_searcher] = lambda: searcher(["541940", "812910"])
    app.dependency_overrides[get_spelling] = lambda: _notes([("vetrinary", "veterinary")])
    body = client.get("/naics/search", params={"q": "vetrinary clinic for a SaaS vet", "limit": 5}).json()
    assert [h["code"] for h in body["hits"]] == ["541940", "812910"]
    assert set(body["hits"][0]) == {"code", "title", "rank", "snippet"}
    # JEV-52: shown so a person sees how the text was read.
    assert body["corrections"] == [{"typed": "vetrinary", "corrected": "veterinary"}]
    assert body["expanded"] == [{"term": "saas", "census": "software publishers software subscriptions"}]


def test_search_without_a_database_is_a_documented_503(client) -> None:
    app.dependency_overrides[get_searcher] = _no_database
    app.dependency_overrides[get_spelling] = lambda: _notes()
    response = client.get("/naics/search", params={"q": "veterinary"})
    assert response.status_code == 503 and "503" in app.openapi()["paths"]["/naics/search"]["get"]["responses"]


@pytest.mark.parametrize("params", [{"q": " "}, {"q": "vet", "limit": 0}, {"q": "vet", "limit": 51}])
def test_bad_search_requests_are_422s(client, params) -> None:
    app.dependency_overrides[get_searcher] = lambda: searcher()
    assert client.get("/naics/search", params=params).status_code == 422


def test_search_mode_classifies_among_the_search_results_in_one_call(client) -> None:
    app.dependency_overrides[get_searcher] = lambda: searcher(["541940", "812910", "621999"])
    app.dependency_overrides[get_spelling] = lambda: _notes([("clinc", "clinic")])
    app.dependency_overrides[get_search_decider] = lambda: Jev()
    body = client.post("/classify", json={"description": "Small-animal veterinary clinic.", "mode": "search"}).json()
    assert (body["mode"], body["gate"], body["code"], body["calls"]) == ("search", "auto_act", "541940", 1)
    assert [f["search_rank"] for f in body["finalists"]] == [1, 2, 3] and body["trace"] == []
    assert [s["code"] for s in body["path"]][-1] == "541940"
    assert body["search_notes"]["corrections"] == [{"typed": "clinc", "corrected": "clinic"}]


def test_search_mode_without_a_database_holds_it_does_not_guess(client) -> None:
    app.dependency_overrides[get_searcher] = _no_database
    app.dependency_overrides[get_search_decider] = lambda: Jev()
    body = client.post("/classify", json={"description": "Vet clinic.", "mode": "search"}).json()
    assert (body["gate"], body["hold_reason"], body["code"]) == ("hold", "service_error", None)



# ---- hybrid (JEV-53) ---------------------------------------------------------------------------------


def test_hybrid_mode_acts_on_search_in_one_call_when_search_is_sure(client) -> None:
    from jev_naics.api.app import get_hybrid_decider

    app.dependency_overrides[get_searcher] = lambda: searcher(["541940", "812910", "621999"])
    app.dependency_overrides[get_spelling] = lambda: _notes([])
    app.dependency_overrides[get_hybrid_decider] = lambda: Jev()
    body = client.post("/classify", json={"description": "Small-animal veterinary clinic.", "mode": "hybrid"}).json()
    assert (body["mode"], body["gate"], body["code"], body["calls"]) == ("hybrid", "auto_act", "541940", 1)
    assert body["trace"] == [] and body["search_notes"] is not None


def test_hybrid_mode_without_a_database_holds_it_does_not_walk_the_tree(client) -> None:
    from jev_naics.api.app import get_hybrid_decider

    app.dependency_overrides[get_searcher] = _no_database
    app.dependency_overrides[get_hybrid_decider] = lambda: Jev()
    body = client.post("/classify", json={"description": "Vet clinic.", "mode": "hybrid"}).json()
    assert (body["gate"], body["hold_reason"], body["calls"]) == ("hold", "service_error", 0)


def test_closed_mode_is_gone(client) -> None:
    body = client.post("/classify", json={"description": "Dental office.", "mode": "closed"})
    assert body.status_code == 422
