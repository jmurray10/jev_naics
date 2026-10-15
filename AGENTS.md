# Agent notes (repo root)

This repo is one Jev proof of concept (JEV-55, parent JEV-2/JEV-48/JEV-50/JEV-52/JEV-53): classify
a business's description of operations into a NAICS 2022 industry, or hold it for a person. It
moved from the `jev_peas` monorepo (`packages/jev-naic-ops`), and the public repo starts from one commit; see
CHANGELOG.md for what changed in the move and DESIGN.md / PEAS.md for the architecture.

- **Use the TypeSafe skill** (`typesafe-ai`) when touching anything that calls Jev. Do not invent
  TypeSafe request or response fields.
- **Questions and thresholds live in `backend/src/jev_naics/questions.py` only.** Every numeric
  constant there is also in CALIBRATION.md, with the comment that justifies it, the model, and the
  date; a test (`backend/tests/test_docs.py`) enforces that the two stay in sync.
- **Thresholds are calibrated on the model pinned in `backend/src/jev_naics/_jev/providers.yaml`**
  — the only file in this repo that names a model version (`jev-1.13.0`). Re-measure (a live run,
  recorded) before changing that pin or a threshold; CALIBRATION.md has the rule in full.
- **Gates fail closed.** NaN, out-of-range numbers, missing answers, and service errors hold. Use
  `jev_naics._jev.gate.is_unit_probability` / `json_number` rather than re-implementing the check.
- **Jev never gets an option it should not have.** Every Choice is over a closed set built in code
  (search results, tree finalists, or their union). Hold is code-owned; it is never a Choice
  option. There is no free-text code and no "closest match" outside the candidates.
- **Replay never invents.** Offline tests and CI answer from `backend/transcripts/*.json`,
  recorded on the real model (`JEV_DECIDER=record` or `record-missing`). A missing recording
  raises; never hand-edit a transcript.
- **Evals are strict.** A hold passes only when the fixture expects a hold. A blind held-out set is
  written and committed before the method it tests exists, and run once, live, not tuned on.
- **Secrets and third-party data stay local.** `TYPESAFE_API_KEY` comes from a git-ignored `.env`
  or the environment only; never read, print, or commit it. Evals and transcripts hold only
  synthetic descriptions and public Census NAICS text. See SECURITY.md.
- **Test databases are named `*_test`** (`NAICS_TEST_DATABASE_URL`); their schema is dropped and
  rebuilt on every test run, so nothing else may point at one.
- **Migrations live in the installed package** (`backend/src/jev_naics/migrations/`), not in
  `db/` — see CHANGELOG.md for why. `db/load.sql` is a separate, hand-written SQL-only mirror of
  the same load, for hosts with no Python.
- **Run the leak scan before any push** that adds files, and before the repo or an image is made
  public.

## Where things go

- The PEAS spec: `backend/agents/naics/agent.yaml` (loaded by `jev_naics._jev.agent_config`). It
  names questions and actuators; it never restates a threshold.
- Questions and thresholds: `backend/src/jev_naics/questions.py` only.
- The library surface: `jev_naics.classify()` (`backend/src/jev_naics/__init__.py`) — the one
  function most callers need; `method="hybrid"` (default), `"search"`, or `"tree"`.
- The three classifiers: `full.py` (tree walk), `search_pick.py` (search then pick), `hybrid.py`
  (search first, tree as fallback). Spelling and modern-term handling: `terms.py`, used by `db.py`.
- The API: `backend/src/jev_naics/api/app.py` (FastAPI, `/v1`).
- Census data and the two builds: `fetch_census.py`, `build_tree.py`, `build_search.py`; see
  DATA.md.

## Commands

Run these from the repo root unless a command says otherwise.

```
docker compose up -d --build                      # db (:5434), api (:8095, Swagger at /docs), web (:8097)
python -m jev_naics.db_cli build                   # migrate, then load (from backend/, or installed)
python -m jev_naics.db_cli verify                  # counts, data version, a known query; exit 1 on a problem
psql <conn> -v ON_ERROR_STOP=1 -f db/load.sql       # the manual, Python-free load (see db/README.md)

cd backend && NAICS_TEST_DATABASE_URL=postgresql://jev:jev@127.0.0.1:5435/jev_naics_test \
  JEV_DECIDER=replay ../.venv/Scripts/python -m pytest -p no:warnings -q   # offline; needs a *_test Postgres on :5435
.venv/Scripts/python -m pytest -q frontend/tests   # the frontend's own tests, from the repo root

cd backend && ../.venv/Scripts/python -m jev_naics --hybrid-eval             # dev + seen sets, replay, no key
cd backend && ../.venv/Scripts/python -m jev_naics --hybrid-eval --heldout   # plus the JEV-53 blind set (already run once)
cd backend && ../.venv/Scripts/python -m jev_naics --search-eval             # search + Jev pick eval

.venv/Scripts/python tools/uat.py                  # Chrome UAT against the running compose stack

cd backend && ../.venv/Scripts/python -m jev_naics --openapi > openapi.json  # committed OpenAPI, re-generate after an API change
```

CI (`.github/workflows/tests.yml`) runs the offline suite against a Postgres service container on
`:5432`, plus the frontend tests — both with `JEV_DECIDER=replay`, no key.

## Linear

(For the maintainer: the Linear workspace is private. Outside contributors work from a GitHub issue
instead and reference it in the pull request; the `JEV-<n>` ids in the docs are history.)

Linear team **Jev** is the source of truth for scope. Reference `JEV-<n>` in branch names
(`jeffmurray/jev-<n>-...`), commit trailers (`Refs: JEV-<n>`), and PR bodies; after a verified
change, add a short factual comment to the issue with what changed and the test output that
proves it.
