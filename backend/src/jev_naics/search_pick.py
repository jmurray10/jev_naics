"""Search, then Jev picks (JEV-50): the find-then-judge pattern.

1. Code searches: Postgres full-text search over the Census text (db.search) returns the SEARCH_K
   industries whose official words best match the description. No model call.
2. Jev judges, in one call: a Choice over only those candidates, each shown with its official
   description, examples, index entries, and what the Census says it is not; a fit Noul per
   candidate; and the vagueness Noul.
3. Code decides, with the same holds as the tree walk (full.py):
   - no_match         search found nothing; Jev is not called
   - too_vague        the vagueness Noul fired
   - invalid_answer   any malformed or missing answer
   - low_probability  the pick's probability is under PROB_SEARCH_PICK, or search found only one
                      industry: nothing to compare and an unmeasured path, so Jev is not asked and a
                      person decides (gate audit, 2026-09-29)
   - misfit           the picked industry's fit Noul is under NOUL_SEARCH_FIT_HOLD
   - service_error    the search or the call failed
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from jev_naics._jev.gate import json_number
from jev_naics._jev import Decider, ReplayMissing
from psycopg import Error as DatabaseError
from typesafe_sdk import Choice, Noul, TypeSafeError

from jev_naics.db import Hit
from jev_naics.env import repo_root
from jev_naics.full import FullResult, HoldReason, _checked, _unit
from jev_naics.questions import NOUL_SEARCH_FIT_HOLD, NOUL_SEARCH_UNDERSPECIFIED, PROB_SEARCH_PICK, SEARCH_K
from jev_naics.search_data import SearchData, load_search_data
from jev_naics.tree import load_tree

SEARCH_TRANSCRIPT = repo_root() / "transcripts" / "naics-search.json"
PICK = "search_pick"
UNDERSPECIFIED = "search_underspecified"
Searcher = Callable[[str, int], list[Hit]]
CRITERION_CHARS = 520
PROFILE_CHARS = 900
PROFILE_LIST = 8  # examples, index entries, and exclusions shown per candidate


def fit_question_id(code: str) -> str:
    return "fit_" + code


def _criterion(title: str, code: str, description: str) -> str:
    text = f"{title} (NAICS {code}). {description}"
    return text if len(text) <= CRITERION_CHARS else text[: CRITERION_CHARS - 1].rstrip() + "…"


def _profile(data: SearchData, code: str) -> dict[str, Any]:
    i = data.industries[code]
    description = i.description if len(i.description) <= PROFILE_CHARS else i.description[:PROFILE_CHARS] + "…"
    return {
        "title": i.title,
        "description": description,
        "examples": list(i.examples[:PROFILE_LIST]),
        "index_entries": list(i.index_entries[:PROFILE_LIST]),
        # The Census's own exclusions: what this industry is not, and where that goes instead.
        "not_this_industry": [f"{activity} (classified in {where})" for where, activity in i.excludes[:PROFILE_LIST]],
    }


def _questions(data: SearchData, codes: list[str]) -> dict[str, Choice | Noul]:
    questions: dict[str, Choice | Noul] = {
        PICK: Choice(
            instructions=("Which NAICS industry in `candidates` best describes the primary activity of the business "
                          "in `ops_description`? Use each industry's description, examples, index entries, and "
                          "what it is not. Judge the primary activity, not a side line."),
            criteria={c: _criterion(data.industries[c].title, c, data.industries[c].description) for c in codes},
        ),
    }
    questions[UNDERSPECIFIED] = Noul(
            instructions=("Is `ops_description` too vague, or does it mix unrelated lines of business, so that one "
                          "NAICS industry cannot be assigned safely?"),
            criteria={"true": "Too vague or mixed to assign one industry.",
                      "false": "Clear enough to assign one industry."},
    )
    for c in codes:
        questions[fit_question_id(c)] = Noul(
            instructions=(f"Does the NAICS industry in `candidates.{c}` describe the primary activity of the business "
                          "in `ops_description`?"),
            criteria={"true": "Yes: this industry is what the business primarily does.",
                      "false": "No: the business primarily does something else."},
        )
    return questions


def classify_search(description: str, *, decider: Decider, searcher: Searcher, data: SearchData | None = None,
                    strict: bool = False, k: int = SEARCH_K) -> FullResult:
    data = data or load_search_data()
    tree = load_tree()
    started = time.perf_counter()
    calls, model, underspecified, rows = 0, None, None, []

    def done(**kwargs) -> FullResult:
        return FullResult(model=model, calls=calls, seconds=round(time.perf_counter() - started, 3),
                          underspecified_noul=underspecified, finalists=rows, **kwargs)

    def hold(reason: HoldReason, why: str, lean: str | None = None, **kwargs) -> FullResult:
        # A hold has no answer: `path` stays empty; the industry Jev leaned to is diagnostic only.
        return done(gate="hold", code=None, title=None, hold_reason=reason, reason=why, path_score=None,
                    probability=kwargs.pop("probability", None), path=[],
                    model_path=tree.path(lean) if lean else [], **kwargs)

    try:
        hits = searcher(description, k)
    except (DatabaseError, OSError, RuntimeError, ValueError) as exc:
        # The database is down, unreachable, or not configured, or the text cannot be sent (ValueError).
        return hold("service_error", f"The search failed: {type(exc).__name__}")
    # Each real industry once, in search order; anything else is dropped before Jev sees it.
    codes = list(dict.fromkeys(h.code for h in hits if h.code in data.industries))
    if not codes:
        return hold("no_match", "No NAICS industry's official text matches these words; Jev was not asked.")
    rank = {code: n for n, code in enumerate(codes, start=1)}
    score = {}
    for h in hits:
        score.setdefault(h.code, json_number(h.rank))
    if len(codes) == 1:
        return hold("low_probability", f"Search found one industry ({codes[0]}): nothing to compare, so a person "
                    "decides. Jev was not asked, so no lean is recorded.")
    try:
        state = {"ops_description": description, "candidates": {c: _profile(data, c) for c in codes}}
        judgment = decider.judge(state, _questions(data, codes))
        calls, model = 1, str(judgment.model) if judgment.model else None
    except ReplayMissing:
        if strict:
            raise
        return hold("service_error", "No recorded answer and no TypeSafe key.")
    except (TypeSafeError, ValueError):
        return hold("service_error", "A TypeSafe call failed or was unreadable; fail closed.")

    choices = judgment.choices if isinstance(judgment.choices, Mapping) else {}
    nouls = judgment.nouls if isinstance(judgment.nouls, Mapping) else {}
    underspecified = _unit(nouls.get(UNDERSPECIFIED))
    dist = _checked(choices.get(PICK), codes)
    rows.extend({"code": c, "title": data.industries[c].title, "p": dist[c] if dist else None,
                 "fit": _unit(nouls.get(fit_question_id(c))), "search_rank": rank[c],
                 "search_score": None if score[c] is None else round(score[c], 4), "path_score": None}
                for c in codes)
    if dist is None:
        return hold("invalid_answer", "The pick was malformed; nothing to act on.")
    picked = max(dist, key=dist.get)
    probability = dist[picked]
    fit = next(r["fit"] for r in rows if r["code"] == picked)
    if underspecified is None:
        return hold("invalid_answer", "The vagueness check had no usable answer.", picked, probability=probability)
    if underspecified >= NOUL_SEARCH_UNDERSPECIFIED:
        return hold("too_vague", f"The description is too vague or mixed (Noul {underspecified:.2f}).", picked,
                    probability=probability)
    if probability < PROB_SEARCH_PICK:
        return hold("low_probability", f"The pick {picked} has p {probability:.2f}, below {PROB_SEARCH_PICK}.",
                    picked, probability=probability, fit_noul=fit)
    if fit is None:
        return hold("invalid_answer", "The fit check had no usable answer.", picked, probability=probability)
    if fit < NOUL_SEARCH_FIT_HOLD:
        return hold("misfit", f"Jev doubts {picked} describes this business (fit {fit:.2f}).", picked,
                    probability=probability, fit_noul=fit)
    return done(gate="auto_act", code=picked, title=data.industries[picked].title, hold_reason=None,
                reason=f"Picked {picked} (search rank {rank[picked]}) at p {probability:.2f}; fit {fit:.2f}.",
                probability=probability, fit_noul=fit, path_score=None, path=tree.path(picked))
