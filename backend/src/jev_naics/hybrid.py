"""Search first, the tree walk as a fallback (JEV-53).

Search then pick (search_pick.py) is fast (one call) and right whenever the right industry is among
its results. It misses when the description's words never meet the Census text ("insurtech"); the
tree walk (full.py) reasons down from the sectors instead, but can prune the right branch early
(veterinary clinics sit under professional services). Measured live on jev-1.13.0 (2026-09-29) on the
typed-description sets, each got 29 of 30 and they missed different cases.

1. Search then pick. If it acts, that is the answer (one call).
2. Only when search holds because the right industry may be missing from its results (no_match,
   low_probability, misfit), the tree walk runs to its finalists (no final pick of its own).
3. The finalists the walk adds join search's results, and Jev picks once over all of them with
   search's own questions and gates (search_pick). The answer can only be an industry the walk added:
   if the walk adds nothing new, or the pick lands on an industry search already offered and held on,
   search's hold stands (asking a near-identical question again is not new evidence).
A description held as too vague, a malformed answer, and a service error are never escalated, and the
walk's own vagueness check still holds (too vague, or no usable answer).
"""

from __future__ import annotations

import dataclasses
import re
import time
from typing import Any

from jev_naics._jev import Decider, ReplayMissing
from typesafe_sdk import TypeSafeError

from jev_naics.db import Hit
from jev_naics.env import repo_root
from jev_naics.full import FullResult, Walk, walk
from jev_naics._jev.gate import is_unit_probability

from jev_naics.questions import NOUL_FULL_UNDERSPECIFIED, SEARCH_K
from jev_naics.search_data import SearchData, load_search_data
from jev_naics.search_pick import Searcher, classify_search
from jev_naics.tree import NaicsTree, load_tree

HYBRID_TRANSCRIPT = repo_root() / "transcripts" / "naics-hybrid.json"
# Search holds that may mean the right industry is not among its results.
ESCALATE = frozenset({"no_match", "low_probability", "misfit"})


def classify_hybrid(description: str, *, decider: Decider, searcher: Searcher, data: SearchData | None = None,
                    tree: NaicsTree | None = None, strict: bool = False) -> FullResult:
    data = data or load_search_data()
    naics = tree or load_tree()
    started = time.perf_counter()
    seen: list[list[Hit]] = []  # search's results, kept so the fallback does not search again

    def search_once(text: str, limit: int) -> list[Hit]:
        seen.append(searcher(text, limit))
        return seen[-1]

    first = classify_search(description, decider=decider, searcher=search_once, data=data, strict=strict)
    if first.gate == "auto_act" or first.hold_reason not in ESCALATE or not seen:
        return dataclasses.replace(first, reason=f"Search: {first.reason}")
    hits = seen[-1]

    w = Walk()

    def finish(result: FullResult, why: str, **extra: Any) -> FullResult:
        return dataclasses.replace(result, reason=why, trace=w.trace, fallback=True,
                                   calls=first.calls + w.calls + extra.get("calls", 0),
                                   seconds=round(time.perf_counter() - started, 3),
                                   model=result.model or w.model or first.model,
                                   alternatives=[{"code": p.node, "title": naics.title(p.node),
                                                  "path_score": round(p.score, 4), "path": list(p.codes)}
                                                 for p in w.beam])

    def held(reason: str, why: str, **extra: Any) -> FullResult:
        return finish(dataclasses.replace(first, hold_reason=reason), why, **extra)

    try:
        walk(description, decider=decider, tree=naics, into=w)
    except ReplayMissing:
        if strict:
            raise
        return held("service_error", "Search held; the tree walk had no recorded answer and no TypeSafe key.")
    except (TypeSafeError, ValueError):
        return held("service_error", "Search held; a tree-walk call failed or was unreadable; fail closed.")
    if w.failed:
        return held("invalid_answer", f"Search held ({first.hold_reason}); the tree walk: {w.failed}")
    if w.underspecified is None or not is_unit_probability(w.underspecified):
        return held("invalid_answer", f"Search held ({first.hold_reason}); the tree walk's vagueness check had "
                                      "no usable answer.")
    if w.underspecified >= NOUL_FULL_UNDERSPECIFIED:
        return held("too_vague", f"Search held ({first.hold_reason}); the tree walk found the description too "
                                 f"vague or mixed (Noul {w.underspecified:.2f}).")

    searched = [h for h in hits if h.code in data.industries]
    added = [p.node for p in w.beam if p.node in data.industries and p.node not in {h.code for h in searched}]
    if not added:
        return finish(first, f"Search held ({first.hold_reason}: {first.reason}) and the tree walk found no "
                             "industry search had not already offered.")
    added = list(dict.fromkeys(added))
    union = searched + [Hit(code=c, title=naics.title(c), rank=0.0, snippet="") for c in added]
    second = classify_search(description, decider=decider, searcher=lambda _text, _limit: union, data=data,
                             strict=strict, k=len(union))
    # A walk finalist has no search rank or score: search never returned it.
    rows = [{**r, "search_rank": None, "search_score": None, "source": "tree"} if r["code"] in added
            else {**r, "source": "search"} for r in second.finalists]
    second = dataclasses.replace(second, finalists=rows)
    note = f"Search held ({first.hold_reason}); the tree walk added {', '.join(added)}."
    if second.gate == "auto_act" and second.code not in added:
        return held(first.hold_reason or "misfit", f"{note} Asked over both, Jev picked {second.code}, which search "
                    "had already offered and held on, not one the tree walk added; search's hold stands.",
                    calls=second.calls)
    reason = second.reason
    if second.code in added:
        reason = re.sub(r"\(search rank \d+\)", "(a tree finalist)", reason)
    return finish(second, f"{note} Picked over both: {reason}", calls=second.calls)


__all__ = ["ESCALATE", "HYBRID_TRANSCRIPT", "SEARCH_K", "classify_hybrid"]
