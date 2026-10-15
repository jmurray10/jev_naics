"""The assist dev set (evals/assist_dev.json, synthetic), replayed from its recording on jev-1.13.0: the
calibration of the NOUL_ASSIST_* bars. Strict: no 6-digit code without passing checks, none wrong."""

import json

import pytest
from jev_naics._jev import ReplayDecider

from jev_naics.assist import ASSIST_TRANSCRIPT, assess
from jev_naics.env import repo_root

CASES = json.loads((repo_root() / "evals" / "assist_dev.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results():
    decider = ReplayDecider(ASSIST_TRANSCRIPT)
    return {c["id"]: (c, assess(c["ops_description"], decider=decider, strict=True)) for c in CASES}


def test_revenue_only_and_generic_text_always_needs_info_and_never_gets_a_code(results) -> None:
    for c, r in results.values():
        if c["kind"] in ("revenue", "generic"):
            assert (c["id"], r.status, r.naics) == (c["id"], "NEEDS_INFO", None)
            assert r.calls == 1 and r.tooltip_id in ("is_operations_text", "states_activity")


def test_every_coded_case_is_right(results) -> None:
    coded = [(c, r) for c, r in results.values() if r.status == "CODED"]
    assert [c["id"] for c, r in coded if c["expect_code"] and r.naics != c["expect_code"]] == []
    assert sum(1 for c, _ in coded if c["kind"] == "clear") == 8


def test_no_status_ever_carries_a_code_except_coded(results) -> None:
    assert [i for i, (_, r) in results.items() if r.naics and r.status != "CODED"] == []


def test_mixed_text_names_the_main_activity_and_confusables_offer_a_pick_list(results) -> None:
    for c, r in results.values():
        if c["kind"] == "mixed":
            assert (r.status, r.tooltip_id) == ("NEEDS_INFO", "single_primary_activity")
    assert results["conf-it"][1].picklist["id"] == "it_services"
    assert results["conf-sw"][1].picklist["id"] == "541511_513210"
