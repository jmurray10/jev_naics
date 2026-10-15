"""NAICS full-text search in Postgres (JEV-50): migrations, the loader, and the query.

Search is code, not a model: Postgres ranks industries by how well their official text matches
the words of a description. No Jev call and no cost. A description is a sentence, not keywords, so
each of its words scores on its own and the scores add up: Postgres's weighted rank for that word
(title > index entries, examples, and activities sent here > description) times the word's IDF,
so a rare word ("grooming") counts for more than a common one ("mobile"), as in BM25.
Measured 2026-09-29 over the 38 eval descriptions with a gold code (evals/full_naics*.json):
the right industry was in the top 10 for 31 of 32 dev and 6 of 6 held-out (plain OR ranking
without IDF: 29 of 32 dev); the miss is "subscription SaaS platform" (no Census text says SaaS).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import Connection
from psycopg.errors import UndefinedTable

from jev_naics._jev.env import find_root, load_env_file
from jev_naics.questions import (
    SEARCH_K,
    SPELL_MAX_EDITS,
    SPELL_MAX_WORD,
    SPELL_MIN_SIMILARITY,
    SPELL_MIN_WORD,
    SPELL_WEIGHT,
    TERM_WEIGHT,
    VOCAB_MIN_WORD,
)
from jev_naics.terms import expand_terms
from jev_naics.search_data import SearchData, load_search_data

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"  # package data: installs carry it
_SPELLING = {"min_sim": SPELL_MIN_SIMILARITY, "max_edits": SPELL_MAX_EDITS, "spell_weight": SPELL_WEIGHT,
             "min_word": SPELL_MIN_WORD, "max_word": SPELL_MAX_WORD}
DEFAULT_LIMIT = SEARCH_K  # the measured candidate count (questions.py)
MAX_LIMIT = 50


@dataclass(frozen=True)
class Hit:
    code: str
    title: str
    rank: float
    snippet: str  # the official text around the matching words, from Postgres ts_headline


def database_url() -> str:
    # The environment wins (Compose sets it). A .env file is read only in a source checkout: an
    # installed copy (pip install into another image, JEV-51) has no pyproject.toml above it to find one. When
    # there is one, both the repo root (the folder above backend/) and backend/ itself are read.
    if not os.environ.get("NAICS_DATABASE_URL", "").strip():
        try:
            package = find_root(Path(__file__))
        except FileNotFoundError:
            package = None
        if package is not None:
            for path in (package.parent / ".env", package / ".env"):
                load_env_file(path, frozenset({"NAICS_DATABASE_URL"}))
    url = os.environ.get("NAICS_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("NAICS_DATABASE_URL is missing (Compose sets it; see .env.example).")
    return url


def connect(url: str | None = None) -> Connection:
    return psycopg.connect(url or database_url(), connect_timeout=5)


def _applied(conn: Connection) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute("SELECT filename FROM schema_migrations")}
    except UndefinedTable:
        conn.rollback()
        return set()


def apply_migrations(conn: Connection) -> list[str]:
    """Apply pending migrations in order; returns the ones applied now."""
    applied, new = _applied(conn), []
    try:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            new.append(path.name)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new


# pg_advisory_lock takes an 8-byte signed key; this one is arbitrary but fixed ("JNB1" packed into
# 4 bytes), so every migrate+load (db_cli build, and the API's own migrate-on-start) takes the same
# session lock, and a concurrent second run waits for it instead of racing the same tables (gate
# audit minor 4).
BUILD_LOCK_KEY = 0x4A4E4231


def migrate_and_load(conn: Connection, data: SearchData) -> tuple[list[str], bool]:
    """apply_migrations then load_search, holding one session advisory lock for both so a second
    concurrent call on another connection blocks until this one finishes, instead of racing it.
    The lock is released before returning, even when either step raises."""
    conn.execute("SELECT pg_advisory_lock(%s)", (BUILD_LOCK_KEY,))
    try:
        applied = apply_migrations(conn)
        loaded = load_search(conn, data)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (BUILD_LOCK_KEY,))
        conn.commit()
    return applied, loaded


_INSERT = """
INSERT INTO naics_industry (code, title, description, examples, index_entries, excludes, sent_here, search)
VALUES (%(code)s, %(title)s, %(description)s, %(examples)s, %(index_entries)s, %(excludes)s, %(sent_here)s,
        setweight(to_tsvector('english', %(title)s), 'A')
        || setweight(to_tsvector('english', %(weight_b)s), 'B')
        || setweight(to_tsvector('english', %(description)s), 'C'))
"""


def load_search(conn: Connection, data: SearchData) -> bool:
    """Load every industry when the stored version differs. Returns True when it (re)loaded.
    One transaction: a failed load leaves the previous data in place."""
    row = conn.execute("SELECT value FROM naics_search_meta WHERE key = 'version'").fetchone()
    words = conn.execute("SELECT value FROM naics_search_meta WHERE key = 'words_version'").fetchone()
    if row and row[0] == data.version and words and words[0] == words_version():
        conn.commit()
        return False
    try:
        conn.execute("DELETE FROM naics_industry")
        with conn.cursor() as cur:
            cur.executemany(_INSERT, [
                {"code": i.code, "title": i.title, "description": i.description, "examples": list(i.examples),
                 "index_entries": list(i.index_entries), "excludes": json.dumps([list(e) for e in i.excludes]),
                 "sent_here": list(i.sent_here),
                 "weight_b": " ; ".join([*i.index_entries, *i.examples, *i.sent_here])}
                for i in data.industries.values()
            ])
        conn.execute("DELETE FROM naics_word")
        conn.execute(_WORDS, {"vocab_min": VOCAB_MIN_WORD})
        conn.execute("INSERT INTO naics_search_meta (key, value) VALUES ('words_version', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (words_version(),))
        conn.execute("DELETE FROM naics_lexeme")
        conn.execute("INSERT INTO naics_lexeme (word, ndoc) SELECT word, ndoc FROM ts_stat('SELECT search FROM naics_industry')")
        conn.execute("INSERT INTO naics_search_meta (key, value) VALUES ('version', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (data.version,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


# Every word of the Census text, for spelling correction (JEV-52).
_WORDS = """
INSERT INTO naics_word (word)
SELECT DISTINCT lower(w) FROM naics_industry,
     regexp_split_to_table(title || ' ' || description || ' ' || array_to_string(examples, ' ') || ' '
                           || array_to_string(index_entries, ' ') || ' ' || array_to_string(sent_here, ' '),
                           '[^A-Za-z]+') AS w
WHERE length(w) >= %(vocab_min)s
"""

# JEV-52: a typed word (SPELL_MIN_WORD to SPELL_MAX_WORD letters) the Census text lacks is corrected to the Census word with
# the fewest edits (at most max_edits), then the highest trigram similarity (at least min_sim).
_CORRECTIONS = """
    SELECT u.typed, best.word
    FROM (SELECT DISTINCT ON (w) w AS typed, ord
          FROM regexp_split_to_table(lower(%(text)s), '[^a-z]+') WITH ORDINALITY AS t(w, ord)
          WHERE length(w) BETWEEN %(min_word)s AND %(max_word)s ORDER BY w, ord) u
    CROSS JOIN LATERAL (
        SELECT nw.word FROM naics_word nw
        WHERE similarity(nw.word, u.typed) >= %(min_sim)s AND levenshtein(nw.word, u.typed) <= %(max_edits)s
        ORDER BY levenshtein(nw.word, u.typed), similarity(nw.word, u.typed) DESC, nw.word
        LIMIT 1
    ) best
    WHERE NOT EXISTS (SELECT 1 FROM naics_word k WHERE k.word = u.typed)
      -- Known when its stem is a Census word ("sells" -> sell): an inflection, not a misspelling.
      -- A stop word has no stem and is never corrected either.
      AND EXISTS (SELECT 1 FROM unnest(to_tsvector('english', u.typed)) s)
      AND NOT EXISTS (SELECT 1 FROM unnest(to_tsvector('english', u.typed)) s
                      JOIN naics_lexeme l ON l.word = s.lexeme)
    ORDER BY u.ord
"""

# Each word scores alone: ts_rank for that one word times its IDF, summed per industry. The typed
# words count fully; a spelling correction counts at spell_weight, and a listed modern term's
# Census wording at term_weight (JEV-52). quote_literal keeps each
# word a literal, so the text is never read as query syntax; 'simple' because the words are stemmed.
_SEARCH = """
WITH fixes AS (""" + _CORRECTIONS + """), weighted AS (
    SELECT t.lexeme, 1.0::float AS weight FROM unnest(to_tsvector('english', %(text)s)) t
    UNION ALL
    SELECT t.lexeme, %(spell_weight)s::float FROM fixes f, unnest(to_tsvector('english', f.word)) t
    UNION ALL
    SELECT t.lexeme, %(term_weight)s::float FROM unnest(to_tsvector('english', %(expansion)s)) t
), terms AS (
    SELECT w.lexeme, max(w.weight) * ln(1 + (n.total - l.ndoc + 0.5) / (l.ndoc + 0.5)) AS idf,
           to_tsquery('simple', quote_literal(w.lexeme)) AS query
    FROM weighted w
    JOIN naics_lexeme l ON l.word = w.lexeme
    CROSS JOIN (SELECT count(*)::float AS total FROM naics_industry) n
    GROUP BY w.lexeme, l.ndoc, n.total
), ranked AS (
    SELECT i.code, sum(ts_rank(i.search, t.query, 1) * t.idf) AS rank
    FROM naics_industry i JOIN terms t ON i.search @@ t.query
    GROUP BY i.code
    ORDER BY rank DESC, i.code
    LIMIT %(limit)s
)
SELECT i.code, i.title, r.rank,
       ts_headline('english', i.description || ' Index: ' || array_to_string(i.index_entries, '; '),
                   (SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) FROM terms),
                   'MaxFragments=2, MaxWords=18, MinWords=6, FragmentDelimiter=" … "') AS snippet
FROM ranked r JOIN naics_industry i USING (code)
ORDER BY r.rank DESC, i.code
"""


def search(conn: Connection, text: str, limit: int = DEFAULT_LIMIT) -> list[Hit]:
    """Industries ranked by how well their official text matches `text`; [] when nothing does."""
    if not text or not text.strip():
        return []
    # Migrated but never loaded (a failed load at start), or loaded with data this code no longer
    # matches (a stale database must not act): an error, never "nothing matches" (gate audit F1).
    row = conn.execute("SELECT value FROM naics_search_meta WHERE key = 'version'").fetchone()
    if row is None:
        conn.rollback()
        raise RuntimeError("the NAICS search data is not loaded")
    current = load_search_data().version  # package data, cached: no extra cost per search
    if row[0] != current:
        conn.rollback()
        raise RuntimeError(f"the NAICS search data is version {row[0]}, not this code's {current}; rebuild")
    expansion = " ".join(census for _term, census in expand_terms(text[:4000]))
    rows = conn.execute(_SEARCH, {"text": text[:4000], "limit": max(1, min(limit, MAX_LIMIT)),
                                  "expansion": expansion, "term_weight": TERM_WEIGHT, **_SPELLING}).fetchall()
    conn.commit()
    return [Hit(code=r[0], title=r[1], rank=float(r[2]), snippet=r[3]) for r in rows]


def words_version() -> str:
    """A hash of how the spelling vocabulary is built, stored with it so a change reloads it."""
    return hashlib.sha256(f"{_WORDS}|{VOCAB_MIN_WORD}".encode("utf-8")).hexdigest()[:16]


def spelling_corrections(conn: Connection, text: str) -> list[tuple[str, str]]:
    """(typed, corrected) for each typed word the search corrected, in the order typed."""
    if not text or not text.strip():
        return []
    rows = conn.execute(_CORRECTIONS, {"text": text[:4000], **_SPELLING}).fetchall()
    conn.commit()
    return [(r[0], r[1]) for r in rows]


def verify(conn: Connection) -> list[str]:
    """What is wrong with the loaded search data; empty when it is ready."""
    version = conn.execute("SELECT value FROM naics_search_meta WHERE key = 'version'").fetchone()
    if version is None:
        return ["no data loaded (run: python -m jev_naics.db_cli build)"]
    problems = []
    stale = version[0] != load_search_data().version
    if stale:
        problems.append(f"data version {version[0]} is not this code's {load_search_data().version}; rebuild")
    count = conn.execute("SELECT count(*) FROM naics_industry").fetchone()[0]
    if count != 1012:
        problems.append(f"{count} industries loaded, expected 1012")
    if conn.execute("SELECT count(*) FROM naics_word").fetchone()[0] < 5000:
        problems.append("the spelling vocabulary is missing or short")
    # search() itself now refuses a stale version (gate audit F1); do not run it against data
    # already known to be wrong, so the known-query check never masks the real problem with its own.
    if not stale:
        hits = search(conn, "small-animal veterinary clinic", 1)
        if not hits or hits[0].code != "541940":
            problems.append("the known query 'small-animal veterinary clinic' does not return 541940 first")
    conn.commit()
    return problems
