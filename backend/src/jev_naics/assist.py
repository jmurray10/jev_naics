"""Real-time assist for the description of operations: as a broker types, how specific a NAICS code the
text supports, the one thing to add when it is not enough, and a pick-list when it sits between codes
that are commonly confused. It never blocks a submission and never writes text: Jev answers yes/no
checks and closed Choices; code picks the status, the tooltip (fixed copy below), and the pick-list.

1. Request A, one call: the tree walk's first level (the sector Choice and its vagueness Noul, the same
   questions as full.py) plus the completeness checks (questions.py: ASSIST_QUESTIONS) and how the
   business delivers (ASSIST_DELIVERY, with a not_stated option).
2. If the text is not operations, not specific, or too vague or mixed, stop: NEEDS_INFO, with the sector
   it leans to when that sector is at PROB_FULL_PATH or more. No 6-digit code is ever returned here.
3. Otherwise the tree walk continues from Request A's beam to its finalists and final pick (full.py,
   with its measured gates), and code reads the outcome:
   CODED      the walk acted and one main activity is clear
   AMBIGUOUS  the walk held unsure and its top two finalists are a listed confusable pair: a pick-list
   PARTIAL    otherwise: the deepest level shared by the finalists that cover PROB_FULL_PATH of the final
              pick, reported as an industry group (4 digits) or a sector (2)
   ERROR      a call failed or an answer was missing or malformed (fail closed)
The bars reused from the tree walk (PROB_FULL_PATH, NOUL_FULL_UNDERSPECIFIED, the fit bar) are its
measured ones; the completeness bars are questions.py's NOUL_ASSIST_*.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from jev_naics._jev import Decider, ReplayMissing
from typesafe_sdk import Choice, Noul, TypeSafeError

from jev_naics.env import repo_root
from jev_naics.full import UNDERSPECIFIED, FullResult, Walk, _checked, _unit, advance, classify_full, \
    level_questions, node_question_id
from jev_naics.questions import (
    ASSIST_CONFIG_VERSION,
    ASSIST_DELIVERY,
    ASSIST_QUESTIONS,
    NOUL_ASSIST_ACTIVITY,
    NOUL_ASSIST_CUSTOMERS,
    NOUL_ASSIST_OPERATIONS,
    NOUL_ASSIST_SINGLE,
    NOUL_FULL_UNDERSPECIFIED,
    PROB_FULL_PATH,
)
from jev_naics.tree import ROOT, NaicsTree, load_tree, sector_of

ASSIST_TRANSCRIPT = repo_root() / "transcripts" / "naics-assist.json"
DELIVERY = "assist_delivery"
NOUL_FIELDS = {"assist_operations_text": "operations", "assist_states_activity": "activity",
               "assist_states_customers": "customers", "assist_single_activity": "single"}
# TOO_SHORT is code-owned and only refine.py emits it: too few words to judge is not a model outcome, an error,
# or vague text, so it has its own status and no call is made.
Status = Literal["CODED", "PARTIAL", "AMBIGUOUS", "NEEDS_INFO", "TOO_SHORT", "ERROR"]

# Fixed copy: Jev only decides which one applies. Shown one at a time, in _tooltip's order.
TOOLTIPS = {
    "too_short": "Add a few more words: say what the business does and for whom. Nothing is checked until the "
                 "description has at least three words.",
    "is_operations_text": "Describe what the business does, not how it earns revenue. For example: "
                          "'We install and maintain commercial HVAC systems.'",
    "states_activity": "What exactly does the business do or sell? Be specific: 'scheduling software for dental "
                       "offices' rather than 'software'.",
    "delivery_mode": "How is it delivered? For example: software customers subscribe to, work done for each "
                     "client, products you make, or products you resell.",
    "states_customers": "Who are the customers? For example: consumers, small businesses, hospitals, or "
                        "government agencies.",
    "single_primary_activity": "Which of these is the main activity? Lead with the one that brings in the most "
                               "revenue.",
}

# Commonly confused codes, from the batch disagreements (2026-10-05). PROVISIONAL: the questions and the
# option -> code mappings need underwriting sign-off. The broker's answer is the code (source user_pick).
PICKLISTS: list[dict[str, Any]] = [
    {"id": "541511_513210", "question": "Do customers subscribe to your own software product, or do you build "
     "software to each client's specifications?",
     "options": [("513210", "Customers subscribe to or license our own software product"),
                 ("541511", "We build software to each client's specifications")]},
    {"id": "it_services", "question": "Which best describes the work?",
     "options": [("541512", "Designing and implementing computer systems"),
                 ("541519", "Ongoing managed IT or security services"),
                 ("541690", "Technical or scientific advisory"),
                 ("541611", "Business or management advisory")]},
    {"id": "541810_541613", "question": "Do you create and place advertising, or advise on marketing strategy?",
     "options": [("541810", "We create and place advertising"),
                 ("541613", "We advise on marketing strategy")]},
]
# Make, sell to businesses, or sell to consumers: the codes come from the walk's own finalists.
TRADE_GROUPS = [("31-33", "We make the products"), ("42", "We sell them to businesses"),
                ("44-45", "We sell them to consumers")]
TRADE_QUESTION = "Do you make the products, sell them to businesses, or sell them to consumers?"


@dataclass(frozen=True)
class Checks:
    operations: float | None
    activity: float | None
    customers: float | None
    single: float | None
    underspecified: float | None
    delivery: str | None
    delivery_p: float | None = None


@dataclass
class AssistResult:
    status: Status
    naics: str | None = None
    naics_title: str | None = None
    naics_partial: str | None = None
    partial_title: str | None = None
    level: int | None = None  # 6, 4, or 2
    candidates: list[dict[str, Any]] = field(default_factory=list)
    checks: Checks | None = None
    sector: dict[str, Any] | None = None  # the sector Jev leans to, with its probability (diagnostic)
    tooltip_id: str | None = None
    picklist: dict[str, Any] | None = None
    reason: str = ""
    model: str | None = None
    calls: int = 0
    seconds: float = 0.0
    config_version: str = ASSIST_CONFIG_VERSION

    @property
    def tooltip(self) -> str | None:
        return TOOLTIPS.get(self.tooltip_id) if self.tooltip_id else None


def _questions() -> dict[str, Choice | Noul]:
    out: dict[str, Choice | Noul] = {
        q: Noul(instructions=text, criteria={"true": yes, "false": no}) for q, (text, yes, no) in ASSIST_QUESTIONS.items()}
    out[DELIVERY] = Choice(instructions="How does the business in `ops_description` deliver what it sells?",
                           criteria=dict(ASSIST_DELIVERY))
    return out


def _tooltip(c: Checks) -> str | None:
    if c.operations is not None and c.operations < NOUL_ASSIST_OPERATIONS:
        return "is_operations_text"
    if c.activity is not None and c.activity < NOUL_ASSIST_ACTIVITY:
        return "states_activity"
    if c.delivery == "not_stated":
        return "delivery_mode"
    # Several activities before customers: on the mixed dev cases (2026-10-05) the customers check also failed,
    # but naming the main activity is what the text lacked.
    if c.single is not None and c.single < NOUL_ASSIST_SINGLE:
        return "single_primary_activity"
    if c.customers is not None and c.customers < NOUL_ASSIST_CUSTOMERS:
        return "states_customers"
    return None


def _sector(sector: Mapping[str, float] | None, tree: NaicsTree) -> dict[str, Any] | None:
    if not sector:
        return None
    code = max(sector, key=sector.get)
    return {"code": code, "title": tree.title(code), "p": sector[code]}


def _deepest_shared(codes: list[str], tree: NaicsTree) -> str | None:
    """The deepest node every code descends from, reported as an industry group or a sector."""
    paths = [tree.path(c) for c in codes]
    shared = None
    for level in zip(*paths):
        if len(set(level)) != 1:
            break
        shared = level[0]
    if shared is None:
        return None
    if len(shared) >= 4 and "-" not in shared:
        return shared[:4]
    return sector_of(shared) if "-" not in shared else shared


def _picklist(finalists: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = sorted((f for f in finalists if f.get("p") is not None), key=lambda f: f["p"], reverse=True)
    if len(ranked) < 2:
        return None
    top = {ranked[0]["code"], ranked[1]["code"]}
    for p in PICKLISTS:
        if top <= {code for code, _ in p["options"]}:
            return {"id": p["id"], "question": p["question"],
                    "options": [{"code": code, "label": label} for code, label in p["options"]]}
    groups = {sector_of(c) for c in top}
    trade = {g for g, _ in TRADE_GROUPS}
    if groups <= trade and len(groups) == 2:
        options = []
        for group, label in TRADE_GROUPS:
            best = next((f["code"] for f in ranked if sector_of(f["code"]) == group), None)
            if best:
                options.append({"code": best, "label": label})
        return {"id": "make_or_resell", "question": TRADE_QUESTION, "options": options}
    return None


def _gate(checks: Checks, lean: dict[str, Any] | None) -> AssistResult | None:
    """Before the walk: a missing answer is an error; text that is not operations, not specific, or too
    vague or mixed needs more information. None means the walk should run."""
    needed = (checks.operations, checks.activity, checks.customers, checks.single, checks.underspecified,
              checks.delivery)
    if any(x is None for x in needed) or lean is None:
        return AssistResult("ERROR", checks=checks, reason="A check had no usable answer; nothing is shown.")
    if (checks.operations < NOUL_ASSIST_OPERATIONS or checks.activity < NOUL_ASSIST_ACTIVITY
            or checks.underspecified >= NOUL_FULL_UNDERSPECIFIED):
        shown = lean["p"] >= PROB_FULL_PATH
        return AssistResult("NEEDS_INFO", checks=checks, sector=lean, tooltip_id=_tooltip(checks) or "states_activity",
                            naics_partial=lean["code"] if shown else None,
                            partial_title=lean["title"] if shown else None, level=2 if shown else None,
                            reason="The text does not yet say enough to assign a code.")
    return None


def decide(checks: Checks, sector: Mapping[str, float] | None, walked: FullResult | None,
           tree: NaicsTree | None = None) -> AssistResult:
    """The status, tooltip, and pick-list from Request A's checks and (when it ran) the tree walk."""
    tree = tree or load_tree()
    lean = _sector(sector, tree)
    early = _gate(checks, lean)
    if early:
        return early
    if walked is None or (walked.gate == "hold" and walked.hold_reason in ("service_error", "invalid_answer")):
        return AssistResult("ERROR", checks=checks, sector=lean, reason="The tree walk failed; nothing is shown.")
    candidates = sorted(({"code": f["code"], "title": f["title"], "p": f.get("p"), "path_score": f.get("path_score"),
                          "fit": f.get("fit")} for f in walked.finalists),
                        key=lambda f: -1 if f["p"] is None else f["p"], reverse=True)[:3]
    base = dict(checks=checks, sector=lean, candidates=candidates)
    if walked.gate == "auto_act" and checks.single >= NOUL_ASSIST_SINGLE:
        return AssistResult("CODED", naics=walked.code, naics_title=tree.title(walked.code),
                            naics_partial=walked.code, partial_title=tree.title(walked.code), level=6,
                            reason=walked.reason, **base)
    if walked.gate == "hold" and walked.hold_reason == "too_vague":
        return AssistResult("NEEDS_INFO", tooltip_id=_tooltip(checks) or "states_activity",
                            reason="The text does not yet say enough to assign a code.", **base)
    covering, mass = [], 0.0
    for c in candidates if candidates and candidates[0]["p"] is not None else []:
        covering.append(c["code"])
        mass += c["p"]
        if mass >= PROB_FULL_PATH:
            break
    partial = _deepest_shared(covering, tree) if mass >= PROB_FULL_PATH else None
    if partial is None and lean["p"] >= PROB_FULL_PATH:
        partial = lean["code"]  # nothing deeper is shared: the sector, when Jev is sure of it
    shown = dict(naics_partial=partial, partial_title=tree.title(partial) if partial else None,
                 level=(4 if len(partial) == 4 else 2) if partial else None)
    pick = _picklist(walked.finalists) if walked.gate == "hold" else None
    if pick:
        return AssistResult("AMBIGUOUS", picklist=pick, tooltip_id=_tooltip(checks),
                            reason="The text sits between codes that are commonly confused.", **shown, **base)
    return AssistResult("PARTIAL", **shown,
                        tooltip_id=_tooltip(checks) or ("single_primary_activity" if walked.gate == "auto_act" else None),
                        reason=walked.reason, **base)


def _read_checks(judgment, underspecified: float | None) -> Checks:
    nouls = judgment.nouls if isinstance(judgment.nouls, Mapping) else {}
    choices = judgment.choices if isinstance(judgment.choices, Mapping) else {}
    values = {f: _unit(nouls.get(q)) for q, f in NOUL_FIELDS.items()}
    dist = _checked(choices.get(DELIVERY), list(ASSIST_DELIVERY))
    delivery = max(dist, key=dist.get) if dist else None
    return Checks(**values, underspecified=underspecified, delivery=delivery,
                  delivery_p=dist[delivery] if dist else None)


def assess(description: str, *, decider: Decider, tree: NaicsTree | None = None, strict: bool = False) -> AssistResult:
    tree = tree or load_tree()
    started = time.perf_counter()
    w = Walk()

    def finish(r: AssistResult, model: str | None) -> AssistResult:
        r.calls, r.model, r.seconds = w.calls, model, round(time.perf_counter() - started, 3)
        return r

    try:
        questions = {**level_questions(tree, w, [ROOT]), **_questions()}
        judgment = decider.judge({"ops_description": description}, questions)
        advance(w, tree, [ROOT], judgment)
        checks = _read_checks(judgment, w.underspecified)
        choices = judgment.choices if isinstance(judgment.choices, Mapping) else {}
        sector = _checked(choices.get(node_question_id(ROOT)), tree.children(ROOT))
        first = _gate(checks, _sector(sector, tree)) if not w.failed else AssistResult(
            "ERROR", checks=checks, reason="The sector answer was malformed; nothing is shown.")
        if first:
            return finish(first, w.model)
        walked = classify_full(description, decider=decider, tree=tree, strict=strict, into=w)
        return finish(decide(checks, sector, walked, tree), walked.model or w.model)
    except ReplayMissing:
        if strict:
            raise
        return finish(AssistResult("ERROR", reason="No recorded answer and no TypeSafe key."), w.model)
    except (TypeSafeError, ValueError):
        return finish(AssistResult("ERROR", reason="A TypeSafe call failed or was unreadable."), w.model)


__all__ = ["ASSIST_TRANSCRIPT", "AssistResult", "Checks", "PICKLISTS", "TOOLTIPS", "assess", "decide"]
