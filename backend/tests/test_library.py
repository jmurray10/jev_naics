"""The library surface: one call, three methods, the same result type as the API."""

import pytest

import jev_naics
from jev_naics.db import Hit
from test_search_pick import VET, Jev, searcher


def test_classify_defaults_to_the_hybrid_and_returns_the_result_type() -> None:
    r = jev_naics.classify("Small-animal veterinary clinic.", decider=Jev(), searcher=searcher(VET))
    assert isinstance(r, jev_naics.NaicsResult)
    assert (r.gate, r.code, r.fallback) == ("auto_act", "541940", False)


def test_each_method_runs_its_classifier(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(jev_naics, "classify_full", lambda d, *, decider, strict=False: seen.append("tree") or "t")
    monkeypatch.setattr(jev_naics, "classify_search",
                        lambda d, *, decider, searcher, strict=False: seen.append("search") or "s")
    assert jev_naics.classify("x", method="tree", decider=object()) == "t"
    assert jev_naics.classify("x", method="search", decider=object(), searcher=object()) == "s"
    assert seen == ["tree", "search"]


def test_search_methods_need_a_database_and_say_so(monkeypatch) -> None:
    monkeypatch.delenv("NAICS_DATABASE_URL", raising=False)
    monkeypatch.setattr(jev_naics, "_database_configured", lambda: False)
    with pytest.raises(RuntimeError, match="NAICS_DATABASE_URL"):
        jev_naics.classify("x", method="hybrid", decider=object())


def test_an_unknown_method_is_refused() -> None:
    with pytest.raises(ValueError, match="method"):
        jev_naics.classify("x", method="closed", decider=object(), searcher=object())
