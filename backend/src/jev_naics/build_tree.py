"""Build data/naics_2022_tree.json from the official Census NAICS 2022 files (JEV-48).

    python -m jev_naics.build_tree

Sources in data/census-2022/ (US Census Bureau, public domain; supplied 2026-09-28):
- 2-6_digit_2022_Codes.xlsx  codes and clean titles for every level
- 2022_NAICS_Descriptions.xlsx  the official description of every code
- naics_manual_full.json  the 1,012 six-digit industries; used for index terms only (its
  descriptions carry PDF page-header debris)
- 2022_to_2017_NAICS.xlsx  the Census 2022-to-2017 concordance (census.gov/naics/concordances,
  fetched 2026-09-29): every retired 2017 industry and the 2022 industries it became
Nothing is typed by hand: every code, title, and description comes from these files.
"""

from __future__ import annotations

import json
import re

from jev_naics.tree import CENSUS_DIR, RANGE_SECTORS, ROOT, TREE_PATH, sector_of



def _rows(name: str) -> list[tuple]:
    import openpyxl

    workbook = openpyxl.load_workbook(CENSUS_DIR / name, read_only=True)
    return list(workbook.worksheets[0].iter_rows(values_only=True))


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _short_description(text: str) -> str:
    """The first paragraph that says what the code comprises (sector texts open with a heading)."""
    paragraphs = [p.strip() for p in str(text or "").split("\n") if p.strip()]
    body = [p for p in paragraphs if p.lower() not in {"the sector as a whole"}]
    return _clean(body[0]) if body else ""


def build() -> dict:
    titles: dict[str, str] = {}
    for row in _rows("2-6_digit_2022_Codes.xlsx")[1:]:
        code, title = row[1], row[2]
        if code is None or title is None:
            continue
        titles[str(code).strip()] = _clean(title)
    descriptions = {str(row[0]).strip(): _short_description(row[2])
                    for row in _rows("2022_NAICS_Descriptions.xlsx")[1:] if row[0] is not None}
    manual = json.loads((CENSUS_DIR / "naics_manual_full.json").read_text(encoding="utf-8"))["industries"]

    nodes: dict[str, dict] = {ROOT: {"title": "All industries", "description": "", "parent": None, "children": []}}
    for code, title in titles.items():
        if "-" in code:
            parent = ROOT
        elif len(code) == 2:
            if code in {c for rng in RANGE_SECTORS for c in RANGE_SECTORS[rng]}:
                continue  # 31, 32, 33 ... live under their range sector
            parent = ROOT
        elif len(code) == 3:
            parent = sector_of(code)
        else:
            parent = code[:-1]
        # The official Descriptions spreadsheet only: about 100 descriptions in the manual JSON
        # end with PDF page-header debris ("NORTH AMERI", "MANUFA"), so it supplies index terms only.
        description = descriptions.get(code, "")
        nodes[code] = {"title": title, "description": description, "parent": parent, "children": []}
        if code in manual:
            nodes[code]["index_terms"] = [_clean(t) for t in manual[code].get("index_terms", [])]
    for code, node in nodes.items():
        if node["parent"] is not None:
            nodes[node["parent"]]["children"].append(code)
    for node in nodes.values():
        node["children"].sort()
    missing = sorted(set(manual) - set(nodes))
    if missing:
        raise ValueError(f"manual industries missing from the code list: {missing[:5]}")
    return {"source": "US Census Bureau NAICS 2022 (data/census-2022)", "nodes": nodes,
            "from_2017": _retired_2017(nodes)}


def _retired_2017(nodes: dict[str, dict]) -> dict[str, list[str]]:
    """Each 2017 industry number that 2022 retired or partly moved -> the 2022 industries it
    went to. A number still used in 2022 is listed only when part of it moved elsewhere
    (2017 325314 became 325314 and 325315), so reading it alone cannot pick one."""
    moved: dict[str, set[str]] = {}
    for row in _rows("2022_to_2017_NAICS.xlsx")[3:]:
        if not row or row[0] is None or row[2] is None:
            continue
        new, old = str(row[0]).strip(), str(row[2]).strip()
        if not (new.isdigit() and old.isdigit()):
            continue
        if new not in nodes or nodes[new]["children"]:
            raise ValueError(f"concordance names {new}, which is not a 2022 industry")
        moved.setdefault(old, set()).add(new)
    return {old: sorted(new) for old, new in sorted(moved.items()) if new != {old}}


def main() -> int:
    tree = build()
    TREE_PATH.write_text(json.dumps(tree, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    leaves = sum(1 for n in tree["nodes"].values() if not n["children"])
    print(f"wrote {TREE_PATH.name}: {len(tree['nodes'])} nodes, {leaves} industries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
