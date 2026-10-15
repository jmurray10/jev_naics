"""E9 spike: run the refine variants (and the assist baseline) on the synthetic dev sets.

Run inside the test container, from /repo/backend (JEV_DECIDER=record-missing needs the key, passed by
`docker exec --env-file .env`, never printed):

    python ../experiments/refine/spike.py --variants wide,score --limit 5      # a small first run
    python ../experiments/refine/spike.py --variants assist-v1,wide,verify,score,union

Writes experiments/refine/runs/<E9>-<variant>-dev-<date>.json: synthetic descriptions and counts only. With
--sample (your own data, make_samples.py) the whole run is written to the git-ignored test_data/refine_runs/.
Every answer is recorded to backend/transcripts/naics-refine.json (record-missing: a replay never invents).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from jev_naics._jev import decider_for
from jev_naics.assist import assess
from jev_naics.lexicon import Entry, Lexicon
from jev_naics.env import repo_root
from jev_naics.refine import REFINE_TRANSCRIPT, VARIANTS, refine
from jev_naics.tree import load_tree

EVALS = repo_root() / "evals"
RUNS = repo_root().parent / "experiments" / "refine" / "runs"
SETS = ["full_naics", "messy_dev", "assist_dev"]


class _FastRecordMissing:
    """Harness only: the product's record-missing decider re-reads and fully rewrites the transcript on every call,
    which is O(N^2) on a 30 MB file. This one loads it once, replays from memory, asks live only on a miss, and writes
    the file atomically every FLUSH_EVERY new answers and at exit. Same keys and entry format as the product's, so
    either decider can read what the other wrote. One process per transcript file."""

    FLUSH_EVERY = 20

    def __init__(self, path: Path) -> None:
        import atexit

        from jev_naics._jev.deciders import JevDecider, _load

        self.path, self.live, self.dirty = Path(path), JevDecider(), 0
        self.model = self.live.model
        self.store = _load(self.path)
        atexit.register(self.flush)

    def judge(self, state, questions):
        from jev_naics._jev.deciders import _from_entry, _to_entry, transcript_key

        key = transcript_key(self.model, state, questions)
        entry = self.store.get(key)
        if entry is not None:
            judgment = _from_entry(entry)
            if str(judgment.model) == str(self.model):
                return judgment
        judgment = self.live.judge(state, questions)
        self.store[key] = _to_entry(judgment, state)
        self.dirty += 1
        if self.dirty >= self.FLUSH_EVERY:
            self.flush()
        return judgment

    def flush(self) -> None:
        if not self.dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.store, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self.dirty = 0


class _Retry:
    """Harness only: retry a failed live call with backoff, logging the real error, so a rate limit or a brief
    outage is not recorded as a classification ERROR. The product keeps failing closed."""

    def __init__(self, inner, tries: int = 6) -> None:
        self.inner, self.tries = inner, tries

    def judge(self, state, questions):
        from typesafe_sdk import TypeSafeError

        for n in range(self.tries):
            try:
                return self.inner.judge(state, questions)
            except TypeSafeError as exc:
                print(f"  retry {n + 1}/{self.tries} after {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                if n == self.tries - 1:
                    raise
                time.sleep(min(60, 3 * 2 ** n))


def load_sample(name: str) -> list[dict]:
    """Your own sample (test_data/refine_<name>.json from make_samples.py, git-ignored). No gold: agreement and holds."""
    rows = json.loads((repo_root().parent / "test_data" / f"refine_{name}.json").read_text())
    return [{"id": f"{name}:{i}", "text": r["text"], "gold": None, "hold": False, "label": r["label"],
             "verdict": r.get("verdict"), "jev_codes": r.get("jev_codes")}
            for i, r in enumerate(rows)]


def load_exio(name: str) -> list[dict]:
    """An ExioNAICS sample (.scratch/exionaics/exio_<name>.json, git-ignored): every code the dataset gives the text."""
    rows = json.loads((repo_root().parent / ".scratch" / "exionaics" / f"exio_{name}.json").read_text())
    return [{"id": f"exio-{name}:{i}", "text": r["text"], "gold": None, "hold": False, "codes": r["codes"]}
            for i, r in enumerate(rows)]


def load_cases(sets: list[str] | None = None) -> list[dict]:
    cases = []
    for name in sets or SETS:
        for c in json.loads((EVALS / f"{name}.json").read_text()):
            gold = c.get("expect_code")
            expect_hold = c.get("expect_hold", c.get("expect_status") == "NEEDS_INFO" or gold is None)
            cases.append({"id": f"{name}:{c['id']}", "text": c["ops_description"], "gold": gold, "hold": bool(expect_hold)})
    return cases


def run_one(variant: str, case: dict, decider, tree, lexicon=None, min_words: int = 3) -> dict:
    t = time.perf_counter()
    if variant == "assist-v1":
        r, wide = assess(case["text"], decider=decider, tree=tree), []
        usage = {}
    else:
        rr = refine(case["text"], decider=decider, variant=variant, tree=tree, lexicon=lexicon, min_words=min_words)
        r, wide, usage = rr.result, rr.wide, rr.usage
    coded = r.naics if r.status == "CODED" else None
    return {"id": case["id"], "gold": case["gold"], "expect_hold": case["hold"], "status": r.status, "code": coded,
            "partial": r.naics_partial, "tooltip": r.tooltip_id, "picklist": (r.picklist or {}).get("id"),
            "picklist_codes": [o["code"] for o in (r.picklist or {}).get("options", [])],
            "gold_in_wide": case["gold"] in [w["code"] for w in wide] if wide else None,
            "wide_top": [w["code"] for w in wide], "calls": r.calls, "seconds": round(time.perf_counter() - t, 2),
            "usage": usage, "reason": r.reason, "label": case.get("label"), "verdict": case.get("verdict"), "jev_codes": case.get("jev_codes"),
            "codes": case.get("codes"), "reading": [x["term"] for x in rr.reading] if variant != "assist-v1" else []}


def summarise(rows: list[dict]) -> dict:
    coded = [r for r in rows if r["code"]]
    gold_rows = [r for r in rows if r["gold"]]
    # An ExioNAICS case carries every code the dataset gives it; a code in that list matches (labels are noisy).
    hit = lambda r: r["code"] == r["gold"] or bool(r.get("codes")) and r["code"] in r["codes"]
    right = [r for r in coded if hit(r)]
    wrong = [r for r in coded if not hit(r)]
    # Strict: a code on a case that expects a hold is wrong too (r["gold"] is None, so it never matches).
    wide_rows = [r for r in gold_rows if r["gold_in_wide"] is not None]
    return {"cases": len(rows), "coded": len(coded), "coded_right": len(right), "coded_wrong": len(wrong),
            "wrong_ids": [r["id"] for r in wrong],
            "gold_cases": len(gold_rows), "gold_coded_right": sum(r["code"] == r["gold"] for r in gold_rows),
            "ambiguous_with_gold_offered": sum(1 for r in gold_rows if r["status"] == "AMBIGUOUS" and r["gold"] in r["picklist_codes"]),
            "recall_at_k": (sum(bool(r["gold_in_wide"]) for r in wide_rows), len(wide_rows)) if wide_rows else None,
            "errors": sum(r["status"] == "ERROR" for r in rows),
            "statuses": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})},
            "calls_mean": round(sum(r["calls"] for r in rows) / len(rows), 2),
            "input_tokens_mean": round(sum(r["usage"].get("input_tokens", 0) for r in rows) / len(rows)),
            "seconds_median": sorted(r["seconds"] for r in rows)[len(rows) // 2]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tag", default="E9")
    ap.add_argument("--min-words", type=int, default=3, help="the short-text gate: fewer words is TOO_SHORT unless a lexicon term is read (1 = gate off)")
    ap.add_argument("--lexicon", help="a lexicon JSON file (E15); recognised terms pass the short-text gate")
    ap.add_argument("--expand-max-words", type=int, help="append Census wording only to texts of at most this many words")
    ap.add_argument("--lexicon-mode", choices=["gate", "full"], default="full",
                    help="gate: recognise the term only; full: also append its Census wording for Jev")
    ap.add_argument("--fast", action="store_true", help="load the transcript once and flush in batches (large transcripts)")
    ap.add_argument("--transcript", help="record to / replay from this file (parallel runs must not share one)")
    ap.add_argument("--exio", choices=["small", "medium", "large"], help="an ExioNAICS sample (public dataset, local only)")
    ap.add_argument("--set", help="one synthetic eval file in backend/evals (e.g. refine_heldout), instead of the dev sets")
    ap.add_argument("--sample", choices=["small", "medium", "large", "reviewed"], help="your own sample (make_samples.py) instead of dev")
    a = ap.parse_args()
    cases = (load_exio(a.exio) if a.exio else load_sample(a.sample) if a.sample else load_cases([a.set] if a.set else None))[: a.limit]
    # Your own samples record to a git-ignored transcript: transcripts in the repo hold synthetic text only.
    scratch = repo_root().parent / ".scratch" / "exionaics"
    transcript = (scratch / "naics-refine-exio.json" if a.exio
                  else repo_root().parent / "test_data" / "naics-refine-real.json" if a.sample else REFINE_TRANSCRIPT)
    if a.transcript:
        transcript = Path(a.transcript)
    inner = _FastRecordMissing(transcript) if a.fast else decider_for(transcript)
    tree, decider = load_tree(), _Retry(inner)
    lexicon = None
    if a.lexicon:
        lexicon = Lexicon.from_json(Path(a.lexicon), a.expand_max_words)
        if a.lexicon_mode == "gate":
            lexicon = Lexicon([Entry(e.term, "", e.why) for e in lexicon.entries], a.expand_max_words)
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    for variant in a.variants.split(","):
        rows = []
        for c in cases:
            rows.append(run_one(variant, c, decider, tree, lexicon, a.min_words))
            print(f"{variant:9s} {rows[-1]['id']:40s} {rows[-1]['status']:10s} {rows[-1]['code'] or '-':7s} gold={c['gold']}", flush=True)
        label = f"exio-{a.exio}" if a.exio else a.sample or a.set or "dev"
        if a.lexicon:
            label += f"-lex{a.lexicon_mode}"
        if a.lexicon and a.expand_max_words is not None:
            label += f"-exp{a.expand_max_words}"
        if a.min_words != 3:
            label += f"-min{a.min_words}"
        out = {"run_id": f"{a.tag}-{variant}-{label}-{stamp}", "variant": variant, "summary": summarise(rows), "rows": rows}
        if a.sample:  # your own data: the whole run (rows and summary) stays in the git-ignored test_data/
            local = repo_root().parent / "test_data" / "refine_runs"
            local.mkdir(parents=True, exist_ok=True)
            (local / f"{out['run_id']}.json").write_text(json.dumps(out, indent=1))
            print(json.dumps(out["summary"], indent=1))
            continue
        if a.exio:  # third-party per-row results stay local; only the summary is tracked
            (scratch / "runs").mkdir(exist_ok=True)
            (scratch / "runs" / f"{out['run_id']}.json").write_text(json.dumps(out))
            out = {k: v for k, v in out.items() if k != "rows"}
        (RUNS / f"{out['run_id']}.json").write_text(json.dumps(out, indent=1))
        print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main()
