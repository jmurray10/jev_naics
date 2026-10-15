"""The NAICS 2022 tree (JEV-48): sectors -> subsectors -> industry groups -> industries.

Loaded from jev_naics/data/naics_2022_tree.json (package data, so an install carries it), which build_tree.py generates from the Census files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from jev_naics.env import repo_root

DATA_DIR = repo_root().parent / "data"
CENSUS_DIR = DATA_DIR / "census-2022"
TREE_PATH = Path(__file__).resolve().parent / "data" / "naics_2022_tree.json"
ROOT = "root"
# Three sectors are code ranges; their two-digit codes live under the range.
RANGE_SECTORS: dict[str, tuple[str, ...]] = {
    "31-33": ("31", "32", "33"),
    "44-45": ("44", "45"),
    "48-49": ("48", "49"),
}


def sector_of(code: str) -> str:
    two = code[:2]
    for sector, members in RANGE_SECTORS.items():
        if two in members:
            return sector
    return two


@dataclass(frozen=True)
class NaicsTree:
    nodes: dict
    ROOT: str = ROOT
    # A 2017 industry number that 2022 retired or partly moved -> its 2022 industries (Census).
    from_2017: dict = field(default_factory=dict)

    def children(self, code: str) -> list[str]:
        return list(self.nodes[code]["children"])

    def parent(self, code: str) -> str | None:
        return self.nodes[code]["parent"]

    def title(self, code: str) -> str:
        return self.nodes[code]["title"]

    def description(self, code: str) -> str:
        return self.nodes[code]["description"]

    def index_terms(self, code: str, limit: int | None = None) -> list[str]:
        terms = list(self.nodes[code].get("index_terms", []))
        return terms if limit is None else terms[:limit]

    def is_leaf(self, code: str) -> bool:
        return not self.nodes[code]["children"]

    def leaves(self) -> list[str]:
        return sorted(code for code, node in self.nodes.items() if code != ROOT and not node["children"])

    def path(self, code: str) -> list[str]:
        out = []
        while code is not None and code != ROOT:
            out.append(code)
            code = self.parent(code)
        return list(reversed(out))

    def as_2022(self, code: str) -> list[str]:
        """The 2022 industries a written six-digit code means: itself, what its retired 2017
        number became (more than one when it split), or nothing when it is not an industry."""
        if code in self.from_2017:  # retired, or still used but partly moved: every 2022 successor
            return list(self.from_2017[code])
        if code in self.nodes and code != ROOT and self.is_leaf(code) and len(code) == 6:
            return [code]
        return []

    def __contains__(self, code: str) -> bool:
        return code in self.nodes


@lru_cache(maxsize=1)
def load_tree() -> NaicsTree:
    data = json.loads(TREE_PATH.read_text(encoding="utf-8"))
    return NaicsTree(data["nodes"], from_2017=data.get("from_2017", {}))


def reference() -> dict:
    """The public NAICS 2022 reference data a caller needs without the classifier (JEV-56): every
    industry's code and official title, and the 2017 -> 2022 concordance. No model call.
    `version` is a hash of exactly that content, so it changes only when the data does."""
    tree = load_tree()
    content = {"industries": {code: tree.title(code) for code in tree.leaves()},
               "from_2017": {old: list(new) for old, new in sorted(tree.from_2017.items())}}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    return {"version": f"naics2022-{digest[:16]}", **content}
