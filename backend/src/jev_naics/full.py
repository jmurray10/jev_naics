"""Full NAICS 2022 classification (JEV-48): walk the Census tree with a beam of paths.

Each step is a Choice over one node's children, described by their official titles and
descriptions, so Jev can only answer with real NAICS codes. The beam keeps the best
FULL_BEAM_WIDTH paths by the geometric mean of their edge probabilities (a lone finalist is
gated on its joint probability instead); all nodes of a level
are asked together (parallel questions in one call). A node with one child needs no question.

The beam's leaves are finalists. One final Choice picks among them with full context (the
official description plus Census index terms), and a fit Noul per finalist asks whether that
industry describes the business, all in one call. The walk finds candidates; the final pick
decides. (Added 2026-09-29: on the first live run the right industry was a runner-up for two
of the three misses, e.g. pet care behind "other personal care".)

Code decides, never the model:
- too_vague        the description is too vague or mixed (a Noul, asked with the first level)
- invalid_answer   any malformed answer, on any branch, or a missing Noul
- low_probability  the final pick's own probability is below PROB_FULL_PATH
- misfit           Jev does not think the picked industry describes the business (its fit Noul)
- service_error    a call failed
Otherwise the picked industry is the answer. The walk's path score is reported, not gated.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from jev_naics._jev.gate import is_unit_probability
from jev_naics._jev import Decider, Judgment, ReplayMissing
from typesafe_sdk import Choice, Noul, TypeSafeError

from jev_naics.env import repo_root
from jev_naics.questions import (
    FULL_BEAM_WIDTH,
    NOUL_FULL_FIT_HOLD,
    NOUL_FULL_UNDERSPECIFIED,
    PROB_FULL_PATH,
)
from jev_naics.tree import ROOT, NaicsTree, load_tree

FULL_TRANSCRIPT = repo_root() / "transcripts" / "naics-full.json"
UNDERSPECIFIED = "full_underspecified"
FINAL = "full_final"
SUM_TOLERANCE = 0.02
CRITERION_CHARS = 520  # long enough to keep most "except ..." clauses of the official text
FINALIST_INDEX_TERMS = 8  # the first Census index terms shown with each finalist
HoldReason = Literal["too_vague", "invalid_answer", "low_probability", "misfit", "service_error", "no_match"]
LEVEL_NAMES = {2: "sector", 3: "subsector", 4: "industry group", 5: "NAICS industry", 6: "national industry"}


def level_name(code: str) -> str:
    return "sector" if "-" in code else LEVEL_NAMES.get(len(code), "industry")


def node_question_id(node: str) -> str:
    return "node_" + node.replace("-", "_")


def fit_question_id(code: str) -> str:
    return "fit_" + code


@dataclass(frozen=True)
class Path:
    codes: tuple[str, ...]
    edges: tuple[float, ...] = ()

    @property
    def node(self) -> str:
        return self.codes[-1] if self.codes else ROOT

    @property
    def score(self) -> float:
        # Beam ranking: the geometric mean of the edges, as in TypeSafe's hierarchical-
        # classification cookbook. (Ranking by joint probability was tried 2026-09-29: it dropped
        # the veterinary clinic's right branch, which starts at p 0.12, from the beam.)
        return math.exp(sum(math.log(p) for p in self.edges) / len(self.edges)) if self.edges else 1.0

    @property
    def joint(self) -> float:
        # The path's joint probability. Used where a path decides on its own (a single finalist),
        # because a geometric mean dilutes a weak early edge (gate audit, 2026-09-29).
        return math.exp(sum(math.log(p) for p in self.edges)) if self.edges else 1.0


@dataclass
class FullResult:
    gate: Literal["auto_act", "hold"]
    code: str | None
    title: str | None
    probability: float | None  # the final pick's own probability (gated)
    path_score: float | None  # the walk's geometric-mean score for the picked path (reported)
    path: list[str]  # sector to industry for the answer; empty on a hold
    hold_reason: HoldReason | None
    reason: str
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    model_path: list[str] = field(default_factory=list)  # Jev's best path on a hold: diagnostic only
    finalists: list[dict[str, Any]] = field(default_factory=list)
    underspecified_noul: float | None = None
    fit_noul: float | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    calls: int = 0
    seconds: float = 0.0
    fallback: bool = False  # hybrid (JEV-53): the tree walk ran because search held


def _criterion(tree: NaicsTree, code: str) -> str:
    text = f"{tree.title(code)} (NAICS {code}). {tree.description(code)}"
    return text if len(text) <= CRITERION_CHARS else text[: CRITERION_CHARS - 1].rstrip() + "…"


def _node_question(tree: NaicsTree, node: str) -> Choice:
    children = tree.children(node)
    return Choice(
        instructions=(
            f"Which NAICS {level_name(children[0])} best describes the primary activity of the business in "
            "`ops_description`? Judge the primary activity, not a side line."
        ),
        criteria={code: _criterion(tree, code) for code in children},
    )


def _fit_question(code: str) -> Noul:
    return Noul(
        instructions=(f"Does the NAICS industry in `finalists.{code}` describe the primary activity of the "
                      "business in `ops_description`?"),
        criteria={"true": "Yes: this industry is what the business primarily does.",
                  "false": "No: the business primarily does something else."},
    )


def _checked(view, options: list[str]) -> dict[str, float] | None:
    if view is None or not isinstance(view.probabilities, Mapping) or not isinstance(view.choice, str):
        return None
    dist = view.probabilities
    parts = [dist.get(o) for o in options]
    if (set(dist) != set(options) or not all(is_unit_probability(x) for x in parts)
            or abs(sum(parts) - 1.0) > SUM_TOLERANCE or view.choice not in options
            or dist[view.choice] != max(parts)):
        return None
    return {o: dist[o] for o in options}


def _unit(view) -> float | None:
    return view.noul if view is not None and is_unit_probability(view.noul) else None


def _descend(tree: NaicsTree, path: Path) -> Path:
    """Follow single-child nodes: they need no question and add no edge."""
    while not tree.is_leaf(path.node) and len(tree.children(path.node)) == 1:
        path = Path(path.codes + (tree.children(path.node)[0],), path.edges)
    return path


@dataclass
class Walk:
    """The tree walk so far: the beam, what each level asked, and the calls made (kept on a failure)."""

    beam: list[Path] = field(default_factory=lambda: [Path(())])
    trace: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    model: str | None = None
    underspecified: float | None = None
    failed: str | None = None  # why nothing in the walk is usable (a malformed answer)


def level_questions(tree: NaicsTree, w: Walk, open_nodes: list[str]) -> dict[str, Choice | Noul]:
    """One level's questions: a Choice per open node, plus the vagueness Noul on the first level."""
    questions: dict[str, Choice | Noul] = {node_question_id(n): _node_question(tree, n) for n in open_nodes}
    if not w.trace:
        questions[UNDERSPECIFIED] = Noul(
            instructions=("Is `ops_description` too vague, or does it mix unrelated lines of business, so "
                          "that one NAICS industry cannot be assigned safely?"),
            criteria={"true": "Too vague or mixed to assign one industry.",
                      "false": "Clear enough to assign one industry."})
    return questions


def advance(w: Walk, tree: NaicsTree, open_nodes: list[str], judgment: Judgment) -> bool:
    """Apply one level's answers to the beam. False when nothing in the walk is usable (w.failed says why)."""
    w.calls += 1
    w.model = str(judgment.model) if judgment.model else w.model
    choices = judgment.choices if isinstance(judgment.choices, Mapping) else {}
    if not w.trace:
        w.underspecified = _unit((judgment.nouls or {}).get(UNDERSPECIFIED)
                                 if isinstance(judgment.nouls, Mapping) else None)
    asked, dists = [], {}
    for node in open_nodes:
        options = tree.children(node)
        dist = _checked(choices.get(node_question_id(node)), options)
        dists[node] = dist
        top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:5] if dist else []
        asked.append({"node": node, "title": "All industries" if node == ROOT else tree.title(node),
                      "options": options, "valid": dist is not None,
                      "top": [{"code": c, "title": tree.title(c), "p": p} for c, p in top]})
    if any(dist is None for dist in dists.values()):
        # Dropping a malformed branch and acting on the rest would decide on a partial answer.
        w.trace.append({"level": len(w.trace) + 1, "asked": asked, "beam": []})
        w.beam, w.failed = [], "An answer at this level was malformed; nothing is acted on."
        return False
    candidates = [p for p in w.beam if tree.is_leaf(p.node)]
    for p in w.beam:
        if tree.is_leaf(p.node) or dists.get(p.node) is None:
            continue
        ranked = sorted(dists[p.node].items(), key=lambda kv: kv[1], reverse=True)[:FULL_BEAM_WIDTH]
        candidates.extend(_descend(tree, Path(p.codes + (child,), p.edges + (prob,)))
                          for child, prob in ranked if prob > 0)
    w.beam = sorted(candidates, key=lambda p: p.score, reverse=True)[:FULL_BEAM_WIDTH]
    w.trace.append({"level": len(w.trace) + 1, "asked": asked,
                    "beam": [{"path": list(p.codes), "score": round(p.score, 4)} for p in w.beam]})
    if not w.beam:
        w.failed = "Every answer at this level was malformed; nothing to act on."
        return False
    return True


def walk(description: str, *, decider: Decider, tree: NaicsTree, into: Walk | None = None) -> Walk:
    """Walk the tree to the beam's finalists: one call per level, every open node asked in parallel.
    A walk passed in `into` continues from its beam. TypeSafe and replay errors propagate; the caller
    decides how to hold."""
    w = into if into is not None else Walk()
    state = {"ops_description": description}
    while not w.failed and any(not tree.is_leaf(p.node) for p in w.beam):
        open_nodes = sorted({p.node for p in w.beam if not tree.is_leaf(p.node)})
        judgment = decider.judge(state, level_questions(tree, w, open_nodes))
        if not advance(w, tree, open_nodes, judgment):
            return w
    return w


def classify_full(description: str, *, decider: Decider, tree: NaicsTree | None = None,
                  strict: bool = False, into: Walk | None = None) -> FullResult:
    """`into`: a walk already begun (assist.py asks the first level itself); it continues from its beam."""
    tree = tree or load_tree()
    started = time.perf_counter()
    w = into if into is not None else Walk()

    def done(**kwargs) -> FullResult:
        return FullResult(trace=w.trace, model=w.model, calls=w.calls,
                          seconds=round(time.perf_counter() - started, 3), underspecified_noul=w.underspecified,
                          **kwargs)

    def hold(reason: HoldReason, why: str, picked: Path | None = None, **kwargs) -> FullResult:
        # A hold carries no usable code: `path` stays empty; Jev's best path is diagnostic only.
        return done(gate="hold", code=None, title=None, hold_reason=reason, reason=why,
                    probability=kwargs.pop("probability", None), path_score=picked.score if picked else None,
                    path=[], model_path=list(picked.codes) if picked else [], **kwargs)

    try:
        walk(description, decider=decider, tree=tree, into=w)
        if w.failed:
            return hold("invalid_answer", w.failed)
        beam, underspecified = w.beam, w.underspecified

        # ---- the final pick among the finalists, with a fit check on each ----
        finalists = list({p.node: p for p in beam}.values())
        by_code = {p.node: p for p in finalists}
        codes = list(by_code)
        alternatives = [{"code": p.node, "title": tree.title(p.node), "path_score": round(p.score, 4),
                         "path": list(p.codes)} for p in finalists]
        if underspecified is None:
            return hold("invalid_answer", "The vagueness check had no usable answer.", alternatives=alternatives)
        if underspecified >= NOUL_FULL_UNDERSPECIFIED:
            return hold("too_vague", f"The description is too vague or mixed (Noul {underspecified:.2f}).",
                        finalists[0], alternatives=alternatives)
        profiles = {c: {"code": c, "title": tree.title(c), "description": tree.description(c),
                        "index_terms": tree.index_terms(c, limit=FINALIST_INDEX_TERMS)} for c in codes}
        final_questions: dict[str, Choice | Noul] = {fit_question_id(c): _fit_question(c) for c in codes}
        if len(codes) > 1:
            final_questions[FINAL] = Choice(
                instructions=("Which NAICS industry in `finalists` best describes the primary activity of the "
                              "business in `ops_description`? Use each industry's description and index terms."),
                criteria={c: _criterion(tree, c) for c in codes})
        final = decider.judge({"ops_description": description, "finalists": profiles}, final_questions)
        w.calls += 1
        w.model = str(final.model) if final.model else w.model
        if len(codes) > 1:
            dist = _checked((final.choices or {}).get(FINAL) if isinstance(final.choices, Mapping) else None, codes)
            if dist is None:
                return hold("invalid_answer", "The final pick was malformed; nothing to act on.",
                            alternatives=alternatives)
        else:
            dist = {codes[0]: by_code[codes[0]].joint}
        nouls = final.nouls if isinstance(final.nouls, Mapping) else {}
        rows = [{"code": c, "title": tree.title(c), "p": dist[c], "path_score": round(by_code[c].score, 4),
                 "fit": _unit(nouls.get(fit_question_id(c)))} for c in codes]
        picked_code = max(dist, key=dist.get)
        picked, probability = by_code[picked_code], dist[picked_code]
        fit = next(r["fit"] for r in rows if r["code"] == picked_code)
        common = dict(alternatives=alternatives, finalists=rows, probability=probability, fit_noul=fit)
        if probability < PROB_FULL_PATH:
            return hold("low_probability", f"The final pick {picked_code} has p {probability:.2f}, below "
                        f"{PROB_FULL_PATH}.", picked, **common)
        if fit is None:
            return hold("invalid_answer", "The fit check had no usable answer.", picked, **common)
        if fit < NOUL_FULL_FIT_HOLD:
            return hold("misfit", f"Jev doubts {picked_code} describes this business (fit {fit:.2f}).", picked,
                        **common)
        return done(gate="auto_act", code=picked_code, title=tree.title(picked_code), hold_reason=None,
                    reason=f"Picked {picked_code} at p {probability:.2f}; fit {fit:.2f}.",
                    path_score=picked.score, path=list(picked.codes), **common)
    except ReplayMissing:
        if strict:
            raise
        return hold("service_error", "No recorded answer and no TypeSafe key.")
    except (TypeSafeError, ValueError):
        # A failed call, or a response the runtime could not read. Anything else is a bug in
        # this code and raises, rather than hiding as a service error.
        return hold("service_error", "A TypeSafe call failed or was unreadable; fail closed.")
