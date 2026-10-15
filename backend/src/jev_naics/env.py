"""Load TYPESAFE_API_KEY from the project .env (loader lives in jev_naics._jev.env)."""

from __future__ import annotations

import os
from pathlib import Path

from jev_naics._jev.env import find_root, load_env_file

ALLOWED_ENV_KEYS = frozenset({"TYPESAFE_API_KEY"})


def repo_root() -> Path:
    """The package root (evals/, transcripts/, data/). In a source checkout that is the folder
    with pyproject.toml; an installed copy has none, so fall back to the src-layout position
    instead of failing at import. Only evals and recording use these paths; the tree ships as
    package data (JEV-49: other services install this package)."""
    try:
        return find_root(Path(__file__))
    except FileNotFoundError:
        return Path(__file__).resolve().parents[2]


def load_typesafe_api_key(root: Path | None = None) -> str | None:
    """Set TYPESAFE_API_KEY from .env if it is not already in the process env: the given root, else
    the repo root (the folder above backend/), else backend/."""
    if root:
        load_env_file(root / ".env", ALLOWED_ENV_KEYS)
    else:
        # An installed copy (JEV-55: any pip install) has no pyproject.toml above
        # it to find a repo root; there is then no .env to read, and the environment still wins.
        try:
            package = find_root(Path(__file__))
        except FileNotFoundError:
            package = None
        if package is not None:
            for path in (package.parent / ".env", package / ".env"):
                load_env_file(path, ALLOWED_ENV_KEYS)
    return os.environ.get("TYPESAFE_API_KEY", "").strip() or None
