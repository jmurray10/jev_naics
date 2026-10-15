"""Consensus: the hybrid and the tree walk both run; code accepts when they agree, and otherwise Jev
judges once over every industry either one considered. Hold stays code-owned."""

import pytest

from jev_naics.consensus import classify_consensus, decide
from jev_naics.full import FullResult
from jev_naics.search_pick import PICK
from test_hybrid import Both
from test_search_pick import VET, Jev, searcher


def _r(gate="hold", code=None, hold_reason="low_probability", finalists=(), alternatives=(), p=None, calls=1):
    return FullResult(gate=gate, code=code, title=None, probability=p, path_score=None, path=[],
                      hold_reason=None if gate == "auto_act" else hold_reason, reason="r",
                      finalists=[{"code": c, "p": 0.5} for c in finalists],
                      alternatives=[{"code": c} for c in alternatives], calls=calls)


ACT_VET = _r("auto_act", "541940", finalists=VET, p=0.95)


def test_when_both_methods_pick_the_same_code_it_is_accepted_without_a_judge_call() -> None:
    jev = Jev()
    out = decide("Vet clinic.", ACT_VET, _r("auto_act", "541940", alternatives=["541940"], p=0.9, calls=5),
                 decider=jev)
    assert (out.step, out.result.gate, out.result.code) == ("agreement", "auto_act", "541940")
    assert jev.calls == [] and out.result.calls == 6


@pytest.mark.parametrize("reason", ["too_vague", "invalid_answer", "service_error"])
def test_a_vague_malformed_or_failed_method_holds_and_is_never_judged(reason) -> None:
    jev = Jev()
    out = decide("Consulting.", ACT_VET, _r(hold_reason=reason, alternatives=["541611", "541618"]), decider=jev)
    assert (out.step, out.result.gate, out.result.hold_reason) == ("hold", "hold", reason)
    assert out.result.code is None and jev.calls == []


def test_different_codes_go_to_one_judge_call_over_only_the_considered_industries() -> None:
    jev = Jev(pick="812910")
    tree = _r("auto_act", "812910", alternatives=["812910", "812990"], p=0.8)
    out = decide("Pet grooming and boarding.", ACT_VET, tree, decider=jev)
    assert (out.step, out.result.gate, out.result.code) == ("judge", "auto_act", "812910")
    state, questions = jev.calls[0]
    # The closed set: every industry either method considered, each once, and nothing else.
    assert list(questions[PICK].criteria) == VET + ["812990"] and set(state["candidates"]) == set(VET + ["812990"])
    assert "search rank" not in out.result.reason
    row = next(c for c in out.candidates if c["code"] == "812910")
    assert row["sources"] == ["hybrid", "tree"] and row["tree_code"] is True


def test_when_both_hold_unsure_the_judge_sees_their_candidates() -> None:
    jev = Jev(pick="541940", p=0.95)
    out = decide("Animal clinic.", _r(finalists=VET), _r(hold_reason="misfit", alternatives=["541940", "541990"]),
                 decider=jev)
    assert (out.step, out.result.code) == ("judge", "541940")
    assert list(jev.calls[0][1][PICK].criteria) == VET + ["541990"]


def test_the_judge_holds_on_its_own_gates() -> None:
    low = decide("Animal clinic.", _r(finalists=VET), _r(), decider=Jev(p=0.6))
    assert (low.step, low.result.gate, low.result.hold_reason) == ("judge", "hold", "low_probability")
    misfit = decide("Animal clinic.", _r(finalists=VET), _r(), decider=Jev(fit=0.2))
    assert misfit.result.hold_reason == "misfit"
    vague = decide("Animal clinic.", _r(finalists=VET), _r(), decider=Jev(underspecified=0.9))
    assert vague.result.hold_reason == "too_vague"


def test_fewer_than_two_real_candidates_holds_without_a_judge_call() -> None:
    jev = Jev()
    out = decide("Animal clinic.", _r(finalists=["541940", "999999"]), _r(), decider=jev)
    assert (out.step, out.result.hold_reason) == ("hold", "low_probability") and jev.calls == []


def test_end_to_end_the_hybrid_and_the_tree_run_and_agree() -> None:
    jev = Both(target="541940")
    out = classify_consensus("Veterinary clinic for dogs and cats.", decider=jev, searcher=searcher())
    assert (out.step, out.result.gate, out.result.code) == ("agreement", "auto_act", "541940")
    assert (out.hybrid.code, out.tree.code) == ("541940", "541940")
    assert out.result.calls == out.hybrid.calls + out.tree.calls


def test_end_to_end_a_vague_description_holds() -> None:
    out = classify_consensus("We do things.", decider=Both(target="541940", underspecified=0.95), searcher=searcher())
    assert (out.result.gate, out.result.hold_reason, out.step) == ("hold", "too_vague", "hold")


def test_the_consensus_endpoint_reports_the_step_and_each_method() -> None:
    from fastapi.testclient import TestClient

    from jev_naics.api.app import app, get_consensus_decider, get_searcher

    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    app.dependency_overrides[get_consensus_decider] = lambda: Both(target="541940")
    try:
        body = TestClient(app).post("/v1/consensus", json={"description": "Vet clinic for dogs and cats."}).json()
    finally:
        app.dependency_overrides.clear()
    assert (body["gate"], body["code"], body["step"]) == ("auto_act", "541940", "agreement")
    assert body["methods"]["hybrid"]["code"] == body["methods"]["tree"]["code"] == "541940"
    assert body["candidates"] == [] and body["calls"] >= 2


# ---- triage: the hybrid and the tree in parallel; accept agreement or a lone tree answer ----------

from jev_naics.consensus import classify_triage, triage  # noqa: E402

ACT_TREE = _r("auto_act", "812910", alternatives=["812910", "812990"], p=0.8, calls=5)


def test_triage_accepts_agreement_without_a_judge_call() -> None:
    jev = Jev()
    out = triage("Vet clinic.", ACT_VET, _r("auto_act", "541940", alternatives=["541940"], p=0.9), decider=jev)
    assert (out.step, out.result.code) == ("agreement", "541940") and jev.calls == []


@pytest.mark.parametrize("reason", ["low_probability", "misfit", "too_vague", "no_match"])
def test_triage_accepts_the_tree_when_only_the_tree_answers(reason) -> None:
    jev = Jev()
    out = triage("Pet grooming.", _r(hold_reason=reason, finalists=VET), ACT_TREE, decider=jev)
    assert (out.step, out.result.gate, out.result.code) == ("tree_only", "auto_act", "812910")
    assert jev.calls == [] and out.result.calls == 6


@pytest.mark.parametrize("reason", ["invalid_answer", "service_error"])
def test_triage_never_counts_a_failed_hybrid_as_no_answer(reason) -> None:
    out = triage("Pet grooming.", _r(hold_reason=reason), ACT_TREE, decider=Jev())
    assert (out.step, out.result.gate, out.result.hold_reason) == ("broker", "hold", reason)


def test_triage_judges_a_split_and_a_lone_hybrid_answer() -> None:
    split = triage("Pet grooming.", ACT_VET, ACT_TREE, decider=Jev(pick="812910"))
    assert (split.step, split.result.code) == ("judge", "812910")
    lone = triage("Vet clinic.", ACT_VET, _r(alternatives=["541940", "541990"]), decider=Jev(p=0.95))
    assert (lone.step, lone.result.code) == ("judge", "541940")


def test_triage_sends_a_judge_hold_and_a_vague_description_to_the_broker() -> None:
    held = triage("Vet clinic.", ACT_VET, _r(), decider=Jev(p=0.5))
    assert (held.step, held.result.gate, held.result.hold_reason) == ("broker", "hold", "low_probability")
    jev = Jev()
    vague = triage("Things.", _r(hold_reason="too_vague"), _r(hold_reason="too_vague"), decider=jev)
    assert (vague.step, vague.result.hold_reason) == ("broker", "too_vague") and jev.calls == []


def test_triage_end_to_end_runs_both_methods() -> None:
    out = classify_triage("Veterinary clinic for dogs and cats.", decider=Both(target="541940"), searcher=searcher())
    assert (out.step, out.result.code, out.hybrid.code, out.tree.code) == ("agreement", "541940", "541940", "541940")


def test_the_triage_endpoint_reports_the_step() -> None:
    from fastapi.testclient import TestClient

    from jev_naics.api.app import app, get_consensus_decider, get_searcher

    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    app.dependency_overrides[get_consensus_decider] = lambda: Both(target="541940")
    try:
        body = TestClient(app).post("/v1/triage", json={"description": "Vet clinic for dogs and cats."}).json()
    finally:
        app.dependency_overrides.clear()
    assert (body["gate"], body["code"], body["step"]) == ("auto_act", "541940", "agreement")


# ---- tree-first: the tree walk's code, unless the hybrid disagrees or finds the text too vague -----

from jev_naics.consensus import classify_tree_first, tree_first  # noqa: E402


def test_tree_first_accepts_agreement_and_a_lone_tree_answer_over_an_unsure_hybrid() -> None:
    agreed = tree_first(ACT_VET, _r("auto_act", "541940", alternatives=["541940"], p=0.9))
    assert (agreed.step, agreed.result.code) == ("agreement", "541940")
    lone = tree_first(_r(hold_reason="misfit", finalists=VET), ACT_TREE)
    assert (lone.step, lone.result.gate, lone.result.code, lone.result.calls) == ("tree_only", "auto_act", "812910", 6)


def test_tree_first_sends_a_disagreement_to_the_broker() -> None:
    out = tree_first(ACT_VET, ACT_TREE)
    assert (out.step, out.result.gate, out.result.hold_reason) == ("broker", "hold", "methods_disagree")
    assert out.result.code is None and "541940" in out.result.reason and "812910" in out.result.reason


@pytest.mark.parametrize("reason", ["too_vague", "invalid_answer", "service_error"])
def test_tree_first_never_overrules_a_vague_or_failed_hybrid(reason) -> None:
    out = tree_first(_r(hold_reason=reason), ACT_TREE)
    assert (out.step, out.result.gate, out.result.hold_reason) == ("broker", "hold", reason)


def test_tree_first_never_acts_on_a_lone_hybrid_answer() -> None:
    out = tree_first(ACT_VET, _r(hold_reason="low_probability"))
    assert (out.step, out.result.gate, out.result.hold_reason) == ("broker", "hold", "low_probability")


def test_tree_first_end_to_end_and_the_endpoint() -> None:
    from fastapi.testclient import TestClient

    from jev_naics.api.app import app, get_consensus_decider, get_searcher

    out = classify_tree_first("Veterinary clinic for dogs and cats.", decider=Both(target="541940"),
                              searcher=searcher())
    assert (out.step, out.result.code) == ("agreement", "541940")
    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    app.dependency_overrides[get_consensus_decider] = lambda: Both(target="541940")
    try:
        body = TestClient(app).post("/v1/tree-first", json={"description": "Vet clinic for dogs and cats."}).json()
    finally:
        app.dependency_overrides.clear()
    assert (body["gate"], body["code"], body["step"]) == ("auto_act", "541940", "agreement")
