"""Search first, the tree walk as a fallback (JEV-53): when search holds because the right industry may
not be among its results, the tree walk's finalists join them and Jev picks once over both."""

import math

import pytest
from jev_naics._jev import ChoiceView, Judgment, NoulView, ReplayMissing

from jev_naics import full, search_pick
from jev_naics.hybrid import classify_hybrid
from test_full_naics import TREE, Oracle
from test_search_pick import VET, searcher

INSURANCE = "524210"  # Insurance Agencies and Brokerages: never among VET's search results


class Both:
    """Answers the tree walk like Oracle (toward `target`), and search's pick toward `target` when it is
    among the candidates (else the first candidate, which then fails its fit check)."""

    def __init__(self, target=INSURANCE, underspecified=0.1, broken_walk=False):
        self.tree = Oracle(target=target, underspecified=underspecified)
        self.target, self.under, self.broken_walk = target, underspecified, broken_walk
        self.calls = []

    def judge(self, state, questions):
        self.calls.append((state, sorted(questions)))
        if search_pick.PICK not in questions:
            judgment = self.tree.judge(state, questions)
            if self.broken_walk and full.FINAL not in questions:
                node = next(q for q in questions if q in judgment.choices)
                view = judgment.choices[node]
                judgment.choices[node] = ChoiceView(view.choice, {**view.probabilities, view.choice: math.nan}, None)
            return judgment
        options = list(questions[search_pick.PICK].criteria)
        pick = self.target if self.target in options else options[0]
        dist = {o: (0.9 if o == pick else 0.1 / (len(options) - 1)) for o in options}
        nouls = {q: NoulView(0.9 if q == search_pick.fit_question_id(self.target) else 0.1)
                 for q in questions if q.startswith("fit_")}
        nouls[search_pick.UNDERSPECIFIED] = NoulView(self.under)
        return Judgment(model="m", choices={search_pick.PICK: ChoiceView(pick, dist, None)}, nouls=nouls)


def _asked_tree(jev):
    return any(q.startswith("node_") or q == full.FINAL for _, qs in jev.calls for q in qs)


def test_when_search_acts_the_tree_walk_is_never_run() -> None:
    jev = Both(target="541940")
    result = classify_hybrid("Veterinary clinic for dogs and cats.", decider=jev, searcher=searcher())
    assert (result.gate, result.code, result.calls) == ("auto_act", "541940", 1)
    assert not _asked_tree(jev) and "search" in result.reason.lower()


def test_a_misfit_search_result_falls_back_to_the_tree_and_one_pick_over_both() -> None:
    jev = Both()
    result = classify_hybrid("An insurtech that sells renters insurance through an app.", decider=jev,
                             searcher=searcher())
    assert (result.gate, result.code) == ("auto_act", INSURANCE)
    assert result.path == TREE.path(INSURANCE)
    final = jev.calls[-1]
    assert search_pick.PICK in final[1] and set(VET) <= set(final[0]["candidates"]) and INSURANCE in final[0]["candidates"]
    assert result.calls == len(jev.calls) and "tree" in result.reason.lower()


def test_search_finding_nothing_falls_back_to_the_tree() -> None:
    result = classify_hybrid("An insurtech.", decider=Both(), searcher=searcher([]))
    assert (result.gate, result.code) == ("auto_act", INSURANCE)


@pytest.mark.parametrize("case", ["vague", "service"])
def test_a_vague_description_or_a_failed_search_is_never_escalated(case) -> None:
    jev = Both(underspecified=0.9)

    def broken(text, limit):
        raise OSError("down")

    result = classify_hybrid("Stuff.", decider=jev if case == "vague" else Both(),
                             searcher=searcher() if case == "vague" else broken)
    assert result.gate == "hold" and result.hold_reason == ("too_vague" if case == "vague" else "service_error")
    assert not _asked_tree(jev)


def test_when_the_tree_adds_nothing_new_search_hold_stands_without_a_second_pick(monkeypatch) -> None:
    from jev_naics import hybrid
    from jev_naics.full import Path

    def only_search_codes(description, *, decider, tree, into):
        into.calls, into.underspecified = 5, 0.1
        into.beam = [Path(tuple(TREE.path(c))) for c in VET]  # finalists search already offered
        return into

    monkeypatch.setattr(hybrid, "walk", only_search_codes)
    jev = Both(target=INSURANCE)
    result = classify_hybrid("Something search half-matches.", decider=jev, searcher=searcher())
    assert result.gate == "hold" and result.hold_reason == "misfit" and result.code is None
    assert sum(search_pick.PICK in qs for _, qs in jev.calls) == 1 and result.calls == 1 + 5
    assert "no industry search had not already offered" in result.reason


def test_a_malformed_walk_holds_and_is_never_acted_on() -> None:
    result = classify_hybrid("An insurtech.", decider=Both(broken_walk=True), searcher=searcher())
    assert result.gate == "hold" and result.hold_reason == "invalid_answer" and result.code is None


def test_a_missing_recording_raises_when_strict_and_holds_otherwise() -> None:
    class Missing(Both):
        def judge(self, state, questions):
            if search_pick.PICK not in questions:
                raise ReplayMissing("no recording")
            return super().judge(state, questions)

    with pytest.raises(ReplayMissing):
        classify_hybrid("An insurtech.", decider=Missing(), searcher=searcher(), strict=True)
    result = classify_hybrid("An insurtech.", decider=Missing(), searcher=searcher())
    assert result.gate == "hold" and result.hold_reason == "service_error"



# ---- gate audit (JEV-53) -----------------------------------------------------------------------------


def _walk_to(codes, underspecified=0.1, calls=5):
    from jev_naics.full import Path

    def fake(description, *, decider, tree, into):
        into.calls, into.underspecified = calls, underspecified
        into.beam = [Path(tuple(TREE.path(c))) for c in codes]
        return into

    return fake


class Flip(Both):
    """Search's first pick leans to the vet clinic but fails its fit check; asked again over the union,
    the same lean passes."""

    def judge(self, state, questions):
        picks = sum(search_pick.PICK in qs for _, qs in self.calls)
        judgment = super().judge(state, questions)
        if search_pick.PICK in questions:
            options = list(questions[search_pick.PICK].criteria)
            dist = {o: (0.9 if o == "541940" else 0.1 / (len(options) - 1)) for o in options}
            judgment.choices[search_pick.PICK] = ChoiceView("541940", dist, None)
            judgment.nouls[search_pick.fit_question_id("541940")] = NoulView(0.3 if picks == 0 else 0.9)
        return judgment


def test_asking_again_never_overturns_search_hold_on_a_code_search_already_offered(monkeypatch) -> None:
    from jev_naics import hybrid

    monkeypatch.setattr(hybrid, "walk", _walk_to([INSURANCE, "524298"]))
    result = classify_hybrid("A pet clinic.", decider=Flip(), searcher=searcher())
    assert result.gate == "hold" and result.code is None and result.hold_reason == "misfit"
    assert "not one the tree walk added" in result.reason


@pytest.mark.parametrize("vague,reason", [(0.95, "too_vague"), (None, "invalid_answer"), (math.nan, "invalid_answer")])
def test_the_tree_walks_own_vagueness_check_still_holds(monkeypatch, vague, reason) -> None:
    from jev_naics import hybrid

    monkeypatch.setattr(hybrid, "walk", _walk_to([INSURANCE, "524298"], underspecified=vague))
    result = classify_hybrid("An insurtech.", decider=Both(), searcher=searcher())
    assert result.gate == "hold" and result.hold_reason == reason and result.code is None


def test_a_tree_finalist_carries_no_invented_search_rank_or_score() -> None:
    result = classify_hybrid("An insurtech that sells renters insurance through an app.", decider=Both(),
                             searcher=searcher())
    rows = {r["code"]: r for r in result.finalists}
    assert rows[INSURANCE]["source"] == "tree" and rows[INSURANCE]["search_rank"] is None
    assert rows[INSURANCE]["search_score"] is None and rows["541940"]["source"] == "search"
    assert "tree finalist" in result.reason and "search rank" not in result.reason.split("Picked over both")[-1]
    assert result.fallback


def test_a_fallback_that_fails_is_still_reported_as_a_fallback() -> None:
    class Down(Both):
        def judge(self, state, questions):
            if search_pick.PICK not in questions:
                from typesafe_sdk import TypeSafeError
                raise TypeSafeError("down")
            return super().judge(state, questions)

    result = classify_hybrid("An insurtech.", decider=Down(), searcher=searcher())
    assert result.hold_reason == "service_error" and result.fallback


def test_when_search_acts_no_fallback_is_reported() -> None:
    result = classify_hybrid("Vet clinic.", decider=Both(target="541940"), searcher=searcher())
    assert result.gate == "auto_act" and not result.fallback
