"""The database build: verify catches a stale or empty load; the Census files match DATA.md; the
SQL-only loader and the Python loader produce the same rows. Postgres tests need NAICS_TEST_DATABASE_URL
naming a *_test database."""

import os
import shutil
from pathlib import Path

import pytest

from jev_naics import fetch_census
from jev_naics.search_data import load_search_data

URL = os.environ.get("NAICS_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not URL.rsplit("/", 1)[-1].endswith("_test"), reason="set NAICS_TEST_DATABASE_URL")
needs_psql = pytest.mark.skipif(shutil.which("psql") is None, reason="psql not on PATH")

REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_SQL = REPO_ROOT / "db" / "load.sql"


def test_the_committed_census_files_match_their_checksums() -> None:
    assert fetch_census.verify_files(fetch_census.CENSUS_DIR) == []


def test_fetch_refuses_a_checksum_mismatch(tmp_path) -> None:
    for name in fetch_census.CHECKSUMS:
        shutil.copy(fetch_census.CENSUS_DIR / name, tmp_path / name)
    (tmp_path / "2022_NAICS_Index_File.xlsx").write_bytes(b"not the census file")
    assert fetch_census.verify_files(tmp_path) == ["2022_NAICS_Index_File.xlsx: checksum mismatch"]


_SMALL_FILE = "2022_NAICS_Structure_Summary_Table.xlsx"  # smallest listed file, cheap to copy in a test


def test_fetch_itself_rejects_a_checksum_mismatch_leaves_no_part_and_keeps_the_existing_file(
    tmp_path, monkeypatch
) -> None:
    existing = tmp_path / _SMALL_FILE
    shutil.copy(fetch_census.CENSUS_DIR / _SMALL_FILE, existing)
    before = existing.read_bytes()

    def fake_urlretrieve(url, dest):
        Path(dest).write_bytes(b"not the real file")

    monkeypatch.setattr(fetch_census.urllib.request, "urlretrieve", fake_urlretrieve)
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_census.fetch(f"https://example.invalid/{_SMALL_FILE}", tmp_path)
    assert not (tmp_path / (_SMALL_FILE + ".part")).exists()
    assert existing.read_bytes() == before  # the good file already there was never touched


def test_fetch_leaves_no_part_file_on_a_network_error(tmp_path, monkeypatch) -> None:
    def fake_urlretrieve(url, dest):
        raise OSError("network is unreachable")

    monkeypatch.setattr(fetch_census.urllib.request, "urlretrieve", fake_urlretrieve)
    with pytest.raises(OSError):
        fetch_census.fetch(f"https://example.invalid/{_SMALL_FILE}", tmp_path)
    assert not (tmp_path / (_SMALL_FILE + ".part")).exists()
    assert not (tmp_path / _SMALL_FILE).exists()


@pytest.fixture
def conn():
    import psycopg
    from jev_naics import db

    with psycopg.connect(URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
        db.apply_migrations(c)
        yield c


@needs_db
def test_a_fresh_build_verifies(conn) -> None:
    from jev_naics import db

    assert db.verify(conn) == ["no data loaded (run: python -m jev_naics.db_cli build)"]
    db.load_search(conn, load_search_data())
    assert db.verify(conn) == []


@needs_db
def test_verify_fails_on_a_stale_data_version(conn) -> None:
    from jev_naics import db

    db.load_search(conn, load_search_data())
    conn.execute("UPDATE naics_search_meta SET value = 'old' WHERE key = 'version'")
    conn.commit()
    assert any("data version" in p for p in db.verify(conn))


@needs_db
def test_migrate_and_load_releases_its_lock_when_it_returns(conn) -> None:
    """migrate_and_load holds one session advisory lock for the whole migrate+load, then releases
    it: a build right after it finishes is free to take the same lock (minor 4)."""
    import psycopg

    from jev_naics import db

    db.migrate_and_load(conn, load_search_data())
    with psycopg.connect(URL) as other:
        got = other.execute("SELECT pg_try_advisory_lock(%s)", (db.BUILD_LOCK_KEY,)).fetchone()[0]
        assert got is True
        other.execute("SELECT pg_advisory_unlock(%s)", (db.BUILD_LOCK_KEY,))
        other.commit()


@needs_db
def test_a_concurrent_build_waits_for_the_advisory_lock_instead_of_racing(conn) -> None:
    """Two migrate+load runs (two `db_cli build`s, or a build racing the API's own migrate-on-start)
    must serialize, not race the same tables (minor 4). `conn` holds the build lock directly (as if
    a build were mid-flight on it); a second connection's migrate_and_load blocks until it is
    released, instead of erroring or running concurrently."""
    import threading
    import time as time_mod

    import psycopg

    from jev_naics import db

    conn.execute("SELECT pg_advisory_lock(%s)", (db.BUILD_LOCK_KEY,))
    conn.commit()
    other = psycopg.connect(URL)
    finished = threading.Event()

    def run_build() -> None:
        db.migrate_and_load(other, load_search_data())
        finished.set()

    thread = threading.Thread(target=run_build)
    try:
        thread.start()
        time_mod.sleep(0.5)
        assert not finished.is_set(), "a concurrent build must wait for the lock, not race it"
        conn.execute("SELECT pg_advisory_unlock(%s)", (db.BUILD_LOCK_KEY,))
        conn.commit()
        thread.join(timeout=10)
        assert finished.is_set(), "the waiting build never completed after the lock was released"
    finally:
        thread.join(timeout=10)
        other.close()


def _sql_only_loader_fixtures(conn):
    """Load via Python, snapshot the rows both loaders must agree on, then wipe the tables so the
    SQL-only loader starts from the same empty state. Returns (python_industry, python_words)."""
    from jev_naics import db

    db.load_search(conn, load_search_data())
    python_industry = conn.execute("SELECT code, title, search::text FROM naics_industry ORDER BY code").fetchall()
    python_words = conn.execute("SELECT word FROM naics_word ORDER BY word").fetchall()
    conn.execute("DELETE FROM naics_industry; DELETE FROM naics_word; DELETE FROM naics_lexeme; DELETE FROM naics_search_meta")
    conn.commit()
    return python_industry, python_words


def _assert_loaded_like_python(conn, python_industry, python_words) -> None:
    from jev_naics import db

    sql_industry = conn.execute("SELECT code, title, search::text FROM naics_industry ORDER BY code").fetchall()
    sql_words = conn.execute("SELECT word FROM naics_word ORDER BY word").fetchall()
    assert sql_industry == python_industry
    assert sql_words == python_words
    assert db.verify(conn) == []


def _split_load_sql() -> tuple[str, str]:
    """`db/load.sql` split around its one `\\copy` line (a psql meta-command, not SQL psycopg can
    send to the server): the SQL text before it (through `CREATE TEMP TABLE _search_json_lines`)
    and the SQL text after it (from `CREATE TEMP TABLE _search_json` through `COMMIT`)."""
    lines = LOAD_SQL.read_text(encoding="utf-8").splitlines(keepends=True)
    copy_idx = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("\\copy"))
    return "".join(lines[:copy_idx]), "".join(lines[copy_idx + 1:])


@needs_db
@needs_psql
def test_the_documented_manual_psql_load_matches_the_python_loader(conn) -> None:
    """The exact command db/README.md documents (`psql <url> -v ON_ERROR_STOP=1 -f db/load.sql`,
    run from the repo root) loads the same naics_industry and naics_word rows as db.load_search."""
    import subprocess

    python_industry, python_words = _sql_only_loader_fixtures(conn)
    result = subprocess.run(
        ["psql", URL, "-v", "ON_ERROR_STOP=1", "-f", str(LOAD_SQL)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    _assert_loaded_like_python(conn, python_industry, python_words)


@needs_db
def test_the_load_sql_body_matches_the_python_loader_via_native_copy(conn) -> None:
    """A psql-free parity check on the same file, for hosts (e.g. some CI images) with no psql
    binary: db/load.sql's SQL runs unmodified through psycopg; only its one `\\copy` line is
    replaced with psycopg's own native COPY, using the identical control-byte CSV trick (so this
    still proves the file's CSV DELIMITER/QUOTE choice round-trips the JSON exactly, and that the
    rest of the file — read straight off disk, not retyped here — matches the Python loader)."""
    import psycopg
    from jev_naics.search_data import SEARCH_DATA_PATH

    python_industry, python_words = _sql_only_loader_fixtures(conn)
    before, after = _split_load_sql()
    with psycopg.connect(URL, autocommit=True, cursor_factory=psycopg.ClientCursor) as loader:
        with loader.cursor() as cur:
            cur.execute(before)  # "BEGIN; CREATE TEMP TABLE _search_json_lines ..." straight from the file
            with cur.copy(
                "COPY _search_json_lines(line) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\x01', QUOTE E'\\x02')"
            ) as copy:
                for line in SEARCH_DATA_PATH.read_text(encoding="utf-8").splitlines():
                    copy.write(line.encode("utf-8") + b"\n")
            cur.execute(after)  # "CREATE TEMP TABLE _search_json ... COMMIT;" straight from the file
    _assert_loaded_like_python(conn, python_industry, python_words)
