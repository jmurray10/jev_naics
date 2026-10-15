# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use GitHub's **Report a
vulnerability** button on this repository's Security tab (private vulnerability reporting). Include
what you found, how to reproduce it, and what it exposes. You should hear back within a week.

In scope: the API, the web image and its Content-Security-Policy, the database build, the logging
rules below, and anything that could leak the TypeSafe key or a database URL. A classifier that
auto-acts on a wrong code is a correctness bug rather than a security one: open a normal issue
(CONTRIBUTING.md).

The API needs a service key on every route except `/v1/health` and `/v1/naics/reference`
(`NAICS_API_KEYS`, below). It has no rate limit, so if you deploy it publicly with a TypeSafe key,
put one in front of it: every live classify call spends that key.

## Every environment variable the backend reads

Verified by grepping `os.environ`/`getenv` across `backend/src` — this is the complete list,
nothing more:

| Variable | What it is | Where it comes from |
|---|---|---|
| `TYPESAFE_API_KEY` | the TypeSafe credential | `jev_naics.env.load_typesafe_api_key`: the process environment, else the repo-root `.env`, else `backend/.env` (git-ignored; see `.env.example`) |
| `NAICS_DATABASE_URL` | the Postgres connection (can carry a password) | `jev_naics.db.database_url`: the same lookup order as the key above |
| `NAICS_API_KEYS` | the service's own keys, comma-separated (a secret). Callers send one as `Authorization: Bearer <key>`. With none, the API refuses to start | `api/auth.py`'s `api_keys()`; the environment only, no `.env` fallback (Compose's `env_file` forwards the repo-root `.env` into the container) |
| `NAICS_AUTH_DISABLED` | `1` switches auth off, for local tests only. Refused unless `NAICS_HOST` is loopback, refused together with `NAICS_REQUIRE_AUTH`, ignored while keys are set | `api/auth.py`'s `auth_enabled()`; the environment only |
| `NAICS_REQUIRE_AUTH` | `1` in every hosted deployment: refuses `NAICS_AUTH_DISABLED` and refuses to start without keys, at startup and at request time | `api/auth.py`'s `auth_enabled()`; the environment only |
| `NAICS_HOST` | the API's bind address (default `127.0.0.1`) | `api/app.py`'s `serve()`; the environment only, no `.env` fallback — Compose sets it to `0.0.0.0` inside the container |
| `NAICS_WEB_ORIGIN` | the single CORS-allowed origin (default `http://127.0.0.1:8097`), never a wildcard | `api/app.py`'s `CORSMiddleware`; the environment only |
| `NAICS_MIGRATE_ON_START` | `true` (default): the API migrates and loads the database at start. `false` (Compose and production): it never does; a deploy step runs `python -m jev_naics.db_cli build`, and `/v1/health` answers 503 until the database is ready. Any other value refuses to start | `api/app.py`'s `migrate_on_start()`; the environment only |
| `NAICS_LOG_LEVEL` | the `jev_naics` logger's level: `DEBUG`, `INFO` (default), `WARNING`, or `ERROR`; anything else refuses to start | `logs.log_level()`; the environment only |
| `JEV_DECIDER` | which decider answers Jev questions: `jev` (live), `record`, `record-missing`, or `replay` | `_jev/deciders.py::decider_for`; the environment only — unset means live with a key, else replay |

The web image reads up to three more, at container start, not the backend: `NAICS_API_UPSTREAM` and
`NAICS_API_KEY` (the optional same-origin proxy, below) and `NAICS_API_ORIGIN`, the API's
origin (`scheme://host[:port]`), written into `js/config.js` and the CSP `connect-src`
(`frontend/docker/15-naics-api-origin.sh`; anything else stops the container). DEPLOY.md lists
every variable for production.

## Service authentication

The API can be hosted as a shared internal service, so network isolation is never its only control:

- **Header.** `Authorization: Bearer <key>`, where `<key>` is one of `NAICS_API_KEYS`. The scheme
  name is case-insensitive; no other header is accepted.
- **Rotation.** `NAICS_API_KEYS` is comma-separated: add the new key, move each caller, remove the old.
- **Length.** Every key must be at least 32 characters, or the API refuses to start. Short keys are
  weak, and log redaction replaces each key wherever it appears, so a short one would mangle log
  text. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **Comparison.** sha256 of both sides, `hmac.compare_digest` against every configured key, no early
  exit (`api/auth.py`'s `key_matches`).
- **Answers.** No credential (or not `Bearer`): **401** with `WWW-Authenticate: Bearer`. A wrong
  key: **403**. Neither echoes the key.
- **Fail closed.** No keys refuses to start (in the lifespan, and in `serve()` before binding). A
  process started without its lifespan still answers **503** on every protected route rather than
  serve them open.
- **Opt-out.** `NAICS_AUTH_DISABLED=1`, for local tests only, refused unless `NAICS_HOST` is loopback
  and refused alongside `NAICS_REQUIRE_AUTH=1`; both rules hold at request time too. `serve()` binds
  the same value the check reads (`auth.bind_host()`: unset, empty or whitespace all mean `127.0.0.1`),
  so the two cannot disagree. Per request, the opt-out is honoured only when the connection's server
  and client addresses are both loopback; anything else gets **503**. That also covers a process
  started with a bare `uvicorn --host 0.0.0.0`, which never reads `NAICS_HOST`. `serve()` runs
  uvicorn with `proxy_headers=False`, so a forged `X-Forwarded-For` cannot rewrite the client address. **Never use the
  opt-out behind a reverse proxy on the same host** (nginx, a sidecar, an SSH tunnel): the proxy
  connects from loopback, so every request it forwards looks local and would be served without a key.
- **Public routes.** Only `/v1/health` and its deprecated unversioned copy `/health` (load balancers
  and the Compose health check call it with no header), `/v1/naics/reference` (the public Census
  export; it has no unversioned copy), and the Swagger and ReDoc pages (`/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json`). Everything that calls Jev (classify, consensus, triage,
  tree-first, assist, refine/assist, finalize) and every lookup that reads the database or the eval set
  (`/naics/search`, `/naics/{code}`, `/naics`, `/evals`) needs a key. `backend/tests/test_auth.py`
  calls every served route without a key, including any left out of the OpenAPI schema, and fails on
  any new route left open.
- **Logs.** Each service key is redacted from log messages, like the TypeSafe key.
- **Rate limiting.** There is none in this service, so nothing is bucketed by key.
- **The static page** (`frontend/`) runs in a browser and never holds a key: never put one in
  `js/config.js`. In Compose the page calls its own origin and the web container's nginx forwards
  `/v1/`, `/docs` and `/openapi.json` to the API with `Authorization: Bearer <NAICS_API_KEY>` added on the
  server (`frontend/docker/15-naics-api-origin.sh`, enabled only when `NAICS_API_UPSTREAM` is set; both
  values must match a strict pattern or the container stops). Anyone who can reach that page can use the
  API through it, so expose it only where that is intended (Compose binds it to `127.0.0.1`), or put
  your own authentication in front of it.

`TYPESAFE_API_KEY` is a secret, and so are `NAICS_API_KEYS` and `NAICS_DATABASE_URL` whenever it carries a password
(in production it does): `/v1/health` reports a database failure by its exception type only,
never its message, which can quote the connection. Nothing in this repo logs it: the decider layer
(`backend/src/jev_naics/_jev/deciders.py`) sends it to TypeSafe and never writes it to a
transcript, a log line, or an eval report. Without a key, everything replays recorded answers —
evals and CI run with no key at all.

## What evals and transcripts may hold

`backend/evals/*.json` and `backend/transcripts/*.json` hold only **synthetic** business
descriptions written for these evals, and **public** Census NAICS 2022 text (titles,
descriptions, index entries — all from census.gov). No client data, no real application, no
real person's business ever goes in an eval fixture or a recorded transcript. Recording a new
transcript (`JEV_DECIDER=record` or `record-missing`) sends only that same synthetic text to
TypeSafe.

## Test data kept out of the repo

Everything below is git-ignored and local: `test_data/` (your own application descriptions and their per-row results; README.md, "Bring your own data"),
`.scratch/` (the public ExioNAICS dataset from Hugging Face, its samples, per-row results and review packs), and the
transcripts recorded on real or ExioNAICS text (`test_data/naics-refine-real.json`, `.scratch/exionaics/naics-refine-exio*.json`).
`backend/transcripts/` holds only recordings of synthetic descriptions (checked: every entry of `naics-refine.json` is a
description in a committed eval file). The review packs were read by model reviewers inside the same session as the
other work. The lexicon (`data/naics_lexicon.json`) is word lists and Census wording, no data about any applicant.

## Logging

The API and `db_cli` log one JSON line per event to stdout (DEPLOY.md lists every event and
field). They never log a description's text (a `classify` line carries its SHA-256 prefix and
length only), a raw query string (a request line carries the route template; Uvicorn's access log,
which prints the query, is off), the TypeSafe key, or `NAICS_DATABASE_URL`, and no traceback.

**A request-path error line carries only the exception's type and the request id — never a
message.** A request's own text (a classify description, a `/v1/naics/search` query) is
client-controlled, and exact-substring redaction only strips text that matches a known secret
verbatim; a fragment, or a different piece of the same text, would not be caught by it. Rather than
rely on that, the middleware's unhandled-error handler (and any other request-path error log) logs
no message at all. Startup and `data_load`-stage errors (there is no client text in play there) and
`db_cli`'s own top-level error line still carry a message, cut to 200 characters with the TypeSafe
key, the database URL, and any URL's credentials replaced by `[redacted]`.

Every response's `X-Request-ID` matches its log line's `request_id`; a caller's own id is echoed
only when it is 1-64 characters of `A-Z a-z 0-9 _ -`.

## The images

The API image runs as an unprivileged user (uid 10001) and carries no `.env`, no key, and no
Census spreadsheets (`.dockerignore`). The release workflow (`.github/workflows/release.yml`)
pushes the api and web images to GHCR with the workflow's own `GITHUB_TOKEN`; no other secret is
stored in GitHub.

## Before every push, and before sharing

Run the leak-scanner check before any push that adds files, and again before the repo or an
image is made public: no secret, no `.env`, no key, and no third-party (non-synthetic,
non-Census) data has reached git, a Docker image, or GitHub. Test PDFs or other local fixtures,
if any are added later, stay git-ignored and local — never committed.

`python tools/leak_scan.py` does the mechanical part: it looks for any value from your local `.env`,
secret-shaped strings, sensitive file names, and credentials in a remote URL, across tracked files
and the whole history, prints only masked excerpts, and exits 1 on a finding. It cannot judge
whether text is third-party data; that stays a human check.

## The public history

The public repository starts from one commit, made by `tools/export_public.sh` from a reviewed tree:
the private history (work addresses, internal references, runs on private data) is never pushed to
it, rewritten or not. The script refuses a dirty tree, checks that no `.env`, `test_data/` or
`.scratch/` file is exported, and runs gitleaks (when installed) and `tools/leak_scan.py` on the
result before anything is pushed.
