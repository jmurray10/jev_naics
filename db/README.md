# Building the NAICS search database

Postgres 16 holds the full official Census text of the 1,012 NAICS 2022 industries, indexed for
weighted full-text search with spelling correction. It holds no client data. Only search and hybrid
need it; the tree walk runs from a JSON file.

The migrations are in `backend/src/jev_naics/migrations/` (package data, so an install carries
them; this is the source of truth — there is no `db/migrations/`). The commands below are what
the Python build (`jev_naics.db_cli`) and the containers (`compose.yaml`) provide; the manual
path further down shows exactly what they do.

## What you need

- Postgres 16 with the contrib extensions **pg_trgm** and **fuzzystrmatch** (both ship with the
  official `postgres:16` image).
- The derived file `naics_2022_search.json`, built from the Census spreadsheets by `build_search.py`
  (see [../DATA.md](../DATA.md)).

## The scripted build (one command)

```
docker compose up -d db                  # Postgres 16 on 127.0.0.1:5434, its own volume
python -m jev_naics.db_cli build         # migrate, then load (skips if the data version is unchanged)
python -m jev_naics.db_cli verify        # counts, data version, and a known query; exit 1 on a problem
```

`docker compose up -d` runs this same build as a one-shot `migrate` job (the API image with
`python -m jev_naics.db_cli build`) before the API starts; the API runs with
`NAICS_MIGRATE_ON_START=false` and never touches the schema itself (JEV-56, DEPLOY.md). Outside
Compose, a backend left at the default `NAICS_MIGRATE_ON_START=true` migrates and loads at start-up.

## What the build does, step by step

**1. Migrate.** The files in `backend/src/jev_naics/migrations/` are applied in name order, each
once, recorded in `schema_migrations`:

| Migration | Creates |
|---|---|
| `0001_schema_migrations.sql` | `schema_migrations(filename, applied_at)` |
| `0002_naics_search.sql` | `naics_industry` (code, title, description, examples[], index_entries[], excludes jsonb, sent_here[], `search tsvector` with a GIN index); `naics_lexeme(word, ndoc)` for rarity ranking; `naics_search_meta(key, value)` |
| `0003_spelling.sql` | the `pg_trgm` and `fuzzystrmatch` extensions; `naics_word(word)` with a trigram GIN index |

**2. Load (one transaction).** The load is skipped if `naics_search_meta` already holds the same
data version and word-list version. Otherwise:

1. `DELETE FROM naics_industry`, then insert every industry from `naics_2022_search.json`. The
   `search` vector is weighted as follows:
   ```sql
   setweight(to_tsvector('english', title), 'A')
   || setweight(to_tsvector('english', index_entries ; examples ; sent_here), 'B')
   || setweight(to_tsvector('english', description), 'C')
   ```
   An industry's own exclusions are stored for display and for Jev, but never searched: they say what
   the industry is *not*.
2. Rebuild `naics_word`: every distinct lower-case word of three or more letters from the title,
   description, examples, index entries and activities sent here. It is the vocabulary for spelling
   correction.
3. Rebuild `naics_lexeme` from `ts_stat('SELECT search FROM naics_industry')`: how many industries
   use each word, for IDF ranking.
4. Record the data version and the word-list version in `naics_search_meta`.

If any step fails, the transaction rolls back and the previous data stays in place.

**3. Verify.**

```sql
SELECT count(*) FROM naics_industry;                        -- 1012
SELECT count(*) > 5000 FROM naics_word;                     -- the spelling vocabulary is loaded
SELECT value FROM naics_search_meta WHERE key = 'version';  -- the data version you built
```

Then search "small-animal veterinary clinic" through the API (`GET /v1/naics/search?q=...`); 541940
Veterinary Services should come first. A test also checks that the saved eval search results still
equal what the database returns.

## The manual path (psql only)

For hosts that can't run the Python build. You still need `naics_2022_search.json`, built once
anywhere (`backend/src/jev_naics/data/naics_2022_search.json`). Run every command from the repo
root (`db/load.sql` reads that file by its path relative to there).

```
createdb -h 127.0.0.1 -p 5434 -U jev jev_naics
for f in backend/src/jev_naics/migrations/0001_schema_migrations.sql \
         backend/src/jev_naics/migrations/0002_naics_search.sql \
         backend/src/jev_naics/migrations/0003_spelling.sql; do
  psql -h 127.0.0.1 -p 5434 -U jev -d jev_naics -v ON_ERROR_STOP=1 -f "$f"
done
psql -h 127.0.0.1 -p 5434 -U jev -d jev_naics -v ON_ERROR_STOP=1 -f db/load.sql
```

`db/load.sql` is the SQL-only loader: no Python needed, just `psql` and the generated JSON. It
mirrors `jev_naics.db.load_search` exactly (same weights and word rules).

The JSON (about 2.5 MB) is too large to pass as a `psql -v` command-line value on any common host:
Linux caps a single argv string at `MAX_ARG_STRLEN` (128 KiB) and Windows caps the whole command
line at a few KiB, and a shell backtick/`$()` to build that value isn't portable to Windows `cmd`
either. So `db/load.sql` has `psql` read the file itself with `\copy`, which streams it
client-side — no server-side filesystem access needed, so this works the same whether Postgres is
local, in Docker, or a managed host. `\copy`'s file name is **not** `:variable`-interpolated by
psql (confirmed: with an unset or set variable, psql passes the literal text, colon and all, to the
OS `open()` call), so the path is written directly into `db/load.sql` rather than taken from a `-v`
argument — this is why the command above has no `-v search_json=...` and why it must be run from
the repo root. The file is read one physical line per row into a temp table (`DELIMITER`/`QUOTE`
set to control bytes that cannot appear in the JSON, so CSV mode does no escaping and each line
comes back out byte for byte), then reassembled into one `jsonb` value with `string_agg` and read
with `jsonb_each`, same as the Python loader.

It does not write `words_version` (only `jev_naics.db.load_search` does, since it is Python that
decides whether the word-building query itself changed). So after a SQL-only load, the backend's
own migrate-and-load (the `db_cli build` job, or a backend with `NAICS_MIGRATE_ON_START=true`)
reloads the data once more the next time it runs — this is
harmless (the data version already matches, only the word list is rebuilt again), but it means a
SQL-only load is not the last word if a backend is also going to start against this database.

`backend/tests/test_db_build.py` proves this path two ways: by shelling out to the real `psql`
binary with the exact command above (skipped when `psql` isn't on `PATH`), and — so the parity is
still checked in an environment without a `psql` binary, such as some CI images — by running the
same `db/load.sql` text through `psycopg`, with its `\copy` line replaced by `psycopg`'s own native
`COPY` (the identical control-byte CSV trick, just over the wire protocol instead of psql's client
command). Both compare the resulting `naics_industry` and `naics_word` rows against the Python
loader's and assert `jev_naics.db.verify(conn) == []`.

## Connection

The backend reads a single variable: `NAICS_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/jev_naics`.
The compose file sets it for the container. Locally, it goes in the git-ignored `.env`. Test
databases must be named `*_test`, because their schema is dropped and rebuilt on every test run.
