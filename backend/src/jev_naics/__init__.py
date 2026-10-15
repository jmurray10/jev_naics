"""NAICS 2022 from a description of operations, with TypeSafe Jev.

    from jev_naics import classify
    r = classify("Mobile car detailing at customers' homes")   # method="hybrid"
    r.gate, r.code, r.title, r.probability, r.fit_noul, r.hold_reason, r.fallback

hybrid and search need the NAICS search database (NAICS_DATABASE_URL); tree does not. The decider is
live Jev with TYPESAFE_API_KEY, else recorded replay (JEV_DECIDER selects record modes).
"""

from __future__ import annotations

from typing import Literal

from jev_naics._jev import Decider, decider_for
from jev_naics.db import Hit
from jev_naics.env import load_typesafe_api_key
from jev_naics.full import FULL_TRANSCRIPT, FullResult, classify_full
from jev_naics.hybrid import HYBRID_TRANSCRIPT, classify_hybrid
from jev_naics.search_pick import SEARCH_TRANSCRIPT, Searcher, classify_search

__version__ = "0.1.1"

Method = Literal["hybrid", "search", "tree"]
NaicsResult = FullResult
_TRANSCRIPTS = {"hybrid": HYBRID_TRANSCRIPT, "search": SEARCH_TRANSCRIPT, "tree": FULL_TRANSCRIPT}


def _database_configured() -> bool:
    from jev_naics import db

    try:
        return bool(db.database_url())
    except RuntimeError:
        return False


def database_searcher() -> Searcher:
    """Search the configured NAICS database, one connection per call."""
    from jev_naics import db

    def search(text: str, limit: int) -> list[Hit]:
        with db.connect() as conn:
            return db.search(conn, text, limit)

    return search


def classify(description: str, *, method: Method = "hybrid", decider: Decider | None = None,
             searcher: Searcher | None = None) -> NaicsResult:
    if method not in _TRANSCRIPTS:
        raise ValueError(f"method must be hybrid, search, or tree; got {method!r}")
    if decider is None:
        load_typesafe_api_key()
        decider = decider_for(_TRANSCRIPTS[method])
    if method == "tree":
        return classify_full(description, decider=decider)
    if searcher is None:
        if not _database_configured():
            raise RuntimeError(f"method {method!r} needs the NAICS search database: set NAICS_DATABASE_URL "
                               "(see db/README.md), or use method='tree'")
        searcher = database_searcher()
    if method == "search":
        return classify_search(description, decider=decider, searcher=searcher)
    return classify_hybrid(description, decider=decider, searcher=searcher)


__all__ = ["Method", "NaicsResult", "classify", "database_searcher"]
