"""Full-NAICS evals on recorded real Jev answers (JEV-48): runs in CI with no key.

Record with JEV_DECIDER=record python -m jev_naics --full-eval (live, costs credits).
Pinned from the 2026-09-29 run on jev-1.13.0 (finalist step, gate-audit fixes, geometric-mean
beam ranking, 520-character option text):
- dev (33): 30 correct, 1 expected hold (the vague one), 2 unexpected holds. The veterinary
  clinic's right branch (professional services, p 0.03 at the root) did not reach the finalists,
  and the car wash lost 811192 one level early. Every finalist fit <= 0.16, so both held.
- held-out (6, committed on its own in f3a3d66 before these changes): 6 correct.
Never a wrong code.
"""

from jev_naics._jev import ReplayDecider

from jev_naics.full import FULL_TRANSCRIPT
from jev_naics.full_eval import HELDOUT, full_report


def test_dev_set_matches_the_recorded_run_and_never_acts_wrongly() -> None:
    assert FULL_TRANSCRIPT.is_file(), "record with JEV_DECIDER=record python -m jev_naics --full-eval"
    report = full_report(ReplayDecider(FULL_TRANSCRIPT))
    assert report["outcomes"] == {"correct_act": 30, "expected_hold": 1, "unexpected_hold": 2}
    held = sorted((r["id"], r["hold"]) for r in report["cases"] if r["outcome"] == "unexpected_hold")
    assert held == [("car-wash", "low_probability"), ("veterinary-clinic", "low_probability")]
    assert not any(r["outcome"] in ("wrong_act", "missed_hold") for r in report["cases"])


def test_held_out_set_is_all_correct() -> None:
    report = full_report(ReplayDecider(FULL_TRANSCRIPT), HELDOUT)
    assert report["outcomes"] == {"correct_act": 6}
