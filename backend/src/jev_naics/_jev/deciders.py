# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-runtime/src/jev_runtime/deciders.py; keep in sync by hand (CHANGELOG.md).
"""Deciders: who answers the closed questions for one agent step."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from typesafe_sdk import TypeSafeClient

from jev_naics._jev.providers import model_for


class ReplayMissing(LookupError):
    """No recorded answer for this (model, state, questions). Nothing is invented."""


@dataclass(frozen=True)
class ChoiceView:
    choice: str
    probabilities: dict[str, float]
    confidence: float | None


@dataclass(frozen=True)
class NoulView:
    """Jev returns a probability of yes; an LLM baseline returns only a verdict.

    A missing number stays None; gates treat it as unusable and hold.
    """

    noul: float | None
    verdict: bool | None = None


@dataclass(frozen=True)
class ScoreView:
    """Probability-weighted position on ordered levels (0 .. n-1), with per-level probabilities."""

    score: float | None
    probabilities: dict[int, float]
    confidence: float | None


@dataclass(frozen=True)
class Judgment:
    """The typed answers for one step, with the model that produced them."""

    model: str
    choices: dict[str, ChoiceView]
    nouls: dict[str, NoulView]
    usage: dict[str, int] | None = None
    scores: dict[str, ScoreView] = field(default_factory=dict)


class Decider(Protocol):
    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Judgment: ...


def _dump_question(question: Any) -> Any:
    if hasattr(question, "model_dump"):
        return question.model_dump(mode="json")
    return question


def _canonical(model: str, state: Mapping[str, Any], questions: Mapping[str, Any]) -> str:
    payload = {
        "model": model,
        "state": state,
        "questions": {qid: _dump_question(q) for qid, q in questions.items()},
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def transcript_key(model: str, state: Mapping[str, Any], questions: Mapping[str, Any]) -> str:
    """Stable id for one exact request: dict order does not matter, content does."""
    return hashlib.sha256(_canonical(model, state, questions).encode("utf-8")).hexdigest()[:24]


def _usage(response: Any) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return {
        key: int(getattr(usage, key))
        for key in ("input_tokens", "output_tokens")
        if isinstance(getattr(usage, key, None), int)
    } or None


def _from_response(response: Any) -> Judgment:
    return Judgment(
        model=str(response.model),
        choices={
            qid: ChoiceView(
                choice=answer.choice,
                probabilities=dict(answer.probabilities),
                confidence=answer.confidence,
            )
            for qid, answer in response.choices.items()
        },
        nouls={qid: NoulView(noul=answer.noul) for qid, answer in response.nouls.items()},
        usage=_usage(response),
        scores={
            qid: ScoreView(
                score=answer.score,
                probabilities={int(k): v for k, v in dict(answer.probabilities).items()},
                confidence=answer.confidence,
            )
            for qid, answer in getattr(response, "scores", {}).items()
        },
    )


def _to_entry(judgment: Judgment, state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": judgment.model,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": state,
        "choices": {
            qid: {"choice": c.choice, "probabilities": c.probabilities, "confidence": c.confidence}
            for qid, c in judgment.choices.items()
        },
        "nouls": {qid: {"noul": n.noul, "verdict": n.verdict} for qid, n in judgment.nouls.items()},
        "usage": judgment.usage,
        "scores": {
            qid: {
                "score": sc.score,
                "probabilities": {str(k): v for k, v in sc.probabilities.items()},
                "confidence": sc.confidence,
            }
            for qid, sc in judgment.scores.items()
        },
    }


def _from_entry(entry: Mapping[str, Any]) -> Judgment:
    return Judgment(
        model=entry["model"],
        choices={qid: ChoiceView(**c) for qid, c in entry["choices"].items()},
        nouls={qid: NoulView(**n) for qid, n in entry["nouls"].items()},
        usage=entry.get("usage"),
        scores={
            qid: ScoreView(
                score=sc["score"],
                probabilities={int(k): v for k, v in sc["probabilities"].items()},
                confidence=sc["confidence"],
            )
            for qid, sc in entry.get("scores", {}).items()
        },
    )


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class JevDecider:
    """Live TypeSafe call on the pinned model. Service errors propagate to the caller's gate."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client
        self.model = model_for("jev")

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Judgment:
        if self.client is not None:
            response = self.client.system_one(state=state, questions=questions, model=self.model)
        else:
            with TypeSafeClient() as owned:
                response = owned.system_one(state=state, questions=questions, model=self.model)
        return _from_response(response)


class ReplayDecider:
    """A recorded real answer for exactly this (model, state, questions), or ReplayMissing."""

    def __init__(self, path: Path, model: str | None = None) -> None:
        self.path = Path(path)
        self.model = model or model_for("jev")

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Judgment:
        key = transcript_key(self.model, state, questions)
        entry = _load(self.path).get(key)
        if entry is None:
            raise ReplayMissing(
                f"no recorded answer {key} in {self.path.name} for model {self.model}. "
                "Record one with a TypeSafe key: set JEV_DECIDER=record and run the same "
                "case or --eval again. Nothing is invented on replay."
            )
        return _from_entry(entry)


class RecordingDecider:
    """Answer live, then save the real answer so replay can return it later."""

    def __init__(self, live: Any, path: Path) -> None:
        """`live` is any decider with a `.model` (JevDecider, ClaudeDecider)."""
        self.live = live
        self.path = Path(path)

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Judgment:
        judgment = self.live.judge(state, questions)
        key = transcript_key(self.live.model, state, questions)
        stored = _load(self.path)
        stored[key] = _to_entry(judgment, state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(stored, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return judgment


class RecordMissingDecider:
    """Replay a recorded real answer; ask live and record only when none is recorded (JEV-52).

    For re-recording after a change that alters some states: every answer already recorded for
    exactly this (model, state, questions) is kept, and only the new ones cost a live call.
    """

    def __init__(self, live: Any, path: Path) -> None:
        self.replay = ReplayDecider(path, model=getattr(live, "model", None))
        self.record = RecordingDecider(live, path)

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Judgment:
        try:
            judgment = self.replay.judge(state, questions)
        except ReplayMissing:
            return self.record.judge(state, questions)
        # A recording served by a model other than the pin is asked again, never reused forever.
        if str(judgment.model) != str(self.replay.model):
            return self.record.judge(state, questions)
        return judgment


def decider_for(transcript: Path, *, client: Any | None = None) -> Decider:
    """JEV_DECIDER wins; otherwise live when a key is configured, else replay."""
    mode = os.environ.get("JEV_DECIDER", "").strip().lower()
    if not mode:
        mode = "jev" if os.environ.get("TYPESAFE_API_KEY", "").strip() else "replay"
    if mode == "jev":
        return JevDecider(client)
    if mode == "record":
        return RecordingDecider(JevDecider(client), transcript)
    if mode == "record-missing":
        return RecordMissingDecider(JevDecider(client), transcript)
    if mode == "replay":
        return ReplayDecider(transcript)
    raise ValueError(f"JEV_DECIDER must be jev, record, record-missing, or replay; got {mode!r}")
