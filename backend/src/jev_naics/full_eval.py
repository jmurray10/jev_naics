"""Full-NAICS eval (JEV-48): strict scoring over evals/full_naics.json (dev) and
evals/full_naics_heldout.json (held out: written before the finalist step, never tuned on).

Record once with a key (JEV_DECIDER=record python -m jev_naics --full-eval); CI replays
transcripts/naics-full.json.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from jev_naics._jev.evals import PASSING, eval_outcome
from jev_naics._jev import Decider

from jev_naics.env import repo_root
from jev_naics.full import classify_full

FULL_EVALS = repo_root() / "evals" / "full_naics.json"
HELDOUT = repo_root() / "evals" / "full_naics_heldout.json"


def full_report(decider: Decider, path: Path = FULL_EVALS) -> dict[str, Any]:
    rows = []
    for case in json.loads(path.read_text(encoding="utf-8")):
        r = classify_full(case["ops_description"], decider=decider, strict=True)
        outcome = eval_outcome(r.gate, r.code, case["expect_code"], case["expect_hold"])
        rows.append({"id": case["id"], "gate": r.gate, "code": r.code, "gold": case["expect_code"],
                     "best_path": r.path, "probability": r.probability, "path_score": r.path_score,
                     "fit": r.fit_noul, "vague": r.underspecified_noul, "hold": r.hold_reason,
                     "finalists": [f["code"] for f in r.finalists], "calls": r.calls, "outcome": outcome})
    return {"cases": rows, "outcomes": dict(Counter(r["outcome"] for r in rows))}


def print_full_report(*reports: tuple[str, dict[str, Any]]) -> int:
    num = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    ok = True
    for name, report in reports:
        print(f"== {name}")
        for r in report["cases"]:
            mark = "PASS" if r["outcome"] in PASSING else "FAIL"
            leaf = r["best_path"][-1] if r["best_path"] else None
            print(f"{mark} {r['id']}: {r['gate']} code={r['code']} gold={r['gold']} picked={leaf} "
                  f"p={num(r['probability'])} path={num(r['path_score'])} fit={num(r['fit'])} "
                  f"vague={num(r['vague'])} finalists={r['finalists']} hold={r['hold']} {r['outcome']}")
        print(report["outcomes"])
        ok = ok and all(r["outcome"] in PASSING for r in report["cases"])
    return 0 if ok else 1
