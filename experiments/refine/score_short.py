"""Score the E15 short/modern-language runs by kind (synthetic, so per-case detail is fine to show).

Per kind and per arm: right (coded the gold), wrong (coded something else, or coded a case that must hold), pick-list
that offers the gold, TOO_SHORT, and other holds. Usage: python experiments/refine/score_short.py [arm ...]
Arms are run-file suffixes: '' (no lexicon), 'lexgate', 'lexfull'; plus assist-v1 for reference.
"""
import collections, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "experiments" / "refine" / "runs"
SET = {e["id"]: e for e in json.loads((ROOT / "backend" / "evals" / "refine_shortmodern.json").read_text())}
FILES = {"no lexicon": "E15-cascade-refine_shortmodern-20261006.json",
         "assist-v1": "E15-assist-v1-refine_shortmodern-20261006.json",
         "lexicon gate": "E15-cascade-refine_shortmodern-lexgate-20261006.json",
         "lexicon full": "E15-cascade-refine_shortmodern-lexfull-20261006.json",
         "gate off": "E15-cascade-refine_shortmodern-min1-20261006.json",
         "gate off+lex": "E15-cascade-refine_shortmodern-lexfull-min1-20261006.json",
         "gate off+lex, <=6w": "E15-cascade-refine_shortmodern-lexfull-exp6-min1-20261007.json"}


def outcome(row, case) -> str:
    hold = case["expect_hold"]
    if row["code"]:
        return "coded_wrong" if hold or row["code"] != case["expect_code"] else "right"
    if row["status"] == "AMBIGUOUS":
        return "picklist_has_gold" if case["expect_code"] in row["picklist_codes"] else "picklist"
    if row["status"] == "TOO_SHORT":
        return "held_ok_too_short" if hold else "too_short_missed"
    if row["status"] == "ERROR":
        return "error"
    return "held_ok" if hold else "held_missed"


def load(name):
    d = json.loads((RUNS / FILES[name]).read_text())
    return {r["id"].split(":", 1)[1]: r for r in d["rows"]}


if __name__ == "__main__":
    arms = {n: load(n) for n in FILES if (RUNS / FILES[n]).exists()}
    kinds = sorted({c["kind"] for c in SET.values()})
    for kind in kinds + ["ALL"]:
        ids = [i for i, c in SET.items() if kind == "ALL" or c["kind"] == kind]
        print(f"\n== {kind} ({len(ids)} cases)")
        for n, rows in arms.items():
            c = collections.Counter(outcome(rows[i], SET[i]) for i in ids if i in rows)
            print(f"  {n:13s} {dict(sorted(c.items()))}")
