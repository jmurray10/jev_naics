"""Measure the refine verify gate on the gold-labelled synthetic sets (E16). Replay only: no Jev call.

For every case where the verify step ran, record the winning candidate's probability p, its fit, the 'one main activity'
check, and whether coding it would be right (the gold code) or wrong (another code, or any code on a case that must
hold). Then sweep the three thresholds around the current ones (questions.py: PROB_SEARCH_PICK, NOUL_SEARCH_FIT_HOLD,
NOUL_ASSIST_SINGLE) and print how many right and wrong codes each setting gives. Run in the test container:
    JEV_DECIDER=replay python ../experiments/refine/calibrate_gate.py
"""
import itertools, json
from pathlib import Path

from jev_naics._jev import decider_for
from jev_naics.lexicon import default_lexicon
from jev_naics.questions import NOUL_ASSIST_SINGLE, NOUL_SEARCH_FIT_HOLD, PROB_SEARCH_PICK, REFINE_MIN_WORDS
from jev_naics.refine import REFINE_TRANSCRIPT, refine

EVALS = Path("/repo/backend/evals")
SETS = ["full_naics", "messy_dev", "assist_dev", "refine_heldout", "refine_techenabled", "refine_shortmodern"]
decider = decider_for(REFINE_TRANSCRIPT)
lex = default_lexicon()
pts = []
for name in SETS:
    for c in json.loads((EVALS / f"{name}.json").read_text()):
        gold = c.get("expect_code")
        hold = bool(c.get("expect_hold", c.get("expect_status") == "NEEDS_INFO" or gold is None))
        rr = refine(c["ops_description"], decider=decider, variant="verify", lexicon=lex, min_words=REFINE_MIN_WORDS)
        if not rr.verify or rr.result.checks is None:
            continue  # stopped before the verify step (completeness checks, too short, or an error)
        best = max(rr.verify, key=lambda r: r["p"])
        pts.append({"set": name, "p": best["p"], "fit": best["fit"], "single": rr.result.checks.single,
                    "right": (not hold) and best["code"] == gold, "wrong": hold or best["code"] != gold})
print(f"{len(pts)} cases reached the verify step: {sum(p['right'] for p in pts)} would be right, "
      f"{sum(p['wrong'] for p in pts)} wrong if coded")


def count(pmin, fmin, smin):
    ok = [x for x in pts if x["p"] >= pmin and x["fit"] >= fmin and x["single"] >= smin]
    return len(ok), sum(x["right"] for x in ok), sum(x["wrong"] for x in ok)


print(f"\ncurrent gate p>={PROB_SEARCH_PICK}, fit>={NOUL_SEARCH_FIT_HOLD}, single>={NOUL_ASSIST_SINGLE}: coded/right/wrong = {count(PROB_SEARCH_PICK, NOUL_SEARCH_FIT_HOLD, NOUL_ASSIST_SINGLE)}")
print("\nwrong candidates (p, fit, single):")
for x in sorted((x for x in pts if x["wrong"]), key=lambda x: -x["p"])[:14]:
    print(f"   {x['set']:20s} p={x['p']:.2f} fit={x['fit']:.2f} single={x['single']:.2f}")
for axis, vals in [("p", [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]), ("fit", [0.2, 0.35, 0.5, 0.65, 0.8]), ("single", [0.2, 0.35, 0.5, 0.65, 0.8])]:
    print(f"\nvary {axis} (others at current):")
    for v in vals:
        a = dict(p=PROB_SEARCH_PICK, fit=NOUL_SEARCH_FIT_HOLD, single=NOUL_ASSIST_SINGLE); a[axis] = v
        n, r, w = count(a["p"], a["fit"], a["single"])
        print(f"   {axis}>={v:<4}: coded {n:3d}  right {r:3d}  wrong {w:2d}")
