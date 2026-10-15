"""On a hold, the candidate list must not read as a pick (JEV-57 demo review): it is titled as candidates, not choices."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]


def _heading(result: dict) -> dict:
    module = (WEB / "js" / "labels.js").resolve().as_uri()
    code = f"import {{ finalistsHeading }} from {json.dumps(module)};\nconsole.log(JSON.stringify(finalistsHeading({json.dumps(result)})));"
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.mark.parametrize("mode", ["search", "hybrid"])
def test_a_hold_titles_the_search_candidates_as_not_chosen(mode: str) -> None:
    h = _heading({"mode": mode, "gate": "hold", "fallback": False})
    assert h["title"] == "Candidates it considered (none chosen)"
    assert "picked" not in h["title"].lower()
    assert "held" in h["note"].lower()
    assert "picked" not in h["note"].lower() and h["pickLabel"] == "lean" and h["factLabel"] == "lean"


def test_a_tree_walk_hold_titles_its_end_points_as_not_chosen() -> None:
    h = _heading({"mode": "full", "gate": "hold", "fallback": False})
    assert h["title"] == "Candidates it considered (none chosen)"


def test_an_assigned_code_keeps_the_existing_headings() -> None:
    assert _heading({"mode": "hybrid", "gate": "auto_act", "fallback": False})["title"] == "What Jev picked among"
    assert _heading({"mode": "hybrid", "gate": "auto_act", "fallback": False})["pickLabel"] == "final pick"
    assert _heading({"mode": "hybrid", "gate": "auto_act", "fallback": False})["factLabel"] == "pick"
    assert _heading({"mode": "full", "gate": "auto_act", "fallback": False})["title"] == "Finalists"
