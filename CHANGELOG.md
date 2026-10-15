# Changelog

## Unreleased

- **Open-source prep** (JEV-59): MIT licence (the Census files stay US Government works); a README
  written for outside readers, with what runs without a key and how the eval sets were made; a replay-mode
  demo (`docs/media/demo.gif`, `tools/record_demo.py`); CONTRIBUTING.md; a private vulnerability-reporting
  section in SECURITY.md; issue and pull-request templates. The public repository starts from one history-free commit
  (`tools/export_public.sh`); the private history is not published. The test suite now runs on Python 3.10 as `pyproject.toml`
  promises (`tomli` fallback in the dev extra). `tests/test_public.py` keeps these true. Nothing about
  classification changed.
- **Ready to publish** (JEV-59): the full demo video (`docs/media/demo.mp4`, 69 s) linked from the README,
  with badges; package metadata (authors, URLs, classifiers); no internal service name or local path in the
  tree (the 10 intake eval cases are relabelled synthetic, text unchanged); SECURITY.md and DESIGN.md no longer
  say the API has no auth; the internal move plan is removed. `tests/test_public.py` keeps these true.
- **No private data in the public tree** (JEV-59): the runs on private samples of real applications
  (`small`, `medium`, `reviewed`) are removed from `experiments/refine/runs/`, and `experiments/EXPERIMENT.md`
  keeps only the synthetic and ExioNAICS results, with the lessons from the private runs stated without their
  data. The scripts take your own CSV (`make_samples.py <csv>`, `run_naics_sample.py`); README "Bring your own
  data". `tools/export_public.sh` builds a fresh, history-free copy for the public repository.
- **The page works under Compose again** (JEV-59): the web container's nginx forwards `/v1/`, `/docs` and
  `/openapi.json` to the API and adds a service key on the server side (`NAICS_API_UPSTREAM`, `NAICS_API_KEY`;
  off unless set, strictly validated, never in the browser). Compose turns it on with the key from `.env`; the
  README's first run creates that key. No internal workspace or service names remain in the tree.

- **Service auth.** The API can be hosted as a shared internal service, so every route except
  `/v1/health` and `/v1/naics/reference` needs `Authorization: Bearer <key>`, a key from `NAICS_API_KEYS`
  (comma-separated, for rotation). No credential is 401, a wrong key 403; keys are compared in constant time. The API
  **refuses to start without keys**; `NAICS_AUTH_DISABLED=1` is a local-test opt-out, refused unless `NAICS_HOST` is
  loopback and refused with `NAICS_REQUIRE_AUTH=1`. Classification is unchanged: no
  classifier, threshold, transcript or response body changed. **Breaking:** `docker compose up` needs
  `NAICS_API_KEYS` in `.env`, and the static page (which sends no key) is refused while keys are set.

- **Refine (experimental)** (`/v1/refine/assist`, `refine.py`, `lexicon.py`, Live assist "Refine" method): a wide pick over
  all 1,012 industries, then a verify step, then a score step only when verify does not code. New and additive: no
  existing endpoint, table or threshold changed. Measured in `experiments/EXPERIMENT.md` (E9 to E16): on 2,000 external
  descriptions 59 more usable codes than the tree-walk assist, the same indefensible rate (about 0.5%), 2.3 calls against
  5.1. Not a replacement: 32% of descriptions end in a pick-list, and the final configuration awaits its blind test.
- **Lexicon before Jev**: terms and abbreviations the Census text lacks ("SaaS", "HVAC") are read first and mapped to Census
  wording (never a code); a recognised term is checked even as one word; the wording is added only to descriptions of 6 words
  or fewer. The list is a draft (60 entries) no person has reviewed; `lexicon.sql` is its schema, in a separate database.
- **No three-word gate in refine**: the completeness checks decide (blind short/modern set: single words right 0 of 20
  with the gate, 18 of 20 without it and with the lexicon). Today's assist keeps its rule.
- **Refine fails closed on a Score answer that is not a distribution** (found by the testing audit).

- **Benchmark page** (JEV-57): `frontend/benchmark.html`, linked from the classifier's header, shows
  the 60-case ExioNAICS blind review (40 assigned, 0 indefensible, 20 held), the review of the
  dataset's own labels, and four paraphrased examples. Static, no script; the source data stays
  local.
- **A hold no longer reads as a pick on the page.** On a hold the candidate list is titled
  "Candidates it considered (none chosen)", and its column and the summary line say "lean" instead
  of "final pick" / "pick". Assigned codes are unchanged. Wording only: nothing about what is
  classified or gated changed.
- README: a tester quick start (clone, optional `.env`, the three URLs).

## 0.1.1 — deploy-ready (JEV-56)

The same images now run in production (AWS, RDS Postgres, configuration from the environment);
DEPLOY.md is the runbook. The classifier, its thresholds, the transcripts, and the data version
are unchanged.

- **The database is built by a deploy step, not by the API.** `NAICS_MIGRATE_ON_START` (default
  `true`, the old behavior); with `false` the API never migrates or loads at start, and
  `/v1/health` verifies the database (`db.verify`: 1,012 industries, this code's data version, a
  known query) and answers **503** with `ok: false` and the problems until it is ready. With the
  default it reports `database: "not_checked"` and checks only the tree, as before. Any other value
  refuses to start. `HealthOut.ok` is now a bool and gains `database` and `problems`.
- **Compose runs the production shape**: a one-shot `migrate` service (`python -m jev_naics.db_cli
  build`, the api image) runs before the API, which waits for it to complete and runs with
  `NAICS_MIGRATE_ON_START=false`.
- **The API image runs as an unprivileged user** (uid 10001).
- **Release workflow.** On a `v*` tag, `.github/workflows/release.yml` runs the offline tests,
  checks the tag matches the package version, and pushes `ghcr.io/jmurray10/jev-naics-api` and
  `jev-naics-web` as `:<version>` and `:latest` with the workflow's own `GITHUB_TOKEN`.
- **Reference-data export.** `GET /v1/naics/reference` (and `python -m jev_naics
  --export-reference`): every industry's code and title, the 2017 -> 2022 concordance, and a
  `version` that is a hash of exactly that content. No model call, no database. `/v1` only — no
  deprecated unversioned alias for a new route.
- **Structured logs.** One JSON line per request on stdout (`jev_naics/logs.py`, level
  `NAICS_LOG_LEVEL`): `classify` lines carry the outcome, latency, and the description's SHA-256
  prefix and length, never its text; other requests log the route template, never the query
  string; a request-path error logs the exception type and the request id only, never a message
  (exact-substring redaction of a client's own text is not trusted to catch every fragment of it);
  a startup or `data_load` error, where there is no client text, keeps a redacted message. Every
  response carries `X-Request-ID` (the caller's, if safe, else a uuid4). `startup` and `data_load`
  lines from the API
  and `db_cli build`. Uvicorn's access log is off.
- **The web image is configured by one variable**, `NAICS_API_ORIGIN`, written into
  `js/config.js` and rendered into the CSP `connect-src` at container start, so the two cannot
  drift; a malformed origin stops the container.

## 0.1.0 — the standalone repo (JEV-55)

The classifier moved out of the `jev_peas` monorepo (`packages/jev-naic-ops`) into this repo.

- **Moved with git history.** Extracted from `jev_peas` (branch
  `jeffmurray/jev-54-intake-hybrid-suggestion`) and merged in with `git merge
  --allow-unrelated-histories` (merge commit `2ad20ea`, parents `0379bf2` and the extracted
  history's tip `fd6b7d6`), so every `packages/jev-naic-ops` commit and its message survived the
  move.
- **The API is versioned: `/v1`.** `GET`/`POST` routes are served under `/v1` (`/v1/classify`,
  `/v1/naics/search`, `/v1/naics/{code}`, `/v1/naics`, `/v1/evals`, `/v1/health`). The same routes
  are also still served unversioned, marked `deprecated` in the OpenAPI schema, for one release —
  they will be removed after that. (FastAPI 0.142.2 copies an explicit `operation_id` verbatim
  into both `include_router` calls, so the two mounts would otherwise collide; each route's id
  instead comes from a small name -> id table keyed by the endpoint function's name, applied via
  `generate_unique_id_function` on each mount.)
- **The 30-code `closed` mode is dropped.** The original classifier (one Jev Choice over a
  hard-coded 30-row NAICS table, plus two Nouls for "too vague" and "no listed code fits") is not
  carried into this repo. `docs/CUSTOM_CLASS_CODES.md` records how that pattern becomes a second,
  future Jev step for a carrier's own class codes, narrowed by NAICS instead of hard-coded.
- **The few jev_peas helpers this package needs are copied in, not imported.** `backend/src/
  jev_naics/_jev/{__init__,gate,env,evals,openapi,agent_config,deciders,providers,
  providers.yaml}` are copies of `jev-common`/`jev-runtime` modules from `jev_peas` commit
  `9705080650cb52ba56a337e12eb7bee24f2b3777` (the exact source path is in each file's header
  comment, e.g. `packages/jev-common/src/jev_common/gate.py` -> `_jev/gate.py`). There is no
  dependency on `jev-common` or `jev-runtime`; this repo installs and runs standalone. Each
  header says "keep in sync by hand" — a future fix to the jev_peas original is not picked up
  automatically, and re-copying it is a manual, reviewed step that belongs in a later changelog
  entry.
- **Migrations stay in the installed package, not in `db/`.** `backend/src/jev_naics/
  migrations/*.sql` (applied by `jev_naics.db_cli` and by the API at start-up) is the source of
  truth; there is no `db/migrations/`. The early design (`docs/specs/2026-09-30-repo-design.md`,
  section 3) called for `db/migrations/`, but an installed library — anyone who `pip install`s
  `jev_naics` and needs to migrate a database — must carry its migrations as package data to do
  that; a folder that lives only in this git checkout could not. `db/README.md` documents the
  build and points here as the source of truth; `db/load.sql` (the manual, Python-free path) is a
  separate, hand-written mirror of the same load, not a migration.
- **The saved-search fingerprint is line-ending-proof.** `terms.py`'s `_normalized_sha256` folds
  CRLF to LF before hashing, so a Windows checkout (or any tree not yet normalized by
  `.gitattributes`) fingerprints the same as an LF checkout. The three
  `backend/evals/search_candidates*.json` fixtures were re-saved with the new
  `search_sql_sha256` this produces; the search results themselves are unchanged.
  `.gitattributes` now forces LF on every text file whose bytes are hashed or replayed
  (`*.json`, `*.sql`, `*.py`, `*.md`, `*.yaml`, `*.yml`, `*.toml`, `*.txt`, `*.js`, `*.css`,
  `*.html`), so a `core.autocrlf=true` checkout cannot reintroduce the mismatch.
- **Separate containers.** `compose.yaml` runs `db` (Postgres 16, `127.0.0.1:5434`), `api`
  (the FastAPI backend, `127.0.0.1:8095`, builds `backend/Dockerfile` standalone — no
  monorepo `packages/` paths), and `web` (the static frontend behind its own nginx,
  `127.0.0.1:8097`, calling the API across that origin — CORS is restricted to
  `NAICS_WEB_ORIGIN`). CI (`.github/workflows/tests.yml`) runs the offline (replay) suite against
  a Postgres service container, plus the frontend's own tests. `tools/uat.py` is the Chrome UAT
  against the running compose stack.
- **Three further deviations from the early design** (`docs/specs/2026-09-30-repo-design.md`):
  `db/build.py` became `python -m jev_naics.db_cli` (`build`, `verify`) — there is no standalone
  build script, only the library's CLI; there is no `db/Dockerfile` — `compose.yaml`'s `db`
  service is the stock `postgres:16` image, with `pg_trgm` and the other search extensions
  created by migration `0003_spelling.sql` (`backend/src/jev_naics/migrations/`), not baked into
  an image; and there is no `db` extra in `backend/pyproject.toml` — psycopg is a base
  dependency now (`jev_naics.db` and `jev_naics.search_pick` import it at module load, so
  `import jev_naics` needs it regardless of which method runs), not an opt-in extra.
