# NAICS classification as its own repo: design (JEV-55)

Status: approved and executed (v0.1.0, 2026-09-30). Section 10 is superseded: intake will call the
jev_naics service over HTTP instead of depending on the package (JEV-56, `DEPLOY.md`). Kept as a
record of the move: the repository was later published under the MIT licence, starting from a single
commit (JEV-59), so the visibility, licence and git-history rows of section 2 describe the private
repository of 2026-09-30, not the public one.

## 1. What and why

`packages/jev-naic-ops` classifies a business's description of operations into a NAICS 2022 code. It
uses search over the official Census text, a walk down the NAICS tree, or both (the hybrid,
JEV-53). Completing an insurance application needs this well beyond this monorepo, so it becomes
its own private repo, shareable later. Anyone opening it should be able to understand it, build the
database, run it, and call it without reading the jev_peas history.

**Success looks like this:**

- `git clone` plus one documented command builds the database from the Census files and starts three
  separate containers (database, backend, frontend).
- The same code is usable two ways: as a Python library (`classify(...)`) and as an HTTP service with
  detailed Swagger.
- README, DESIGN.md, PEAS.md, DATA.md, CALIBRATION.md and a database-build guide exist and match the code.
- Every eval that passes here passes there, offline (replay) and live, with the same numbers.
- The intake app uses the new repo, and `packages/jev-naic-ops` is removed from this monorepo.

## 2. Decisions already made (2026-09-30)

| Question | Decision |
|---|---|
| How consumers use it | **Both**: a Python library and an HTTP service. The intake app uses the library, pinned to a release tag. |
| Git history | **Kept**: the package's history is extracted with it. |
| Timing | **After** the jev_peas PRs 12 to 18 merge; extract from `main`. |
| Visibility | **Private** GitHub repo under jmurray10; shared later, after a leak scan. |
| The 30-code JEV-2 table (`closed` mode) | **Not moved.** The README and `docs/CUSTOM_CLASS_CODES.md` describe how that closed-set pattern becomes a carrier's own class codes, run as a second Jev step after NAICS (section 8a). |
| Licence | **All rights reserved** while private. |
| Early push | **Allowed:** the docs (README, DESIGN, PEAS, DATA, the custom class codes guide, the database build guide, LICENSE) go to the empty repo now. The code, data and tests arrive with the history extraction after the merges (section 9). |

Repo: **https://github.com/jmurray10/jev_naics** (private, created 2026-09-30, empty). Python package: `jev_naics`.

## 3. Repository layout

```
jev_naics/
  LICENSE                All rights reserved (while private)
  README.md              what it is, quick start (compose), library and API examples, results table
  DESIGN.md              architecture, the three methods, gates, data flow, failure modes
  PEAS.md                the agent as PEAS: performance, environment, actuators, sensors
  DATA.md                Census sources, provenance, checksums, licence (public domain), refresh
  CALIBRATION.md         every threshold, how and when it was measured, eval sets and results
  SECURITY.md            the TypeSafe key, no client data in evals or transcripts, leak scan
  CHANGELOG.md           releases (SemVer tags; the library and the API version together)
  docs/CUSTOM_CLASS_CODES.md   future: a carrier's own class codes as a second Jev step (section 8a)
  compose.yaml           db + api + web, all on 127.0.0.1
  db/
    README.md            the database build, step by step (scripted and manual)
    migrations/          0001_schema_migrations.sql, 0002_naics_search.sql, 0003_spelling.sql
    build.py             one command: migrate, then load the search data (idempotent by data version)
    Dockerfile           postgres:16 with pg_trgm and fuzzystrmatch enabled on init
  data/
    census-2022/         the 9 source files, committed (public domain), checksums in DATA.md
    fetch_census.py      re-download from census.gov and verify the checksums
  backend/
    pyproject.toml       package jev_naics; extras: api, db, dev
    src/jev_naics/
      __init__.py        the library surface: classify(), NaicsResult, Method
      tree.py  search_pick.py  full.py  hybrid.py  questions.py  terms.py  db.py
      build_tree.py  build_search.py  search_data.py   (data/ generated package data)
      _jev/              the few helpers taken from jev-common and jev-runtime (section 6)
      api/app.py         FastAPI: /v1 routes, Swagger, CORS for the web origin only
    tests/  evals/  transcripts/  agents/naics/agent.yaml
    Dockerfile
  frontend/
    index.html  css/  js/  config.js     (API base URL set at deploy time)
    Dockerfile           nginx serving static files; no Python, no key
  tools/uat.py           Chrome UAT against the three containers
  .github/workflows/     offline tests (replay), OpenAPI drift check
```

## 4. The three parts, separated

**Database.** Postgres 16 with `pg_trgm` and `fuzzystrmatch`. Tables: `naics_industry` (1,012 rows
with weighted full-text vectors), `naics_word` (spelling vocabulary), `naics_lexeme` (rarity weights),
`naics_search_meta` (data version and query fingerprint) and `schema_migrations`. Its own container
and volume. The backend reaches it only through `NAICS_DATABASE_URL`.

**Backend.** The FastAPI service and the library in one Python package. The API no longer serves the
page. Routes move under `/v1/` (the old unversioned paths stay one release as aliases):
`POST /v1/classify`, `GET /v1/naics/search`, `GET /v1/naics/{code}`, `GET /v1/naics`, `GET /v1/health`
and `GET /v1/evals`. CORS allows only the configured frontend origin.

**Frontend.** Today's page (description, mode switch, results, the tree walk view), served by nginx
from its own container. It calls the API at the base URL in `config.js`. It holds no key and makes no
model call.

## 5. The database build, scripted and documented

`db/README.md` walks through it end to end, and `db/build.py` does it in one command:

1. **Sources.** The 9 Census 2022 files in `data/census-2022/`, from https://www.census.gov/naics/,
   checked against the SHA-256 values listed in DATA.md. `fetch_census.py` re-downloads the 8
   spreadsheets and refuses on a checksum mismatch. The manual JSON has no public download of that
   form, so it is kept as committed.
2. **Package data.** `build_tree.py` reads the files and writes `naics_2022_tree.json` (2,126 nodes,
   the 2017-to-2022 concordance). `build_search.py` writes `naics_2022_search.json` (descriptions,
   examples, index entries, exclusions). Both are deterministic; a test rebuilds them and compares.
3. **Schema.** The migrations are applied in order and tracked in `schema_migrations`.
4. **Load.** `naics_industry`, `naics_word` and `naics_lexeme` are filled in one transaction and
   stamped with the data version. Re-running with the same version does nothing; a new version
   reloads.
5. **Verify.** Row counts (1,012 industries), a known query ("veterinary clinic" returns 541940
   first), and the saved eval search results equal the live database (the existing drift test).

A manual path in the same README gives the raw `psql` steps for people who don't run Python.

## 6. Shared helpers from this monorepo

The package uses about 60 lines from `jev-common` (`gate.is_unit_probability`, `gate.json_number`,
the `.env` loader, `evals.eval_outcome`) and the decider layer of `jev-runtime` (Jev, replay, record,
record-missing, the transcript key, and `providers.yaml`, the only file that names the model). They
are **copied** into `jev_naics/_jev/`, each with a header naming its source commit, and nothing else
from those packages comes along. A test keeps the rule that only `providers.yaml` names a model.

A copy was chosen over publishing `jev-common` and `jev-runtime`. The new repo must stand alone, and
they are small. If they change here, the copies are updated by hand; CHANGELOG.md records it.

## 7. Library and service contract

```python
from jev_naics import classify, Method

result = classify("Mobile car detailing at customers' homes", method="hybrid")
# result.gate ("auto_act" | "hold"), result.code, result.title, result.probability,
# result.fit_noul, result.hold_reason, result.fallback, result.finalists, result.calls
```

- `method`: `hybrid` (default when the database is configured), `search`, or `tree`. The old 30-code
  `closed` mode is not moved; see section 8a for what replaces the idea.
- The decider comes from the environment: Jev with a key, replay without one, and record or
  record-missing on request, exactly as today.
- The HTTP service returns the same fields. Swagger documents every field, every hold reason, an
  example for each method, the error models (409 no recording, 422 bad input, 503 no database) and the
  cost of each mode in Jev calls. The OpenAPI JSON is committed, and CI fails if it drifts from the
  code.

## 8. Docs, and what each must answer

- **README:** what problem it solves, the results table (the blind held-out sets), a 5-minute start,
  a library example, a curl example and links to the rest.
- **DESIGN.md:** the three methods and when each runs; find-then-judge; code owns every gate;
  failure modes and holds; why the hybrid only acts on an industry the walk added.
- **PEAS.md:** performance (right code, correct hold, never a wrong act), environment, actuators
  (emit a code, or hold for a person), sensors (the description, the Census text, the database), and
  what the model is never offered.
- **CALIBRATION.md:** every threshold in `questions.py`, the measurement behind it, the model version,
  and the rule to re-measure before changing the model pin.
- **DATA.md:** each Census file, its URL, its checksum, what is derived from it, and the licence.
- **SECURITY.md:** key handling, and that evals and transcripts contain only synthetic descriptions
  and public Census text.

## 8a. Custom class codes (a future second step)

Insurance applications often need a carrier's own class code (a general liability or workers'
compensation class, or an internal appetite class), not only NAICS. The 30-code JEV-2 classifier
was the first version of this pattern: one Choice over a closed table, with code-owned holds.
It is not moved as a NAICS mode. Instead, `docs/CUSTOM_CLASS_CODES.md` and a README section describe
how to adapt it to class codes:

1. **The table is data.** A class-code file (code, title, description, what it includes and
   excludes, and optional example wording) replaces the hard-coded 30 NAICS codes. It is loaded into
   its own Postgres table beside the NAICS tables, with the same full-text search.
2. **NAICS narrows it.** A crosswalk (NAICS code to the class codes that can apply) cuts the table to
   a small closed set once NAICS is known, whether written or suggested. With no crosswalk entry,
   search over the class-code text finds the candidates (find, then judge).
3. **Jev picks among only those candidates.** One Choice, a fit Noul per candidate and a vagueness
   Noul, with the same gates and holds as the NAICS search pick. A class code is never free text:
   none-of-the-above is a code-owned hold.
4. **API.** `POST /v1/class-codes/classify {description, naics?, table}` returns the same shape as
   NAICS classification (gate, code, probability, fit, hold reason, candidates). It is a secondary
   Jev call after NAICS resolves.
5. **Measured before use.** Each table gets its own eval set and thresholds in `questions.py`,
   calibrated on the pinned model (as for NAICS). An uncalibrated table only suggests; it never acts.

This is future work: only the documentation lands in this move.

## 9. Moving it with history

1. After the merges, run `git filter-repo --path packages/jev-naic-ops --path-rename
   packages/jev-naic-ops/:backend/` on a fresh clone. The package's commits keep their messages
   and dates.
2. Restructure in reviewed commits: separate db/ and frontend/, add `_jev/`, rename the package to
   `jev_naics`, add `/v1`. The tests pass after each step.
3. Push to https://github.com/jmurray10/jev_naics, and tag `v0.1.0` once the full test suite, the live evals and the UAT
   pass.

## 10. Intake app cut-over

> Superseded (JEV-56): intake calls `POST /v1/classify` on the deployed service (`NAICS_API_URL`)
> and keeps a reference-data snapshot from `GET /v1/naics/reference`; no git dependency, no token.


- The intake app depends on `jev-naics @ git+https://github.com/jmurray10/jev_naics@v0.1.0#subdirectory=backend`.
  The imports change from `jev_naic_ops` to `jev_naics`, and the suggestion behavior and recorded
  answers are unchanged (the same questions and states).
- Intake's compose uses the new repo's database image instead of the naic-ops stack.
- `packages/jev-naic-ops` is deleted here, along with its CI entry and CLAUDE.md rows, in the same PR.
  The intake tests and its NAICS replay pins must pass unchanged.

## 11. Testing and verification

- All of today's naic-ops tests pass in the new layout. The Postgres tests run against a `*_test`
  database, and the replay pins are unchanged.
- Live: the hybrid eval on the dev sets matches the recorded numbers. The blind sets are not re-run
  (they were run once); their replays must match.
- UAT: the page in its own container against the API in its own container, including the CORS
  refusal from a foreign origin.
- A gate audit after the restructure, and a leak scan before the first push and again before sharing.

## 12. Out of scope

A hosted deployment, authentication on the API, other NAICS years (the 2017 concordance stays a
lookup only), and any change to the thresholds or methods. The move is a relocation plus
documentation; accuracy work continues after it.

## 13. Resolved in review (2026-09-30)

- `closed` 30-code mode: not moved; replaced by the custom class codes guide (section 8a).
- Licence: all rights reserved while private.
- Docs may be pushed to the empty repo before the history extraction. The extraction then merges the
  package history into it (`git merge --allow-unrelated-histories`); the docs paths don't overlap the
  code paths.
- Census source: https://www.census.gov/naics/ (the 2022 files). `naics_manual_full.json` is a JSON
  conversion of the 2022 NAICS Manual from the same site. It is used for index terms only; the
  conversion is not scripted, and DATA.md says so.
