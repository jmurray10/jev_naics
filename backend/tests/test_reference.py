"""The NAICS reference export (JEV-56): every 2022 industry's code and title and the 2017 -> 2022
concordance, from the tree, for callers that need the list but not the classifier."""

import json
import re

from fastapi.testclient import TestClient

from jev_naics.__main__ import main
from jev_naics.api.app import app
from jev_naics.tree import load_tree, reference


def test_the_reference_has_every_industry_and_a_concordance_onto_real_industries() -> None:
    ref = reference()
    assert set(ref) == {"version", "industries", "from_2017"}
    assert len(ref["industries"]) == 1012
    assert all(re.fullmatch(r"\d{6}", code) for code in ref["industries"])
    tree = load_tree()
    assert all(ref["industries"][c] == tree.title(c) for c in tree.leaves())
    assert ref["from_2017"] and all(re.fullmatch(r"\d{6}", old) for old in ref["from_2017"])
    assert all(new and all(n in ref["industries"] for n in new) for new in ref["from_2017"].values())


def test_the_version_is_stable_and_follows_the_content() -> None:
    ref = reference()
    assert re.fullmatch(r"naics2022-[0-9a-f]{16}", ref["version"])
    assert reference()["version"] == ref["version"]


def test_the_api_serves_it_documented_and_without_a_model_call() -> None:
    r = TestClient(app).get("/v1/naics/reference")
    assert r.status_code == 200 and r.json() == reference()
    op = app.openapi()["paths"]["/v1/naics/reference"]["get"]
    assert op["tags"] == ["naics"] and op["operationId"] == "getReference"
    assert "/naics/reference" not in app.openapi()["paths"]  # new: no deprecated unversioned alias


def test_the_cli_prints_the_same_json(capsysbinary) -> None:
    assert main(["--export-reference"]) == 0
    out = capsysbinary.readouterr().out
    assert b"\r\n" not in out and json.loads(out) == reference()
