"""Build src/jev_naics/data/naics_2022_search.json from the Census NAICS 2022 files (JEV-50).

    python -m jev_naics.build_search

Sources in data/census-2022/ (US Census Bureau, public domain):
- 6-digit_2022_Codes.xlsx          the 1,012 industries and their titles
- 2022_NAICS_Descriptions.xlsx      official descriptions (the body, the "Illustrative Examples"
                                    list, and a "Cross-References." section, split apart here)
- 2022_NAICS_Index_File.xlsx        20,373 index entries on six-digit industries (plus 25
                                    "see type" pointers with no code, skipped)
- 2022_NAICS_Cross_References.xlsx  4,601 "X--are classified in Industry Y" notes
Not used: naics_manual_full.json (PDF page-header debris in its text). Nothing is typed by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from jev_naics.search_data import SEARCH_DATA_PATH
from jev_naics.tree import CENSUS_DIR, load_tree

# "... --are classified in U.S. Industry 812910, Pet Care ..." (also "Industry Group 5321").
_TARGET = re.compile(r"classified in (?:U\.S\. )?Industry (?:Group )?(\d{4,6})")


def _rows(name: str) -> list[tuple]:
    import openpyxl

    workbook = openpyxl.load_workbook(CENSUS_DIR / name, read_only=True)
    return [r for r in workbook.worksheets[0].iter_rows(values_only=True) if any(c is not None for c in r)]


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _title(text: object) -> str:
    # 799 Census titles end with "T", the trilateral (US, Canada, Mexico) marker.
    title = _clean(text)
    return title[:-1].rstrip() if title.endswith("T") and not title.endswith(" T") else title


def _split_description(text: object) -> tuple[str, list[str]]:
    """The body, and the "Illustrative Examples" list; the cross-reference section is dropped
    (the Cross_References file has the same notes, one per row)."""
    raw = str(text or "")
    raw = re.split(r"\n\s*Cross-References\.", raw, maxsplit=1)[0]
    body, _, examples = raw.partition("Illustrative Examples:")
    lines = [_clean(line) for line in examples.split("\n")]
    return _clean(body), [line for line in lines if line]


def _activity(note: str) -> str:
    """The activity a cross-reference sends elsewhere: the text before "--are classified"."""
    head = re.split(r"--|, are classified| are classified", note, maxsplit=1)[0]
    return _clean(head).rstrip(";,.")


def build() -> dict:
    tree = load_tree()
    leaves = set(tree.leaves())
    titles = {str(r[0]).strip(): _title(r[1]) for r in _rows("6-digit_2022_Codes.xlsx")[1:]}
    descriptions = {str(r[0]).strip(): _split_description(r[2]) for r in _rows("2022_NAICS_Descriptions.xlsx")[1:]}
    index: dict[str, list[str]] = defaultdict(list)
    for row in _rows("2022_NAICS_Index_File.xlsx")[1:]:
        code = str(row[0]).strip()
        if code == "******":
            continue  # 25 "Hospitals -- see type" pointers that name no industry
        index[code].append(_clean(row[1]))
    excludes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sent_here: dict[str, list[str]] = defaultdict(list)
    for row in _rows("2022_NAICS_Cross_References.xlsx")[1:]:
        where, note = str(row[0]).strip(), _clean(row[1])
        match = _TARGET.search(note)
        if not match:
            continue  # "classified according to the primary activity" names no industry
        target, activity = match.group(1), _activity(note)
        # A note on a 5-digit industry applies to each six-digit industry under it.
        for code in ([where] if where in leaves else [c for c in tree.children(where)] if where in tree else []):
            excludes[code].append((target, activity))
        # The activity belongs to the target only when the target is one industry.
        kids = tree.children(target) if target in tree and not tree.is_leaf(target) else []
        home = target if target in leaves else kids[0] if len(kids) == 1 and kids[0] in leaves else None
        if home and activity not in sent_here[home]:
            sent_here[home].append(activity)

    industries = {}
    for code in sorted(leaves):
        if code not in titles or code not in descriptions:
            raise ValueError(f"industry {code} is missing from the Census code or description file")
        body, examples = descriptions[code]
        industries[code] = {"title": titles[code], "description": body, "examples": examples,
                            "index_entries": index.get(code, []),
                            "excludes": [list(pair) for pair in excludes.get(code, [])],
                            "sent_here": sent_here.get(code, [])}
    unknown = sorted(set(index) - leaves)
    if unknown:
        raise ValueError(f"index entries on unknown codes: {unknown[:5]}")
    version = hashlib.sha256(json.dumps(industries, sort_keys=True).encode()).hexdigest()[:16]
    return {"source": "US Census Bureau NAICS 2022 (data/census-2022)", "version": version,
            "industries": industries}


def main() -> int:
    data = build()
    SEARCH_DATA_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                newline="\n")
    industries = data["industries"].values()
    print(f"wrote {SEARCH_DATA_PATH.name}: {len(data['industries'])} industries, "
          f"{sum(len(i['index_entries']) for i in industries)} index entries, "
          f"{sum(len(i['excludes']) for i in industries)} exclusions, "
          f"{sum(len(i['sent_here']) for i in industries)} activities sent to one industry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
