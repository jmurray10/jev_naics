"""The full NAICS 2022 tree (JEV-48), built from the official Census files, never hand-typed."""

import json

import pytest

from jev_naics.tree import CENSUS_DIR, TREE_PATH, NaicsTree, load_tree


@pytest.fixture(scope="module")
def tree() -> NaicsTree:
    assert TREE_PATH.is_file(), "build it: python -m jev_naics.build_tree"
    return load_tree()


def test_the_tree_has_every_2022_industry_and_sector(tree) -> None:
    assert len(tree.leaves()) == 1012
    assert len(tree.children(tree.ROOT)) == 20
    assert {"31-33", "44-45", "48-49"} <= set(tree.children(tree.ROOT))


def test_every_leaf_matches_the_census_manual_and_is_reachable_from_the_root(tree) -> None:
    manual = json.loads((CENSUS_DIR / "naics_manual_full.json").read_text(encoding="utf-8"))
    assert set(tree.leaves()) == set(manual["industries"])
    for code in ("621210", "713940", "811192", "513210", "459310"):
        path = tree.path(code)
        assert path[0] in tree.children(tree.ROOT) and path[-1] == code
        assert [len(c) for c in path[1:]] == list(range(3, 3 + len(path) - 1))


def test_titles_are_clean_and_nodes_carry_their_official_description(tree) -> None:
    assert tree.title("111") == "Crop Production"  # the Descriptions file's trailing T marker is gone
    assert tree.title("621210") == "Offices of Dentists"
    assert "dental" in tree.description("621210").lower()
    assert tree.title("31-33") == "Manufacturing"


def test_parents_and_children_agree(tree) -> None:
    for code in tree.leaves():
        assert code in tree.children(tree.parent(code))
    assert tree.parent("331110")[:3] == "331" and tree.parent("331") == "31-33"


def test_no_description_carries_pdf_page_header_debris(tree) -> None:
    import re

    debris = [code for code in tree.leaves() if re.search(r"[A-Z]{4,}[A-Z ,&-]*$", tree.description(code))]
    assert debris == []


def test_a_written_code_reads_as_its_2022_industries_from_the_census_concordance() -> None:
    tree = load_tree()
    assert tree.as_2022("621210") == ["621210"]  # a 2022 industry reads as itself
    assert tree.as_2022("511210") == ["513210"]  # 2017 software publishers moved in 2022
    assert tree.as_2022("448140") == ["458110"]  # 2017 family clothing stores
    assert tree.as_2022("212113") == ["212114", "212115"]  # a 2017 industry split in two
    assert tree.as_2022("999999") == [] and tree.as_2022("62") == []  # not an industry
    assert len(tree.from_2017) == 140 and all(len(new) >= 1 for new in tree.from_2017.values())
    assert all(new in tree and tree.is_leaf(new) for news in tree.from_2017.values() for new in news)
    # 139 retired numbers, plus the one 2017 number still used in 2022 that was also partly moved.
    kept = {old: new for old, new in tree.from_2017.items() if old in tree}
    assert kept == {"325314": ["325314", "325315"]}
    assert tree.as_2022("325314") == ["325314", "325315"]  # the number alone cannot say which
    assert tree.as_2022("325315") == ["325315"]
