"""Modern business terms (JEV-52): words the 2022 Census text lacks, expanded to Census wording."""

import re

from jev_naics.search_data import load_search_data
from jev_naics.terms import expand_terms, load_terms, terms_version


def _census_words() -> set[str]:
    words: set[str] = set()
    for i in load_search_data().industries.values():
        text = " ".join([i.title, i.description, *i.examples, *i.index_entries, *i.sent_here])
        words.update(re.findall(r"[a-z]+", text.lower()))
    return words


def test_every_expansion_is_census_wording_never_a_code() -> None:
    census = _census_words()
    for term in load_terms():
        assert not re.search(r"\d", term.census), term.term  # words, not codes
        missing = [w for w in re.findall(r"[a-z]+", term.census.lower()) if w not in census]
        assert not missing, (term.term, missing)


def test_terms_match_whole_words_only_and_say_what_they_expanded() -> None:
    assert expand_terms("Northwind sells a subscription SaaS platform") == [
        ("saas", "software publishers software subscriptions")]
    assert expand_terms("We build a Mobile App for runners") == [
        ("mobile app", "software publishers application software"),
        ("app", "software publishers application software")]
    assert expand_terms("happy apple orchard, sassafras tea") == []  # "app", "saas" inside words: no
    assert expand_terms("B2B wholesale of bolts") == []  # b2b says nothing about the industry


def test_the_terms_have_a_version_for_saved_search_results() -> None:
    assert len(terms_version()) == 16


def test_the_fingerprint_hash_ignores_line_endings(tmp_path) -> None:
    """terms_version/terms_code_version hash a file's bytes for the saved search results'
    fingerprint (search_eval.search_sql_fingerprint). A raw hashlib.sha256(path.read_bytes())
    changes if the same file is checked out with CRLF instead of LF line endings (a Windows
    checkout before .gitattributes normalizes it, or any not-yet-normalized tree): then a fixture
    saved from an LF checkout looks stale on a CRLF one and vice versa (JEV-55 Ruling 12). The
    hash must be the same either way, so both functions hash through a line-ending-normalizing
    helper, _normalized_sha256."""
    from jev_naics.terms import _normalized_sha256

    content = b'{"terms": [{"term": "saas", "census": "software publishers", "why": "example"}]}\n'
    lf_path, crlf_path = tmp_path / "lf.json", tmp_path / "crlf.json"
    lf_path.write_bytes(content)
    crlf_path.write_bytes(content.replace(b"\n", b"\r\n"))
    assert _normalized_sha256(lf_path) == _normalized_sha256(crlf_path)
    # Not a vacuous "everything hashes the same" helper: still distinguishes real content changes.
    (tmp_path / "different.json").write_bytes(content.replace(b"saas", b"paas"))
    assert _normalized_sha256(tmp_path / "different.json") != _normalized_sha256(lf_path)


def test_terms_version_and_terms_code_version_use_the_normalized_hash() -> None:
    from pathlib import Path

    from jev_naics import terms as terms_module
    from jev_naics.terms import TERMS_PATH, _normalized_sha256, terms_code_version

    assert terms_version() == _normalized_sha256(TERMS_PATH)
    assert terms_code_version() == _normalized_sha256(Path(terms_module.__file__))
