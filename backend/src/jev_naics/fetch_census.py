"""The Census NAICS 2022 source files (https://www.census.gov/naics/) and their checksums.

    python -m jev_naics.fetch_census verify      # check data/census-2022 against DATA.md
    python -m jev_naics.fetch_census fetch URL.. # download a replacement, refused on a checksum mismatch
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

from jev_naics.tree import CENSUS_DIR  # <repo>/data/census-2022 (tree.DATA_DIR, fixed in this task)

CHECKSUMS = {  # the same values as DATA.md
    "2022_NAICS_Cross_References.xlsx": "3c50c3bfa9d76862aea471cc8831fd726f0b978619d833e48c54a749d9622144",
    "2022_NAICS_Descriptions.xlsx": "6222c4d87dcf984970e0ff8a49862ed54b546b089d03956be3f285900cd3d66c",
    "2022_NAICS_Index_File.xlsx": "6506b37b9546dd9cec1f8b79e0b38b68e547a5cce5fd6f8332d35024dbd6cd63",
    "2022_NAICS_Structure.xlsx": "217c9e0d4d74e7517bc288f5f308b73aa0de5ee787976a6dd222412be28ada22",
    "2022_NAICS_Structure_Summary_Table.xlsx": "856c8d9409470f76b48d8bd725851abea1a0a8bb7fae31a800614d62b36ef607",
    "2022_to_2017_NAICS.xlsx": "76ed2d5bc8cd3443dc86f0c3225b83e6d187f3fc47d4c5696c911a3234f1716f",
    "2-6_digit_2022_Codes.xlsx": "be12ba41002803359f49181c9bf33a03fbd08578f4f4a4c0bbad7aadaaea0316",
    "6-digit_2022_Codes.xlsx": "3e3c90d4d36d874c0fd2da22a222c794654dbfa404320304152f5317781aefb7",
    "naics_manual_full.json": "a79606a79661d815f8d65441607635845648eb8227c580d68bd60859f3a3114c",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_files(folder: Path) -> list[str]:
    problems = []
    for name, digest in CHECKSUMS.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif _sha256(path) != digest:
            problems.append(f"{name}: checksum mismatch")
    return problems


def fetch(url: str, folder: Path = CENSUS_DIR) -> Path:
    """Download one file into `folder`; refused (and not kept) unless its name and checksum are
    listed. Any existing file at `folder / name` is untouched until the download's checksum
    verifies; on any error (network failure or a checksum mismatch) the partial `.part` file is
    removed, never left behind."""
    name = url.rsplit("/", 1)[-1]
    if name not in CHECKSUMS:
        raise ValueError(f"{name} is not a listed Census file")
    tmp = folder / (name + ".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        if _sha256(tmp) != CHECKSUMS[name]:
            raise ValueError(f"{name}: checksum mismatch; not kept")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp.replace(folder / name)


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        for u in sys.argv[2:]:
            print(fetch(u))
    problems = verify_files(CENSUS_DIR)
    print("\n".join(problems) or "all 9 Census files match their checksums")
    raise SystemExit(1 if problems else 0)
