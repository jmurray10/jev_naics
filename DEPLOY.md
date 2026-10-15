# Deploying jev_naics

Production is the same Docker images as the local stack, run on AWS, with configuration from the
environment (JEV-56). Nothing is built on the server and no file is edited in an image.

## Local POC and production

| | Local POC (`docker compose up -d --build`) | Production (AWS) |
|---|---|---|
| DB | `postgres:16` container, `127.0.0.1:5434` | RDS Postgres 16 (`pg_trgm`, `fuzzystrmatch`), `sslmode=require` |
| BE | `api` image built locally | `ghcr.io/jmurray10/jev-naics-api:<version>`, pinned by version |
| FE | nginx `web` image, `127.0.0.1:8097` | `ghcr.io/jmurray10/jev-naics-web:<version>`, with `NAICS_API_ORIGIN` set to the API's URL |
| Config | git-ignored `.env` (only `TYPESAFE_API_KEY`) | the same variable names, from Secrets Manager / the task definition |
| DB load | the one-shot `migrate` service: `python -m jev_naics.db_cli build` | the same command, the api image, as a one-off task before the API rolls |
| Callers (any service that calls the API) | `NAICS_API_URL=http://host.docker.internal:8095`, `NAICS_API_KEY` = one of the API's keys | `NAICS_API_URL` = the deployed API's URL; `NAICS_API_KEY` from the caller's secret store, sent as `Authorization: Bearer <key>` |

Only the API holds a TypeSafe key. No git or registry token is needed at run time (the images are
pulled from GHCR; a private package needs the cluster's pull credentials, see below).

## Images

`.github/workflows/release.yml` runs on a pushed tag `v<version>`: it runs the offline tests, checks
that the tag matches `backend/pyproject.toml`'s `version`, then builds and pushes

```
ghcr.io/jmurray10/jev-naics-api:<version>   and  :latest     (backend/Dockerfile)
ghcr.io/jmurray10/jev-naics-web:<version>   and  :latest     (frontend/Dockerfile)
```

with the workflow's own `GITHUB_TOKEN` (`packages: write`); no other secret. **Deploy by version,
never `:latest`** — rollback is "deploy the previous version", which only works when every task
names one. While the GHCR packages are private, give the ECS task execution role a registry
credential (`repositoryCredentials`, a Secrets Manager secret holding a GitHub token with
`read:packages` only), or mirror the images into ECR.

The api image runs as an unprivileged user (uid 10001), listens on port **8095** (fixed), and
carries the generated NAICS data — no Census spreadsheets, no `.env`, no key. The same image runs
the database build: its command `python -m jev_naics.db_cli build` instead of the default
`python -m jev_naics --serve`.

## RDS (Postgres 16)

1. Create an RDS PostgreSQL 16 instance (or Aurora PostgreSQL 16) in private subnets; only the
   API's and the migrate task's security groups may reach 5432.
2. Create the database and an owning role (as the RDS master user):

   ```sql
   CREATE ROLE naics_owner LOGIN PASSWORD '...';
   CREATE DATABASE jev_naics OWNER naics_owner;
   ```

3. **Extensions.** Migration `0003_spelling.sql` runs `CREATE EXTENSION IF NOT EXISTS pg_trgm` and
   `... fuzzystrmatch`, so the migrate role needs permission to create them. Both are *trusted*
   extensions on Postgres 13+, so a role with `CREATE` on the database (its owner, as above) may
   create them without `rds_superuser`. Checked on Postgres 16 with a non-superuser owner:
   `db_cli build` on an empty database applied all three migrations, loaded the data, and verified.
   If your policy forbids that, have the master user run the two `CREATE EXTENSION` statements in
   `jev_naics` once before the first build; the migration then finds them already there.
4. **Optional: a read-only role for the API.** With `NAICS_MIGRATE_ON_START=false` the API only
   reads. Checked on Postgres 16: a role with only `SELECT` passes `db_cli verify`'s checks, search,
   and spelling corrections, and is refused writes.

   ```sql
   CREATE ROLE naics_api LOGIN PASSWORD '...';
   GRANT CONNECT ON DATABASE jev_naics TO naics_api;
   -- in jev_naics:
   GRANT USAGE ON SCHEMA public TO naics_api;
   ALTER DEFAULT PRIVILEGES FOR ROLE naics_owner IN SCHEMA public GRANT SELECT ON TABLES TO naics_api;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO naics_api;   -- if the tables already exist
   ```

5. **TLS.** Put `sslmode=require` in the connection string:
   `postgresql://naics_owner:<password>@<endpoint>:5432/jev_naics?sslmode=require`. That encrypts
   the connection; to also verify the server certificate use `sslmode=verify-full` with
   `sslrootcert=` pointing at the RDS CA bundle mounted into the task (the image does not carry it).

## Environment variables

The complete list (SECURITY.md has the same table for the backend; keep them in sync).

| Variable | Container | Production value | Secret |
|---|---|---|---|
| `NAICS_DATABASE_URL` | migrate, api | the RDS URL above (`?sslmode=require`); the owner role for migrate, the owner or `naics_api` for the api | **yes** (carries a password): Secrets Manager |
| `TYPESAFE_API_KEY` | api | the TypeSafe key | **yes**: Secrets Manager |
| `NAICS_API_KEYS` | api | the service keys, comma-separated, one per caller, each at least 32 characters (rotate by adding the new key before removing the old). **Required**: with none, or with a shorter key, the API refuses to start | **yes**: Secrets Manager |
| `NAICS_REQUIRE_AUTH` | api | `1`, so a stray `NAICS_AUTH_DISABLED` refuses to start instead of opening the service | no |
| `NAICS_AUTH_DISABLED` | api | **unset**. Local tests only; refused on a non-loopback `NAICS_HOST` or connection. Never behind a reverse proxy on the same host, which makes every request look loopback | no |
| `NAICS_MIGRATE_ON_START` | api | `false` — **set it explicitly in the task definition.** The code defaults to `true` (the local-POC shape): an api task definition that omits it migrates and loads at every start instead of waiting for the one-off migrate job, which is exactly the race the migrate-task design and the build advisory lock (below) exist to avoid | no |
| `NAICS_HOST` | api | `0.0.0.0` (the default `127.0.0.1` is unreachable from a load balancer) | no |
| `NAICS_WEB_ORIGIN` | api | the web page's origin, e.g. `https://naics.example.com` — the one CORS-allowed origin | no |
| `NAICS_LOG_LEVEL` | migrate, api | `INFO` (default); `DEBUG`, `WARNING`, or `ERROR`. Anything else refuses to start | no |
| `JEV_DECIDER` | api | **unset** (live with the key). Never `replay` or `record*` in production | no |
| `NAICS_API_UPSTREAM` | web | **unset** unless the page should reach the API through the web container (Compose sets `http://api:8095`). With it, nginx forwards `/v1/`, `/docs` and `/openapi.json` there and `NAICS_API_ORIGIN` is the page's own origin | no |
| `NAICS_API_KEY` | web | with `NAICS_API_UPSTREAM`: one of the API's `NAICS_API_KEYS`, added by nginx on the server side. Anyone who can reach the page can use the API through it | **yes**: Secrets Manager |
| `NAICS_API_ORIGIN` | web | **must be set in production** — the image's own default (`ENV NAICS_API_ORIGIN=http://127.0.0.1:8095`, frontend/Dockerfile) is the local-POC API, and the container still starts on it (a valid origin, just the wrong one), so an omitted variable fails silently: the page loads but every call goes to `127.0.0.1:8095` in the browser, not the real API. Set it to the API's origin as browsers reach it, e.g. `https://naics-api.example.com` | no |

The migrate task needs only `NAICS_DATABASE_URL` (no key: building the database makes no model
call).

## The web image: config.js and the CSP

The page calls the API at `window.JEV_NAICS_API` (`js/config.js`), and nginx's Content-Security-Policy
`connect-src` must name that same origin, or the browser blocks every call. The web image keeps the
pair together from **one variable**, `NAICS_API_ORIGIN`: at container start
`frontend/docker/15-naics-api-origin.sh` overwrites `js/config.js` with it, and nginx renders it into
the CSP (`frontend/nginx.conf` is a template). It must be `scheme://host[:port]` with no path; anything
else stops the container rather than serving a page that cannot reach its API. Do not mount a
`config.js` over the image's — it is rewritten at start, and a mounted CSP would drift from it.

The other half of the pairing is on the API: `NAICS_WEB_ORIGIN` must be the page's origin, or CORS
refuses the page's calls. TLS terminates at the load balancer; both containers speak plain HTTP
behind it.

## Deploy order

1. **Migrate job.** Run the api image once as a one-off task (ECS `run-task`) with command
   `python -m jev_naics.db_cli build` and `NAICS_DATABASE_URL`. It applies new migrations, loads the
   data only when the data version changed (one transaction: a failed load leaves the old data in
   place), then verifies; exit code 0 means ready, 1 means a problem (printed, one JSON `error` line,
   never a raw traceback). Stop the deploy on a non-zero exit. Running it again is safe: an unchanged
   data version is a no-op. **Run a single migrate task at a time** — two at once is now also safe
   (`db.migrate_and_load` takes a Postgres session advisory lock around migrate+load, so a second
   concurrent run waits for the first instead of racing the same tables), but a deploy pipeline
   should still only start one; the lock makes an accidental second one harmless, not a feature to
   rely on.
2. **API.** Roll the api service to the new version. Point the target group's health check at
   `GET /v1/health`, success code `200` only: with `NAICS_MIGRATE_ON_START=false` it verifies the
   database (1,012 industries, this image's data version, a known query) and answers **503** with
   `ok: false` and the problems until the migrate job has run. A database error is reported by its
   type only. Set the target group's **health-check grace period** to cover how long a fresh task
   takes to answer `/v1/health` at all (container start plus the tree load), so ECS does not cycle a
   task that just hasn't had time to come up yet. Set the **health-check timeout above 5 seconds**:
   `/v1/health`'s database check uses `db.connect`'s 5-second `connect_timeout`, so a 5-second (or
   shorter) ALB timeout can mark a task unhealthy on a slow connect alone, before the check itself
   ever answers — either raise the ALB timeout past 5s, or, if your policy caps it at 5s, lower
   `connect_timeout` in `jev_naics.db.connect` to leave headroom and re-measure. **If every target in
   the group goes unhealthy, an ALB fails open** and routes to all of them anyway (it does not have a
   healthy fallback) — `/v1/classify` still holds on its own via `service_error` when the database
   really is unreachable, per the gates above, so a bad deploy degrades to holds, not wrong answers.
3. **Web.** Roll the web service with `NAICS_API_ORIGIN` set (the image's own default is the local
   POC's API; see the environment-variable table above).

A release whose data version changed (CHANGELOG says so; it changes only when the Census-derived
data or how it is loaded changes) briefly leaves the *old* API tasks reporting 503 between steps 1
and 2, because their data version no longer matches; roll the API straight after the migrate job.

## Rollback

Deploy the previous version's images: api, then web. If the previous version has a different data
version, run **its** migrate job first (`db_cli build` from the previous api image): the version
mismatch makes it reload that version's data, in one transaction. Migrations only add (`CREATE ...
IF NOT EXISTS`), so an older image ignores a newer migration's tables. If health still answers 503,
its `problems` say why.

## Logging

The API and `db_cli` write **one JSON object per line to stdout** (logger `jev_naics`, level
`NAICS_LOG_LEVEL`, default `INFO`). On AWS the container's stdout is the log: the ECS `awslogs`
driver (or FireLens) sends each line to CloudWatch Logs as is, where Logs Insights can query its
fields (`fields event, request_id, gate, hold_reason, latency_ms | filter event = "classify"`).
Uvicorn's own access log is turned off, because it prints the raw query string.

| Event | When | Fields (besides `ts`, `level`, `logger`, `event`) |
|---|---|---|
| `startup` | the API starts | `version`, `migrate_on_start` |
| `data_load` | `db_cli build`, or the API's own load when `NAICS_MIGRATE_ON_START=true` | `data_version`, `industries`, `reloaded` (false: the data version was already current); `db_cli` adds `migrations_applied` |
| `classify` | each `/v1/classify` (and the deprecated `/classify`) that got past validation | `request_id`, `method`, `path`, `status`, `latency_ms`, `mode`, `gate`, `hold_reason`, `code`, `probability`, `fit_noul`, `fallback`, `calls`, `model`, `data_version`, `description_sha256` (first 12 hex), `description_len` |
| `request` | every other request | `request_id`, `method`, `path` (the route template, e.g. `/v1/naics/{code}`, never the raw path or query string), `status`, `latency_ms`; a search the database failed adds `error` (the exception type). A passing health check is `DEBUG` |
| `error` | an unhandled request error (answered 500 with `{"detail": "internal error", "request_id": ...}`) | `request_id`, `error` (the exception type) only — **no `message`.** A request's own text (a description, a search query) is client-controlled, so exact-substring redaction is not trusted to catch every fragment of it; the fail-closed fix is to log no message at all on the request path |
| `error` | a failed load at start (`db_cli build`, or the API's own load), or `db_cli` itself failing before it gets that far | `stage` (data_load lines only), `error` (the exception type), `message` (at most 200 characters, with the TypeSafe key, `NAICS_DATABASE_URL`, and any URL's credentials replaced by `[redacted]`) — safe here because there is no client text in play at startup or in the CLI |

**A description is never logged.** It comes from client documents, so a `classify` line carries only
its SHA-256 prefix and length: enough to tell whether two requests sent the same text, not what it
said. No line carries a TypeSafe key or a database password.

**Correlating with a caller.** Every response has an `X-Request-ID` header, and the request's log
line has the same `request_id`. A caller that sends its own `X-Request-ID` (1-64 characters of
`A-Z a-z 0-9 _ -`; anything else is replaced by a new uuid4) gets it back and logged, so a calling
service can send its own request id and find the service's line for it in CloudWatch.

## Checks after a deploy

```
curl -s https://<api>/v1/health                       # {"ok": true, ..., "database": "ready", "problems": []}
curl -s https://<api>/v1/naics/reference | jq .version  # the reference data version callers pin
curl -s -X POST https://<api>/v1/classify -H 'content-type: application/json' \
  -d '{"description": "Small-animal veterinary clinic.", "mode": "hybrid"}'   # costs one Jev call
```
