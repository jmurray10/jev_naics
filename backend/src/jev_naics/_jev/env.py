# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-common/src/jev_common/env.py; keep in sync by hand (CHANGELOG.md).
"""Load allowlisted keys from a .env file without extra dependencies.

`env_path` here keeps the monorepo branch verbatim; in this repo (no `packages/` directory) it
returns `backend/.env`. The repo-root `.env` (the folder above `backend/`) is also read, by
`jev_naics.env.load_typesafe_api_key` and `jev_naics.db.database_url`, not by this function.
"""

from __future__ import annotations

import os
from collections.abc import Collection
from pathlib import Path


def find_root(start: Path) -> Path:
    """Nearest directory at or above `start` that holds a pyproject.toml."""
    for parent in (start.resolve(), *start.resolve().parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise FileNotFoundError(f"no pyproject.toml above {start}")


def env_path(start: Path) -> Path:
    """The one .env for the whole monorepo: the nearest directory holding `packages/`.

    Outside the monorepo (the Docker image), the package's own .env; Compose passes
    keys into the container, so that file normally does not exist there.
    """
    package_root = find_root(start)
    # Only a package that sits directly in <repo>/packages/ belongs to the monorepo.
    # (Walking up for any "packages" dir would match e.g. Windows' AppData\Local\Packages.)
    if package_root.parent.name == "packages":
        return package_root.parent.parent / ".env"
    return package_root / ".env"


def load_env_file(path: Path, allowed: Collection[str]) -> None:
    """Set allowed keys from `path`. A key already in the process env wins."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in allowed or os.environ.get(key, "").strip():
            continue
        value = value.strip().strip("'").strip('"')
        if value:
            os.environ[key] = value
