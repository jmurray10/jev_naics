# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-runtime/src/jev_runtime/providers.py; keep in sync by hand (CHANGELOG.md).
"""Model names live in providers.yaml and nowhere else."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

PROVIDERS_PATH = Path(__file__).with_name("providers.yaml")


@cache
def _providers() -> dict[str, str]:
    return dict(yaml.safe_load(PROVIDERS_PATH.read_text(encoding="utf-8")))


def model_for(decider: str) -> str:
    try:
        return _providers()[decider]
    except KeyError:
        raise KeyError(f"no model for decider {decider!r} in {PROVIDERS_PATH.name}") from None
