"""Modern business terms (JEV-52): words the 2022 Census NAICS text does not use ("SaaS",
"telehealth", "ghost kitchen"), each expanded to Census wording before search.

The list is data (data/naics_terms.json), reviewed by a person and versioned in git; its hash
goes into the saved search results' fingerprint. A term expands to words, never to a code: search
still ranks every industry and Jev still picks. Matching is whole-word and case-insensitive.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TERMS_PATH = Path(__file__).resolve().parent / "data" / "naics_terms.json"


@dataclass(frozen=True)
class Term:
    term: str
    census: str  # Census wording the term means; empty when the term names no industry
    why: str


@lru_cache(maxsize=1)
def load_terms() -> tuple[Term, ...]:
    raw = json.loads(TERMS_PATH.read_text(encoding="utf-8"))
    return tuple(Term(t["term"].lower(), t["census"], t["why"]) for t in raw["terms"])


def _normalized_sha256(path: Path) -> str:
    """A hash of `path`'s bytes with CRLF folded to LF first (JEV-55 Ruling 12), so a file that is
    checked out with Windows line endings (a Windows checkout before .gitattributes normalizes it,
    or any not-yet-normalized tree) fingerprints the same as its LF checkout. Without this, the
    saved search results' fingerprint (search_eval.search_sql_fingerprint, which folds in
    terms_version and terms_code_version) goes stale on every CRLF/LF mismatch even though nothing
    about the terms or the matching code actually changed."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def terms_version() -> str:
    return _normalized_sha256(TERMS_PATH)


def terms_code_version() -> str:
    """A hash of this module (how terms are matched), for the saved search results' fingerprint."""
    return _normalized_sha256(Path(__file__))


def expand_terms(text: str) -> list[tuple[str, str]]:
    """(term, Census wording) for each listed term in `text`, whole words only, in list order.
    A term that names no industry ("b2b") is matched but expands to nothing, so it is left out."""
    lowered = text.lower()
    found = []
    for t in load_terms():
        if t.census and re.search(rf"(?<![a-z0-9]){re.escape(t.term)}(?![a-z0-9])", lowered):
            found.append((t.term, t.census))
    return found
