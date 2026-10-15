"""Build a blind review pack for the off-label codes of an ExioNAICS run (E13). Local, git-ignored output.

For every description where any variant coded an industry the dataset does not list for it, one item with the
description and the distinct proposed codes (no method names, no dataset labels), shuffled. Also writes the full
Census text for every code in play (title, description, index entries, what the industry is not), so a
reviewer judges from the Census text itself. Items are split into REVIEWERS files; the first OVERLAP items go to
every reviewer, to measure agreement between reviewers.
Run in the test container: [TAG=E14 VARIANTS=assist-v1,cascade SUFFIX_cascade=-lexfull-exp6-min1] python ../experiments/refine/make_review.py large 20261007 4
"""
import json, os, random, sys
from pathlib import Path

from jev_naics.search_data import load_search_data

D = Path(__file__).resolve().parents[2] / ".scratch" / "exionaics"
size, day, reviewers = sys.argv[1], sys.argv[2], int(sys.argv[3])
TAG = os.environ.get("TAG", "E13")
VARIANTS = os.environ.get("VARIANTS", "assist-v1,cascade,cascadeb").split(",")
SUFFIX = {v: os.environ.get("SUFFIX_" + v.replace("-", "_"), "") for v in VARIANTS}  # e.g. SUFFIX_cascade=-lexfull-exp6-min1
OVERLAP = 15
rows = {v: json.loads((D / "runs" / f"{TAG}-{v}-exio-{size}{SUFFIX[v]}-{day}.json").read_text())["rows"]
        for v in VARIANTS}
texts = json.loads((D / f"exio_{size}.json").read_text())
items, key = [], {}
for i, t in enumerate(texts):
    codes = set()
    for rs in rows.values():
        r = rs[i]
        if r["code"] and r["code"] not in (r["codes"] or []):
            codes.add(r["code"])
    if codes:
        rid = f"r{len(items):03d}"
        items.append({"id": rid, "description": t["text"], "proposed_codes": sorted(codes)})
        key[rid] = i
random.Random(9).shuffle(items)
data = load_search_data()
census = {}
for it in items:
    for c in it["proposed_codes"]:
        if c not in census:
            x = data.industries[c]
            census[c] = {"title": x.title, "description": x.description, "index_entries": list(x.index_entries)[:25],
                         "not_this_industry": [f"{t} (NAICS {k})" for k, t in x.excludes][:8]}
(D / f"review_{size}_census.json").write_text(json.dumps(census, indent=1))
(D / f"review_{size}_key.json").write_text(json.dumps(key))
shared, rest = items[:OVERLAP], items[OVERLAP:]
for n in range(reviewers):
    mine = shared + rest[n::reviewers]
    (D / f"review_{size}_input_{n}.json").write_text(json.dumps(mine, indent=1))
    print(f"reviewer {n}: {len(mine)} items ({OVERLAP} shared)")
print("distinct codes with Census text:", len(census), "| cases:", len(items))
