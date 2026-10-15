"""The hybrid (JEV-53) on recorded real Jev answers: CI replays transcripts/naics-hybrid.json over the saved
search results. Recorded 2026-09-29 on jev-1.13.0 with JEV_DECIDER=record-missing.
"""

from jev_naics._jev import ReplayDecider

from jev_naics.full_eval import FULL_EVALS, HELDOUT
from jev_naics.hybrid import HYBRID_TRANSCRIPT
from jev_naics.search_eval import (
    HELDOUT_CANDIDATES,
    MESSY_DEV,
    MESSY_HELDOUT,
    SEARCH_HELDOUT,
    hybrid_report,
    saved_searcher,
)


def _report(path, candidates=None):
    searcher = saved_searcher(candidates) if candidates else saved_searcher()
    return hybrid_report(ReplayDecider(HYBRID_TRANSCRIPT), searcher, path)


def test_where_search_is_sure_the_hybrid_is_search_in_one_call() -> None:
    for path, outcomes in ((FULL_EVALS, {"correct_act": 32, "expected_hold": 1}), (MESSY_DEV, {"correct_act": 18}),
                           (HELDOUT, {"correct_act": 6})):
        report = _report(path)
        assert report["outcomes"] == outcomes and report["fallbacks"] == 0
        assert report["calls"] == len(report["cases"])


def test_the_tree_fallback_recovers_what_search_never_found() -> None:
    report = _report(MESSY_HELDOUT, HELDOUT_CANDIDATES)
    assert report["outcomes"] == {"correct_act": 12} and report["fallbacks"] == 1
    insurtech = next(c for c in report["cases"] if c["id"] == "ho-jargon-insurtech")
    assert insurtech["fallback"] and insurtech["code"] == "524210"


def test_a_real_split_still_holds_after_the_fallback() -> None:
    report = _report(SEARCH_HELDOUT)
    assert report["outcomes"] == {"correct_act": 7, "unexpected_hold": 1}
    bakery = next(c for c in report["cases"] if c["outcome"] == "unexpected_hold")
    assert bakery["fallback"] and bakery["hold"] == "low_probability" and bakery["code"] is None


def test_the_jev53_blind_held_out_run_once() -> None:
    # Written by a separate agent and committed (087c462) before the hybrid was built; run once, live,
    # after the gate audit. Search alone on the same set: 8 right, 8 held; the tree walk alone: 14, 2.
    from jev_naics.search_eval import HYBRID_CANDIDATES, HYBRID_HELDOUT

    report = _report(HYBRID_HELDOUT, HYBRID_CANDIDATES)
    assert report["outcomes"] == {"correct_act": 15, "unexpected_hold": 1} and report["fallbacks"] == 8
    assert next(c for c in report["cases"] if c["outcome"] == "unexpected_hold")["id"] == "hy-quirk-vet"
