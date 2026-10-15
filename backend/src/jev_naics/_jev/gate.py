# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-common/src/jev_common/gate.py; keep in sync by hand (CHANGELOG.md).
"""Numbers a gate may trust. Anything else fails closed."""

from __future__ import annotations

import math


def is_unit_probability(value: object) -> bool:
    """A usable model number: a finite float in [0, 1]. NaN, inf, bool, and None are not."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def json_number(value: float | None) -> float | None:
    """JSON has no NaN or inf; an unusable number is null."""
    return value if value is not None and math.isfinite(value) else None
