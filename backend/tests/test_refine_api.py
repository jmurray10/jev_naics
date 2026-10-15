"""POST /v1/refine/assist: the lexicon and the short-text gate as configured for the product (E15)."""

import pytest
from fastapi.testclient import TestClient

from jev_naics.api.app import app, get_refine_decider
from test_refine import Oracle


def _client(oracle):
    app.dependency_overrides[get_refine_decider] = lambda: oracle
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def test_a_single_recognised_term_is_checked_and_the_reading_is_reported() -> None:
    o = Oracle(target="513210")
    r = _client(o).post("/v1/refine/assist", json={"description": "SaaS"})
    assert r.status_code == 200 and o.calls, "the term must reach Jev"
    j = r.json()
    assert j["status"] != "TOO_SHORT" and j["variant"] == "cascade"
    assert [x["term"] for x in j["reading"]] == ["saas"] and "software" in j["reading"][0]["census"]


def test_the_wording_is_added_for_jev_only_to_a_short_text() -> None:
    seen = []

    class Spy(Oracle):
        def judge(self, state, questions):
            seen.append(state["ops_description"])
            return super().judge(state, questions)

    c = _client(Spy(target="513210"))
    c.post("/v1/refine/assist", json={"description": "SaaS"})
    short, seen[:] = list(seen), []
    c.post("/v1/refine/assist", json={"description": "We run a SaaS product for dental offices and we sell yearly plans"})
    assert short and all(x.startswith("SaaS [Census wording: ") for x in short)
    assert seen and all("[Census wording" not in x for x in seen)


def test_an_unrecognised_one_word_text_goes_to_the_completeness_checks_not_a_word_count() -> None:
    o = Oracle(ops=0.1)
    j = _client(o).post("/v1/refine/assist", json={"description": "Consulting"}).json()
    assert j["status"] == "NEEDS_INFO" and j["naics"] is None and j["calls"] == 1 and j["tooltip_id"]


def test_without_the_lexicon_the_three_word_rule_applies_as_before() -> None:
    o = Oracle()
    j = _client(o).post("/v1/refine/assist", json={"description": "SaaS", "lexicon": False}).json()
    assert (j["status"], j["calls"], j["tooltip_id"], j["reading"]) == ("TOO_SHORT", 0, "too_short", []) and not o.calls
