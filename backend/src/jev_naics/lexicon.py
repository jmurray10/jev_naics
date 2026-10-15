"""The lexicon: how people describe a business, read as the Census would before Jev sees it (E15).

People write "SaaS", "HVAC", "ghost kitchen", "dropshipping"; the Census text does not. A lexicon entry maps such a
term to Census wording (words that appear in the Census titles, descriptions, or index entries of the industry
the term usually means), never to a code. Code reads the text against the lexicon before any Jev call (PEAS: a
sensor stage), so that
  1. a recognised term is enough for a short text to be checked ("saas" is one word and means something), and
  2. the Census wording is added after what the user wrote, so Jev reads the user's own words and the Census
     words together. Nothing is replaced and no code is chosen here: Jev still picks from a list code built.
A term that names no industry ("b2b", "fintech") has an empty `census`: it is recognised (the text is not
too short) and adds nothing to what Jev sees. Matching is whole-word, case-insensitive, longest term first.

Sources: a reviewed JSON file (experiments, and the packaged fallback) or the `naics_lexicon` table in its own
database (`lexicon.sql`; never the Census tables). Each entry carries who reviewed it, when, and why.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

LEXICON_PATH = Path(__file__).resolve().parent / "data" / "naics_lexicon.json"
MAX_EXPANSION_CHARS = 400  # what is appended for Jev: a handful of terms, never a paragraph


@dataclass(frozen=True)
class Entry:
    term: str
    census: str  # Census wording the term means; empty when the term names no industry
    why: str
    reviewed_by: str = ""
    reviewed_on: str = ""


@dataclass(frozen=True)
class Reading:
    """What code read in the text: the recognised terms, and the text Jev sees."""

    hits: tuple[Entry, ...]
    text: str  # the user's own words, then the Census wording for each hit, in this order

    @property
    def recognised(self) -> bool:
        return bool(self.hits)


class Lexicon:
    def __init__(self, entries: list[Entry], expand_max_words: int | None = None) -> None:
        # The Census wording is added only to a text of at most this many words (None: always). A term in a long
        # description is already surrounded by the user's own context, and the added wording can read as a second
        # activity (E15: a soybean farm that mentions a podcast stopped coding). Recognition is never limited.
        self.expand_max_words = expand_max_words
        # Longest term first, so "software as a service" is read before "service"-like shorter terms.
        self.entries = tuple(sorted(entries, key=lambda e: (-len(e.term), e.term)))
        self._patterns = tuple(
            (e, re.compile(rf"(?<![a-z0-9]){re.escape(e.term.lower())}(?![a-z0-9])")) for e in self.entries)

    @classmethod
    def from_json(cls, path: Path, expand_max_words: int | None = None) -> "Lexicon":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Entry(t["term"].lower(), t.get("census", ""), t["why"], t.get("reviewed_by", ""),
                          t.get("reviewed_on", "")) for t in raw["terms"]], expand_max_words)

    @classmethod
    def from_db(cls, conn: Any, expand_max_words: int | None = None) -> "Lexicon":
        """The `naics_lexicon` table of the lexicon database (lexicon.sql). Nothing else is read."""
        rows = conn.execute("SELECT term, census, why, reviewed_by, reviewed_on::text FROM naics_lexicon "
                            "WHERE active ORDER BY term").fetchall()
        return cls([Entry(r[0].lower(), r[1], r[2], r[3] or "", r[4] or "") for r in rows], expand_max_words)

    def read(self, text: str) -> Reading:
        lowered = " ".join(text.lower().split())
        hits: list[Entry] = []
        used = lowered
        for entry, pattern in self._patterns:
            if pattern.search(used):
                hits.append(entry)
                used = pattern.sub(" ", used)  # a longer term consumes its words: "app" inside "mobile app"
        extra = []
        for e in hits:
            if e.census and e.census not in extra:
                extra.append(e.census)
        wording = "; ".join(extra)[:MAX_EXPANSION_CHARS]
        if self.expand_max_words is not None and len(text.split()) > self.expand_max_words:
            wording = ""  # recognised, but the user's own words are enough
        return Reading(tuple(hits), f"{text.strip()} [Census wording: {wording}]" if wording else text.strip())


@lru_cache(maxsize=1)
def default_lexicon() -> Lexicon:
    """The packaged lexicon with the measured expansion limit (questions.REFINE_EXPAND_MAX_WORDS)."""
    from jev_naics.questions import REFINE_EXPAND_MAX_WORDS

    return Lexicon.from_json(LEXICON_PATH, REFINE_EXPAND_MAX_WORDS)


__all__ = ["Entry", "Lexicon", "Reading", "MAX_EXPANSION_CHARS", "LEXICON_PATH", "default_lexicon"]
