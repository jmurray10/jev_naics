"""E15 regression (python compare_lex.py [arm-suffix] [day]): cascade with the lexicon and the word gate off, against the plain cascade, on every set.

Reads per-row run files (synthetic sets in experiments/refine/runs; your own samples in test_data/refine_runs and
ExioNAICS rows in .scratch/exionaics/runs, both git-ignored; a set whose files are missing is skipped). Prints counts
only: how many cases changed, and for the sets with a gold code, right/wrong before and after. Changed cases on
your own data are listed by id for review.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS, LOCAL, EXIO = ROOT / "experiments/refine/runs", ROOT / "test_data/refine_runs", ROOT / ".scratch/exionaics/runs"
D = "20261006"                                  # the baselines
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "lexfull-min1"   # the arm: e.g. lexfull-exp6-min1
ND = sys.argv[2] if len(sys.argv) > 2 else D                     # the arm's day
SETS = {  # name: (baseline file, new file, gold?)
    "dev": (RUNS / f"E10-cascade-dev-{D}.json", RUNS / f"E15-cascade-dev-{SUFFIX}-{ND}.json", True),
    "heldout": (RUNS / f"E11-cascade-refine_heldout-{D}.json", RUNS / f"E15-cascade-refine_heldout-{SUFFIX}-{ND}.json", True),
    "techenabled": (RUNS / f"E12-cascade-refine_techenabled-{D}.json", RUNS / f"E15-cascade-refine_techenabled-{SUFFIX}-{ND}.json", True),
    **{f"your {s}": (LOCAL / f"E10-cascade-{s}-{D}.json", LOCAL / f"E15-cascade-{s}-{SUFFIX}-{ND}.json", False)
       for s in ("reviewed", "medium", "small")},
    "exio medium": (EXIO / f"E13-cascade-exio-medium-{D}.json", EXIO / f"E15-cascade-exio-medium-{SUFFIX}-{ND}.json", False),
}


def rows(p):
    return {r["id"]: r for r in json.loads(p.read_text())["rows"]}


for name, (b, n, gold) in SETS.items():
    if not (b.exists() and n.exists()):
        print(f"{name:14s} (missing {'baseline' if not b.exists() else 'new run'})")
        continue
    B, N = rows(b), rows(n)
    changed = [i for i in B if (B[i]["status"], B[i]["code"]) != (N[i]["status"], N[i]["code"])]
    line = f"{name:14s} n={len(B):4d} changed={len(changed):3d}"
    if gold or name == "exio medium":
        def right(r):
            return bool(r["code"]) and (r["code"] == r.get("gold") or r["code"] in (r.get("codes") or []))
        def wrong(r):
            return bool(r["code"]) and not right(r)
        line += f" | right {sum(right(r) for r in B.values())}->{sum(right(r) for r in N.values())}" \
                f" | off/wrong {sum(wrong(r) for r in B.values())}->{sum(wrong(r) for r in N.values())}"
    print(line)
    trans = {}
    for i in changed:
        k = f"{B[i]['status']}{'(' + B[i]['code'] + ')' if B[i]['code'] else ''} -> {N[i]['status']}{'(' + N[i]['code'] + ')' if N[i]['code'] else ''}"
        trans[k] = trans.get(k, 0) + 1
    for k, v in sorted(trans.items(), key=lambda kv: -kv[1])[:6] if not gold else []:
        print(f"      {v:3d}  {k}")
