"""Refine: a wide pick over every NAICS industry, then a verify step over the few best (iterations to compare).

The tree walk can prune the right branch at the first or second level (car washes under personal services,
veterinary clinics under health care). The wide pick never prunes: one call asks the completeness checks,
a Choice over the 22 sector groups (manufacturing as its three Census halves, 31, 32, 33), and one Choice
per block of whole groups (REFINE_CHOICE_MAX options at most; the API allows 255) over every industry's
short profile. An industry's wide score is P(its group) x P(it | its group within its block).

Then the REFINE_TOP_K best are verified in one call with their full Census profiles. Variants:
  wide    no verify call: the wide pick's best, gated like the tree walk's final pick (PROB_FULL_PATH)
  verify  search's pick over the top K: Choice plus a fit Noul each, search's gates (search_pick.py)
  score   per candidate a 3-level Score (different / related / the primary activity), plus the pick asked
          twice in opposite option orders. Code: exactly one candidate at the top level, and both picks
          agree on it, is the code; any other mix of top and middle levels is a pick-list over those
          candidates; none above the bottom level is a sector at most. No fitted number: the nearest level
          decides (the entity-alignment pattern).
  scoreb  as score, but the candidates are the REFINE_PER_BLOCK best of every block by the block's own
          pick, with no P(group) weighting (E9: the group pick files a vet clinic under health care and
          buries it)
  agree   one wide call shared by `verify` and `scoreb`, each with its own verify call (3 calls). Code owns the
          rule: scoreb's code stands unless verify coded a different one (then a two-option pick-list); when
          scoreb did not code, verify's code stands if scoreb also scored that code "primary"
  cascade `verify` first; when it codes, that is the answer (2 calls). Otherwise `scoreb` runs on the same wide
          call (3 calls) and its pick-list, partial or hold stands. Text that failed the completeness checks, or
          was too short, stops at the first call exactly as in every variant
  cascadeb cascade whose verify and score steps show each candidate the Census boundary text too: what the
          industry is not and where that goes (`excludes`), and the activities other industries send to it
          (`sent_here`), within a budget (E12)
  union   as score, over the top K plus the tree walk's finalists (about 4 more calls)
Every option is a real industry built here, in code; hold is code-owned and never an option. The completeness
gate is assist.py's: text that is not operations, not specific, or too vague never gets a code.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from jev_naics._jev import Decider, ReplayMissing, transcript_key
from typesafe_sdk import Choice, Noul, Score, TypeSafeError

from jev_naics import assist
from jev_naics.assist import AssistResult, Checks, _deepest_shared, _gate, _read_checks, _sector, _tooltip
from jev_naics.env import repo_root
from jev_naics.lexicon import Lexicon
from jev_naics.full import SUM_TOLERANCE, UNDERSPECIFIED, Walk, _checked, _unit, classify_full, level_questions
from jev_naics.questions import (
    NOUL_SEARCH_FIT_HOLD,
    PROB_FULL_PATH,
    PROB_SEARCH_PICK,
    REFINE_BLOCK_MASS_MIN,
    REFINE_BOUNDARY_CHARS,
    REFINE_BOUNDARY_ITEMS,
    REFINE_CHOICE_MAX,
    REFINE_PER_BLOCK,
    REFINE_SHORT_CHARS,
    REFINE_SHORT_TERMS,
    REFINE_TOP_K,
)
from jev_naics.search_data import SearchData, load_search_data
from jev_naics.search_pick import _profile
from jev_naics.tree import ROOT, NaicsTree, load_tree, sector_of

REFINE_TRANSCRIPT = repo_root() / "transcripts" / "naics-refine.json"
Variant = Literal["wide", "verify", "score", "scoreb", "agree", "cascade", "cascadeb", "union"]
VARIANTS: tuple[str, ...] = ("wide", "verify", "score", "scoreb", "agree", "cascade", "cascadeb", "union")
GROUP = "refine_group"
PICK, PICK_REVERSED = "refine_pick", "refine_pick_reversed"
MIN_WORDS = 3
# The verify Score's levels, written before any answer was seen; the nearest level names the outcome.
FIT_LEVELS = [
    "Different: the business's primary activity is something else.",
    "Related: a neighbouring or partly matching activity, or one of several activities, but not clearly the "
    "primary one.",
    "Primary: this industry is what the business primarily does.",
]


def fit_score_id(code: str) -> str:
    return "refine_fit_" + code


def wide_id(block: int) -> str:
    return f"refine_wide_{block}"


def group_of(code: str) -> str:
    """The wide pick's sector group: the NAICS sector, with manufacturing split into 31, 32, 33."""
    sector = sector_of(code)
    return code[:2] if sector == "31-33" else sector


@lru_cache(maxsize=1)
def _layout() -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """The sector groups, and blocks of whole groups (each at most REFINE_CHOICE_MAX industries)."""
    tree = load_tree()
    leaves = sorted(tree.leaves())
    groups: dict[str, list[str]] = {}
    for c in leaves:
        groups.setdefault(group_of(c), []).append(c)
    blocks: list[list[str]] = []
    for g in sorted(groups, key=lambda g: -len(groups[g])):  # largest first, then fill
        members = groups[g]
        target = next((b for b in blocks if len(b) + len(members) <= REFINE_CHOICE_MAX), None)
        if target is None:
            blocks.append(list(members))
        else:
            target.extend(members)
    return tuple(sorted(groups)), tuple(tuple(sorted(b)) for b in blocks)


def _short(data: SearchData, code: str) -> str:
    i = data.industries[code]
    terms = [t for t in i.index_entries if t.lower() != i.title.lower()][:REFINE_SHORT_TERMS]
    text = i.title + (" — " + "; ".join(terms) if terms else "")
    return text if len(text) <= REFINE_SHORT_CHARS else text[: REFINE_SHORT_CHARS - 1].rstrip() + "…"


def _group_text(tree: NaicsTree, group: str) -> str:
    """A group described by what it contains (its subsectors), not only its umbrella title."""
    sector = "31-33" if group in ("31", "32", "33") else group
    subs = [c for c in tree.children(sector) if c.startswith(group) or "-" not in sector]
    if group in ("31", "32", "33"):
        title = f"Manufacturing ({group})"
    else:
        title = tree.title(group)
    return f"{title}: " + "; ".join(tree.title(s) for s in subs)


def wide_questions(tree: NaicsTree, data: SearchData) -> dict[str, Any]:
    groups, blocks = _layout()
    out: dict[str, Any] = {
        GROUP: Choice(instructions="Which group of NAICS sectors best describes the primary activity of the business "
                                   "in `ops_description`? Judge the primary activity, not a side line.",
                      criteria={g: _group_text(tree, g) for g in groups})}
    for n, block in enumerate(blocks):
        out[wide_id(n)] = Choice(
            instructions="Which NAICS industry in this list best describes the primary activity of the business in "
                         "`ops_description`? Judge the primary activity, not a side line.",
            criteria={c: _short(data, c) for c in block})
    return out


def wide_rank(judgment, tree: NaicsTree) -> tuple[list[tuple[str, float]], dict[str, float] | None, str | None]:
    """Every industry's wide score, best first; the group distribution; or why the answer is unusable."""
    scored, _, gdist, why = _wide(judgment)
    return scored, gdist, why


def _wide(judgment) -> tuple[list[tuple[str, float]], list[list[tuple[str, float]]], dict[str, float] | None, str | None]:
    """(weighted ranking, each block's own ranking, group distribution, why unusable). A malformed block only
    fails closed when its groups carry REFINE_BLOCK_MASS_MIN or more of the group pick; otherwise it is
    skipped (a block of unrelated sectors still has to pick something, and may be malformed)."""
    groups, blocks = _layout()
    choices = judgment.choices if isinstance(judgment.choices, Mapping) else {}
    gdist = _checked(choices.get(GROUP), list(groups))
    if gdist is None:
        return [], [], None, "The sector-group pick was malformed."
    scored: list[tuple[str, float]] = []
    per_block: list[list[tuple[str, float]]] = []
    for n, block in enumerate(blocks):
        dist = _checked(choices.get(wide_id(n)), list(block))
        if dist is None:
            if sum(gdist[g] for g in {group_of(c) for c in block}) >= REFINE_BLOCK_MASS_MIN:
                return [], [], gdist, f"Wide block {n} was malformed."
            continue
        mass: dict[str, float] = {}
        for c in block:
            mass[group_of(c)] = mass.get(group_of(c), 0.0) + dist[c]
        for c in block:
            g = group_of(c)
            within = dist[c] / mass[g] if mass[g] > 0 else 0.0
            scored.append((c, gdist[g] * within))
        per_block.append(sorted(((c, dist[c]) for c in block), key=lambda kv: kv[1], reverse=True))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored, per_block, gdist, None


def _sector_dist(gdist: Mapping[str, float]) -> dict[str, float]:
    """The group distribution as NAICS sectors (31, 32, 33 back into 31-33), for the meter."""
    out: dict[str, float] = {}
    for g, p in gdist.items():
        s = "31-33" if g in ("31", "32", "33") else g
        out[s] = out.get(s, 0.0) + p
    return out


def _boundary(i) -> str:
    """The Census text that says where an industry stops: what it is not (and where that goes), and what is
    sent to it. Only the Census's own words, cut to REFINE_BOUNDARY_ITEMS items of REFINE_BOUNDARY_CHARS."""
    cut = lambda t: t if len(t) <= REFINE_BOUNDARY_CHARS else t[: REFINE_BOUNDARY_CHARS - 1].rstrip() + "…"
    parts = []
    if i.excludes:
        parts.append("Not this industry: " + "; ".join(f"{cut(t)} (NAICS {c})" for c, t in i.excludes[:REFINE_BOUNDARY_ITEMS]) + ".")
    if i.sent_here:
        parts.append("Belongs here: " + "; ".join(cut(t) for t in i.sent_here[:REFINE_BOUNDARY_ITEMS]) + ".")
    return " ".join(parts)


def verify_questions(codes: list[str], variant: str, boundary: bool = False) -> dict[str, Any]:
    pick_text = ("Which NAICS industry in `candidates` best describes the primary activity of the business in "
                 "`ops_description`? Use each industry's description, examples, index entries, and what it is not.")
    load_tree()
    data = load_search_data()

    def crit(order: list[str]) -> dict[str, str]:
        return {c: f"{data.industries[c].title} (NAICS {c}). {data.industries[c].description[:400]}"
                + (" " + _boundary(data.industries[c]) if boundary else "") for c in order}

    out: dict[str, Any] = {PICK: Choice(instructions=pick_text, criteria=crit(codes))}
    if variant == "verify":
        for c in codes:
            out["fit_" + c] = Noul(
                instructions=(f"Does the NAICS industry in `candidates.{c}` describe the primary activity of the "
                              "business in `ops_description`?"),
                criteria={"true": "Yes: this industry is what the business primarily does.",
                          "false": "No: the business primarily does something else."})
        return out
    out[PICK_REVERSED] = Choice(instructions=pick_text, criteria=crit(list(reversed(codes))))
    for c in codes:
        out[fit_score_id(c)] = Score(
            instructions=(f"How well does the NAICS industry in `candidates.{c}` describe the primary activity of the "
                          "business in `ops_description`?"),
            criteria=list(FIT_LEVELS))
    return out


def _level(view) -> int | None:
    if view is None or not isinstance(view.probabilities, Mapping):
        return None
    probs = view.probabilities
    if set(probs) != set(range(len(FIT_LEVELS))) or not all(_unit_ok(p) for p in probs.values()):
        return None
    if abs(sum(probs.values()) - 1.0) > SUM_TOLERANCE:  # not a distribution: fail closed like every Choice (E15 audit)
        return None
    expected = sum(level * p for level, p in probs.items())
    return min(range(len(FIT_LEVELS)), key=lambda level: abs(level - expected))


def _unit_ok(p) -> bool:
    from jev_naics._jev.gate import is_unit_probability

    return is_unit_probability(p)


@dataclass
class RefineResult:
    result: AssistResult
    variant: str
    wide: list[dict[str, Any]] = field(default_factory=list)  # the top of the wide pick
    verify: list[dict[str, Any]] = field(default_factory=list)  # per candidate: p, reversed p, level or fit
    agree: bool | None = None  # score/union: the two opposite-order picks agree
    usage: dict[str, int] = field(default_factory=dict)
    reading: list[dict[str, str]] = field(default_factory=list)  # lexicon terms read in the text, and their Census wording


def _picklist_for(codes: list[str], tree: NaicsTree) -> dict[str, Any]:
    """A curated pick-list when the codes are a listed pair, else one built from the candidates' own titles."""
    for p in assist.PICKLISTS:
        if set(codes[:2]) <= {c for c, _ in p["options"]}:
            return {"id": p["id"], "question": p["question"],
                    "options": [{"code": c, "label": label} for c, label in p["options"]]}
    return {"id": "candidates", "question": "Which of these is the business's main activity?",
            "options": [{"code": c, "label": tree.title(c)} for c in codes]}


def _add_usage(total: dict[str, int], judgment) -> None:
    for k, v in (getattr(judgment, "usage", None) or {}).items():
        total[k] = total.get(k, 0) + int(v)


class _Memo:
    """Answers a repeated identical question from the first answer, so a variant that runs two methods over the
    same wide call makes it once. Counts what actually went to the decider."""

    def __init__(self, inner: Decider) -> None:
        self.inner, self.seen, self.calls, self.usage = inner, {}, 0, {}

    def judge(self, state, questions):
        key = transcript_key("memo", state, questions)
        if key not in self.seen:
            self.seen[key] = self.inner.judge(state, questions)
            self.calls += 1
            _add_usage(self.usage, self.seen[key])
        return self.seen[key]


def _agree(description: str, *, decider: Decider, tree, data, strict: bool, lexicon: Lexicon | None = None,
           min_words: int = MIN_WORDS) -> RefineResult:
    started = time.perf_counter()
    memo = _Memo(decider)
    v = refine(description, decider=memo, variant="verify", tree=tree, data=data, strict=strict, lexicon=lexicon,
               min_words=min_words)
    s = refine(description, decider=memo, variant="scoreb", tree=tree, data=data, strict=strict, lexicon=lexicon,
               min_words=min_words)
    r = s.result
    x, y = v.result.naics, r.naics
    if r.status == "CODED" and x and x != y:
        r = AssistResult("AMBIGUOUS", picklist=_picklist_for([y, x], tree), tooltip_id=_tooltip(r.checks) if r.checks else None,
                         candidates=r.candidates, checks=r.checks, sector=r.sector, naics_partial=r.naics_partial,
                         partial_title=r.partial_title, level=r.level,
                         reason=f"The two methods coded different industries ({y} and {x}).")
    elif r.status != "CODED" and x and any(row["code"] == x and row.get("level") == 2 for row in s.verify):
        r = AssistResult("CODED", naics=x, naics_title=tree.title(x), naics_partial=x, partial_title=tree.title(x),
                         level=6, candidates=s.result.candidates, checks=r.checks, sector=r.sector,
                         reason=f"Verify coded {x} and the score step also judged it the primary activity.")
    r.calls, r.model, r.seconds = memo.calls, s.result.model, round(time.perf_counter() - started, 3)
    return RefineResult(r, "agree", wide=s.wide, verify=s.verify, agree=s.agree, usage=memo.usage, reading=s.reading)


def _cascade(description: str, *, decider: Decider, tree, data, strict: bool, boundary: bool = False,
             lexicon: Lexicon | None = None, min_words: int = MIN_WORDS) -> RefineResult:
    started = time.perf_counter()
    memo = _Memo(decider)
    v = refine(description, decider=memo, variant="verify", tree=tree, data=data, strict=strict, boundary=boundary,
               lexicon=lexicon, min_words=min_words)
    if v.result.status in ("CODED", "TOO_SHORT", "NEEDS_INFO", "ERROR"):
        out, source = v, v
    else:
        out = source = refine(description, decider=memo, variant="scoreb", tree=tree, data=data, strict=strict,
                              boundary=boundary, lexicon=lexicon, min_words=min_words)
    r = out.result
    r.calls, r.seconds = memo.calls, round(time.perf_counter() - started, 3)
    return RefineResult(r, "cascadeb" if boundary else "cascade", wide=source.wide, verify=source.verify,
                        agree=source.agree, usage=memo.usage, reading=v.reading)


def refine(description: str, *, decider: Decider, variant: str = "score", tree: NaicsTree | None = None,
           data: SearchData | None = None, strict: bool = False, boundary: bool = False,
           lexicon: Lexicon | None = None, min_words: int = MIN_WORDS) -> RefineResult:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {', '.join(VARIANTS)}; got {variant!r}")
    tree, data = tree or load_tree(), data or load_search_data()
    if variant in ("cascade", "cascadeb"):
        return _cascade(description, decider=decider, tree=tree, data=data, strict=strict,
                        boundary=boundary or variant == "cascadeb", lexicon=lexicon, min_words=min_words)
    if variant == "agree":
        return _agree(description, decider=decider, tree=tree, data=data, strict=strict, lexicon=lexicon,
                      min_words=min_words)
    started = time.perf_counter()
    usage: dict[str, int] = {}
    calls, model = 0, None

    reading = lexicon.read(description) if lexicon else None
    seen = [{"term": e.term, "census": e.census} for e in reading.hits] if reading else []
    text = reading.text if reading else description  # the user's words, then the Census wording for each term

    def done(r: AssistResult, **kw) -> RefineResult:
        r.calls, r.model, r.seconds = calls, model, round(time.perf_counter() - started, 3)
        return RefineResult(r, variant, usage=usage, reading=seen, **kw)

    if len(description.split()) < min_words and not (reading and reading.recognised):
        # Code-owned: too few words to judge; no call is made.
        return done(AssistResult("TOO_SHORT", tooltip_id="too_short",
                                 reason=f"Fewer than {min_words} words: nothing to check yet."))
    state = {"ops_description": text}
    try:
        w = Walk()
        first = {**wide_questions(tree, data), **assist._questions(),
                 UNDERSPECIFIED: level_questions(tree, w, [ROOT])[UNDERSPECIFIED]}
        j1 = decider.judge(state, first)
        calls, model = 1, str(j1.model) if j1.model else None
        _add_usage(usage, j1)
        nouls = j1.nouls if isinstance(j1.nouls, Mapping) else {}
        checks = _read_checks(j1, _unit(nouls.get(UNDERSPECIFIED)))
        ranked, per_block, gdist, broken = _wide(j1)
        sectors = _sector_dist(gdist) if gdist else None
        early = _gate(checks, _sector(sectors, tree)) if not broken else AssistResult(
            "ERROR", checks=checks, reason=broken)
        picked = ([cp for blk in per_block for cp in blk[:REFINE_PER_BLOCK]] if variant == "scoreb"
                  else ranked[:REFINE_TOP_K])
        top = [{"code": c, "title": tree.title(c), "wide": round(p, 4)} for c, p in picked]
        if early:
            return done(early, wide=top)
        lean = _sector(sectors, tree)
        base = dict(checks=checks, sector=lean)

        if variant == "wide":
            code, p = ranked[0]
            cands = [{"code": t["code"], "title": t["title"], "p": t["wide"], "path_score": None, "fit": None} for t in top]
            if p >= PROB_FULL_PATH and checks.single >= assist.NOUL_ASSIST_SINGLE:
                return done(AssistResult("CODED", naics=code, naics_title=tree.title(code), naics_partial=code,
                                         partial_title=tree.title(code), level=6, candidates=cands,
                                         reason=f"Wide pick {code} at {p:.2f}.", **base), wide=top)
            return done(_partial([t["code"] for t in top], [t["wide"] for t in top], lean, tree, checks,
                                 cands, base, "The wide pick was not sure of one industry."), wide=top)

        codes = [t["code"] for t in top]
        if variant == "union":
            walked = classify_full(text, decider=decider, tree=tree, strict=strict)
            calls += walked.calls
            codes = list(dict.fromkeys(codes + [f["code"] for f in walked.finalists] + [a["code"] for a in walked.alternatives]))
        vstate = {"ops_description": text, "candidates": {c: _profile(data, c) for c in codes}}
        j2 = decider.judge(vstate, verify_questions(codes, "verify" if variant == "verify" else "score", boundary))
        calls += 1
        model = str(j2.model) if j2.model else model
        _add_usage(usage, j2)
        choices = j2.choices if isinstance(j2.choices, Mapping) else {}
        dist = _checked(choices.get(PICK), codes)
        if dist is None:
            return done(AssistResult("ERROR", reason="The verify pick was malformed.", **base), wide=top)

        if variant == "verify":
            vn = j2.nouls if isinstance(j2.nouls, Mapping) else {}
            rows = [{"code": c, "title": tree.title(c), "p": dist[c], "fit": _unit(vn.get("fit_" + c)), "path_score": None}
                    for c in codes]
            rows.sort(key=lambda r: r["p"], reverse=True)
            best = rows[0]
            if best["fit"] is None:
                return done(AssistResult("ERROR", reason="A fit check had no usable answer.", **base), wide=top)
            if best["p"] >= PROB_SEARCH_PICK and best["fit"] >= NOUL_SEARCH_FIT_HOLD and checks.single >= assist.NOUL_ASSIST_SINGLE:
                return done(AssistResult("CODED", naics=best["code"], naics_title=best["title"], naics_partial=best["code"],
                                         partial_title=best["title"], level=6, candidates=rows[:3],
                                         reason=f"Verified {best['code']} at p {best['p']:.2f}; fit {best['fit']:.2f}.",
                                         **base), wide=top, verify=rows)
            pick = assist._picklist(rows)
            if pick:
                r = AssistResult("AMBIGUOUS", picklist=pick, tooltip_id=_tooltip(checks), candidates=rows[:3],
                                 reason="The text sits between codes that are commonly confused.", **base)
                return done(_with_partial(r, rows, lean, tree), wide=top, verify=rows)
            return done(_partial([r["code"] for r in rows], [r["p"] for r in rows], lean, tree, checks, rows[:3], base,
                                 "The verify step was not sure of one industry."), wide=top, verify=rows)

        rdist = _checked(choices.get(PICK_REVERSED), list(reversed(codes)))
        scores = j2.scores if isinstance(getattr(j2, "scores", None), Mapping) else {}
        levels = {c: _level(scores.get(fit_score_id(c))) for c in codes}
        if rdist is None or any(v is None for v in levels.values()):
            return done(AssistResult("ERROR", reason="A verify answer was malformed.", **base), wide=top)
        first_pick, second_pick = max(dist, key=dist.get), max(rdist, key=rdist.get)
        agree = first_pick == second_pick
        rows = [{"code": c, "title": tree.title(c), "p": dist[c], "p_reversed": rdist[c], "level": levels[c],
                 "fit": round(sum(k * v for k, v in scores[fit_score_id(c)].probabilities.items()) / 2, 4),
                 "path_score": None} for c in codes]
        rows.sort(key=lambda r: (r["level"], r["p"]), reverse=True)
        primary = [r["code"] for r in rows if r["level"] == 2]
        related = [r["code"] for r in rows if r["level"] == 1]
        cands = rows[:3]
        if len(primary) == 1 and agree and first_pick == primary[0] and checks.single >= assist.NOUL_ASSIST_SINGLE:
            code = primary[0]
            return done(AssistResult("CODED", naics=code, naics_title=tree.title(code), naics_partial=code,
                                     partial_title=tree.title(code), level=6, candidates=cands,
                                     reason=f"{code} is the only candidate judged the primary activity, and both "
                                            "option orders picked it.", **base), wide=top, verify=rows, agree=agree)
        offered = (primary if len(primary) >= 2 else primary + related)[:4]
        if len(offered) >= 2:
            r = AssistResult("AMBIGUOUS", picklist=_picklist_for(offered, tree), tooltip_id=_tooltip(checks),
                             candidates=cands, reason=("Several candidates fit: " if len(primary) >= 2 else
                                                       "The candidates only partly fit: ") + ", ".join(offered) + ".",
                             **base)
            return done(_with_partial(r, rows, lean, tree), wide=top, verify=rows, agree=agree)
        return done(_partial([r["code"] for r in rows if r["level"] >= 1] or [rows[0]["code"]], None, lean, tree,
                             checks, cands, base, "No candidate was judged the primary activity."),
                    wide=top, verify=rows, agree=agree)
    except ReplayMissing:
        if strict:
            raise
        return done(AssistResult("ERROR", reason="No recorded answer and no TypeSafe key."))
    except (TypeSafeError, ValueError):
        return done(AssistResult("ERROR", reason="A TypeSafe call failed or was unreadable."))


def _shown(codes: list[str], weights: list[float] | None, lean, tree: NaicsTree) -> dict[str, Any]:
    """The meter's level: the deepest node shared by the candidates (weighted ones covering PROB_FULL_PATH)."""
    covering = codes
    if weights is not None:
        covering, mass = [], 0.0
        for c, wgt in zip(codes, weights):
            covering.append(c)
            mass += wgt
            if mass >= PROB_FULL_PATH:
                break
        if mass < PROB_FULL_PATH:
            covering = []
    partial = _deepest_shared(covering, tree) if covering else None
    if partial is None and lean and lean["p"] >= PROB_FULL_PATH:
        partial = lean["code"]
    return dict(naics_partial=partial, partial_title=tree.title(partial) if partial else None,
                level=(4 if len(partial) == 4 else 2) if partial else None)


def _with_partial(r: AssistResult, rows, lean, tree) -> AssistResult:
    shown = _shown([x["code"] for x in rows[:2]], None, lean, tree)
    r.naics_partial, r.partial_title, r.level = shown["naics_partial"], shown["partial_title"], shown["level"]
    return r


def _partial(codes, weights, lean, tree, checks: Checks, cands, base, why) -> AssistResult:
    return AssistResult("PARTIAL", candidates=cands, tooltip_id=_tooltip(checks), reason=why,
                        **_shown(codes, weights, lean, tree), **base)


__all__ = ["REFINE_TRANSCRIPT", "RefineResult", "VARIANTS", "refine", "wide_questions", "wide_rank"]
