"""Score `reviewed` runs (your own sample with review verdicts, from `spike.py --sample reviewed`) against the verdicts.

Per-row results are your data: this reads them from test_data/refine_runs/ (git-ignored) and, on first use,
moves them there from experiments/refine/runs/, leaving only each run's summary in the tracked folder.
Classes: right (equals the label, or the defensible/label-wrong Jev code), KNOWN_WRONG (the reviewed wrong
code), arguable (the reviewed arguable code), unknown_other (a code nobody reviewed), picklist_has_ok
(AMBIGUOUS and the label or a defensible code is offered), picklist, held.
"""
import collections, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS, LOCAL = ROOT / "experiments/refine/runs", ROOT / "test_data/refine_runs"
LOCAL.mkdir(parents=True, exist_ok=True)


def load(variant: str, day: str) -> dict:
    tag = "E10" if variant == "cascade" else "E9"
    name = f"{tag}-{variant}-reviewed-{day}.json"
    pub, loc = RUNS / name, LOCAL / name
    if "rows" in json.loads(pub.read_text()):
        shutil.copy(pub, loc)
        d = json.loads(pub.read_text())
        pub.write_text(json.dumps({k: v for k, v in d.items() if k != "rows"}, indent=1))
    return json.loads(loc.read_text())


def cls(r: dict) -> str:
    v, c = r["verdict"], r["code"]
    jc = (r["jev_codes"] or "").replace(",", " ").replace(";", " ").split()
    if not c:
        if r["status"] == "AMBIGUOUS":
            ok = r["label"] in r["picklist_codes"] or (v == "defensible" and set(jc) & set(r["picklist_codes"]))
            return "picklist_has_ok" if ok else "picklist"
        return "held"
    if v == "wrong" and c in jc:
        return "KNOWN_WRONG"
    if v == "arguable" and c in jc:
        return "arguable"
    if c == r["label"] or (v in ("defensible", "label wrong") and c in jc):
        return "right"
    return "unknown_other"


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "20261006"
    for variant in sys.argv[2:] or ["assist-v1", "verify", "scoreb"]:
        d = load(variant, day)
        s = d["summary"]
        print(variant, dict(collections.Counter(cls(r) for r in d["rows"])), "calls", s["calls_mean"],
              "tokens", s["input_tokens_mean"], "errors", s["errors"])
        for verdict in ("wrong", "arguable"):
            print("   ", verdict, dict(collections.Counter(cls(r) for r in d["rows"] if r["verdict"] == verdict)))
