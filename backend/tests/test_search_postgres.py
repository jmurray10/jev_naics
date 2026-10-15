"""NAICS full-text search in Postgres (JEV-50). Skipped unless NAICS_TEST_DATABASE_URL names a
database ending in `_test` (its schema is rebuilt per test run).

    docker compose exec postgres createdb -U jev jev_naics_test
    set NAICS_TEST_DATABASE_URL=postgresql://jev:jev@127.0.0.1:5434/jev_naics_test
"""

import os

import pytest

psycopg = pytest.importorskip("psycopg")

from jev_naics.db import apply_migrations, connect, load_search, search  # noqa: E402
from jev_naics.search_data import load_search_data  # noqa: E402

URL = os.environ.get("NAICS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.rsplit("/", 1)[-1].endswith("_test"),
                                reason="set NAICS_TEST_DATABASE_URL to a *_test database")


@pytest.fixture(scope="module")
def conn():
    with connect(URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
        apply_migrations(c)
        assert load_search(c, load_search_data()) is True
        yield c


def test_every_industry_is_loaded_once_and_a_same_version_reload_is_a_no_op(conn) -> None:
    assert conn.execute("SELECT count(*) FROM naics_industry").fetchone()[0] == 1012
    assert load_search(conn, load_search_data()) is False
    version = conn.execute("SELECT value FROM naics_search_meta WHERE key = 'version'").fetchone()[0]
    assert version == load_search_data().version


def test_a_plain_description_finds_its_industry_by_the_official_words(conn) -> None:
    hits = search(conn, "Small-animal veterinary clinic providing vaccinations and spay and neuter surgery.")
    assert hits[0].code == "541940" and hits[0].title == "Veterinary Services"
    assert hits[0].rank > hits[-1].rank and hits[0].snippet


def test_an_activity_another_industry_sends_here_is_found_here(conn) -> None:
    # The pet grooming note sits under 541940 as an exclusion; search finds it at 812910.
    hits = search(conn, "dog grooming and boarding", limit=5)
    assert hits[0].code == "812910"


def test_nothing_to_search_returns_nothing_not_everything(conn) -> None:
    assert search(conn, "") == [] and search(conn, "   ") == [] and search(conn, "the and of") == []
    assert search(conn, "xqzvw") == []


def test_the_query_is_data_never_sql(conn) -> None:
    hits = search(conn, "'); DROP TABLE naics_industry; -- veterinary")
    assert hits and conn.execute("SELECT count(*) FROM naics_industry").fetchone()[0] == 1012


def test_a_rare_word_outweighs_a_common_one(conn) -> None:
    # "mobile" is in many industries (mobile homes); "grooming" in few. IDF ranking puts pet care
    # in the top candidates for the eval's mobile dog groomer (plain OR ranking had it 21st).
    hits = search(conn, "Mobile dog grooming van offering baths, haircuts, and nail trims at customers' homes.")
    assert "812910" in [h.code for h in hits[:10]]
    words = conn.execute("SELECT count(*) FROM naics_lexeme").fetchone()[0]
    assert words > 5000


def test_the_saved_eval_search_results_are_what_the_database_returns(conn) -> None:
    # Offline replay searches evals/search_candidates.json; it must be real database output.
    import json

    from jev_naics.questions import SEARCH_K
    from jev_naics.search_eval import SEARCH_CANDIDATES

    saved = json.loads(SEARCH_CANDIDATES.read_text(encoding="utf-8"))["results"]
    assert len(saved) >= 39
    for text, hits in saved.items():
        live = search(conn, text, SEARCH_K)
        assert [h["code"] for h in hits] == [h.code for h in live], text[:60]
        assert [h["snippet"] for h in hits] == [h.snippet for h in live]


def test_an_empty_search_table_is_an_error_not_no_match(conn) -> None:
    # Migrated but never loaded: search must say the database is not ready, not "nothing matches".
    conn.execute("DELETE FROM naics_search_meta")
    try:
        with pytest.raises(RuntimeError):
            search(conn, "veterinary")
    finally:
        conn.rollback()
        conn.execute("INSERT INTO naics_search_meta (key, value) VALUES ('version', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (load_search_data().version,))
        conn.commit()


def test_a_stale_data_version_is_an_error_not_a_silent_answer(conn) -> None:
    # Gate audit F1: a loaded-but-stale database (the code's data moved on) must not act as if
    # nothing is wrong. search compares the stored version against this code's, every call.
    conn.execute("UPDATE naics_search_meta SET value = 'old' WHERE key = 'version'")
    conn.commit()
    try:
        with pytest.raises(RuntimeError, match="the NAICS search data is version old, not this code's"):
            search(conn, "veterinary")
        # The failed call leaves no open transaction behind: the next statement just works.
        assert conn.execute("SELECT 1").fetchone() == (1,)
    finally:
        conn.rollback()
        conn.execute("UPDATE naics_search_meta SET value = %s WHERE key = 'version'",
                     (load_search_data().version,))
        conn.commit()


# ---- JEV-52: spelling -------------------------------------------------------------------------------

from jev_naics.db import spelling_corrections  # noqa: E402


def test_a_misspelled_word_is_corrected_to_the_nearest_census_word(conn) -> None:
    assert spelling_corrections(conn, "vetrinary clinc for dogs") == [("vetrinary", "veterinary"),
                                                                      ("clinc", "clinic")]
    hits = search(conn, "vetrinary clinc for dogs and cats, vacinations and surgery")
    assert "541940" in [h.code for h in hits[:10]]  # it ranked 11th before correction


def test_known_words_and_far_away_words_are_left_alone(conn) -> None:
    assert spelling_corrections(conn, "plumbing contractor for commercial buildings") == []
    # Not a misspelling of anything close: more than two edits from any Census word.
    assert spelling_corrections(conn, "doordash kubernetes b2b") == []


def test_a_real_word_the_census_lacks_cannot_outweigh_the_rest(conn) -> None:
    # "meditation" is one edit from "mediation"; the correction counts at reduced weight, so the
    # software words still find software publishers.
    hits = search(conn, "We build and sell a meditation mobile app; revenue is in-app subscriptions.")
    assert "513210" in [h.code for h in hits[:10]]


def test_an_inflected_real_word_is_not_a_misspelling(conn) -> None:
    # "sells" / "settles" / "guests" share a stem with Census words: known words, never "corrected"
    # (to sell, kettles, guest). Only a word whose stem the Census lacks is a candidate.
    text = "It sells software, settles card payments, and serves guests."
    assert spelling_corrections(conn, text) == []
    # Real words the Census never uses ("seeing", "dents") are candidates and can be "corrected"
    # wrongly (seeding, dental): known false friends, counted at half weight, typed words kept.
    assert spelling_corrections(conn, "seeing dents") == [("seeing", "seeding"), ("dents", "dental")]


def test_a_modern_term_finds_the_industry_its_census_wording_names(conn) -> None:
    text = ("Northwind Labs sells a subscription SaaS platform for warehouse scheduling to mid-market "
            "logistics companies.")
    assert "513210" in [h.code for h in search(conn, text)]  # esp-no-naics: missing before JEV-52


def test_a_very_long_word_is_not_a_spelling_candidate_and_never_crashes_search(conn) -> None:
    # JEV-52 gate audit: levenshtein refuses arguments over 255 letters; such a word is not a typo.
    assert spelling_corrections(conn, "a" * 300) == []
    hits = search(conn, "we run a bakery " + "bakery" * 43)
    assert hits and hits[0].code.startswith("3118")


def test_the_word_list_reloads_when_its_query_changes(conn, monkeypatch) -> None:
    from jev_naics import db

    monkeypatch.setattr(db, "_WORDS", db._WORDS + "\n")  # any change to how the vocabulary is built
    assert db.load_search(conn, load_search_data()) is True
