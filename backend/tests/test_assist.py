"""Real-time assist: as a broker types, how specific a NAICS code the text supports and what is missing.
Code decides the status, the tooltip, and the pick-list; Jev answers yes/no checks and closed Choices only."""

import pytest
from jev_naics._jev import ChoiceView, Judgment, NoulView

from jev_naics import assist
from jev_naics.assist import Checks, assess, decide
from jev_naics.full import FINAL, FullResult
from jev_naics.questions import NOUL_ASSIST_ACTIVITY, NOUL_ASSIST_OPERATIONS
from test_full_naics import TREE, Oracle

GOOD = dict(operations=0.95, activity=0.9, customers=0.9, single=0.9, underspecified=0.1, delivery="own_software_product")


def _checks(**kw) -> Checks:
    return Checks(**{**GOOD, **kw})


def _walked(gate="auto_act", code="513210", finalists=(("513210", 0.9), ("541511", 0.06), ("518210", 0.04)),
            hold_reason=None, calls=5):
    return FullResult(gate=gate, code=code if gate == "auto_act" else None, title=None, probability=finalists[0][1],
                      path_score=0.8, path=[], hold_reason=None if gate == "auto_act" else hold_reason or "low_probability",
                      reason="r", finalists=[{"code": c, "title": TREE.title(c), "p": p, "path_score": 0.5, "fit": 0.8}
                                             for c, p in finalists], calls=calls)


SECTOR = {"51": 0.92}


def test_a_clear_coded_description_returns_the_code_and_no_tooltip() -> None:
    r = decide(_checks(), SECTOR, _walked())
    assert (r.status, r.naics, r.level, r.tooltip_id, r.picklist) == ("CODED", "513210", 6, None, None)
    assert [c["code"] for c in r.candidates] == ["513210", "541511", "518210"]


@pytest.mark.parametrize("field, tooltip", [("operations", "is_operations_text"), ("activity", "states_activity")])
def test_revenue_only_or_unspecific_text_needs_info_and_never_gets_a_code(field, tooltip) -> None:
    r = decide(_checks(**{field: 0.1}), SECTOR, None)
    assert (r.status, r.naics, r.tooltip_id) == ("NEEDS_INFO", None, tooltip)
    # The sector the text leans to is still shown, at its own measured bar.
    assert (r.naics_partial, r.level) == ("51", 2)


def test_needs_info_shows_no_sector_when_jev_is_unsure_of_it() -> None:
    r = decide(_checks(activity=0.1), {"51": 0.4}, None)
    assert (r.status, r.naics_partial, r.level) == ("NEEDS_INFO", None, None)


def test_the_hard_rule_holds_even_if_a_walk_result_is_passed_with_failed_checks() -> None:
    r = decide(_checks(operations=0.1), SECTOR, _walked())
    assert r.status == "NEEDS_INFO" and r.naics is None and r.candidates == []


def test_a_vague_or_mixed_description_needs_info() -> None:
    r = decide(_checks(underspecified=0.9, single=0.2), SECTOR, None)
    assert (r.status, r.naics, r.tooltip_id) == ("NEEDS_INFO", None, "single_primary_activity")


def test_tooltip_priority_delivery_then_customers() -> None:
    held = _walked("hold", finalists=(("541611", 0.5), ("541618", 0.3), ("541990", 0.2)))
    assert decide(_checks(delivery="not_stated", customers=0.1), SECTOR, held).tooltip_id == "delivery_mode"
    assert decide(_checks(customers=0.1), SECTOR, held).tooltip_id == "states_customers"


def test_a_coded_walk_over_several_activities_is_partial() -> None:
    r = decide(_checks(single=0.2), SECTOR, _walked())
    assert (r.status, r.naics, r.tooltip_id) == ("PARTIAL", None, "single_primary_activity")


def test_a_hold_between_a_confusable_pair_is_ambiguous_with_a_pick_list() -> None:
    held = _walked("hold", finalists=(("541511", 0.48), ("513210", 0.45), ("518210", 0.07)))
    r = decide(_checks(), SECTOR, held)
    assert (r.status, r.naics, r.picklist["id"]) == ("AMBIGUOUS", None, "541511_513210")
    assert {o["code"] for o in r.picklist["options"]} == {"541511", "513210"}
    # The meter still shows what the confused codes share: here only the sector Jev leaned to.
    assert (r.naics_partial, r.level) == ("51", 2)
    it = decide(_checks(), {"54": 0.95}, _walked("hold", finalists=(("541512", 0.5), ("541519", 0.4), ("541690", 0.1))))
    assert (it.status, it.naics_partial, it.level) == ("AMBIGUOUS", "5415", 4)


def test_the_make_or_resell_pick_list_offers_only_candidates_that_were_considered() -> None:
    held = _walked("hold", finalists=(("337110", 0.5), ("423310", 0.4), ("238350", 0.1)))
    r = decide(_checks(), {"31-33": 0.8}, held)
    assert r.status == "AMBIGUOUS" and r.picklist["id"] == "make_or_resell"
    assert [o["code"] for o in r.picklist["options"]] == ["337110", "423310"]


def test_an_unsure_hold_outside_any_pair_reports_the_deepest_shared_level() -> None:
    held = _walked("hold", finalists=(("541611", 0.4), ("541618", 0.35), ("541990", 0.25)))
    r = decide(_checks(), SECTOR, held)
    # 541611 + 541618 cover 0.75 >= the 0.70 bar; they share 5416.
    assert (r.status, r.naics, r.naics_partial, r.level) == ("PARTIAL", None, "5416", 4)


@pytest.mark.parametrize("missing", ["operations", "activity", "single", "underspecified"])
def test_a_missing_check_is_an_error_not_a_guess(missing) -> None:
    r = decide(_checks(**{missing: None}), SECTOR, _walked())
    assert (r.status, r.naics) == ("ERROR", None)


def test_a_failed_walk_is_an_error() -> None:
    r = decide(_checks(), SECTOR, _walked("hold", hold_reason="service_error"))
    assert (r.status, r.naics) == ("ERROR", None)


class AssistJev:
    """Answers the assist checks itself and every tree question like Oracle."""

    def __init__(self, target="621210", **checks):
        self.oracle, self.checks, self.calls = Oracle(target=target), {**GOOD, **checks}, []

    def judge(self, state, questions):
        self.calls.append(sorted(questions))
        mine = {q for q in questions if q.startswith("assist_")}
        rest = self.oracle.judge(state, {q: v for q, v in questions.items() if q not in mine})
        nouls, choices = dict(rest.nouls), dict(rest.choices)
        for q in mine:
            if q == assist.DELIVERY:
                options = list(questions[q].criteria)
                d = self.checks["delivery"]
                choices[q] = ChoiceView(d, {o: (0.93 if o == d else 0.01) for o in options}, None)
            else:
                nouls[q] = NoulView(self.checks[assist.NOUL_FIELDS[q]])
        if assist.UNDERSPECIFIED in questions:
            nouls[assist.UNDERSPECIFIED] = NoulView(self.checks["underspecified"])
        return Judgment(model="m", choices=choices, nouls=nouls)


def test_end_to_end_a_clear_description_walks_to_a_code() -> None:
    jev = AssistJev(target="621210")
    r = assess("Family dental practice with two dentists.", decider=jev)
    assert (r.status, r.naics, r.level) == ("CODED", "621210", 6)
    # Request A asked the checks and the sectors together; the walk continued from it.
    assert assist.DELIVERY in jev.calls[0] and "node_root" in jev.calls[0]
    assert FINAL in jev.calls[-1] and r.calls == len(jev.calls)


def test_end_to_end_revenue_only_text_costs_one_call() -> None:
    jev = AssistJev(operations=0.05)
    r = assess("Management fees.", decider=jev)
    assert (r.status, r.naics, r.tooltip_id, r.calls) == ("NEEDS_INFO", None, "is_operations_text", 1)


def test_the_bars_are_measured_constants() -> None:
    assert 0 < NOUL_ASSIST_OPERATIONS < 1 and 0 < NOUL_ASSIST_ACTIVITY < 1


# ---- the API: /v1/assist on each pause, /v1/finalize on submit ------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from jev_naics.api.app import app, get_assist_decider, get_consensus_decider, get_searcher
    from test_hybrid import Both
    from test_search_pick import VET, searcher

    app.dependency_overrides[get_assist_decider] = lambda: AssistJev(target="541940")
    app.dependency_overrides[get_consensus_decider] = lambda: Both(target="541940")
    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_assist_endpoint_returns_the_meter_the_tooltip_and_the_checks(client) -> None:
    body = client.post("/v1/assist", json={"description": "Veterinary clinic for dogs and cats."}).json()
    assert (body["status"], body["naics"], body["level"]) == ("CODED", "541940", 6)
    assert body["checks"]["delivery_mode"]["choice"] == "own_software_product" and body["tooltip"] is None
    assert body["sector"]["code"] == "54" and body["config_version"] and body["calls"] >= 2


def test_the_assist_endpoint_never_codes_revenue_only_text(client) -> None:
    from jev_naics.api.app import app, get_assist_decider

    app.dependency_overrides[get_assist_decider] = lambda: AssistJev(operations=0.05)
    body = client.post("/v1/assist", json={"description": "Management fees."}).json()
    assert (body["status"], body["naics"], body["tooltip_id"]) == ("NEEDS_INFO", None, "is_operations_text")
    assert body["tooltip"].startswith("Describe what the business does")


def test_finalize_runs_the_ensemble_and_records_the_source(client) -> None:
    body = client.post("/v1/finalize", json={"description": "Veterinary clinic for dogs and cats."}).json()
    assert (body["gate"], body["code"], body["source"]) == ("auto_act", "541940", "jev_ensemble")
    assert body["ensemble"]["step"] == "agreement"


def test_finalize_takes_the_brokers_pick_as_the_code_and_keeps_the_ensemble_for_audit(client) -> None:
    body = client.post("/v1/finalize", json={"description": "Pet clinic.",
                                             "user_pick": {"picklist_id": "541511_513210", "code": "513210"}}).json()
    assert (body["gate"], body["code"], body["source"], body["title"]) == \
        ("auto_act", "513210", "user_pick", "Software Publishers")
    assert body["ensemble"]["code"] == "541940"


def test_finalize_refuses_a_pick_that_is_not_a_2022_industry(client) -> None:
    r = client.post("/v1/finalize", json={"description": "Pet clinic.",
                                          "user_pick": {"picklist_id": "x", "code": "511210"}})
    assert r.status_code == 422


def test_several_activities_outrank_missing_customers_in_the_tooltip() -> None:
    r = decide(_checks(underspecified=0.9, single=0.1, customers=0.1), SECTOR, None)
    assert r.tooltip_id == "single_primary_activity"
