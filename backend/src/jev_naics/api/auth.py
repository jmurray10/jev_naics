"""Service authentication for the hosted API (SECURITY.md, "Service authentication").

How it works:

- Callers send `Authorization: Bearer <key>`.
- Valid keys come from `NAICS_API_KEYS`, comma-separated, so a key can be rotated with no
  downtime: add the new key, move the caller, then remove the old key.
- A presented key is compared in constant time: both sides are hashed with sha256 and every
  configured key is compared with `hmac.compare_digest`, with no early exit.
- A missing credential answers 401 (with `WWW-Authenticate: Bearer`); a wrong one answers 403.
- It fails closed. The API refuses to start with no keys, and a request that reaches a protected
  route without a usable configuration (for example a process started without its lifespan) gets
  503, never an open service.
- `NAICS_AUTH_DISABLED=1` switches auth off, for local tests only. It is refused unless the bind
  host (`NAICS_HOST`, the same value `serve()` binds) is loopback, and refused together with
  `NAICS_REQUIRE_AUTH=1`. Even then it is honoured per request only when the connection's server and
  client addresses are both loopback. It is ignored while keys are configured: keys always win.

Every variable here is read from the environment only, never from `.env` (`jev_naics.env` reads
only the TypeSafe key). There is no rate limiter in this service, so nothing is bucketed by key.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

API_KEYS_ENV = "NAICS_API_KEYS"
AUTH_DISABLED_ENV = "NAICS_AUTH_DISABLED"
REQUIRE_AUTH_ENV = "NAICS_REQUIRE_AUTH"
DEFAULT_HOST = "127.0.0.1"
# A shorter key is refused at startup: it is weak, and log redaction (logs.redact replaces every
# configured key wherever it appears) would mangle ordinary log text around a short common string.
MIN_KEY_LENGTH = 32

# Declares the Bearer scheme in the OpenAPI spec (Swagger's Authorize button). It never decides a
# request: auto_error=False, and require_api_key reads the header itself so that a missing
# credential is 401 and a wrong one 403, which HTTPBearer's own errors do not distinguish.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearer",
                           description=f"One of the keys in the service's `{API_KEYS_ENV}`.")


class AuthConfigError(RuntimeError):
    """The auth environment is unusable: the API refuses to start, and protected routes answer 503."""


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def api_keys() -> list[str]:
    """The configured keys from NAICS_API_KEYS (comma-separated; blanks dropped)."""
    return [k.strip() for k in os.environ.get("NAICS_API_KEYS", "").split(",") if k.strip()]


def bind_host() -> str:
    """NAICS_HOST, the address `serve()` binds: the one source of truth for both the bind and the
    opt-out's loopback check. Unset, empty, or whitespace all mean 127.0.0.1 in both places."""
    return os.environ.get("NAICS_HOST", "").strip() or DEFAULT_HOST


def is_loopback(host: str) -> bool:
    """True for localhost and any loopback address (127.0.0.0/8, ::1). 0.0.0.0 is not loopback."""
    host = host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_enabled() -> bool:
    """True when requests must carry a key; False only for the local-test opt-out.

    Raises AuthConfigError when the environment is unusable: no keys and no opt-out; the opt-out
    together with NAICS_REQUIRE_AUTH; the opt-out on a host that is not loopback; or
    NAICS_REQUIRE_AUTH with no keys; any key shorter than MIN_KEY_LENGTH."""
    keys = api_keys()
    disabled = _truthy(os.environ.get("NAICS_AUTH_DISABLED", ""))
    required = _truthy(os.environ.get("NAICS_REQUIRE_AUTH", ""))
    if disabled and required:
        raise AuthConfigError(f"{AUTH_DISABLED_ENV} and {REQUIRE_AUTH_ENV} are both set; unset one")
    short = sum(len(k) < MIN_KEY_LENGTH for k in keys)
    if short:
        raise AuthConfigError(f"{API_KEYS_ENV}: {short} key(s) shorter than {MIN_KEY_LENGTH} characters; "
                              "generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    if keys:
        return True  # keys always win over the opt-out
    if required:
        raise AuthConfigError(f"{REQUIRE_AUTH_ENV} is set but {API_KEYS_ENV} has no keys")
    if not disabled:
        raise AuthConfigError(f"{API_KEYS_ENV} has no keys; set it (comma-separated), or "
                              f"{AUTH_DISABLED_ENV}=1 for a local test on a loopback host")
    if not is_loopback(bind_host()):
        raise AuthConfigError(f"{AUTH_DISABLED_ENV} is refused: NAICS_HOST is not a loopback address")
    return False


def peer_is_loopback(request: Request) -> bool:
    """True only when both ends of this connection are loopback: the address the server accepted
    it on (`scope["server"]`) and the client's (`scope["client"]`). Either missing (a Unix socket,
    an unusual server) is not loopback. This is what holds the opt-out to local use even when the
    process was started some other way than `serve()`, e.g. a bare `uvicorn --host 0.0.0.0`, which
    never reads NAICS_HOST."""
    for end in ("server", "client"):
        addr = request.scope.get(end)
        if not addr or not is_loopback(str(addr[0])):
            return False
    return True


def check_startup() -> None:
    """Called first in the API's lifespan: an unusable auth environment refuses to start."""
    auth_enabled()


def presented_key(request: Request) -> str | None:
    """The credential in `Authorization: Bearer <key>`, or None when there is none."""
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        return None
    return credential.strip()


def key_matches(presented: str, keys: list[str]) -> bool:
    """Constant-time membership: sha256 both sides (equal lengths, so compare_digest leaks no
    length), and compare against every key with no early exit, so timing does not reveal which
    key matched, or whether one did."""
    digest = hashlib.sha256(presented.encode("utf-8")).digest()
    matched = False
    for key in keys:
        matched |= hmac.compare_digest(digest, hashlib.sha256(key.encode("utf-8")).digest())
    return matched


def require_api_key(request: Request,
                    _documented: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> None:
    """FastAPI dependency on every protected route. 503 when auth is not configured, 401 with no
    credential, 403 with a wrong one. Re-reads the environment on each request, so the opt-out's
    host and NAICS_REQUIRE_AUTH rules hold at request time too, not only at startup. The opt-out is
    honoured only on a connection whose server and client addresses are both loopback; any other
    connection gets 503, as if no auth were configured."""
    try:
        enabled = auth_enabled()
    except AuthConfigError as exc:
        raise HTTPException(status_code=503, detail="service authentication is not configured") from exc
    if not enabled:
        if peer_is_loopback(request):
            return
        raise HTTPException(status_code=503, detail="service authentication is not configured")
    presented = presented_key(request)
    if presented is None:
        raise HTTPException(status_code=401, detail="missing credential: send Authorization: Bearer <key>",
                            headers={"WWW-Authenticate": "Bearer"})
    if not key_matches(presented, api_keys()):
        raise HTTPException(status_code=403, detail="invalid credential")
