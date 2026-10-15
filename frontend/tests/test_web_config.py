"""The web image is configured per environment by one variable, NAICS_API_ORIGIN (JEV-56): it sets
both js/config.js (where the page calls) and the CSP connect-src (what the browser allows), so the
two cannot drift apart. A malformed origin stops the container instead of serving a broken page."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]
SCRIPT = WEB / "docker" / "15-naics-api-origin.sh"
SH = shutil.which("sh")


def test_the_csp_connect_src_is_the_configured_origin() -> None:
    conf = (WEB / "nginx.conf").read_text(encoding="utf-8")
    assert "connect-src 'self' ${NAICS_API_ORIGIN};" in conf
    assert "127.0.0.1:8095" not in conf.split("Content-Security-Policy", 1)[1].split(";", 2)[1]


def test_the_image_templates_the_conf_and_runs_the_script_with_a_local_default() -> None:
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")
    assert "/etc/nginx/templates/default.conf.template" in dockerfile
    assert "/docker-entrypoint.d/15-naics-api-origin.sh" in dockerfile
    assert "ENV NAICS_API_ORIGIN=http://127.0.0.1:8095" in dockerfile


def _run(origin: str, root: Path) -> subprocess.CompletedProcess:
    (root / "js").mkdir(exist_ok=True)
    return subprocess.run([SH, str(SCRIPT)], env={"NAICS_API_ORIGIN": origin, "NAICS_WEB_ROOT": str(root),
                                                  "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True)


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("origin", ["https://naics.example.com", "http://127.0.0.1:8095",
                                    "https://api.internal-1.example:8443"])
def test_a_good_origin_writes_config_js(tmp_path, origin) -> None:
    result = _run(origin, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "js" / "config.js").read_text(encoding="utf-8").strip().endswith(f'"{origin}";')


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("origin", ["", "naics.example.com", "https://naics.example.com/", "https://a.example/v1",
                                    "javascript:alert(1)", 'https://a.example";alert(1);"', "https://a b.example",
                                    "*"])
def test_a_bad_origin_stops_the_container(tmp_path, origin) -> None:
    result = _run(origin, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "js" / "config.js").exists()


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("origin", [
    # Review Important 2: grep -Eq matches line by line even under -q, so a value whose first line
    # is a valid origin could smuggle anything past the check on later lines, e.g. into a mounted
    # file or an injected script tag, unless the whole value is rejected the moment it has a line
    # break at all.
    "https://naics.example.com\n<script>alert(1)</script>",
    "https://naics.example.com\r\ninjected: 1",
    "https://naics.example.com\nhttps://evil.example",
])
def test_a_multiline_origin_stops_the_container(tmp_path, origin) -> None:
    result = _run(origin, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "js" / "config.js").exists()


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("origin", ["https://naics.example.com/evil/path", "https://naics.example.com/"])
def test_a_trailing_path_stops_the_container(tmp_path, origin) -> None:
    result = _run(origin, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "js" / "config.js").exists()


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("origin", ['https://naics.example.com"', "https://naics.example.com'; alert(1); '"])
def test_an_origin_with_quotes_stops_the_container(tmp_path, origin) -> None:
    result = _run(origin, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "js" / "config.js").exists()
