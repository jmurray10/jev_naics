"""Check and score the hidden-error audit (E16). Run in the test container: python ../experiments/refine/check_audit.py

1. Verifies every reviewer quote is an exact phrase from the Census text of the code it names (title, description, index
   entries, examples, 'not this industry', 'sent here'), normalised for case and spacing. This is the evidence that the
   entry was read; the share of verified quotes is reported per reviewer, and items with an unverified quote are
   reported separately (not silently dropped).
2. Scores type A (label-matching codes: hidden wrong rate), B (holds: should it have acted), C (pick-lists: options
   contain the best code; was one option clearly best). Counts only.
"""
import collections, json, re, sys
from pathlib import Path

from jev_naics.search_data import load_search_data

D = Path(__file__).resolve().parents[2] / ".scratch" / "exionaics"
data = load_search_data()
norm = lambda t: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t.lower())).strip()


def census_text(code: str) -> str:
    i = data.industries[code]
    parts = [i.title, i.description, *i.index_entries, *i.examples, *i.sent_here, *[t for _, t in i.excludes]]
    return norm(" ".join(parts))


key = json.loads((D / "audit_key.json").read_text())
inputs, outs = {}, {}
for n in range(3):
    for it in json.loads((D / f"audit_input_{n}.json").read_text()):
        inputs[it["id"]] = it
    for e in json.loads((D / f"audit_output_{n}.json").read_text()):
        outs.setdefault(e["id"], []).append((n, e))
quote_ok = collections.defaultdict(lambda: [0, 0])
bad = set()
for rid, lst in outs.items():
    for n, e in lst:
        code = e.get("best_code") or inputs[rid].get("proposed_code") or (inputs[rid].get("options") or [None])[0]
        ok = bool(code) and code in data.industries and len(norm(e.get("quote", "")).split()) >= 3 \
            and norm(e["quote"]) in census_text(code)
        quote_ok[n][0] += ok
        quote_ok[n][1] += 1
        if not ok:
            bad.add((rid, n))
print("quotes verified against the Census text, per reviewer:", {n: f"{a}/{b}" for n, (a, b) in sorted(quote_ok.items())})
first = {rid: lst[0][1] for rid, lst in outs.items()}  # the first reviewer's judgement is the item's judgement
second = {rid: lst[1][1] for rid, lst in outs.items() if len(lst) > 1}
print("items judged:", len(first), "| items judged by two or more reviewers:", len(second))


def tally(kind, field):
    return collections.Counter(str(e.get(field)) for rid, e in first.items() if inputs[rid]["type"] == kind)


print("\nA  codes that match a dataset label:", dict(tally("A", "verdict")))
print("B  holds: does the description support a code?", dict(tally("B", "supports_a_code")))
print("C  pick-lists: options contain the best code:", dict(tally("C", "options_contain_best")), "| one option clearly best (a question was not needed):", dict(tally("C", "clear_single_best")))
for kind, field in (("A", "verdict"), ("B", "supports_a_code"), ("C", "clear_single_best")):
    both = [(first[r][field], second[r][field]) for r in second if inputs[r]["type"] == kind]
    if both:
        print(f"   reviewer agreement on shared {kind} items: {sum(a == b for a, b in both)}/{len(both)}")
# split C by whether the options contained a dataset label, and B by whether the best code equals a label
cl = collections.Counter()
for rid, e in first.items():
    if inputs[rid]["type"] == "C":
        has = bool(set(key[rid]["label"] or []) & set(inputs[rid]["options"]))
        cl[("options include a label" if has else "no label in options", e.get("options_contain_best"))] += 1
print("   pick-lists by label presence:", dict(cl))
print("\nitems whose quote did not verify (judgement kept, flagged):", len(bad))
