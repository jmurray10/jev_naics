"""Structured logs (JEV-56): one JSON line per request on stdout (CloudWatch reads container
stdout), a request id on every response, and never a description's text, a key, or a database
password in a log line."""

import hashlib
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from jev_naics import logs
from jev_naics.api.app import app, get_hybrid_decider, get_searcher, get_spelling
from jev_naics.search_data import load_search_data
from test_api import _notes
from test_search_pick import VET, Jev, searcher

# A distinctive description: it must never reach a log line.
SECRET_TEXT = "Zanzibar quokka grooming parlour for veterinary clinics"
DB_PASSWORD = "pw-Hunter2-do-not-log"
FAKE_URL = f"postgresql://naics:{DB_PASSWORD}@db.internal:5432/jev_naics?sslmode=require"
FAKE_KEY = "ts-key-do-not-log-0123456789"


@pytest.fixture
def lines(monkeypatch):
    """Every JSON line the jev_naics logger writes during the test, through the real formatter."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logs.JsonFormatter())
    logger = logging.getLogger("jev_naics")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    monkeypatch.setenv("NAICS_DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)

    def read() -> list[dict]:
        text = buffer.getvalue()
        assert SECRET_TEXT not in text and DB_PASSWORD not in text and FAKE_KEY not in text
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    yield read
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.fixture
def client():
    app.dependency_overrides[get_searcher] = lambda: searcher(VET)
    app.dependency_overrides[get_spelling] = lambda: _notes([])
    app.dependency_overrides[get_hybrid_decider] = lambda: Jev()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_classify_call_logs_one_classify_line_and_never_the_description(client, lines) -> None:
    r = client.post("/v1/classify", json={"description": SECRET_TEXT, "mode": "hybrid"},
                    headers={"X-Request-ID": "intake-req-42"})
    assert r.status_code == 200 and r.headers["X-Request-ID"] == "intake-req-42"
    events = lines()
    classify = [e for e in events if e["event"] == "classify"]
    assert len(classify) == 1 and len(events) == 1
    e = classify[0]
    assert e["request_id"] == "intake-req-42" and e["logger"] == "jev_naics" and e["level"] == "INFO"
    body = r.json()
    assert (e["mode"], e["gate"], e["code"], e["hold_reason"], e["fallback"], e["calls"], e["model"]) == \
        ("hybrid", body["gate"], body["code"], body["hold_reason"], body["fallback"], body["calls"], body["model"])
    assert e["probability"] == body["probability"] and e["fit_noul"] == body["fit_noul"]
    assert e["data_version"] == load_search_data().version
    assert e["description_sha256"] == hashlib.sha256(SECRET_TEXT.encode("utf-8")).hexdigest()[:12]
    assert e["description_len"] == len(SECRET_TEXT)
    assert e["status"] == 200 and isinstance(e["latency_ms"], (int, float)) and e["path"] == "/v1/classify"
    assert "description" not in e


def test_the_deprecated_alias_logs_classify_too_with_a_generated_request_id(client, lines) -> None:
    r = client.post("/classify", json={"description": SECRET_TEXT, "mode": "hybrid"})
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 36  # a uuid4
    [e] = lines()
    assert (e["event"], e["request_id"], e["path"]) == ("classify", rid, "/classify")


@pytest.mark.parametrize("sent", ["x" * 65, "bad id with spaces", "evil\r\nInjected: 1", "<script>"])
def test_an_unsafe_request_id_is_replaced(client, lines, sent) -> None:
    r = client.get("/v1/naics/621210", headers={"X-Request-ID": sent})
    rid = r.headers["X-Request-ID"]
    assert rid != sent and len(rid) == 36
    assert lines()[0]["request_id"] == rid


def test_a_search_logs_the_route_template_not_the_query(client, lines) -> None:
    r = client.get("/v1/naics/search", params={"q": SECRET_TEXT})
    assert r.status_code == 200
    [e] = lines()
    assert (e["event"], e["method"], e["path"], e["status"]) == ("request", "GET", "/v1/naics/search", 200)
    assert "latency_ms" in e and "query" not in e


def test_a_passing_health_check_is_debug_not_info(client, lines) -> None:
    client.get("/v1/health")
    assert lines() == []


def test_a_code_lookup_logs_the_template(client, lines) -> None:
    client.get("/v1/naics/621210")
    assert lines()[0]["path"] == "/v1/naics/{code}"


def test_an_unhandled_error_logs_only_its_type_and_the_request_id(client, lines) -> None:
    # A fragment of the description, with a newline in it: never the exact description text, so
    # the old exact-substring redaction would have let it straight through. The request-path fix
    # is to log no message at all for this path, so a fragment cannot leak either.
    fragment = "quokka grooming\nparlour"

    def broken():
        raise RuntimeError(f"cannot reach {FAKE_URL} with key {FAKE_KEY}: {fragment}")

    app.dependency_overrides[get_hybrid_decider] = broken
    r = client.post("/v1/classify", json={"description": SECRET_TEXT, "mode": "hybrid"},
                    headers={"X-Request-ID": "req-500"})
    assert r.status_code == 500 and r.headers["X-Request-ID"] == "req-500"
    assert r.json()["request_id"] == "req-500" and DB_PASSWORD not in r.text and FAKE_KEY not in r.text
    events = lines()  # asserts no secret, password, or the full description in any line
    assert fragment not in json.dumps(events)
    [error] = [e for e in events if e["event"] == "error"]
    assert (error["request_id"], error["error"]) == ("req-500", "RuntimeError")
    assert "message" not in error  # request-scoped errors log the type and request id only


def test_a_search_handler_error_logs_only_its_type_never_the_query(client, lines) -> None:
    # A type the route's own except clause does not catch, so it reaches the middleware's generic
    # handler the same way any other unanticipated request-path error would.
    def broken(text, limit):
        raise LookupError(f"search blew up on: {SECRET_TEXT}")

    app.dependency_overrides[get_searcher] = lambda: broken
    r = client.get("/v1/naics/search", params={"q": SECRET_TEXT}, headers={"X-Request-ID": "req-502"})
    assert r.status_code == 500 and r.headers["X-Request-ID"] == "req-502"
    events = lines()  # asserts no secret, password, or the query text in any line
    [error] = [e for e in events if e["event"] == "error"]
    assert (error["request_id"], error["error"]) == ("req-502", "LookupError")
    assert "message" not in error


def test_the_startup_line(monkeypatch, lines) -> None:
    monkeypatch.setenv("NAICS_MIGRATE_ON_START", "false")
    with TestClient(app):
        pass
    [e] = [e for e in lines() if e["event"] == "startup"]
    assert e["migrate_on_start"] is False and e["version"] == app.version


def test_the_log_level_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("NAICS_LOG_LEVEL", raising=False)
    assert logs.log_level() == logging.INFO
    monkeypatch.setenv("NAICS_LOG_LEVEL", "warning")
    assert logs.log_level() == logging.WARNING
    monkeypatch.setenv("NAICS_LOG_LEVEL", "loud")
    with pytest.raises(ValueError, match="NAICS_LOG_LEVEL"):
        logs.log_level()


def test_redact_removes_secrets_from_the_environment_and_url_credentials(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    text = logs.redact(f"a {FAKE_URL} b {FAKE_KEY} c postgresql://other:pass2@h/db")
    assert DB_PASSWORD not in text and FAKE_KEY not in text and "pass2" not in text


def test_db_cli_build_logs_one_data_load_line(monkeypatch, lines) -> None:
    from jev_naics import db, db_cli

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *_a, **_k):  # migrate_and_load's advisory lock/unlock; a no-op here
            return None

        def commit(self):
            return None

    monkeypatch.setattr(db, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(db, "apply_migrations", lambda conn: [])
    monkeypatch.setattr(db, "load_search", lambda conn, data: True)
    monkeypatch.setattr(db, "verify", lambda conn: [])
    assert db_cli.main(["build"]) == 0
    [e] = [e for e in lines() if e["event"] == "data_load"]
    assert (e["data_version"], e["industries"], e["reloaded"]) == (load_search_data().version, 1012, True)
    assert e["migrations_applied"] == []


def test_db_cli_build_failure_logs_one_error_line_and_exits_1_not_a_traceback(monkeypatch, lines, capsys) -> None:
    from jev_naics import db, db_cli

    def broken(*_a, **_k):
        raise RuntimeError(f"cannot reach {FAKE_URL}")

    monkeypatch.setattr(db, "connect", broken)
    assert db_cli.main(["build"]) == 1
    out = capsys.readouterr()
    assert "Traceback" not in out.out and "Traceback" not in out.err
    [error] = [e for e in lines() if e["event"] == "error"]
    assert error["error"] == "RuntimeError"
    assert DB_PASSWORD not in error["message"] and "[redacted]" in error["message"]


def test_the_lifespan_load_logs_a_data_load_line(monkeypatch, lines) -> None:
    from jev_naics import db

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
    monkeypatch.setattr(db, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(db, "apply_migrations", lambda conn: [])
    monkeypatch.setattr(db, "load_search", lambda conn, data: False)
    with TestClient(app):
        pass
    events = lines()
    assert [e["event"] for e in events] == ["startup", "data_load"]
    assert events[1]["reloaded"] is False and events[1]["industries"] == 1012


def test_a_failed_lifespan_load_logs_an_error_without_the_password(monkeypatch, lines) -> None:
    from jev_naics import db

    def refused(*_a, **_k):
        raise db.psycopg.OperationalError(f"connection to {FAKE_URL} failed")

    monkeypatch.delenv("NAICS_MIGRATE_ON_START", raising=False)
    monkeypatch.setattr(db, "connect", refused)
    with TestClient(app):
        pass
    [e] = [e for e in lines() if e["event"] == "error"]
    assert (e["error"], e["stage"]) == ("OperationalError", "data_load")
