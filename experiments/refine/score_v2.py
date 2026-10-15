"""Score the fresh blind set (E16) once it has been run. Strict, per kind. Counts only. Python 3, no container needed.
Usage: python experiments/refine/score_v2.py <day>  (reads experiments/refine/runs/E16-<variant>-refine_v2[-lexfull-exp6-min1]-<day>.json)
A code on a case that must hold is wrong; a hold on a case with a gold code is a miss; a pick-list that offers the gold code
is reported separately and is not a code."""
import collections, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "experiments" / "refine" / "runs"
SET = {e["id"]: e for e in json.loads((ROOT / "backend" / "evals" / "refine_v2.json").read_text())}
day = sys.argv[1]
FILES = {"assist-v1 (today)": f"E16-assist-v1-refine_v2-{day}.json",
         "refine cascade (final config)": f"E16-cascade-refine_v2-lexfull-exp6-min1-{day}.json"}


def outcome(r, c):
    if r["code"]:
        return "coded_wrong" if c["expect_hold"] or r["code"] != c["expect_code"] else "right"
    if r["status"] == "AMBIGUOUS":
        return "picklist_has_gold" if c["expect_code"] in r["picklist_codes"] else ("picklist_no_gold" if not c["expect_hold"] else "picklist_on_hold_case")
    if r["status"] == "ERROR":
        return "error"
    return "held_ok" if c["expect_hold"] else "held_missed"


rows = {n: {r["id"].split(":", 1)[1]: r for r in json.loads((RUNS / f).read_text())["rows"]} for n, f in FILES.items() if (RUNS / f).exists()}
for kind in sorted({c["kind"] for c in SET.values()}) + ["ALL"]:
    ids = [i for i, c in SET.items() if kind == "ALL" or c["kind"] == kind]
    print(f"\n== {kind} ({len(ids)})")
    for n, rs in rows.items():
        print(f"  {n:30s} {dict(sorted(collections.Counter(outcome(rs[i], SET[i]) for i in ids if i in rs).items()))}")
