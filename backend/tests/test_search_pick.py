"""Search, then Jev picks (JEV-50): Postgres full-text search finds the candidate industries, and
Jev picks one of only those in a single call, with a fit check on each; code decides."""

import math

import pytest
from jev_naics._jev import ChoiceView, Judgment, NoulView, ReplayMissing

from jev_naics.db import Hit
from jev_naics.questions import SEARCH_K
from jev_naics.search_pick import PICK, UNDERSPECIFIED, classify_search, fit_question_id

VET = ["541940", "812910", "621999"]


def searcher(codes=VET):
    calls = []

    def search(text, limit):
        calls.append((text, limit))
        return [Hit(code=c, title=f"title {c}", rank=1.0 / (i + 1), snippet="…") for i, c in enumerate(codes)][:limit]

    search.calls = calls
    return search


class Jev:
    """Picks `pick` at p, answers each fit Noul (fit for the pick, 0.1 otherwise) and vagueness."""

    def __init__(self, pick="541940", p=0.9, fit=0.9, underspecified=0.1, dist=None, omit=()):
        self.pick, self.p, self.fit, self.under, self.dist, self.omit = pick, p, fit, underspecified, dist, omit
        self.calls = []

    def judge(self, state, questions):
        self.calls.append((state, questions))
        choices = {}
        if PICK in questions:
            options = list(questions[PICK].criteria)
            rest = (1 - self.p) / (len(options) - 1)
            choices[PICK] = ChoiceView(self.pick, self.dist or {o: (self.p if o == self.pick else rest)
                                                                for o in options}, None)
        nouls = {q: NoulView(self.fit if q == fit_question_id(self.pick) else 0.1) for q in questions
                 if q.startswith("fit_")}
        nouls[UNDERSPECIFIED] = NoulView(self.under)
        for q in self.omit:
            choices.pop(q, None)
            nouls.pop(q, None)
        return Judgment(model="m", choices=choices, nouls=nouls)


def test_a_sure_fitting_pick_among_the_search_results_is_the_answer() -> None:
    jev, search = Jev(), searcher()
    result = classify_search("Small-animal veterinary clinic.", decider=jev, searcher=search)
    assert (result.gate, result.code, result.title) == ("auto_act", "541940", "Veterinary Services")
    assert result.path == ["54", "541", "5419", "54194", "541940"] and result.calls == 1
    assert search.calls == [("Small-animal veterinary clinic.", SEARCH_K)]
    # Jev saw only the search results, each with its official text, and nothing else.
    state, questions = jev.calls[0]
    assert list(questions[PICK].criteria) == VET and set(state["candidates"]) == set(VET)
    assert state["candidates"]["541940"]["examples"][:2] == ["Animal hospitals", "Veterinary clinics"]
    assert any("812910" in note for note in state["candidates"]["541940"]["not_this_industry"])
    assert [f["code"] for f in result.finalists] == VET and result.finalists[0]["search_rank"] == 1


@pytest.mark.parametrize(
    ("jev", "reason"),
    [
        (Jev(underspecified=0.9), "too_vague"),
        (Jev(p=0.5), "low_probability"),
        (Jev(fit=0.2), "misfit"),
        (Jev(p=math.nan), "invalid_answer"),
        (Jev(dist={"541940": 0.9, "812910": 0.3, "621999": 0.1}), "invalid_answer"),  # sums past 1
        (Jev(pick="999999"), "invalid_answer"),  # not a search result
        (Jev(omit=(PICK,)), "invalid_answer"),
        (Jev(omit=(UNDERSPECIFIED,)), "invalid_answer"),
        (Jev(omit=("fit_541940",)), "invalid_answer"),
    ],
)
def test_code_holds_on_every_doubt_and_never_acts_on_a_bad_answer(jev, reason) -> None:
    result = classify_search("Small-animal veterinary clinic.", decider=jev, searcher=searcher())
    assert (result.gate, result.code, result.hold_reason, result.path) == ("hold", None, reason, [])


def test_no_search_result_holds_without_calling_jev() -> None:
    jev = Jev()
    result = classify_search("xqzvw", decider=jev, searcher=searcher([]))
    assert (result.gate, result.hold_reason, result.calls) == ("hold", "no_match", 0) and jev.calls == []


def test_a_lone_search_result_holds_without_calling_jev() -> None:
    # One result leaves nothing to compare, and that path is unmeasured: a person decides.
    jev = Jev(fit=0.99)
    result = classify_search("Zoo", decider=jev, searcher=searcher(["712130"]))
    assert (result.gate, result.hold_reason, result.calls, result.code) == ("hold", "low_probability", 0, None)
    # Jev was never asked, so no lean is recorded: the search hit is not Jev's (gate audit).
    assert jev.calls == [] and result.model_path == []


def test_duplicate_or_unknown_hits_are_dropped_before_jev_sees_them() -> None:
    jev = Jev()
    result = classify_search("vet", decider=jev, searcher=searcher(["541940", "541940", "999999", "812910"]))
    assert list(jev.calls[0][1][PICK].criteria) == ["541940", "812910"]
    assert result.gate == "auto_act"
    lone = classify_search("vet", decider=Jev(), searcher=searcher(["541940", "541940", "999999"]))
    assert lone.hold_reason == "low_probability"  # one real candidate left


def test_a_nan_search_score_never_reaches_the_output() -> None:
    def odd(text, limit):
        return [Hit("541940", "t", float("nan"), ""), Hit("812910", "t", 0.5, "")]

    result = classify_search("vet", decider=Jev(), searcher=odd)
    assert result.finalists[0]["search_score"] is None


def test_a_failed_call_holds_unless_strict() -> None:
    class Down:
        def judge(self, state, questions):
            raise ReplayMissing("none")

    assert classify_search("vet", decider=Down(), searcher=searcher()).hold_reason == "service_error"
    with pytest.raises(ReplayMissing):
        classify_search("vet", decider=Down(), searcher=searcher(), strict=True)


def test_a_search_failure_holds_as_a_service_error() -> None:
    def broken(text, limit):
        raise OSError("database down")

    result = classify_search("vet", decider=Jev(), searcher=broken)
    assert (result.gate, result.hold_reason) == ("hold", "service_error")


def test_a_searcher_value_error_holds_as_a_service_error() -> None:
    def bad_text(text, limit):
        raise UnicodeEncodeError("utf-8", text, 0, 1, "surrogates not allowed")

    result = classify_search("bowling alley", decider=Jev(), searcher=bad_text)
    assert (result.gate, result.hold_reason) == ("hold", "service_error")
