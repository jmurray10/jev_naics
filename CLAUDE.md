# CLAUDE.md

Rules for working in this repository. Shared rules for every coding agent live in `AGENTS.md` and
are imported here; this file adds what is specific to Claude Code.

@AGENTS.md

## What this repo is

`jev_naics`: classify a business's description of operations into a NAICS 2022 industry (one of
1,012), or hold it for a person. Part of the Jev proofs of concept in Linear team **Jev**
(parent JEV-2; the full-NAICS, search, spelling, and hybrid work is JEV-48/50/52/53). This repo
is the standalone move of that work, JEV-55 — see CHANGELOG.md for what the move changed.

| Part | What it is |
|---|---|
| `backend/` | the Python library `jev_naics` and its FastAPI service (`/v1`, Swagger at `/docs`) |
| `db/` | nothing but `load.sql` and a pointer — the migrations live in the package (`backend/src/jev_naics/migrations/`) |
| `frontend/` | a static page (and `benchmark.html`, the JEV-57 results), its own nginx container, calling the API at a configured origin |
| `data/census-2022/` | the public Census source files (see DATA.md) |

Design notes: DESIGN.md (architecture, methods, gates, failure modes), PEAS.md (performance,
environment, actuators, sensors), CALIBRATION.md (every threshold, measured), DATA.md (the Census
sources and checksums), SECURITY.md, `docs/CUSTOM_CLASS_CODES.md` (a future extension, not built).

## Hard requirements

1. **Jev never gets an option it should not have.** Every Choice is over a closed set built in
   code: Postgres search results, tree-walk finalists, or their union. Hold is code-owned; it is
   never a Choice option. None-of-the-above is a hold code decides, never something Jev chooses.
2. **Gates fail closed.** Gate on the picked industry's probability and fit, never on Choice
   confidence. NaN, out-of-range numbers, missing answers, and service errors hold. Use
   `jev_naics._jev.gate` rather than re-implementing the checks.
3. **Nothing invents evidence or scores.** No hard-coded confidence, no canned model answer. A
   result's `reason` and every probability come from Jev's actual answer or from code that ran
   (search's full-text rank, the spelling correction) — never fabricated.
4. **Thresholds are measured.** They live in `backend/src/jev_naics/questions.py` with a comment
   saying how they were calibrated, on the model pinned in
   `backend/src/jev_naics/_jev/providers.yaml` (the only file that names a model). Re-measure
   before changing the pin or a threshold — CALIBRATION.md has every current number and its
   justification, and the rule for changing one.
5. **Evals are strict.** A hold passes only when the fixture expects a hold. New behavior gets a
   failing test first; a claim about live behavior gets a live run, quoted, not paraphrased.
6. **Secrets and third-party data stay local.** `TYPESAFE_API_KEY` comes from the git-ignored
   `.env` or the environment only; never read, print, or commit it. `NAICS_DATABASE_URL` the same
   (it can carry a password). Evals and transcripts hold only synthetic descriptions and public
   Census text. Run the leak scan before any push that adds files.
7. **Replay never invents.** Offline evals and CI answer from `backend/transcripts/*.json`,
   recorded on the real model. A missing recording raises; never hand-edit a transcript.
8. **Test databases are named `*_test`.** The Postgres-marked tests (`test_db_build.py`,
   `test_search_postgres.py`) refuse to run unless `NAICS_TEST_DATABASE_URL` ends in `_test`, and
   each drops and recreates the whole `public` schema at the start of its run; never point it at
   a database that matters.

## Where things go

- The PEAS spec: `backend/agents/naics/agent.yaml`, loaded by `jev_naics._jev.agent_config`. It
  names questions and actuators; it never restates a threshold.
- Questions and thresholds: `backend/src/jev_naics/questions.py` only.
- The library surface most callers need: `jev_naics.classify()`
  (`backend/src/jev_naics/__init__.py`).
- The three classifiers: `full.py` (the tree walk, JEV-48), `search_pick.py` (search then Jev
  picks, JEV-50), `hybrid.py` (search first, the tree walk as a fallback, JEV-53).
- Spelling correction and modern-term reading (JEV-52, no model call): `terms.py`, used by
  `db.py`'s search query.
- The API: `backend/src/jev_naics/api/app.py` (FastAPI, routes under `/v1`, the same routes also
  served unversioned and marked deprecated for one release).
- The few jev_peas helpers this package needs, copied so it stands alone: `backend/src/
  jev_naics/_jev/` — each file's header names the exact jev_peas commit and source path.
- Census data and the two builds that derive from it: `fetch_census.py`, `build_tree.py`,
  `build_search.py` — see DATA.md.

## Commands

```
docker compose up -d --build                      # db (:5434), api (:8095, Swagger at /docs), web (:8097)
python -m jev_naics.db_cli build                   # migrate, then load (skips if the data version is unchanged)
python -m jev_naics.db_cli verify                  # counts, data version, a known query; exit 1 on a problem
psql <conn> -v ON_ERROR_STOP=1 -f db/load.sql       # manual, Python-free load, from the repo root (db/README.md)

cd backend && NAICS_TEST_DATABASE_URL=postgresql://jev:jev@127.0.0.1:5435/jev_naics_test \
  JEV_DECIDER=replay ../.venv/Scripts/python -m pytest -p no:warnings -q   # offline; needs a *_test Postgres on :5435
.venv/Scripts/python -m pytest -q frontend/tests   # frontend tests, from the repo root

cd backend && ../.venv/Scripts/python -m jev_naics --hybrid-eval             # dev + seen sets, replay, no key needed
cd backend && ../.venv/Scripts/python -m jev_naics --hybrid-eval --heldout   # plus the JEV-53 blind set (already run once)
cd backend && ../.venv/Scripts/python -m jev_naics --search-eval             # search + Jev pick eval

.venv/Scripts/python tools/uat.py                  # Chrome UAT against the running compose stack

cd backend && ../.venv/Scripts/python -m jev_naics --openapi > openapi.json  # re-generate the committed OpenAPI after an API change
```

## Linear

(For the maintainer: the Linear workspace is private. Outside contributors work from a GitHub issue
instead and reference it in the pull request; the `JEV-<n>` ids in the docs are history.)

Linear team **Jev** is the source of truth for scope. Before starting work, read the JEV issue it
belongs to; reference the issue id in branch names (`jeffmurray/jev-<n>-...`), commit trailers
(`Refs: JEV-<n>`), and PR bodies; after a verified change, add a short factual comment to the
issue with what changed and the test output that proves it.
