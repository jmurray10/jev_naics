"""Optional same-origin proxy in the web image (JEV-59). The API needs a service key and the page sends
none, so in Compose the page used to be refused. With NAICS_API_UPSTREAM and NAICS_API_KEY set, nginx
forwards /v1/, /docs and /openapi.json to the API and adds the key itself: the key stays in the web
container, never in the browser. Without NAICS_API_UPSTREAM nothing changes. Anything malformed stops the
container, because the values are written into nginx configuration."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
SCRIPT = WEB / "docker" / "15-naics-api-origin.sh"
SH = shutil.which("sh")
KEY = "k" * 20 + "_-AZaz09" + "x" * 12  # 40 characters of the token_urlsafe alphabet


def _run(tmp: Path, **env: str) -> subprocess.CompletedProcess:
    (tmp / "js").mkdir(exist_ok=True)
    base = {"NAICS_API_ORIGIN": "http://127.0.0.1:8097", "NAICS_WEB_ROOT": str(tmp),
            "NAICS_NGINX_SNIPPETS": str(tmp / "naics"), "PATH": "/usr/bin:/bin"}
    return subprocess.run([SH, str(SCRIPT)], env={**base, **env}, capture_output=True, text=True)


def test_the_conf_includes_the_optional_snippets() -> None:
    assert "include /etc/nginx/naics/*.conf;" in (WEB / "nginx.conf").read_text(encoding="utf-8")


@pytest.mark.skipif(SH is None, reason="no sh")
def test_without_an_upstream_no_proxy_is_written(tmp_path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "naics").glob("*.conf")) if (tmp_path / "naics").exists() else True


@pytest.mark.skipif(SH is None, reason="no sh")
def test_with_an_upstream_and_a_key_the_proxy_adds_the_key(tmp_path) -> None:
    result = _run(tmp_path, NAICS_API_UPSTREAM="http://api:8095", NAICS_API_KEY=KEY)
    assert result.returncode == 0, result.stderr
    conf = (tmp_path / "naics" / "api-proxy.conf").read_text(encoding="utf-8")
    for route in ("location /v1/", "location = /docs", "location = /openapi.json"):
        assert route in conf
    assert conf.count("proxy_pass http://api:8095;") == 3
    assert conf.count(f'proxy_set_header Authorization "Bearer {KEY}";') == 3
    assert KEY not in (tmp_path / "js" / "config.js").read_text(encoding="utf-8")  # never sent to the browser


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("key", ["", "short", KEY + '"; return 200 "x', KEY + "\nadd_header X 1;", KEY + " " + KEY,
                                 KEY + "," + KEY])
def test_a_missing_or_malformed_key_stops_the_container(tmp_path, key) -> None:
    result = _run(tmp_path, NAICS_API_UPSTREAM="http://api:8095", NAICS_API_KEY=key)
    assert result.returncode != 0
    assert not (tmp_path / "naics" / "api-proxy.conf").exists()


@pytest.mark.skipif(SH is None, reason="no sh")
@pytest.mark.parametrize("upstream", ["api:8095", "http://api:8095/v1", "http://api:8095;", "http://a pi:8095",
                                      "http://api:8095\nhttp://evil"])
def test_a_malformed_upstream_stops_the_container(tmp_path, upstream) -> None:
    result = _run(tmp_path, NAICS_API_UPSTREAM=upstream, NAICS_API_KEY=KEY)
    assert result.returncode != 0
    assert not (tmp_path / "naics" / "api-proxy.conf").exists()


def test_compose_runs_the_page_through_the_proxy() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    web = compose.split("\n  web:", 1)[1]
    assert "NAICS_API_ORIGIN: http://127.0.0.1:8097" in web
    assert "NAICS_API_UPSTREAM: http://api:8095" in web
    assert "NAICS_API_KEY:" in web
