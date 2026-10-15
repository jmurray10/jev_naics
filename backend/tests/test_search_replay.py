"""Search-then-pick evals on recorded real Jev answers (JEV-50): runs in CI with no key and no
database, over the search results saved from the real database (evals/search_candidates.json).

Record with the database and a key:
    NAICS_DATABASE_URL=... JEV_DECIDER=record python -m jev_naics --search-eval
Pinned from the 2026-09-29 run on jev-1.13.0 (K=10, IDF-ranked search), one Jev call per case:
- dev (33): 32 correct, 1 expected hold (the vague one), 0 unexpected holds. The tree walk's two
  misses (veterinary clinic, car wash) are right here: search ranked both first. esp-no-naics
  ("subscription SaaS platform") used to be an unexpected hold (search did not return 513210, Jev
  leaned to 541614 at p 0.99, and its fit Noul of 0.07 held it) until JEV-52's modern-term list
  started reading "SaaS" as software publishers' Census wording, which fixed it.
- JEV-48 held-out (6): 6 correct. It informed SEARCH_K and the ranking, so it is not independent.
- fresh held-out (8, committed alone in 6c28ea3 before any run; close competitors): 7 correct, 1
  hold. The retail bakery split between retail and commercial bakeries (p 0.49), and the pick bar
  held it: the first case where PROB_SEARCH_PICK, not the fit Noul, decided.
Never a wrong code.
"""

from jev_naics._jev import ReplayDecider

from jev_naics.full_eval import HELDOUT
from jev_naics.search_eval import (
    HELDOUT_CANDIDATES,
    MESSY_DEV,
    MESSY_HELDOUT,
    SEARCH_CANDIDATES,
    SEARCH_HELDOUT,
    saved_searcher,
    search_report,
)
from jev_naics.search_pick import SEARCH_TRANSCRIPT


def test_dev_set_matches_the_recorded_run_and_never_acts_wrongly() -> None:
    assert SEARCH_TRANSCRIPT.is_file() and SEARCH_CANDIDATES.is_file(), "record with --search-eval"
    report = search_report(ReplayDecider(SEARCH_TRANSCRIPT), saved_searcher())
    # JEV-52: the modern-term list reads "SaaS" as software publishers, so esp-no-naics is right now.
    assert report["outcomes"] == {"correct_act": 32, "expected_hold": 1}
    assert not any(r["outcome"] in ("wrong_act", "missed_hold") for r in report["cases"])


def test_held_out_set_is_all_correct() -> None:
    report = search_report(ReplayDecider(SEARCH_TRANSCRIPT), saved_searcher(), HELDOUT)
    assert report["outcomes"] == {"correct_act": 6}


def test_the_saved_search_results_match_this_code_or_replay_refuses() -> None:
    # The saved results record the K, data version, and search SQL they came from (gate audit):
    # a change to any of them must be re-saved from the database, never replayed stale.
    import json

    import pytest

    from jev_naics import db, search_eval
    from jev_naics.questions import SEARCH_K
    from jev_naics.search_data import load_search_data

    saved = json.loads(SEARCH_CANDIDATES.read_text(encoding="utf-8"))
    assert saved["search_k"] == SEARCH_K and saved["data_version"] == load_search_data().version
    assert saved["search_sql_sha256"] == search_eval.search_sql_fingerprint()
    search = saved_searcher()
    with pytest.raises(LookupError):
        search(next(iter(saved["results"])), SEARCH_K + 5)
    assert db.DEFAULT_LIMIT == SEARCH_K


def test_fresh_held_out_set_holds_the_close_call_and_never_acts_wrongly() -> None:
    report = search_report(ReplayDecider(SEARCH_TRANSCRIPT), saved_searcher(), SEARCH_HELDOUT)
    assert report["outcomes"] == {"correct_act": 7, "unexpected_hold": 1}
    [held] = [r for r in report["cases"] if r["outcome"] == "unexpected_hold"]
    assert (held["id"], held["hold"], held["lean"]) == ("retail-bakery", "low_probability", "311811")
    assert held["probability"] < 0.7


def test_messy_input_dev_set_is_all_right() -> None:
    # JEV-52: typos (spelling correction), jargon (the term list), several activities, and (added
    # after the gate audit, to measure TERM_WEIGHT) six where a listed term is incidental.
    report = search_report(ReplayDecider(SEARCH_TRANSCRIPT), saved_searcher(), MESSY_DEV)
    assert report["outcomes"] == {"correct_act": 18}


def test_messy_blind_held_out_set_run_once_never_acts_wrongly() -> None:
    # Written and committed (2c5420b) by a separate agent. Run twice, never to choose anything: first
    # with TERM_WEIGHT 1.0, then after the gate audit's dev-only weight sweep set it to 0.25. Both runs:
    # 11 right, 1 held (insurtech: the right industry was not among the search results; the fit Noul
    # held the lean). All four several-activity cases were right both times, so the planned
    # primary-revenue clause step was not built.
    report = search_report(ReplayDecider(SEARCH_TRANSCRIPT), saved_searcher(HELDOUT_CANDIDATES), MESSY_HELDOUT)
    assert report["outcomes"] == {"correct_act": 11, "unexpected_hold": 1}
    [held] = [r for r in report["cases"] if r["outcome"] == "unexpected_hold"]
    assert (held["id"], held["hold"], held["gold_rank"]) == ("ho-jargon-insurtech", "misfit", None)


def test_the_fingerprint_covers_the_vocabulary_and_the_term_matching_code(monkeypatch) -> None:
    from jev_naics import db, search_eval, terms

    before = search_eval.search_sql_fingerprint()
    monkeypatch.setattr(db, "_WORDS", db._WORDS + " ")
    assert search_eval.search_sql_fingerprint() != before
    monkeypatch.undo()
    assert search_eval.search_sql_fingerprint() == before
    assert terms.terms_version() != terms.terms_code_version()  # the list and the matcher are both hashed
