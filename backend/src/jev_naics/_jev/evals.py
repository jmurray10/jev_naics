# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-common/src/jev_common/evals.py; keep in sync by hand (CHANGELOG.md).
"""Strict eval scoring shared by the single-decision packages."""

from __future__ import annotations

from typing import Literal

Outcome = Literal["correct_act", "expected_hold", "unexpected_hold", "wrong_act", "missed_hold"]
OUTCOMES: tuple[Outcome, ...] = (
    "correct_act",
    "expected_hold",
    "unexpected_hold",
    "wrong_act",
    "missed_hold",
)
PASSING: frozenset[str] = frozenset({"correct_act", "expected_hold"})


def eval_outcome(
    result_gate: str,
    emitted: str | None,
    gold: str | None,
    expect_hold: bool = False,
) -> Outcome:
    """One fixture's outcome. A hold passes only when the fixture expects a hold."""
    if expect_hold:
        return "expected_hold" if result_gate == "hold" else "missed_hold"
    if result_gate == "hold":
        return "unexpected_hold"
    return "correct_act" if emitted == gold else "wrong_act"


def summarize(outcomes: list[str]) -> str:
    return " ".join(f"{name}={outcomes.count(name)}" for name in OUTCOMES)
