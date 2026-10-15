"""jev-naics as an installed package (JEV-49: another service installs it in its image).

An install has no pyproject.toml above the code, so nothing may look for the repo at import time,
the full tree must ship as package data, and (JEV-51) the search database URL must be readable
from the environment without a .env file.
"""

import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "jev_naics"


def test_an_installed_copy_imports_and_loads_the_full_tree(tmp_path) -> None:
    shutil.copytree(SRC, tmp_path / "site" / "jev_naics", ignore=shutil.ignore_patterns("__pycache__"))
    code = ("from jev_naics.tree import load_tree; import jev_naics.full; "
            "from jev_naics.db import database_url, MIGRATIONS_DIR; import jev_naics.search_pick; "
            "from jev_naics.search_data import load_search_data; assert len(load_search_data().industries) == 1012; "
            "from jev_naics.terms import load_terms; assert len(load_terms()) > 20; "
            "print(len(load_tree().leaves()), len(load_tree().from_2017), database_url(), "
            "len(list(MIGRATIONS_DIR.glob('*.sql'))))")
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                         env={"PYTHONPATH": str(tmp_path / "site"), "SYSTEMROOT": r"C:\Windows", "PATH": "",
                              "NAICS_DATABASE_URL": "postgresql://example/jev_naics"})
    assert run.returncode == 0, run.stderr[-800:]
    assert run.stdout.split() == ["1012", "140", "postgresql://example/jev_naics", "3"]


def test_the_distribution_is_named_jev_naics() -> None:
    from importlib.metadata import version
    assert version("jev-naics")


def test_classify_in_an_installed_copy_with_no_pyproject_above_holds_instead_of_crashing(tmp_path) -> None:
    """env.load_typesafe_api_key() used to call _jev.env.find_root with no fallback, raising
    FileNotFoundError as soon as jev_naics.classify() ran in an install with no pyproject.toml
    above the installed files (JEV-55). It must hold (e.g. service_error), never crash."""
    shutil.copytree(SRC, tmp_path / "site" / "jev_naics", ignore=shutil.ignore_patterns("__pycache__"))
    code = ("import jev_naics\n"
            "r = jev_naics.classify('Small-animal veterinary clinic.', method='tree')\n"
            "print(r.gate, r.hold_reason)\n")
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                         env={"PYTHONPATH": str(tmp_path / "site"), "SYSTEMROOT": r"C:\Windows", "PATH": "",
                              "JEV_DECIDER": "replay"})
    assert run.returncode == 0, run.stderr[-2000:]
    gate, _, hold_reason = run.stdout.strip().partition(" ")
    assert (gate, hold_reason) == ("hold", "service_error")
