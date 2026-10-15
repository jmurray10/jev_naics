"""Structured logs (JEV-56): one JSON object per line on stdout, which a container platform (AWS
CloudWatch, via the task's log driver) collects as is.

Never logged: a description's text (it comes from client documents; a classify line carries its
SHA-256 prefix and length instead), a raw query string, the TypeSafe key, a service key (NAICS_API_KEYS), or the database URL.
`redact` strips the last three from any message before it is written.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER = "jev_naics"
log = logging.getLogger(LOGGER)
_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_SECRET_ENV = ("TYPESAFE_API_KEY", "NAICS_DATABASE_URL")
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s:]*:[^/@\s]*@", re.IGNORECASE)
_PASSWORD_PARAM = re.compile(r"(password\s*=\s*)\S+", re.IGNORECASE)
MAX_MESSAGE = 200


def log_level() -> int:
    """NAICS_LOG_LEVEL: DEBUG, INFO (default), WARNING, or ERROR. Anything else refuses to start."""
    value = os.environ.get("NAICS_LOG_LEVEL", "").strip().upper() or "INFO"
    if value not in _LEVELS:
        raise ValueError(f"NAICS_LOG_LEVEL must be one of {', '.join(_LEVELS)}; got {value!r}")
    return _LEVELS[value]


def redact(text: str, also: tuple[str, ...] = ()) -> str:
    """`text` without the TypeSafe key, the service's NAICS_API_KEYS, the database URL, any URL's user:password, or the `also` strings (a
    request's description), cut to MAX_MESSAGE."""
    secrets = [os.environ.get(name, "").strip() for name in _SECRET_ENV] + [a.strip() for a in also]
    secrets += [k.strip() for k in os.environ.get("NAICS_API_KEYS", "").split(",")]  # each service key
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = _URL_CREDENTIALS.sub(r"\1[redacted]@", text)
    text = _PASSWORD_PARAM.sub(r"\1[redacted]", text)
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE - 1] + "…"


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ts, level, logger, event, then the event's fields."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name,
            "event": getattr(record, "event", None) or "log"}
        fields = getattr(record, "fields", None)
        if fields:
            out.update(fields)
        else:  # a plain log call (a library's, or ours without fields): its message, redacted
            out["message"] = redact(record.getMessage())
        if record.exc_info and record.exc_info[0] is not None:
            out.setdefault("error", record.exc_info[0].__name__)
        return json.dumps(out, default=str, ensure_ascii=False)


def configure() -> None:
    """Send the jev_naics logger to stdout as JSON at NAICS_LOG_LEVEL. Idempotent."""
    level = log_level()
    log.setLevel(level)
    log.propagate = False
    if not any(getattr(h, "_jev_naics", False) for h in log.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._jev_naics = True  # type: ignore[attr-defined]
        log.addHandler(handler)


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log one event line. Callers pass only fields that are safe to log: never free text."""
    log.log(level, name, extra={"event": name, "fields": fields})


def error(exc: BaseException, also: tuple[str, ...] = (), **fields: Any) -> None:
    """An "error" line: the exception's type and its redacted, shortened message (`also`: extra
    text to strip too). No traceback: its frames can quote a connection string. Startup and
    data_load stage errors only: there is no client text in play there, so a message is useful and
    safe once the known secrets are stripped. Never call this for a request-path error — use
    `request_error` there, where the message could otherwise carry a client's own text."""
    event("error", logging.ERROR, error=type(exc).__name__, message=redact(str(exc), also), **fields)


def request_error(exc: BaseException, **fields: Any) -> None:
    """An "error" line for a failure on the request path: the exception's type only, never its
    message. A request's text (a description, a search query) is attacker- or client-controlled,
    and exact-substring redaction only strips text that matches a known secret verbatim — a
    fragment, a re-encoding, or just a different piece of the same text would still get through.
    Logging no message at all is the only fail-closed choice here."""
    event("error", logging.ERROR, error=type(exc).__name__, **fields)
