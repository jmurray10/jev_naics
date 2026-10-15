"""Nested random samples of DISTINCT ExioNAICS descriptions (public, apache-2.0; Hugging Face Yvnminc/ExioNAICS).

Reads .scratch/exionaics/ExioNAICS.csv (git-ignored third-party data), writes
.scratch/exionaics/exio_{small,medium,large}.json (small ⊂ medium ⊂ large, seed 11). Each entry: the description and
EVERY valid NAICS 2022 6-digit code the dataset gives it (45% of descriptions carry two or more, and JEV-57 found
about 30% of labels wrong), so a match is "any of its codes". Run inside the test container:
    python ../experiments/refine/make_exio.py
Prints counts only.
"""
import csv, json, random
from collections import defaultdict
from pathlib import Path

from jev_naics.tree import load_tree

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "exionaics"
csv.field_size_limit(10**9)
leaves = set(load_tree().leaves())
by: dict[str, set[str]] = defaultdict(set)
with open(OUT / "ExioNAICS.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        text = " ".join(r["Company Description"].split())
        code = r["2022 NAICS Code"].strip()
        if text and 3 <= len(text.split()) and len(text) <= 1500 and code in leaves:
            by[text].add(code)
items = [{"text": t, "codes": sorted(c)} for t, c in sorted(by.items())]
random.Random(11).shuffle(items)
print("usable distinct descriptions:", len(items), "| with 1 code:", sum(len(i["codes"]) == 1 for i in items))
for name, n in {"small": 100, "medium": 500, "large": 2000}.items():
    (OUT / f"exio_{name}.json").write_text(json.dumps(items[:n]))
    print(name, n)
