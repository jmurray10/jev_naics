"""Refine (E9): the wide pick over every industry plus a verify step. A fake decider answers; code decides."""

import math

import pytest
from jev_naics._jev import ChoiceView, Judgment, NoulView, ScoreView
from jev_naics.full import UNDERSPECIFIED
from typesafe_sdk import Choice, Noul, Score

from jev_naics import refine as R
from jev_naics.questions import REFINE_CHOICE_MAX
from jev_naics.tree import load_tree

TREE = load_tree()
TEXT = "We repair and service plumbing, heating and air-conditioning systems for homes."


def _dist(options, right, p):
    rest = (1 - p) / (len(options) - 1)
    return {o: (p if o == right else rest) for o in options}


class Oracle:
    """Picks `target` wherever it is offered; primary-level for `primary`, different for the rest."""

    def __init__(self, target="238220", primary=None, reversed_pick=None, ops=0.95, under=0.1, broken=()):
        self.target, self.primary = target, set(primary if primary is not None else [target])
        self.reversed_pick, self.ops, self.under, self.broken = reversed_pick, ops, under, set(broken)
        self.calls = []

    def judge(self, state, questions):
        self.calls.append(sorted(questions))
        choices, nouls, scores = {}, {}, {}
        for qid, q in questions.items():
            if isinstance(q, Choice):
                options = list(q.criteria)
                if qid == R.GROUP:
                    right = R.group_of(self.target)
                elif qid == R.PICK_REVERSED and self.reversed_pick in options:
                    right = self.reversed_pick
                elif self.target in options:
                    right = self.target
                else:
                    right = options[0]
                d = _dist(options, right, 0.9)
                if qid in self.broken:
                    d[right] = math.nan
                choices[qid] = ChoiceView(right, d, None)
            elif isinstance(q, Noul):
                v = self.under if qid == UNDERSPECIFIED else (self.ops if qid == "assist_operations_text" else 0.9)
                nouls[qid] = NoulView(v)
            elif isinstance(q, Score):
                code = qid.removeprefix("refine_fit_")
                top = code in self.primary
                scores[qid] = ScoreView(1.9 if top else 0.1, {0: 0.05 if top else 0.9, 1: 0.05, 2: 0.9 if top else 0.05}, None)
        return Judgment(model="m", choices=choices, nouls=nouls, scores=scores, usage={"input_tokens": 100})


def test_every_industry_is_in_exactly_one_wide_block_within_the_option_limit() -> None:
    groups, blocks = R._layout()
    flat = [c for b in blocks for c in b]
    assert sorted(flat) == sorted(TREE.leaves()) and len(flat) == len(set(flat)) == 1012
    assert max(len(b) for b in blocks) <= REFINE_CHOICE_MAX < 255


def test_a_clear_description_is_coded_by_the_score_variant_in_two_calls() -> None:
    o = Oracle()
    r = R.refine(TEXT, decider=o, variant="scoreb")
    assert (r.result.status, r.result.naics, r.result.calls) == ("CODED", "238220", 2) and r.agree is True
    assert r.usage["input_tokens"] == 200


def test_fewer_than_three_words_is_its_own_status_and_makes_no_call() -> None:
    o = Oracle()
    r = R.refine("licensing fees", decider=o, variant="scoreb")
    assert (r.result.status, r.result.naics, r.result.calls, r.result.tooltip_id) == ("TOO_SHORT", None, 0, "too_short")
    assert o.calls == []


@pytest.mark.parametrize("kw", [dict(ops=0.1), dict(under=0.9)])
def test_text_that_fails_the_completeness_check_never_gets_a_code(kw) -> None:
    o = Oracle(**kw)
    r = R.refine(TEXT, decider=o, variant="scoreb")
    assert r.result.status == "NEEDS_INFO" and r.result.naics is None and len(o.calls) == 1


def test_two_primary_candidates_become_a_pick_list_not_a_code() -> None:
    offered = [w["code"] for w in R.refine(TEXT, decider=Oracle(), variant="scoreb").wide]
    other = next(c for c in offered if c != "238220")
    r = R.refine(TEXT, decider=Oracle(primary=["238220", other]), variant="scoreb")
    assert r.result.naics is None and r.result.status == "AMBIGUOUS"
    assert {"238220", other} <= {o["code"] for o in r.result.picklist["options"]}


def test_the_two_option_orders_disagreeing_is_not_a_code() -> None:
    codes = [c for c, _ in R._wide(Oracle().judge({}, R.wide_questions(TREE, R.load_search_data())))[0][:4]]
    other = next(c for c in codes if c != "238220")
    r = R.refine(TEXT, decider=Oracle(reversed_pick=other), variant="score")
    assert r.result.naics is None and r.agree is False


def test_a_malformed_verify_pick_fails_closed() -> None:
    r = R.refine(TEXT, decider=Oracle(broken=[R.PICK]), variant="scoreb")
    assert (r.result.status, r.result.naics) == ("ERROR", None)


def test_a_malformed_block_of_unrelated_sectors_is_skipped_but_a_relevant_one_holds() -> None:
    _, blocks = R._layout()
    target_block = next(n for n, b in enumerate(blocks) if "238220" in b)
    other_block = next(n for n, b in enumerate(blocks) if n != target_block and "238220" not in b)
    ok = R.refine(TEXT, decider=Oracle(broken=[R.wide_id(other_block)]), variant="scoreb")
    assert ok.result.status == "CODED"
    bad = R.refine(TEXT, decider=Oracle(broken=[R.wide_id(target_block)]), variant="scoreb")
    assert (bad.result.status, bad.result.naics) == ("ERROR", None)


def test_the_wide_variant_uses_one_call_and_unknown_variants_are_rejected() -> None:
    o = Oracle()
    assert R.refine(TEXT, decider=o, variant="wide").result.calls == 1
    with pytest.raises(ValueError):
        R.refine(TEXT, decider=o, variant="nope")


def test_agree_shares_the_wide_call_and_codes_when_both_methods_agree() -> None:
    r = R.refine(TEXT, decider=Oracle(), variant="agree")
    assert (r.result.status, r.result.naics, r.result.calls) == ("CODED", "238220", 3)


def test_agree_recovers_a_code_verify_coded_that_the_score_step_also_called_primary() -> None:
    offered = [w["code"] for w in R.refine(TEXT, decider=Oracle(), variant="scoreb").wide]
    other = next(c for c in offered if c != "238220")
    two = Oracle(primary=["238220", other])
    assert R.refine(TEXT, decider=two, variant="scoreb").result.status == "AMBIGUOUS"
    r = R.refine(TEXT, decider=Oracle(primary=["238220", other]), variant="agree")
    assert (r.result.status, r.result.naics) == ("CODED", "238220")


def test_agree_never_codes_text_that_fails_the_completeness_check() -> None:
    r = R.refine(TEXT, decider=Oracle(ops=0.1), variant="agree")
    assert (r.result.status, r.result.naics, r.result.calls) == ("NEEDS_INFO", None, 1)


def test_cascade_stops_at_verify_when_it_codes() -> None:
    r = R.refine(TEXT, decider=Oracle(), variant="cascade")
    assert (r.result.status, r.result.naics, r.result.calls) == ("CODED", "238220", 2)


def test_cascade_runs_the_score_step_only_when_verify_does_not_code() -> None:
    offered = [w["code"] for w in R.refine(TEXT, decider=Oracle(), variant="scoreb").wide]
    other = next(c for c in offered if c != "238220")
    # verify's Noul says the pick does not fit (the Oracle answers Nouls 0.9, so break the fit via a low-prob pick)
    class Unsure(Oracle):
        def judge(self, state, questions):
            j = super().judge(state, questions)
            if R.PICK in questions and R.PICK_REVERSED not in questions:  # the verify call
                from dataclasses import replace
                nouls = {k: (type(v)(0.1) if k.startswith("fit_") else v) for k, v in j.nouls.items()}
                return replace(j, nouls=nouls)
            return j
    r = R.refine(TEXT, decider=Unsure(primary=["238220", other]), variant="cascade")
    assert r.result.status == "AMBIGUOUS" and r.result.naics is None and r.result.calls == 3


def test_cascade_never_codes_incomplete_or_short_text_and_spends_at_most_one_call() -> None:
    assert R.refine(TEXT, decider=Oracle(ops=0.1), variant="cascade").result.calls == 1
    assert R.refine("fees", decider=Oracle(), variant="cascade").result.status == "TOO_SHORT"


def test_the_boundary_text_is_the_census_words_and_within_budget() -> None:
    from jev_naics.questions import REFINE_BOUNDARY_CHARS, REFINE_BOUNDARY_ITEMS
    data = R.load_search_data()
    text = R._boundary(data.industries["513210"])
    assert text.startswith("Not this industry: ") and "(NAICS 541511)" in text and "Belongs here: " in text
    assert text.count("(NAICS") <= REFINE_BOUNDARY_ITEMS
    assert len(text) <= 2 * REFINE_BOUNDARY_ITEMS * (REFINE_BOUNDARY_CHARS + 20) + 60


def test_cascadeb_shows_the_boundary_text_and_cascade_does_not() -> None:
    plain, rich = Oracle(), Oracle()
    R.refine(TEXT, decider=plain, variant="cascade")
    R.refine(TEXT, decider=rich, variant="cascadeb")
    seen = []

    class Spy(Oracle):
        def judge(self, state, questions):
            if R.PICK in questions:
                seen.append(next(iter(questions[R.PICK].criteria.values())))
            return super().judge(state, questions)

    R.refine(TEXT, decider=Spy(), variant="cascade")
    R.refine(TEXT, decider=Spy(), variant="cascadeb")
    assert "Not this industry" not in seen[0] and "Not this industry" in seen[1] or "Belongs here" in seen[1]


@pytest.mark.parametrize("probs", [{0: 0.0, 1: 0.0, 2: 0.5}, {0: 0.9, 1: 0.9, 2: 0.9}, {0: float("nan"), 1: 0.0, 2: 1.0}])
def test_a_score_answer_that_is_not_a_distribution_is_a_malformed_answer_not_a_pick_list(probs) -> None:
    from dataclasses import replace

    class Bad(Oracle):
        def judge(self, state, questions):
            j = super().judge(state, questions)
            return replace(j, scores={k: ScoreView(1.9, dict(probs), None) for k in j.scores})

    r = R.refine(TEXT, decider=Bad(), variant="scoreb")
    assert (r.result.status, r.result.naics, r.result.picklist) == ("ERROR", None, None)
