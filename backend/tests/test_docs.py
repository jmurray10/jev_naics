"""The docs name what the code has: every threshold in CALIBRATION.md, every route in README, every
Census checksum in DATA.md."""

from pathlib import Path

from jev_naics import fetch_census, questions

ROOT = Path(__file__).resolve().parents[2]


def test_every_threshold_is_documented() -> None:
    text = (ROOT / "CALIBRATION.md").read_text(encoding="utf-8")
    names = [n for n in dir(questions) if n.isupper() and isinstance(getattr(questions, n), (int, float))]
    assert names and [n for n in names if n not in text] == []


def test_every_census_checksum_is_in_data_md() -> None:
    text = (ROOT / "DATA.md").read_text(encoding="utf-8")
    assert [n for n, d in fetch_census.CHECKSUMS.items() if d not in text] == []


def test_the_readme_uses_the_versioned_api_and_real_commands() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/v1/classify" in text and "docker compose up -d --build" in text and "db_cli" in text


def test_every_environment_variable_is_in_security_and_deploy() -> None:
    import re

    src = ROOT / "backend" / "src"
    names = set()
    for path in src.rglob("*.py"):
        names |= set(re.findall(r"""(?:environ(?:\.get)?\(|environ\[|getenv\()["']([A-Z][A-Z0-9_]+)["']""",
                                path.read_text(encoding="utf-8")))
    assert {"NAICS_DATABASE_URL", "NAICS_MIGRATE_ON_START"} <= names
    for doc in ("SECURITY.md", "DEPLOY.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert [n for n in sorted(names) if f"`{n}`" not in text] == [], doc
        assert "`NAICS_API_ORIGIN`" in text, doc  # the web image's one variable


def test_the_api_version_is_the_package_version() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: the dev extra installs tomli, the same parser
        import tomli as tomllib

    from jev_naics import __version__
    from jev_naics.api.app import app

    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert app.version == __version__ == pyproject["project"]["version"]
    assert f"## {__version__}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_benchmark_is_documented_and_its_data_stays_local() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "benchmark.html" in readme and "127.0.0.1:8097" in readme and "TYPESAFE_API_KEY" in readme
    data = (ROOT / "DATA.md").read_text(encoding="utf-8")
    assert "ExioNAICS" in data and ".scratch/" in data
    assert "ExioNAICS" in (ROOT / "CALIBRATION.md").read_text(encoding="utf-8")
    assert "benchmark" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## 0.1.1")[0].lower()
