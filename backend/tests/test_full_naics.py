"""Full-NAICS beam search (JEV-48): Choices over each node's children, top paths kept, code gates."""

import math

import pytest
from jev_naics._jev import ChoiceView, Judgment, NoulView

from jev_naics.full import FINAL, UNDERSPECIFIED, classify_full, fit_question_id, node_question_id
from jev_naics.tree import load_tree

TREE = load_tree()
DENTIST = TREE.path("621210")  # 62 -> 621 -> 6212 -> 62121 -> 621210


class Oracle:
    """Answers every node Choice toward `walk` (p on its child), the final pick toward `target`,
    and the Nouls. By default the walk and the final pick agree."""

    def __init__(self, target="621210", p=0.9, underspecified=0.1, fit=0.9, broken=None, walk=None, final_p=0.9):
        self.target, self.walk = target, TREE.path(walk or target)
        self.p, self.under, self.fit, self.broken, self.final_p = p, underspecified, fit, broken, final_p
        self.calls = []

    def _dist(self, options, right, p, second=None):
        if second in options and second != right and len(options) > 2:
            rest = (1 - p - 0.35) / (len(options) - 2)
            return {c: (p if c == right else 0.35 if c == second else rest) for c in options}
        rest = (1 - p) / (len(options) - 1) if len(options) > 1 else 0.0
        return {c: (p if c == right else rest) for c in options}

    def judge(self, state, questions):
        self.calls.append((state, sorted(questions)))
        choices, nouls = {}, {}
        for qid, question in questions.items():
            if qid == UNDERSPECIFIED:
                nouls[qid] = NoulView(self.under)
            elif qid.startswith("fit_"):
                nouls[qid] = NoulView(self.fit if qid == fit_question_id(self.target) else 0.1)
            elif qid == FINAL:
                options = list(question.criteria)
                right = self.target if self.target in options else options[0]
                choices[qid] = ChoiceView(right, self._dist(options, right, self.final_p), None)
            else:
                options = list(question.criteria)
                right = next((c for c in options if c in self.walk), options[0])
                second = next((c for c in options if c in TREE.path(self.target)), None)
                dist = self._dist(options, right, self.p, second)
                if self.broken == qid:
                    dist[right] = math.nan
                choices[qid] = ChoiceView(right, dist, None)
        return Judgment(model="m", choices=choices, nouls=nouls)


def test_a_clear_description_walks_the_tree_to_the_right_industry() -> None:
    result = classify_full("Family dental practice with two dentists.", decider=Oracle())
    assert (result.gate, result.code, result.title) == ("auto_act", "621210", "Offices of Dentists")
    assert result.path == DENTIST
    assert result.probability == pytest.approx(0.9) and 0 < result.path_score <= 0.9


def test_every_level_offers_only_that_nodes_children_and_jev_sees_only_the_description() -> None:
    oracle = Oracle()
    result = classify_full("Family dental practice.", decider=oracle)
    for level in result.trace:
        for asked in level["asked"]:
            assert set(asked["options"]) == set(TREE.children(asked["node"]))
    assert all(set(state) <= {"ops_description", "finalists"} for state, _q in oracle.calls)
    assert oracle.calls[0][0] == {"ops_description": "Family dental practice."}


def test_the_beam_keeps_up_to_three_paths_and_reports_every_finalist() -> None:
    result = classify_full("Family dental practice.", decider=Oracle(p=0.6))
    assert 1 <= len(result.alternatives) <= 3 and result.code in [a["code"] for a in result.alternatives]
    assert all(len(level["beam"]) <= 3 for level in result.trace)


@pytest.mark.parametrize(
    ("oracle", "reason"),
    [
        (Oracle(underspecified=0.9), "too_vague"),
        (Oracle(final_p=0.5), "low_probability"),
        (Oracle(fit=0.2), "misfit"),
    ],
)
def test_code_holds_when_unsure_vague_or_the_industry_does_not_fit(oracle, reason) -> None:
    result = classify_full("Something.", decider=oracle)
    assert (result.gate, result.code, result.hold_reason) == ("hold", None, reason)


def test_a_malformed_answer_drops_that_path_and_never_acts_on_it() -> None:
    result = classify_full("Family dental practice.", decider=Oracle(broken=node_question_id("root")))
    assert (result.gate, result.hold_reason) == ("hold", "invalid_answer")


def test_a_failed_call_holds() -> None:
    class Down:
        def judge(self, state, questions):
            raise ValueError("unreadable")

    result = classify_full("Family dental practice.", decider=Down())
    assert (result.gate, result.hold_reason) == ("hold", "service_error")


def test_the_final_pick_can_choose_a_runner_up_path_the_walk_ranked_lower() -> None:
    # The walk prefers the physicians branch; the right answer (dentists) is only a runner-up.
    oracle = Oracle(target="621210", walk="621111", p=0.6)
    result = classify_full("Family dental practice.", decider=oracle)
    assert (result.gate, result.code) == ("auto_act", "621210")
    assert "621210" in [f["code"] for f in result.finalists]


def test_a_finalist_that_fails_its_fit_check_is_not_acted_on() -> None:
    result = classify_full("Family dental practice.", decider=Oracle(fit=0.2))
    assert (result.gate, result.hold_reason) == ("hold", "misfit")


class SplitRoot:
    """Root: 62 at 0.4, 81 at 0.6. Every node under 81 answers NaN; 62 walks to dentists at 1.0.
    The gate audit's repro: the favoured branch is malformed (JEV-48, 2026-09-29)."""

    def judge(self, state, questions):
        choices, nouls = {}, {}
        for qid, question in questions.items():
            if qid == UNDERSPECIFIED:
                nouls[qid] = NoulView(0.1)
            elif qid.startswith("fit_"):
                nouls[qid] = NoulView(0.9)
            else:
                options = list(question.criteria)
                if qid == node_question_id("root"):
                    dist = {c: 0.0 for c in options} | {"62": 0.4, "81": 0.6}
                    choices[qid] = ChoiceView("81", dist, None)
                elif all(c.startswith("8") for c in options):
                    choices[qid] = ChoiceView(options[0], {c: math.nan for c in options}, None)
                else:
                    right = next((c for c in options if c in DENTIST), options[0])
                    choices[qid] = ChoiceView(right, {c: (1.0 if c == right else 0.0) for c in options}, None)
        return Judgment(model="m", choices=choices, nouls=nouls)


def test_a_malformed_answer_on_any_branch_holds_instead_of_acting_on_what_is_left() -> None:
    result = classify_full("Family dental practice.", decider=SplitRoot())
    assert (result.gate, result.code, result.hold_reason) == ("hold", None, "invalid_answer")


def test_a_hold_carries_no_usable_path_only_a_diagnostic_one() -> None:
    result = classify_full("Something.", decider=Oracle(underspecified=0.9))
    assert result.path == [] and result.code is None
    assert result.model_path and result.model_path[-1] == "621210"


def test_paths_rank_by_geometric_mean_but_a_lone_finalist_is_gated_on_its_joint_probability() -> None:
    from jev_naics.full import Path

    weak_start = Path(("a", "b", "c", "d", "e"), (0.35, 1.0, 1.0, 1.0, 1.0))
    assert weak_start.score == pytest.approx(0.35 ** 0.2)  # ranking: the cookbook's geometric mean
    assert weak_start.joint == pytest.approx(0.35)  # deciding alone: no dilution
