"""Score ExioNAICS runs (E13) from the local per-row files in .scratch/exionaics/runs/. Prints counts only.

Per variant: of N descriptions, how many coded, how many of those match any of the dataset's codes for that text
(`match`), how many coded a code the dataset does NOT list (`off_label`: NOT the same as wrong, since the labels
are noisy: JEV-57 found about 30% wrong), pick-lists whose options include a labelled code, and holds.
Usage: TAG=E14 python experiments/refine/score_exio.py <size> [day] [variants...]   (TAG defaults to E13)
"""
import collections, json, os, sys
from pathlib import Path

RUNS = Path(__file__).resolve().parents[2] / ".scratch" / "exionaics" / "runs"


def load(variant: str, size: str, day: str, tag: str = os.environ.get("TAG", "E13")) -> dict:
    # SUFFIX_<variant>, e.g. SUFFIX_cascade=-lexfull-exp6-min1, for runs whose file name carries the lexicon arm
    suffix = os.environ.get("SUFFIX_" + variant.replace("-", "_"), "")
    return {r["id"]: r for r in json.loads((RUNS / f"{tag}-{variant}-exio-{size}{suffix}-{day}.json").read_text())["rows"]}


def score(rows: dict) -> dict:
    c = collections.Counter()
    for r in rows.values():
        codes = set(r["codes"] or [])
        if r["code"]:
            c["coded"] += 1
            c["match" if r["code"] in codes else "off_label"] += 1
        elif r["status"] == "AMBIGUOUS":
            c["picklist_has_label" if codes & set(r["picklist_codes"]) else "picklist"] += 1
        else:
            c[r["status"].lower()] += 1
    return dict(c)


if __name__ == "__main__":
    size, day = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "20261006"
    for v in sys.argv[3:] or ["assist-v1", "cascade", "cascadeb"]:
        rows = load(v, size, day)
        s = score(rows)
        toks = sum((r["usage"] or {}).get("input_tokens", 0) for r in rows.values()) / len(rows)
        print(f"{v:9s} n={len(rows)} {s} calls={sum(r['calls'] for r in rows.values())/len(rows):.2f} in_tok={toks:.0f}")
