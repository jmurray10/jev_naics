"""The lexicon (E15): how people describe a business, read as the Census would before Jev sees it."""

import json
import os
import re
from pathlib import Path

import pytest

from jev_naics import refine as R
from jev_naics.lexicon import MAX_EXPANSION_CHARS, Entry, Lexicon
from jev_naics.terms import load_terms
from test_refine import Oracle

REPO = Path(__file__).resolve().parents[2]
V1 = REPO / "experiments" / "refine" / "lexicon_v1.json"
LEX = Lexicon([Entry("saas", "software publishers software subscriptions", "513210"),
               Entry("mobile app", "software publishers application software", "513210"),
               Entry("app", "software publishers application software", "513210"),
               Entry("b2b", "", "names no industry"),
               Entry("e-commerce", "retailers retail", "retail")])


def test_a_term_is_matched_as_a_whole_word_ignoring_case_and_the_longest_term_wins() -> None:
    assert [e.term for e in LEX.read("We sell SaaS to dentists").hits] == ["saas"]
    assert LEX.read("A happy application").hits == ()  # "app" inside "happy"/"application" is not a hit
    assert [e.term for e in LEX.read("our mobile app").hits] == ["mobile app"]  # "app" is consumed by the longer term
    assert [e.term for e in LEX.read("B2B e-commerce").hits] == ["e-commerce", "b2b"] or \
        {e.term for e in LEX.read("B2B e-commerce").hits} == {"b2b", "e-commerce"}


def test_jev_sees_the_users_words_first_then_the_census_wording_and_nothing_is_replaced() -> None:
    r = LEX.read("SaaS")
    assert r.text == "SaaS [Census wording: software publishers software subscriptions]" and r.recognised
    assert LEX.read("plain plumbing").text == "plain plumbing" and not LEX.read("plain plumbing").recognised
    assert LEX.read("B2B").text == "B2B" and LEX.read("B2B").recognised  # recognised, adds nothing


def test_the_expansion_is_bounded() -> None:
    big = Lexicon([Entry(f"term{i}", "word " * 60, "x") for i in range(10)])
    text = big.read(" ".join(f"term{i}" for i in range(10))).text
    assert len(text) - len(" ".join(f"term{i}" for i in range(10))) <= MAX_EXPANSION_CHARS + 40


def test_a_recognised_single_word_is_checked_and_an_unrecognised_one_is_still_too_short() -> None:
    o = Oracle(target="513210")
    r = R.refine("SaaS", decider=o, variant="cascade", lexicon=LEX)
    assert r.result.status != "TOO_SHORT" and o.calls, "a recognised term must reach Jev"
    assert [x["term"] for x in r.reading] == ["saas"]
    assert R.refine("fees", decider=Oracle(), variant="cascade", lexicon=LEX).result.status == "TOO_SHORT"
    assert R.refine("SaaS", decider=Oracle(), variant="cascade").result.status == "TOO_SHORT"  # no lexicon: as before


def test_what_jev_is_asked_carries_the_census_wording_and_it_is_the_only_change() -> None:
    seen: list[str] = []

    class Spy(Oracle):
        def judge(self, state, questions):
            seen.append(state["ops_description"])
            return super().judge(state, questions)

    R.refine("Subscription SaaS for dental offices.", decider=Spy(), variant="verify", lexicon=LEX)
    R.refine("Subscription SaaS for dental offices.", decider=Spy(), variant="verify")
    assert seen[0] == "Subscription SaaS for dental offices. [Census wording: software publishers software subscriptions]"
    assert seen[-1] == "Subscription SaaS for dental offices."
    assert not any("[Census wording" in x for x in seen[-2:])


def test_text_with_no_listed_term_is_asked_exactly_as_before() -> None:
    a, b = [], []

    class Spy(Oracle):
        def __init__(self, sink, **kw):
            super().__init__(**kw)
            self.sink = sink

        def judge(self, state, questions):
            self.sink.append((state["ops_description"], sorted(questions)))
            return super().judge(state, questions)

    R.refine("We install and maintain commercial HVAC-free plumbing.", decider=Spy(a), variant="cascade", lexicon=LEX)
    R.refine("We install and maintain commercial HVAC-free plumbing.", decider=Spy(b), variant="cascade")
    assert a == b


def test_the_frozen_lexicon_only_uses_census_words_and_keeps_every_jev_52_term() -> None:
    raw = json.loads(V1.read_text(encoding="utf-8"))["terms"]
    census = json.loads((REPO / "data" / "census-2022" / "naics_manual_full.json").read_text(encoding="utf-8"))
    vocab = set(re.findall(r"[a-z][a-z0-9\-]+", json.dumps(census).lower()))
    for t in raw:
        assert t["term"] == t["term"].lower() and t["why"], t
        assert not any(c.isdigit() and len(c) == 6 for c in t["census"].split()), "a term maps to words, never a code"
        assert all(w in vocab for w in re.findall(r"[a-z][a-z0-9\-]*", t["census"])), t["term"]
    assert {t.term for t in load_terms()} <= {t["term"] for t in raw}
    assert len({t["term"] for t in raw}) == len(raw)


URL = os.environ.get("NAICS_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not URL.rsplit("/", 1)[-1].endswith("_test"), reason="set NAICS_TEST_DATABASE_URL to a *_test database")
def test_the_lexicon_table_loads_into_a_lexicon_and_reads_only_active_rows() -> None:
    psycopg = pytest.importorskip("psycopg")
    ddl = (REPO / "backend" / "src" / "jev_naics" / "lexicon.sql").read_text(encoding="utf-8")
    with psycopg.connect(URL) as conn:
        conn.execute("DROP TABLE IF EXISTS naics_lexicon")
        conn.execute(ddl)
        conn.execute("INSERT INTO naics_lexicon (term, census, why, reviewed_by, reviewed_on) VALUES "
                     "('saas', 'software publishers software subscriptions', '513210', 'a person', '2026-10-06')")
        conn.execute("INSERT INTO naics_lexicon (term, why, active) VALUES ('retired', 'x', false)")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO naics_lexicon (term, why) VALUES ('NotLower', 'x')")
        conn.rollback()
    with psycopg.connect(URL) as conn:
        conn.execute("DROP TABLE IF EXISTS naics_lexicon")
        conn.execute(ddl)
        conn.execute("INSERT INTO naics_lexicon (term, census, why, reviewed_by, reviewed_on) VALUES "
                     "('saas', 'software publishers software subscriptions', '513210', 'a person', '2026-10-06')")
        conn.execute("INSERT INTO naics_lexicon (term, why, active) VALUES ('retired', 'x', false)")
        lex = Lexicon.from_db(conn)
        assert [e.term for e in lex.entries] == ["saas"] and lex.entries[0].reviewed_by == "a person"
        assert lex.read("saas").recognised and not lex.read("retired").recognised
        conn.execute("DROP TABLE naics_lexicon")
        conn.commit()


def test_the_census_wording_is_added_only_to_short_texts_but_the_term_is_always_recognised() -> None:
    short_only = Lexicon(list(LEX.entries), expand_max_words=6)
    r = short_only.read("SaaS")
    assert r.text.endswith("software subscriptions]") and r.recognised
    long_text = "Soybean and corn farm on 800 acres, and the owner also runs a small SaaS side project"
    r = short_only.read(long_text)
    assert r.recognised and r.text == long_text  # recognised, no wording added
    assert LEX.read(long_text).text != long_text  # no limit: always expanded
