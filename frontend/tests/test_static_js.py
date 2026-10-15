"""Every page script parses (a syntax error would break the page without failing any API test)."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]
JS = sorted((WEB / "js").glob("*.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", JS, ids=[p.name for p in JS])
def test_page_script_parses(script: Path) -> None:
    result = subprocess.run(["node", "--input-type=module", "--check"], input=script.read_text(encoding="utf-8"),
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_the_page_calls_the_versioned_api_at_the_configured_base() -> None:
    app_js = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert "window.JEV_NAICS_API" in app_js and '"/v1' in app_js
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index("js/config.js") < html.index("js/app.js") and "/static/" not in html


def test_the_page_never_holds_a_key() -> None:
    for path in WEB.rglob("*"):
        if path.suffix in {".js", ".html", ".conf"}:
            assert "TYPESAFE" not in path.read_text(encoding="utf-8").upper().replace("TYPESAFE.AI", "")
