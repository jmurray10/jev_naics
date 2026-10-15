"""Deploy-time database build (JEV-56): with NAICS_MIGRATE_ON_START=false the API never migrates or
loads on start, and /v1/health answers 503 (ok false) until `python -m jev_naics.db_cli build` has
loaded the current data. With the default (true) start-up and health behave as before."""

import os

import pytest
from fastapi.testclient import TestClient

from jev_naics import db
from jev_naics.api import app as api
from jev_naics.search_data import load_search_data

URL = os.environ.get("NAICS_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not URL.rsplit("/", 1)[-1].endswith("_test"), reason="set NAICS_TEST_DATABASE_URL")


class Touched(Exception):
    pass


def _no_db(*_a, **_k):
    raise Touched("the API touched the database")


@pytest.mark.parametrize("value, expected", [(None, True), ("", True), ("true", True), ("1", True), ("YES", True),
                                             ("false", False), ("0", False), (" No ", False)])
def test_the_flag_reads_true_unless_set_false(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("NAICS_MIGRATE_ON_START", raising=False)
    else:
        monkeypatch.setenv("NAICS_MIGRATE_ON_START", value)
    assert api.migrate_on_start() is expected


def test_an_unreadable_flag_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "maybe")
    with pytest.raises(ValueError, match="NAICS_MIGRATE_ON_START"):
        api.migrate_on_start()
    with pytest.raises(ValueError):
        with TestClient(api.app):
            pass


def test_startup_with_the_flag_false_never_touches_the_database(monkeypatch) -> None:
    touched = []
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "false")
    monkeypatch.setenv("NAICS_DATABASE_URL", "postgresql://u:p@db.invalid:5432/x")
    for name in ("connect", "apply_migrations", "load_search"):
        monkeypatch.setattr(db, name, lambda *a, _n=name, **k: touched.append(_n) or _no_db())
    with TestClient(api.app):  # runs the lifespan (a failure there is logged and swallowed, so count calls)
        pass
    assert touched == []


def test_startup_with_the_default_still_migrates_and_loads(monkeypatch) -> None:
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *_a, **_k):  # migrate_and_load's advisory lock/unlock; a no-op here
            return None

        def commit(self):
            return None

    monkeypatch.delenv("NAICS_MIGRATE_ON_START", raising=False)
    monkeypatch.setenv("NAICS_DATABASE_URL", "postgresql://u:p@db.invalid:5432/x")
    monkeypatch.setattr(db, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(db, "apply_migrations", lambda conn: calls.append("migrate") or [])
    monkeypatch.setattr(db, "load_search", lambda conn, data: calls.append("load") or False)
    with TestClient(api.app):
        pass
    assert calls == ["migrate", "load"]


def test_health_with_the_default_does_not_check_the_database(monkeypatch) -> None:
    monkeypatch.delenv("NAICS_MIGRATE_ON_START", raising=False)
    monkeypatch.setattr(db, "connect", _no_db)
    r = TestClient(api.app).get("/v1/health")
    assert r.status_code == 200
    assert (r.json()["ok"], r.json()["database"], r.json()["industries"]) == (True, "not_checked", 1012)


def test_health_holds_when_the_database_cannot_be_reached(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "false")
    monkeypatch.setenv("NAICS_DATABASE_URL", "postgresql://u:s3cret-pw@db.invalid:5432/x")

    def refused(*_a, **_k):
        raise db.psycopg.OperationalError("connection to db.invalid (u:s3cret-pw) refused")

    monkeypatch.setattr(db, "connect", refused)
    r = TestClient(api.app).get("/v1/health")
    assert r.status_code == 503
    body = r.json()
    assert (body["ok"], body["database"]) == (False, "not_ready")
    assert body["problems"] == ["database unreachable: OperationalError"]
    assert "s3cret" not in r.text


def test_health_holds_when_no_database_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "false")
    monkeypatch.setattr(db, "database_url", lambda: (_ for _ in ()).throw(RuntimeError("NAICS_DATABASE_URL is missing")))
    r = TestClient(api.app).get("/v1/health")
    assert (r.status_code, r.json()["ok"], r.json()["database"]) == (503, False, "not_ready")


def test_health_503_is_documented() -> None:
    responses = api.app.openapi()["paths"]["/v1/health"]["get"]["responses"]
    assert "503" in responses


@pytest.fixture
def empty_db(monkeypatch):
    import psycopg

    with psycopg.connect(URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "false")
    monkeypatch.setenv("NAICS_DATABASE_URL", URL)
    return URL


@needs_db
def test_health_on_an_empty_then_migrated_then_loaded_then_stale_database(empty_db) -> None:
    client = TestClient(api.app)
    with TestClient(api.app):  # start-up with the flag false leaves the empty database alone
        pass
    with db.connect(empty_db) as conn:
        assert db._applied(conn) == set()

    empty = client.get("/v1/health")  # not even migrated
    assert (empty.status_code, empty.json()["ok"], empty.json()["database"]) == (503, False, "not_ready")

    with db.connect(empty_db) as conn:
        db.apply_migrations(conn)
    migrated = client.get("/v1/health")  # migrated, nothing loaded
    assert migrated.status_code == 503
    assert migrated.json()["problems"] == ["no data loaded (run: python -m jev_naics.db_cli build)"]

    with db.connect(empty_db) as conn:
        db.load_search(conn, load_search_data())
    ready = client.get("/v1/health")
    assert (ready.status_code, ready.json()["ok"], ready.json()["database"]) == (200, True, "ready")

    with db.connect(empty_db) as conn:
        conn.execute("UPDATE naics_search_meta SET value = 'old' WHERE key = 'version'")
        conn.commit()
    stale = client.get("/v1/health")
    assert stale.status_code == 503 and any("data version" in p for p in stale.json()["problems"])


@needs_db
def test_classify_holds_service_error_on_a_stale_search_version(empty_db) -> None:
    """Gate audit F1: a loaded database whose data version no longer matches this code's must not
    act. search mode and hybrid mode both hold service_error rather than searching anyway (hybrid
    does not escalate a service_error hold to the tree walk: ESCALATE excludes it)."""
    client = TestClient(api.app)
    with db.connect(empty_db) as conn:
        db.apply_migrations(conn)
        db.load_search(conn, load_search_data())
        conn.execute("UPDATE naics_search_meta SET value = 'old' WHERE key = 'version'")
        conn.commit()

    search_body = client.post("/v1/classify", json={"description": "Vet clinic.", "mode": "search"}).json()
    assert (search_body["gate"], search_body["hold_reason"], search_body["code"]) == ("hold", "service_error", None)

    hybrid_body = client.post("/v1/classify", json={"description": "Vet clinic.", "mode": "hybrid"}).json()
    assert (hybrid_body["gate"], hybrid_body["hold_reason"], hybrid_body["code"]) == ("hold", "service_error", None)


@needs_db
def test_db_cli_build_makes_an_empty_database_ready(empty_db, capsys) -> None:
    from jev_naics import db_cli

    assert db_cli.main(["build"]) == 0
    assert TestClient(api.app).get("/v1/health").status_code == 200
    assert db_cli.main(["build"]) == 0  # idempotent: the data version is unchanged
    assert "data already current" in capsys.readouterr().out
