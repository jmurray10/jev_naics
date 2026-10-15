"""Hidden-error audit pack for the ExioNAICS large run (E16). Local, git-ignored output. Run in the test container.

The earlier reviews only judged codes the dataset does not list. This samples what was never looked at, from the
refine (cascade) results on the 2,000 descriptions:
  A  codes that MATCH a dataset label (the label is about 30% wrong, so a match can be wrong too)
  B  holds (PARTIAL / NEEDS_INFO): should the system have acted?
  C  pick-lists: do the options contain the best industry, and was one option clearly best?
Reviewers must give an exact quote from the Census text for their best code; `check_audit.py` verifies the quote is in
the Census text, which is the evidence that the entry was read. Items are shuffled and split across reviewers, with
a few shared to measure agreement. Usage: python ../experiments/refine/make_audit_pack.py 3
"""
import json, random, sys
from pathlib import Path

from jev_naics.search_data import load_search_data

D = Path(__file__).resolve().parents[2] / ".scratch" / "exionaics"
reviewers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
N_A, N_B, N_C, SHARED = 120, 80, 80, 15
rows = json.loads((D / "runs" / "E14-cascade-exio-large-lexfull-exp6-min1-20261007.json").read_text())["rows"]
texts = json.loads((D / "exio_large.json").read_text())
rng = random.Random(21)
A = [i for i, r in enumerate(rows) if r["code"] and r["code"] in (r["codes"] or [])]
B = [i for i, r in enumerate(rows) if not r["code"] and r["status"] in ("PARTIAL", "NEEDS_INFO")]
C = [i for i, r in enumerate(rows) if r["status"] == "AMBIGUOUS"]
C_lab = [i for i in C if set(rows[i]["codes"] or []) & set(rows[i]["picklist_codes"])]
C_no = [i for i in C if i not in set(C_lab)]
pick = ([("A", i) for i in rng.sample(A, N_A)] + [("B", i) for i in rng.sample(B, N_B)]
        + [("C", i) for i in rng.sample(C_lab, N_C // 2)] + [("C", i) for i in rng.sample(C_no, N_C // 2)])
rng.shuffle(pick)
items, key = [], {}
for n, (kind, i) in enumerate(pick):
    rid = f"x{n:03d}"
    it = {"id": rid, "type": kind, "description": texts[i]["text"]}
    if kind == "A":
        it["proposed_code"] = rows[i]["code"]
    elif kind == "C":
        it["options"] = rows[i]["picklist_codes"]
    items.append(it)
    key[rid] = {"case": i, "type": kind, "status": rows[i]["status"], "code": rows[i]["code"], "label": rows[i]["codes"]}
data = load_search_data()
need = {c for it in items for c in ([it["proposed_code"]] if "proposed_code" in it else it.get("options", []))}
census = {c: {"title": data.industries[c].title, "description": data.industries[c].description,
              "index_entries": list(data.industries[c].index_entries)[:25],
              "not_this_industry": [f"{t} (NAICS {k})" for k, t in data.industries[c].excludes][:8]} for c in sorted(need)}
(D / "audit_census.json").write_text(json.dumps(census, indent=1))
(D / "audit_key.json").write_text(json.dumps(key))
shared, rest = items[:SHARED], items[SHARED:]
for n in range(reviewers):
    mine = shared + rest[n::reviewers]
    (D / f"audit_input_{n}.json").write_text(json.dumps(mine, indent=1))
    print(f"reviewer {n}: {len(mine)} items")
import collections
print("types:", dict(collections.Counter(i["type"] for i in items)), "| codes with Census text:", len(census),
      "| pool sizes A/B/C:", len(A), len(B), len(C))
