"""jev_naics stands alone: no import of jev_peas' helper packages, and one file names the model."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
FOREIGN = ("jev_common", "jev_runtime")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_no_module_imports_the_monorepo_helpers() -> None:
    tests = Path(__file__).parent
    offenders = [str(p) for p in [*SRC.rglob("*.py"), *tests.glob("*.py")] if _imports(p) & set(FOREIGN)]
    assert offenders == []


def test_only_providers_yaml_names_a_model() -> None:
    """Mirrors jev_peas' packages/jev-runtime/tests/test_model_pin.py: no .py source hard-codes a
    quoted model literal, and among the yaml/toml config files only _jev/providers.yaml names one.
    Comments and docstrings that cite the calibration model (e.g. "calibrated on jev-1.13.0") are
    allowed and are not flagged."""
    literal = re.compile(r"""["']jev-\d""")
    hits = [
        f"{path.relative_to(SRC)}:{number}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(line)
    ]
    assert hits == []

    pattern = re.compile(r"jev-\d+\.\d+\.\d+")
    named = [str(p.relative_to(SRC)) for p in [*SRC.rglob("*.yaml"), *SRC.rglob("*.toml")]
             if pattern.search(p.read_text(encoding="utf-8"))]
    assert named == [str(Path("jev_naics") / "_jev" / "providers.yaml")]
