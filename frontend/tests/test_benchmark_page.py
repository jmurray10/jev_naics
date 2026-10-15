"""The demo's benchmark page (JEV-57): shipped in the image, linked from the classifier, and its totals add up."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
PAGE = WEB / "benchmark.html"


def _cell(name: str) -> int:
    match = re.search(rf'data-total="{name}">(\d+)<', PAGE.read_text(encoding="utf-8"))
    assert match, name
    return int(match.group(1))


def test_the_benchmark_page_ships_in_the_web_image_and_is_linked_from_the_classifier() -> None:
    assert PAGE.is_file()
    assert "COPY frontend/benchmark.html" in (WEB / "Dockerfile").read_text(encoding="utf-8")
    assert 'href="benchmark.html"' in (WEB / "index.html").read_text(encoding="utf-8")
    assert 'href="./"' in PAGE.read_text(encoding="utf-8")


def test_the_benchmark_totals_are_consistent() -> None:
    assert _cell("cases") == _cell("assigned") + _cell("held")
    assert _cell("assigned") == _cell("defensible") + _cell("arguable") + _cell("not-defensible")
    assert _cell("held") == _cell("hold-ok") + _cell("hold-miss")
    assert _cell("cases") == _cell("labels-right") + _cell("labels-partly") + _cell("labels-wrong")


def test_the_benchmark_page_runs_no_script() -> None:
    assert "<script" not in PAGE.read_text(encoding="utf-8")
