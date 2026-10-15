"""Shared test setup.

The API refuses to start without service keys (SECURITY.md). The existing API tests exercise
classification, not auth, so every test starts with the local-test opt-out on a loopback host and
no keys. The auth tests (test_auth.py) set their own environment over this."""

import pytest
from fastapi.testclient import TestClient

_REAL_INIT = TestClient.__init__


def _loopback_init(self, app, base_url="http://127.0.0.1", *args, client=("127.0.0.1", 50000), **kwargs):
    """The opt-out is honoured only when the connection's server and client are both loopback.
    Starlette's TestClient defaults to the names "testserver" and "testclient", which are not
    addresses, so present the in-process client as a local developer's would be: 127.0.0.1 on
    both ends. A test can still pass its own base_url or client (test_auth.py does)."""
    _REAL_INIT(self, app, base_url, *args, client=client, **kwargs)


@pytest.fixture(autouse=True)
def _local_auth_opt_out(monkeypatch):
    for name in ("NAICS_API_KEYS", "NAICS_REQUIRE_AUTH", "NAICS_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NAICS_AUTH_DISABLED", "1")
    monkeypatch.setattr(TestClient, "__init__", _loopback_init)
