"""Service authentication (SECURITY.md): Bearer keys from NAICS_API_KEYS, 401 without a
credential, 403 with a wrong one, startup refused without keys, and a loopback-only opt-out."""

import hmac

import pytest
from fastapi.testclient import TestClient

from jev_naics import logs
from jev_naics.api import auth
from jev_naics.api.app import app, get_full_decider
from test_full_naics import Oracle

# Fakes of the minimum length (32), hyphenated so they are not secret-shaped (tools/leak_scan.py).
KEY_A, KEY_B = "test-key-alpha-for-the-auth-tests", "test-key-bravo-for-the-auth-tests"
BODY = {"description": "Family dental practice.", "mode": "full"}
# Health (load balancers and the Compose health check call it with no header) and the public Census
# reference export; FastAPI's own docs pages. Everything else needs a key.
PUBLIC = {"/health", "/v1/health", "/v1/naics/reference", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.delenv("NAICS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NAICS_API_KEYS", f" {KEY_A} ,, {KEY_B} ")
    app.dependency_overrides[get_full_decider] = lambda: Oracle()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---- which routes are protected -----------------------------------------------------------

def _flat_routes(routes=None) -> list:
    """Every route the app serves, flattened through its included routers (FastAPI 0.14x wraps an
    include as one _IncludedRouter whose effective_route_contexts() are the real routes), including
    any with include_in_schema=False, which the OpenAPI spec would not show."""
    out = []
    for r in app.routes if routes is None else routes:
        contexts = getattr(r, "effective_route_contexts", None)
        if contexts is not None:
            out.extend(contexts())
        else:
            out.append(r)
    return out


def test_every_served_route_but_the_public_ones_needs_a_key() -> None:
    flat = _flat_routes()
    assert len(flat) >= 19 + 4  # 19 API operations plus the four docs pages
    open_routes = sorted(r.path for r in flat
                         if not any(d.call is auth.require_api_key
                                    for d in getattr(getattr(r, "dependant", None), "dependencies", [])))
    assert set(open_routes) == PUBLIC, open_routes


def test_the_flattening_sees_routes_left_out_of_the_schema() -> None:
    from fastapi import APIRouter, FastAPI

    hidden = APIRouter()

    @hidden.get("/hidden", include_in_schema=False)
    def _hidden() -> dict:
        return {}

    probe = FastAPI()
    probe.include_router(hidden, prefix="/v1")
    assert "/v1/hidden" not in probe.openapi()["paths"]
    assert "/v1/hidden" in [r.path for r in _flat_routes(probe.routes)]


def test_every_served_route_actually_refuses_without_a_key(keyed) -> None:
    for r in _flat_routes():
        for method in sorted(r.methods - {"HEAD"}):
            url = r.path.replace("{code}", "621210")
            resp = keyed.request(method, url, json={"description": "Vet clinic."}, params={"q": "vet"})
            assert resp.status_code == (200 if r.path in PUBLIC else 401), (method, r.path, resp.status_code)


def _operations() -> list[tuple[str, str]]:
    return [(method, path) for path, ops in app.openapi()["paths"].items() for method in ops]


def test_the_spec_marks_every_operation_but_health_and_reference_as_needing_a_key() -> None:
    spec = app.openapi()["paths"]
    open_ops = sorted(path for method, path in _operations() if "security" not in spec[path][method])
    assert open_ops and set(open_ops) <= PUBLIC, open_ops
    assert {"/v1/health", "/health", "/v1/naics/reference"} <= set(open_ops)


def test_every_operation_in_the_spec_actually_refuses_without_a_key(keyed) -> None:
    """Behaviour, not only the spec: call every documented operation (both the /v1 and the
    deprecated unversioned copies) with no credential."""
    ops = _operations()
    assert len(ops) >= 19  # 7 /v1-only routes, plus 6 served at /v1 and unversioned
    for method, path in ops:
        url = path.replace("{code}", "621210")
        r = keyed.request(method, url, json={"description": "Vet clinic."}, params={"q": "vet"})
        assert r.status_code == (200 if path in PUBLIC else 401), (method, path, r.status_code)


@pytest.mark.parametrize("path", ["/classify", "/v1/classify"])
def test_classify_without_a_credential_is_401_on_both_paths(keyed, path) -> None:
    r = keyed.post(path, json=BODY)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method, path, kwargs", [
    ("post", "/v1/consensus", {"json": {"description": "Vet clinic."}}),
    ("post", "/v1/triage", {"json": {"description": "Vet clinic."}}),
    ("post", "/v1/tree-first", {"json": {"description": "Vet clinic."}}),
    ("post", "/v1/assist", {"json": {"description": "Vet clinic."}}),
    ("post", "/v1/refine/assist", {"json": {"description": "Vet clinic."}}),
    ("post", "/v1/finalize", {"json": {"description": "Vet clinic."}}),
    ("get", "/v1/naics/search", {"params": {"q": "vet"}}),
    ("get", "/v1/naics/621210", {}),
    ("get", "/v1/naics", {"params": {"q": "tattoo"}}),
    ("get", "/v1/evals", {}),
    ("get", "/evals", {}),
])
def test_every_protected_route_refuses_without_and_with_a_wrong_key(keyed, method, path, kwargs) -> None:
    assert getattr(keyed, method)(path, **kwargs).status_code == 401
    assert getattr(keyed, method)(path, headers=bearer("wrong-key"), **kwargs).status_code == 403


def test_health_and_reference_stay_public(keyed) -> None:
    assert keyed.get("/v1/health").status_code == 200
    assert keyed.get("/health").status_code == 200
    assert keyed.get("/v1/naics/reference").status_code == 200


# ---- 401 vs 403 ------------------------------------------------------------------------------

@pytest.mark.parametrize("header", ["", "Bearer", "Bearer   ", f"Basic {KEY_A}", KEY_A])
def test_a_missing_or_non_bearer_credential_is_401(keyed, header) -> None:
    r = keyed.post("/v1/classify", json=BODY, headers={"Authorization": header})
    assert r.status_code == 401
    assert KEY_A not in r.text


def test_a_wrong_key_is_403_and_never_echoed(keyed) -> None:
    r = keyed.post("/v1/classify", json=BODY, headers=bearer("not-a-real-key"))
    assert r.status_code == 403
    assert "not-a-real-key" not in r.text and KEY_A not in r.text


def test_a_near_miss_is_403(keyed) -> None:
    for near in (KEY_A[:-1], KEY_A + "x", KEY_A.upper(), f"{KEY_A},{KEY_B}"):
        assert keyed.post("/v1/classify", json=BODY, headers=bearer(near)).status_code == 403


def test_auth_is_checked_before_the_body_is_validated(keyed) -> None:
    assert keyed.post("/v1/classify", json={"description": ""}).status_code == 401


# ---- a good key ------------------------------------------------------------------------------

@pytest.mark.parametrize("key", [KEY_A, KEY_B])
def test_either_configured_key_classifies_so_a_key_can_rotate(keyed, key) -> None:
    r = keyed.post("/v1/classify", json=BODY, headers=bearer(key))
    assert r.status_code == 200
    assert (r.json()["gate"], r.json()["code"]) == ("auto_act", "621210")


def test_the_scheme_name_is_case_insensitive(keyed) -> None:
    assert keyed.post("/v1/classify", json=BODY, headers={"Authorization": f"bearer {KEY_A}"}).status_code == 200


def test_auth_does_not_change_the_result(keyed, monkeypatch) -> None:
    keyed_body = keyed.post("/v1/classify", json=BODY, headers=bearer(KEY_A)).json()
    monkeypatch.delenv("NAICS_API_KEYS")
    monkeypatch.setenv("NAICS_AUTH_DISABLED", "1")
    open_body = TestClient(app).post("/v1/classify", json=BODY).json()
    keyed_body.pop("seconds"), open_body.pop("seconds")
    assert keyed_body == open_body


# ---- constant-time comparison ----------------------------------------------------------------

def test_every_key_is_compared_with_no_early_exit(monkeypatch) -> None:
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: calls.append((len(a), len(b))) or real(a, b))
    keys = [KEY_A, "k2", "a-much-longer-third-key-value"]
    assert auth.key_matches(KEY_A, keys)  # matches the first key, still compares all three
    assert auth.key_matches("x", keys) is False
    assert len(calls) == 6 and {c for c in calls} == {(32, 32)}  # sha256 digests: equal lengths


# ---- startup ---------------------------------------------------------------------------------

def _starts(monkeypatch, **env) -> bool:
    for name in ("NAICS_API_KEYS", "NAICS_AUTH_DISABLED", "NAICS_REQUIRE_AUTH", "NAICS_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    try:
        with TestClient(app):
            return True
    except auth.AuthConfigError:
        return False


@pytest.mark.parametrize("env", [
    {},                                                                    # no keys, no opt-out
    {"NAICS_API_KEYS": " , "},                                             # only blanks
    {"NAICS_AUTH_DISABLED": "0"},                                          # opt-out not on
    {"NAICS_AUTH_DISABLED": "1", "NAICS_REQUIRE_AUTH": "1"},               # contradictory
    {"NAICS_AUTH_DISABLED": "1", "NAICS_REQUIRE_AUTH": "1", "NAICS_API_KEYS": KEY_A},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "0.0.0.0"},                 # the container's bind
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "10.0.0.5"},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "::"},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "naics.internal"},
    {"NAICS_REQUIRE_AUTH": "1"},                                           # required, no keys
    {"NAICS_API_KEYS": "short-key"},                                       # under 32 characters
    {"NAICS_API_KEYS": f"{KEY_A},{'x' * 31}"},                             # one short key spoils all
])
def test_startup_refuses(monkeypatch, env) -> None:
    assert _starts(monkeypatch, **env) is False


@pytest.mark.parametrize("env", [
    {"NAICS_API_KEYS": KEY_A},
    {"NAICS_API_KEYS": KEY_A, "NAICS_HOST": "0.0.0.0", "NAICS_REQUIRE_AUTH": "1"},  # the hosted shape
    {"NAICS_AUTH_DISABLED": "1"},                                          # default host 127.0.0.1
    {"NAICS_AUTH_DISABLED": "true", "NAICS_HOST": "localhost"},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "::1"},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "127.0.0.2"},
])
def test_startup_accepts(monkeypatch, env) -> None:
    assert _starts(monkeypatch, **env) is True


def test_serve_refuses_before_binding(monkeypatch) -> None:
    from jev_naics.api import app as api

    monkeypatch.delenv("NAICS_AUTH_DISABLED")
    monkeypatch.setattr(api.uvicorn, "run", lambda *a, **k: pytest.fail("bound without keys"))
    with pytest.raises(auth.AuthConfigError):
        api.serve()


# ---- request time (no lifespan) --------------------------------------------------------------

@pytest.mark.parametrize("env", [
    {},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_REQUIRE_AUTH": "1"},
    {"NAICS_AUTH_DISABLED": "1", "NAICS_HOST": "0.0.0.0"},
])
def test_without_the_lifespan_a_bad_config_is_503_never_open(monkeypatch, env) -> None:
    for name in ("NAICS_API_KEYS", "NAICS_AUTH_DISABLED", "NAICS_REQUIRE_AUTH", "NAICS_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    client = TestClient(app)  # not entered: the lifespan's startup check never ran
    assert client.post("/v1/classify", json=BODY).status_code == 503
    assert client.get("/v1/health").status_code == 200


def test_keys_win_over_the_opt_out(keyed, monkeypatch) -> None:
    monkeypatch.setenv("NAICS_AUTH_DISABLED", "1")
    assert keyed.post("/v1/classify", json=BODY).status_code == 401


# ---- logs and CORS ---------------------------------------------------------------------------

def test_service_keys_are_redacted_from_log_messages(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_API_KEYS", f"{KEY_A}, {KEY_B}")
    text = logs.redact(f"failed with {KEY_A} then {KEY_B}")
    assert KEY_A not in text and KEY_B not in text and text.count("[redacted]") == 2


def test_cors_lets_the_web_origin_send_a_bearer_header(keyed) -> None:
    r = keyed.options("/v1/classify", headers={"Origin": "http://127.0.0.1:8097",
                                               "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "authorization, content-type"})
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_the_openapi_spec_declares_bearer_and_the_auth_errors() -> None:
    spec = app.openapi()
    assert spec["components"]["securitySchemes"]["bearer"] == {
        "type": "http", "scheme": "bearer", "description": "One of the keys in the service's `NAICS_API_KEYS`."}
    op = spec["paths"]["/v1/classify"]["post"]
    assert op["security"] == [{"bearer": []}] and {"401", "403"} <= set(op["responses"])
    assert "security" not in spec["paths"]["/v1/health"]["get"]


# ---- the opt-out is local only: bind host and connection (review of #1) ----------------------

@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_naics_host_binds_and_checks_the_same_loopback_address(monkeypatch, value) -> None:
    """serve() and the opt-out read one value: an empty NAICS_HOST must not bind every interface
    while the opt-out believes it is on loopback."""
    from jev_naics.api import app as api

    monkeypatch.setenv("NAICS_HOST", value)
    bound = {}
    monkeypatch.setattr(api.uvicorn, "run", lambda _app, **kw: bound.update(kw))
    api.serve()  # the opt-out is on (conftest): accepted only because the bind really is loopback
    assert bound["host"] == auth.bind_host() == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5"])
def test_serve_binds_naics_host_and_refuses_the_opt_out_off_loopback(monkeypatch, host) -> None:
    from jev_naics.api import app as api

    monkeypatch.setenv("NAICS_HOST", host)
    monkeypatch.setattr(api.uvicorn, "run", lambda *a, **k: pytest.fail("bound with auth off"))
    with pytest.raises(auth.AuthConfigError):
        api.serve()
    monkeypatch.delenv("NAICS_AUTH_DISABLED")
    monkeypatch.setenv("NAICS_API_KEYS", KEY_A)
    bound = {}
    monkeypatch.setattr(api.uvicorn, "run", lambda _app, **kw: bound.update(kw))
    api.serve()
    assert bound["host"] == host


def _opt_out_client(base_url: str, client: tuple[str, int]) -> TestClient:
    app.dependency_overrides[get_full_decider] = lambda: Oracle()
    return TestClient(app, base_url=base_url, client=client)


def test_the_opt_out_works_on_a_loopback_connection() -> None:
    try:
        for base, peer in (("http://127.0.0.1:8095", ("127.0.0.1", 50000)), ("http://[::1]:8095", ("::1", 50000))):
            assert _opt_out_client(base, peer).post("/v1/classify", json=BODY).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_a_bare_uvicorn_on_every_interface_does_not_honour_the_opt_out() -> None:
    """`uvicorn jev_naics.api.app:app --host 0.0.0.0` never reads NAICS_HOST, so the env check
    passes; the connection's own server address (a LAN address here) is what refuses it."""
    try:
        r = _opt_out_client("http://192.168.1.20:8095", ("192.168.1.30", 50000)).post("/v1/classify", json=BODY)
        assert r.status_code == 503
        r = _opt_out_client("http://192.168.1.20:8095", ("127.0.0.1", 50000)).post("/v1/classify", json=BODY)
        assert r.status_code == 503  # a loopback client is not enough when the server end is not
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("client", [("10.0.0.7", 50000), ("203.0.113.9", 50000), ("testclient", 50000)])
def test_a_client_from_a_non_loopback_address_does_not_get_the_opt_out(client) -> None:
    try:
        tc = _opt_out_client("http://127.0.0.1:8095", client)
        assert tc.post("/v1/classify", json=BODY).status_code == 503
        assert tc.get("/v1/health").status_code == 200  # public routes are unaffected
    finally:
        app.dependency_overrides.clear()


def test_a_connection_with_no_address_does_not_get_the_opt_out() -> None:
    from starlette.requests import Request

    for scope in ({"type": "http", "server": None, "client": ("127.0.0.1", 1)},
                  {"type": "http", "server": ("127.0.0.1", 8095), "client": None}):
        assert auth.peer_is_loopback(Request(scope)) is False
    assert auth.peer_is_loopback(Request({"type": "http", "server": ("127.0.0.1", 8095),
                                          "client": ("127.0.0.1", 1)})) is True


def test_a_key_of_exactly_the_minimum_length_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv("NAICS_API_KEYS", "k" * auth.MIN_KEY_LENGTH)
    assert auth.auth_enabled() is True
    monkeypatch.setenv("NAICS_API_KEYS", "k" * (auth.MIN_KEY_LENGTH - 1))
    with pytest.raises(auth.AuthConfigError, match="shorter than 32"):
        auth.auth_enabled()


def test_unset_naics_host_with_a_bare_uvicorn_on_every_interface(monkeypatch) -> None:
    """The second bypass in full: NAICS_HOST unset (so the env check passes on the 127.0.0.1
    default) while `uvicorn --host 0.0.0.0` actually accepts a LAN connection."""
    monkeypatch.delenv("NAICS_HOST", raising=False)
    assert auth.auth_enabled() is False  # the env alone would honour the opt-out ...
    try:
        r = _opt_out_client("http://10.1.2.3:8095", ("10.1.2.99", 50000)).post("/v1/classify", json=BODY)
        assert r.status_code == 503  # ... but the connection is not loopback
    finally:
        app.dependency_overrides.clear()


def test_empty_naics_host_request_time(monkeypatch) -> None:
    """NAICS_HOST="" resolves to 127.0.0.1 for the check; a LAN connection is still refused."""
    monkeypatch.setenv("NAICS_HOST", "")
    assert auth.bind_host() == "127.0.0.1"
    try:
        assert _opt_out_client("http://127.0.0.1:8095", ("127.0.0.1", 1)).post(
            "/v1/classify", json=BODY).status_code == 200
        assert _opt_out_client("http://10.1.2.3:8095", ("10.1.2.99", 1)).post(
            "/v1/classify", json=BODY).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_serve_never_trusts_forwarded_headers(monkeypatch) -> None:
    """With uvicorn's default proxy_headers=True, a loopback peer's X-Forwarded-For rewrites
    scope["client"], so a forged `X-Forwarded-For: 127.0.0.1` could satisfy the opt-out."""
    from jev_naics.api import app as api

    bound = {}
    monkeypatch.setattr(api.uvicorn, "run", lambda _app, **kw: bound.update(kw))
    api.serve()
    assert bound["proxy_headers"] is False


def test_a_forwarded_header_does_not_change_the_opt_out_decision() -> None:
    """The app itself never reads X-Forwarded-For: only the connection's own addresses count."""
    try:
        tc = _opt_out_client("http://127.0.0.1:8095", ("10.0.0.7", 50000))
        r = tc.post("/v1/classify", json=BODY, headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
                                                        "Forwarded": "for=127.0.0.1"})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
