"""Search-then-pick eval (JEV-50): the same dev and held-out sets as the tree walk, strict scoring.

CI has no Postgres, so the search results for the eval descriptions are saved in
evals/search_candidates.json when the eval runs against the database; offline replay searches
that file instead. A Postgres test checks the file still equals what the database returns, so it
can never drift into invented candidates.

Record once with a key and the database (JEV_DECIDER=record, NAICS_DATABASE_URL set):
    python -m jev_naics --search-eval
CI replays transcripts/naics-search.json over the saved candidates.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jev_naics._jev.evals import PASSING, eval_outcome
from jev_naics._jev import Decider

from jev_naics import db
from jev_naics.db import Hit
from jev_naics.env import repo_root
from jev_naics.questions import (
    SPELL_MAX_EDITS,
    SPELL_MAX_WORD,
    SPELL_MIN_SIMILARITY,
    SPELL_MIN_WORD,
    SPELL_WEIGHT,
    TERM_WEIGHT,
    VOCAB_MIN_WORD,
)
from jev_naics.search_data import load_search_data
from jev_naics.terms import terms_code_version, terms_version
from jev_naics.full_eval import FULL_EVALS, HELDOUT
from jev_naics.questions import SEARCH_K
from jev_naics.search_pick import Searcher, classify_search

SEARCH_CANDIDATES = repo_root() / "evals" / "search_candidates.json"
# Written after the JEV-50 gate audit and committed (6c28ea3) before any run: the JEV-48 held-out set
# had informed SEARCH_K and the ranking, so it no longer counts as held out for search mode.
SEARCH_HELDOUT = repo_root() / "evals" / "search_heldout.json"
# JEV-52 messy input (typos, jargon, several activities): a dev set, and a blind held-out set written
# and committed (2c5420b) by a separate agent; the tuning session has not read its descriptions.
MESSY_DEV = repo_root() / "evals" / "messy_dev.json"
MESSY_HELDOUT = repo_root() / "evals" / "messy_heldout.json"
# The blind held-out set's search results live apart (gate audit): the tuning file never holds them.
HELDOUT_CANDIDATES = repo_root() / "evals" / "search_candidates_heldout.json"
# JEV-53: a blind set for the hybrid, written by a separate agent and committed (087c462) before it was
# built; its search results also live apart.
HYBRID_HELDOUT = repo_root() / "evals" / "hybrid_heldout.json"
HYBRID_CANDIDATES = repo_root() / "evals" / "search_candidates_hybrid.json"


def eval_descriptions(paths: tuple[Path, ...] = (FULL_EVALS, HELDOUT, SEARCH_HELDOUT, MESSY_DEV)) -> list[str]:
    """The descriptions whose search results are saved; the blind held-out set only on request."""
    return [c["ops_description"] for path in paths
            for c in json.loads(path.read_text(encoding="utf-8"))]


def search_sql_fingerprint() -> str:
    """A hash of the search query, so saved results made by an older ranking are refused."""
    # The ranking query, how the spelling vocabulary is built, and how terms are matched (JEV-52 audit).
    parts = f"{db._SEARCH}|{db._WORDS}|{terms_code_version()}"
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:16]


def _made_with() -> dict[str, Any]:
    return {"search_k": SEARCH_K, "data_version": load_search_data().version,
            "search_sql_sha256": search_sql_fingerprint(), "terms_version": terms_version(),
            "spelling": [SPELL_MIN_SIMILARITY, SPELL_MAX_EDITS, SPELL_WEIGHT, TERM_WEIGHT, SPELL_MIN_WORD,
                         SPELL_MAX_WORD, VOCAB_MIN_WORD]}


def save_candidates(searcher: Searcher, descriptions: list[str] | None = None,
                    path: Path = SEARCH_CANDIDATES) -> dict[str, Any]:
    """Search each description with the real database and save the results, with what made them.
    Defaults: this package's eval descriptions. A caller can save its own set the same way."""
    texts = eval_descriptions() if descriptions is None else descriptions
    saved = {**_made_with(), "results": {
        d: [{"code": h.code, "title": h.title, "rank": round(h.rank, 6), "snippet": h.snippet}
            for h in searcher(d, SEARCH_K)] for d in texts}}
    path.write_text(json.dumps(saved, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return saved


def saved_searcher(path: Path = SEARCH_CANDIDATES) -> Searcher:
    """Search the saved results. Refuses (LookupError) when they were made with another K, data
    version, or search query, or when a description has none saved: nothing stale or invented."""
    saved = json.loads(path.read_text(encoding="utf-8"))
    stale = {k: (saved.get(k), v) for k, v in _made_with().items() if saved.get(k) != v}
    results = saved.get("results", {})

    def search(text: str, limit: int) -> list[Hit]:
        if stale:
            raise LookupError(f"saved search results are stale {stale}; re-save with --search-eval and the database")
        if limit != saved["search_k"]:
            raise LookupError(f"saved results hold {saved['search_k']} hits, not {limit}")
        if text not in results:
            raise LookupError(f"no saved search results for {text[:60]!r}; run --search-eval with the database")
        return [Hit(**h) for h in results[text]]

    return search


def search_report(decider: Decider, searcher: Searcher, path: Path = FULL_EVALS) -> dict[str, Any]:
    rows = []
    for case in json.loads(path.read_text(encoding="utf-8")):
        r = classify_search(case["ops_description"], decider=decider, searcher=searcher, strict=True)
        outcome = eval_outcome(r.gate, r.code, case["expect_code"], case["expect_hold"])
        found = [f["code"] for f in r.finalists]
        rows.append({"id": case["id"], "gate": r.gate, "code": r.code, "gold": case["expect_code"],
                     "lean": (r.path or r.model_path or [None])[-1], "probability": r.probability,
                     "fit": r.fit_noul, "vague": r.underspecified_noul, "hold": r.hold_reason,
                     "gold_rank": found.index(case["expect_code"]) + 1 if case["expect_code"] in found else None,
                     "outcome": outcome})
    return {"cases": rows, "outcomes": dict(Counter(r["outcome"] for r in rows))}


def hybrid_report(decider: Decider, searcher: Searcher, path: Path = FULL_EVALS) -> dict[str, Any]:
    """The hybrid (JEV-53) on one eval file, strict; `fallback` marks the cases where the tree walk ran."""
    from jev_naics.hybrid import classify_hybrid

    rows = []
    for case in json.loads(path.read_text(encoding="utf-8")):
        r = classify_hybrid(case["ops_description"], decider=decider, searcher=searcher, strict=True)
        outcome = eval_outcome(r.gate, r.code, case["expect_code"], case["expect_hold"])
        found = [f["code"] for f in r.finalists]
        rows.append({"id": case["id"], "gate": r.gate, "code": r.code, "gold": case["expect_code"],
                     "lean": (r.path or r.model_path or [None])[-1], "probability": r.probability,
                     "fit": r.fit_noul, "vague": r.underspecified_noul, "hold": r.hold_reason,
                     "gold_rank": found.index(case["expect_code"]) + 1 if case["expect_code"] in found else None,
                     "fallback": r.fallback, "calls": r.calls, "outcome": outcome})
    return {"cases": rows, "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "fallbacks": sum(r["fallback"] for r in rows), "calls": sum(r["calls"] for r in rows)}


def print_search_report(*reports: tuple[str, dict[str, Any]]) -> int:
    num = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    ok = True
    for name, report in reports:
        print(f"== {name}")
        for r in report["cases"]:
            mark = "PASS" if r["outcome"] in PASSING else "FAIL"
            print(f"{mark} {r['id']}: {r['gate']} code={r['code']} gold={r['gold']} (search rank {r['gold_rank']}) "
                  f"lean={r['lean']} p={num(r['probability'])} fit={num(r['fit'])} vague={num(r['vague'])} "
                  f"hold={r['hold']}{' tree-fallback' if r.get('fallback') else ''} {r['outcome']}")
        print(report["outcomes"], *(f"fallbacks={report['fallbacks']} calls={report['calls']}",)
              if "fallbacks" in report else ())
        ok = ok and all(r["outcome"] in PASSING for r in report["cases"])
    return 0 if ok else 1
