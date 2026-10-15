"""The committed package data equals a fresh build from the Census files."""

import json

from jev_naics import build_search, build_tree
from jev_naics.search_data import SEARCH_DATA_PATH
from jev_naics.tree import TREE_PATH


def test_the_tree_rebuilds_from_the_census_files() -> None:
    assert build_tree.build() == json.loads(TREE_PATH.read_text(encoding="utf-8"))


def test_the_search_data_rebuilds_from_the_census_files() -> None:
    assert build_search.build() == json.loads(SEARCH_DATA_PATH.read_text(encoding="utf-8"))
