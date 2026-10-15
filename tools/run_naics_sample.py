"""Run rows of a CSV of descriptions through the NAICS endpoints of the running API.

    python3 tools/run_naics_sample.py test_data/applications.csv -n 100
    python3 tools/run_naics_sample.py test_data/applications.csv -n 100 --random 1 --methods triage

The input is your own CSV with a DESCRIPTION_OF_OPERATIONS column (README.md, "Bring your own data").

For each method adds JEV_NAICS_<METHOD> (the code, HOLD, or ERROR) plus _PROB and _HOLD, and for
consensus, triage, and tree_first _STEP. --methods picks any of hybrid, search, tree, consensus, triage, tree_first (default: the
first three). Writes <input>_jev[_random<seed>].csv beside the input, or --out. The input and output hold
your application data: keep them under the git-ignored test_data/. Needs the compose stack up
(`docker compose up -d --build`) and TYPESAFE_API_KEY in .env for live answers.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Column name -> (route, body extras).
METHODS = {"hybrid": ("/v1/classify", {"mode": "hybrid"}), "search": ("/v1/classify", {"mode": "search"}),
           "tree": ("/v1/classify", {"mode": "full"}), "consensus": ("/v1/consensus", {}),
           "triage": ("/v1/triage", {}), "tree_first": ("/v1/tree-first", {})}
STEPPED = {"consensus", "triage", "tree_first"}


def call(api: str, description: str, method: str) -> dict:
    route, extra = METHODS[method]
    req = urllib.request.Request(f"{api}{route}", data=json.dumps({"description": description, **extra}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"gate": "error", "hold_reason": f"HTTP {e.code}"}
    except Exception as e:  # a failed call is recorded, never guessed
        return {"gate": "error", "hold_reason": type(e).__name__}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("csv_path", type=Path)
    p.add_argument("-n", type=int, default=100)
    p.add_argument("--random", type=int, metavar="SEED", help="sample N random rows (seeded) instead of the first N")
    p.add_argument("--methods", default="hybrid,search,tree", help="comma-separated: " + ", ".join(METHODS))
    p.add_argument("--out", type=Path, help="output path (default: beside the input)")
    p.add_argument("--api", default="http://127.0.0.1:8095")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    if unknown := [m for m in methods if m not in METHODS]:
        p.error(f"unknown methods: {', '.join(unknown)}")

    with args.csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if args.random is None:
            rows = [r for _, r in zip(range(args.n), reader)]
        else:
            rows = random.Random(args.random).sample(list(reader), args.n)
    fields = list(reader.fieldnames or [])

    # One call per distinct description and method; repeated descriptions share the answer.
    jobs = sorted({(r["DESCRIPTION_OF_OPERATIONS"], m) for r in rows for m in methods})
    with ThreadPoolExecutor(args.workers) as pool:
        answers = dict(zip(jobs, pool.map(lambda j: call(args.api, *j), jobs)))

    out_fields = list(fields)
    for m in methods:
        col = f"JEV_NAICS_{m.upper()}"
        out_fields += [col, f"{col}_PROB", f"{col}_HOLD"] + ([f"{col}_STEP"] if m in STEPPED else [])
    suffix = "_jev.csv" if args.random is None else f"_jev_random{args.random}.csv"
    out_path = args.out or args.csv_path.with_name(args.csv_path.stem + suffix)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, out_fields)
        w.writeheader()
        for r in rows:
            for m in methods:
                a, col = answers[(r["DESCRIPTION_OF_OPERATIONS"], m)], f"JEV_NAICS_{m.upper()}"
                acted = a.get("gate") == "auto_act"
                r[col] = a.get("code") if acted else ("ERROR" if a.get("gate") == "error" else "HOLD")
                r[f"{col}_PROB"] = a.get("probability") if acted else ""
                r[f"{col}_HOLD"] = "" if acted else a.get("hold_reason") or ""
                if m in STEPPED:
                    r[f"{col}_STEP"] = a.get("step", "")
            w.writerow(r)
    print(f"wrote {out_path} ({len(rows)} rows, {len(jobs)} requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
