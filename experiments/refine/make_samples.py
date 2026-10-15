"""Nested random samples of DISTINCT descriptions from your own data (small ⊂ medium ⊂ large), seed fixed.

    python experiments/refine/make_samples.py test_data/applications.csv [--exclude test_data/reviewed.csv]

The input is a CSV with a DESCRIPTION_OF_OPERATIONS column and, optionally, NAICS_CODE (the application's own
label, used only for counts; it is not ground truth). Writes test_data/refine_{small,medium,large}.json, which
`spike.py --sample <name>` reads. Keep the input and the output under the git-ignored test_data/: real
application data never enters the repo. --exclude drops descriptions already in another CSV (any column whose
name contains "desc"), so a sample can be kept unseen. Prints counts only.
"""
import argparse
import csv
import json
import random
from pathlib import Path

T = Path(__file__).resolve().parents[2] / "test_data"
csv.field_size_limit(10**9)


def norm(s: str) -> str:
    return " ".join(s.lower().split())


ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("csv", type=Path, help="your CSV (DESCRIPTION_OF_OPERATIONS, optional NAICS_CODE)")
ap.add_argument("--exclude", type=Path, help="a CSV of descriptions to leave out")
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()

seen: set[str] = set()
if a.exclude:
    rv = csv.DictReader(open(a.exclude, encoding="utf-8-sig"))
    dcol = next(c for c in rv.fieldnames or [] if "desc" in c.lower())
    seen = {norm(r[dcol]) for r in rv}
pool: dict[str, dict] = {}
for r in csv.DictReader(open(a.csv, encoding="utf-8-sig")):
    d = (r.get("DESCRIPTION_OF_OPERATIONS") or "").strip()
    if d and norm(d) not in seen and len(d) <= 1500:
        pool.setdefault(norm(d), {"text": d, "label": (r.get("NAICS_CODE") or "").strip()})
items = sorted(pool.values(), key=lambda x: x["text"])
random.Random(a.seed).shuffle(items)
T.mkdir(exist_ok=True)
print("distinct:", len(items), "excluded:", len(seen))
for k, n in {"small": 100, "medium": 500, "large": 2000}.items():
    (T / f"refine_{k}.json").write_text(json.dumps(items[:n]))
    print(k, min(n, len(items)))
