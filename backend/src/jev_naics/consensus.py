"""Consensus: run the hybrid and the tree walk, accept when they agree, else Jev judges once.

On a private sample of real applications (2026-10-05, jev-1.13.0; the data and its results are not
published), the hybrid and the tree walk rarely disagreed when both acted. Agreement is strong evidence; a
lone or split answer is not.

1. Both methods run on the description (sequentially: a recording decider writes one file).
2. Both act on the same code: that code is the answer, with no further call (step "agreement").
3. Either one held as too vague, on a malformed answer, or on a service error: hold with that reason.
   Vagueness and failures are never judged away (step "hold").
4. Otherwise (they picked different codes, one acted and one held unsure, or both held unsure): Jev
   picks once over every industry either method considered, its answer and its search results or
   finalists, with search's questions and gates (search_pick). The closed set is built here, in code;
   hold stays code-owned and is never an option (step "judge").
Fewer than two real candidates hold without a call: there is nothing to compare.

Triage (classify_triage, /v1/triage) is the broker-facing variant: the two methods run in parallel, and a
lone tree-walk answer is accepted as well as agreement (the tree's own gates already passed; a hybrid that
held on a malformed answer or a service error is a failure, not "no answer", and holds). Everything else
is judged as above; a judge hold, or a vague, malformed, or failed method, goes back to the broker.

Tree-first (classify_tree_first, /v1/tree-first) makes the tree walk the default and uses the hybrid
only as a check, with no judge: the tree's code stands when the hybrid agrees or held unsure (low
probability, misfit, no match). It goes to the broker when the hybrid picked a different code
(methods_disagree), found the description too vague, or failed, and whenever the tree walk held. On the
same private review this rule set acted on fewer descriptions than the tree alone, at a higher defensible
rate (the hybrid's vagueness check removes most of the tree's arguable one-liners).

The judge adds no threshold of its own: it is search's pick (PROB_SEARCH_PICK, NOUL_SEARCH_FIT_HOLD,
NOUL_SEARCH_UNDERSPECIFIED) over a union of at most SEARCH_K + 2 * FULL_BEAM_WIDTH + 2 industries,
the same shape as the hybrid's lightly measured union pick (questions.py).
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from jev_naics._jev import Decider, Judgment, RecordingDecider, RecordMissingDecider

from jev_naics.db import Hit
from jev_naics.env import repo_root
from jev_naics.full import FullResult, HoldReason, classify_full
from jev_naics.hybrid import classify_hybrid
from jev_naics.search_data import SearchData, load_search_data
from jev_naics.search_pick import Searcher, classify_search
from jev_naics.tree import NaicsTree, load_tree

CONSENSUS_TRANSCRIPT = repo_root() / "transcripts" / "naics-consensus.json"
# Holds the judge must not overturn: a vague description, a malformed answer, a failed call.
NO_JUDGE = frozenset({"too_vague", "invalid_answer", "service_error"})
# A method that failed has not answered "no": it is never treated as a hold the other method can overrule.
FAILED = frozenset({"invalid_answer", "service_error"})
Step = Literal["agreement", "tree_only", "judge", "hold", "broker"]
# Why a result went back to the broker: a method's own hold reason, or the two methods disagreeing.
BrokerReason = HoldReason | Literal["methods_disagree"]


@dataclass
class ConsensusResult:
    result: FullResult  # the answer or the hold, with the judge's finalists when it ran
    step: Step  # which step produced it, for audit
    hybrid: FullResult
    tree: FullResult
    candidates: list[dict[str, Any]] = field(default_factory=list)  # what the judge was offered


def _considered(r: FullResult) -> list[str]:
    """The method's answer, then every industry it weighed: search results or tree finalists."""
    return ([r.code] if r.code else []) + [f["code"] for f in r.finalists] + [a["code"] for a in r.alternatives]


def decide(description: str, hybrid: FullResult, tree: FullResult, *, decider: Decider,
           data: SearchData | None = None, strict: bool = False) -> ConsensusResult:
    data = data or load_search_data()
    calls = hybrid.calls + tree.calls
    model = hybrid.model or tree.model

    def held(reason: HoldReason, why: str) -> ConsensusResult:
        r = FullResult(gate="hold", code=None, title=None, probability=None, path_score=None, path=[],
                       hold_reason=reason, reason=why, model=model, calls=calls)
        return ConsensusResult(r, "hold", hybrid, tree)

    if hybrid.gate == tree.gate == "auto_act" and hybrid.code == tree.code:
        why = (f"The hybrid and the tree walk both picked {hybrid.code} (p {hybrid.probability:.2f} and "
               f"{tree.probability:.2f}).")
        return ConsensusResult(dataclasses.replace(hybrid, reason=why, calls=calls), "agreement", hybrid, tree)
    for name, r in (("hybrid", hybrid), ("tree walk", tree)):
        if r.gate == "hold" and r.hold_reason in NO_JUDGE:
            return held(r.hold_reason, f"The {name} held ({r.hold_reason}): {r.reason}")

    return _judge(description, hybrid, tree, decider=decider, data=data, strict=strict, held=held)


def _judge(description: str, hybrid: FullResult, tree: FullResult, *, decider: Decider, data: SearchData,
           strict: bool, held) -> ConsensusResult:
    """One Jev pick over every industry either method considered (the closed set), with search's gates."""
    calls = hybrid.calls + tree.calls
    model = hybrid.model or tree.model
    union = list(dict.fromkeys(c for c in _considered(hybrid) + _considered(tree) if c in data.industries))
    if len(union) < 2:
        return held("low_probability", f"The methods considered {len(union)} real industry: nothing to compare.")
    offered = {"hybrid": set(_considered(hybrid)), "tree": set(_considered(tree))}
    hits = [Hit(code=c, title=data.industries[c].title, rank=0.0, snippet="") for c in union]
    judged = classify_search(description, decider=decider, searcher=lambda _text, _limit: hits, data=data,
                             strict=strict, k=len(union))
    # The judge's candidates have no search rank or score: they came from the two methods.
    rows = [{"code": f["code"], "title": f["title"], "p": f["p"], "fit": f["fit"],
             "sources": [s for s in ("hybrid", "tree") if f["code"] in offered[s]],
             "hybrid_code": f["code"] == hybrid.code, "tree_code": f["code"] == tree.code}
            for f in judged.finalists]
    split = (f"the hybrid {'picked ' + hybrid.code if hybrid.code else 'held (' + str(hybrid.hold_reason) + ')'}, "
             f"the tree walk {'picked ' + tree.code if tree.code else 'held (' + str(tree.hold_reason) + ')'}")
    reason = re.sub(r" \(search rank \d+\)", "", judged.reason)
    result = dataclasses.replace(judged, reason=f"Judged because {split}: {reason}", finalists=rows,
                                 calls=calls + judged.calls, model=judged.model or model)
    return ConsensusResult(result, "judge", hybrid, tree, rows)


def classify_consensus(description: str, *, decider: Decider, searcher: Searcher, data: SearchData | None = None,
                       tree: NaicsTree | None = None, strict: bool = False) -> ConsensusResult:
    data, naics = data or load_search_data(), tree or load_tree()
    started = time.perf_counter()
    hybrid = classify_hybrid(description, decider=decider, searcher=searcher, data=data, tree=naics, strict=strict)
    walked = classify_full(description, decider=decider, tree=naics, strict=strict)
    out = decide(description, hybrid, walked, decider=decider, data=data, strict=strict)
    out.result = dataclasses.replace(out.result, seconds=round(time.perf_counter() - started, 3))
    return out


def triage(description: str, hybrid: FullResult, tree: FullResult, *, decider: Decider,
           data: SearchData | None = None, strict: bool = False) -> ConsensusResult:
    data = data or load_search_data()
    calls = hybrid.calls + tree.calls
    model = hybrid.model or tree.model

    def broker(reason: HoldReason, why: str) -> ConsensusResult:
        r = FullResult(gate="hold", code=None, title=None, probability=None, path_score=None, path=[],
                       hold_reason=reason, reason=why, model=model, calls=calls)
        return ConsensusResult(r, "broker", hybrid, tree)

    if hybrid.gate == tree.gate == "auto_act" and hybrid.code == tree.code:
        why = (f"The hybrid and the tree walk both picked {hybrid.code} (p {hybrid.probability:.2f} and "
               f"{tree.probability:.2f}).")
        return ConsensusResult(dataclasses.replace(hybrid, reason=why, calls=calls), "agreement", hybrid, tree)
    if hybrid.gate == "hold" and hybrid.hold_reason in FAILED:
        return broker(hybrid.hold_reason, f"The hybrid held ({hybrid.hold_reason}): {hybrid.reason}")
    if hybrid.gate == "hold" and tree.gate == "auto_act":
        why = f"Only the tree walk answered (the hybrid held: {hybrid.hold_reason}): {tree.reason}"
        return ConsensusResult(dataclasses.replace(tree, reason=why, calls=calls), "tree_only", hybrid, tree)
    for name, r in (("hybrid", hybrid), ("tree walk", tree)):
        if r.gate == "hold" and r.hold_reason in NO_JUDGE:
            return broker(r.hold_reason, f"The {name} held ({r.hold_reason}): {r.reason}")
    out = _judge(description, hybrid, tree, decider=decider, data=data, strict=strict, held=broker)
    if out.step == "judge" and out.result.gate == "hold":
        out.step = "broker"
    return out


def tree_first(hybrid: FullResult, tree: FullResult) -> ConsensusResult:
    calls = hybrid.calls + tree.calls

    def broker(reason: BrokerReason, why: str) -> ConsensusResult:
        r = FullResult(gate="hold", code=None, title=None, probability=None, path_score=None, path=[],
                       hold_reason=reason, reason=why, model=tree.model or hybrid.model, calls=calls)
        return ConsensusResult(r, "broker", hybrid, tree)

    if tree.gate == "hold":
        return broker(tree.hold_reason or "invalid_answer", f"The tree walk held ({tree.hold_reason}): {tree.reason}")
    if hybrid.gate == "auto_act" and hybrid.code == tree.code:
        why = (f"The tree walk and the hybrid both picked {tree.code} (p {tree.probability:.2f} and "
               f"{hybrid.probability:.2f}).")
        return ConsensusResult(dataclasses.replace(tree, reason=why, calls=calls), "agreement", hybrid, tree)
    if hybrid.gate == "auto_act":
        return broker("methods_disagree", f"The tree walk picked {tree.code} but the hybrid picked {hybrid.code}.")
    if hybrid.hold_reason in NO_JUDGE:
        return broker(hybrid.hold_reason, f"The tree walk picked {tree.code}, but the hybrid held "
                      f"({hybrid.hold_reason}): {hybrid.reason}")
    why = f"The tree walk picked {tree.code}; the hybrid held unsure ({hybrid.hold_reason}). {tree.reason}"
    return ConsensusResult(dataclasses.replace(tree, reason=why, calls=calls), "tree_only", hybrid, tree)


class _Serialized:
    """A recording decider writes its transcript file on every answer: one call at a time."""

    def __init__(self, inner: Decider) -> None:
        self.inner, self.lock = inner, threading.Lock()

    def judge(self, state, questions) -> Judgment:
        with self.lock:
            return self.inner.judge(state, questions)


def _both(description: str, *, decider: Decider, searcher: Searcher, data: SearchData, tree: NaicsTree,
          strict: bool) -> tuple[Decider, FullResult, FullResult]:
    """The hybrid and the tree walk, in parallel; a recording decider is serialized."""
    if isinstance(decider, (RecordingDecider, RecordMissingDecider)):
        decider = _Serialized(decider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        hybrid = pool.submit(classify_hybrid, description, decider=decider, searcher=searcher, data=data,
                             tree=tree, strict=strict)
        walked = pool.submit(classify_full, description, decider=decider, tree=tree, strict=strict)
        return decider, hybrid.result(), walked.result()


def classify_triage(description: str, *, decider: Decider, searcher: Searcher, data: SearchData | None = None,
                    tree: NaicsTree | None = None, strict: bool = False) -> ConsensusResult:
    data, naics = data or load_search_data(), tree or load_tree()
    started = time.perf_counter()
    decider, h, t = _both(description, decider=decider, searcher=searcher, data=data, tree=naics, strict=strict)
    out = triage(description, h, t, decider=decider, data=data, strict=strict)
    out.result = dataclasses.replace(out.result, seconds=round(time.perf_counter() - started, 3))
    return out


def classify_tree_first(description: str, *, decider: Decider, searcher: Searcher, data: SearchData | None = None,
                        tree: NaicsTree | None = None, strict: bool = False) -> ConsensusResult:
    data, naics = data or load_search_data(), tree or load_tree()
    started = time.perf_counter()
    _, h, t = _both(description, decider=decider, searcher=searcher, data=data, tree=naics, strict=strict)
    out = tree_first(h, t)
    out.result = dataclasses.replace(out.result, seconds=round(time.perf_counter() - started, 3))
    return out


__all__ = ["CONSENSUS_TRANSCRIPT", "ConsensusResult", "NO_JUDGE", "BrokerReason", "classify_consensus", "classify_tree_first",
           "classify_triage", "decide", "tree_first", "triage"]
