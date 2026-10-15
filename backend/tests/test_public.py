"""What a public release needs (JEV-59): an open licence the README agrees with, the files an outside
contributor looks for, no internal workspace names in tracked files, and a test suite that runs on
every Python the package says it supports."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    paths = [ROOT / p for p in out.splitlines() if p and not p.endswith((".xlsx", ".png", ".gif", ".mp4", ".ico"))]
    return [p for p in paths if p.is_file()]


def test_the_licence_is_mit_and_the_readme_and_package_say_so() -> None:
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "All rights reserved" not in licence
    assert "census.gov" in licence  # the Census files stay outside the licence, named as US Government works
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "MIT" in readme and "All rights reserved" not in readme
    assert re.search(r'^license = "MIT"$', (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"), re.M)


def test_the_files_an_outside_contributor_looks_for_exist() -> None:
    assert (ROOT / "CONTRIBUTING.md").is_file()
    assert "## Reporting a vulnerability" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    templates = ROOT / ".github"
    assert (templates / "pull_request_template.md").is_file()
    assert sorted(p.name for p in (templates / "ISSUE_TEMPLATE").glob("*.md"))


def test_no_tracked_file_names_an_internal_workspace() -> None:
    # Spelled in two pieces so this file does not match its own pattern.
    pattern = re.compile("close" "os|linear" r"\.app/|street" "dog|data" "_agent|\bSTR-\d+", re.I)
    hits = [str(p.relative_to(ROOT)) for p in _tracked_text_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []


def test_no_tracked_file_names_an_internal_service_or_a_local_path() -> None:
    # Spelled in pieces so this file does not match its own pattern.
    pattern = re.compile("jev" "-intake|" r"[A-Za-z]:[\\/]Users[\\/]|/c/" "Users/", re.I)
    hits = [str(p.relative_to(ROOT)) for p in _tracked_text_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []


def test_there_is_one_way_to_publish_and_it_drops_the_private_history() -> None:
    """The public repository starts from one commit (tools/export_public.sh); no doc offers a rewrite of the
    private history, and none claims the public history shows anything it no longer carries."""
    assert (ROOT / "tools" / "export_public.sh").is_file()
    assert not (ROOT / "tools" / "rewrite" "_author_email.sh").exists()
    docs = {p: p.read_text(encoding="utf-8") for p in ROOT.glob("*.md")}
    assert [p.name for p, t in docs.items() if re.search("rewrite" "_author_email|with its git history|"
                                                          "commit history shows", t)] == []


def test_the_docs_do_not_say_the_api_has_no_auth() -> None:
    assert "no authentication of its own" not in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    out_of_scope = (ROOT / "DESIGN.md").read_text(encoding="utf-8").split("## Out of scope")[1]
    assert "API authentication" not in out_of_scope


def test_the_readme_links_the_full_demo_video() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/media/demo.mp4" in readme and (ROOT / "docs" / "media" / "demo.mp4").is_file()


def test_the_readme_says_what_runs_without_a_key() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## What runs without a key" in readme
    assert "no LLM" in readme


def test_the_readme_demo_exists_and_says_it_is_a_replay() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/media/demo.gif" in readme and (ROOT / "docs" / "media" / "demo.gif").is_file()
    assert "tools/record_demo.py" in readme and "replay" in readme.split("docs/media/demo.gif")[1][:400]


PUBLIC_SETS = re.compile(r"-(dev|refine_[a-z_0-9]+|exio-(small|medium|large))(-|$)")


def test_no_run_on_private_data_is_published() -> None:
    """Runs on private samples (small, medium, reviewed: real applications) stay local; only the synthetic sets
    and the public ExioNAICS samples are tracked."""
    runs = sorted((ROOT / "experiments" / "refine" / "runs").glob("*.json"))
    assert runs and [p.name for p in runs if not PUBLIC_SETS.search(p.stem)] == []


def test_the_data_scripts_take_your_own_file() -> None:
    hits = [str(p.relative_to(ROOT)) for p in _tracked_text_files() if ("account" "_data") in p.read_text(encoding="utf-8", errors="ignore")]  # split so this file does not match
    assert hits == []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Bring your own data" in readme and "DESCRIPTION_OF_OPERATIONS" in readme
