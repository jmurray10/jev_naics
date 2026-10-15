# Data

All classification data comes from the **US Census Bureau, NAICS 2022**: https://www.census.gov/naics/
These are US Government works and are not covered by this repo's licence. Nothing is typed by hand
except the reviewed modern-terms list, and that maps words to Census wording, never to a code.

## Source files (`data/census-2022/`)

| File | Bytes | SHA-256 | Used for |
|---|---|---|---|
| `2022_NAICS_Structure.xlsx` | 88,218 | `217c9e0d4d74e7517bc288f5f308b73aa0de5ee787976a6dd222412be28ada22` | not used by the builds (kept for reference) |
| `2022_NAICS_Structure_Summary_Table.xlsx` | 12,212 | `856c8d9409470f76b48d8bd725851abea1a0a8bb7fae31a800614d62b36ef607` | not used by the builds (kept for reference) |
| `2-6_digit_2022_Codes.xlsx` | 82,460 | `be12ba41002803359f49181c9bf33a03fbd08578f4f4a4c0bbad7aadaaea0316` | the tree: codes and titles at every level (`build_tree.py`) |
| `6-digit_2022_Codes.xlsx` | 44,036 | `3e3c90d4d36d874c0fd2da22a222c794654dbfa404320304152f5317781aefb7` | search: the 1,012 industries and titles (`build_search.py`) |
| `2022_NAICS_Descriptions.xlsx` | 263,810 | `6222c4d87dcf984970e0ff8a49862ed54b546b089d03956be3f285900cd3d66c` | both: official descriptions; search also splits out illustrative examples and cross-references |
| `2022_NAICS_Index_File.xlsx` | 499,358 | `6506b37b9546dd9cec1f8b79e0b38b68e547a5cce5fd6f8332d35024dbd6cd63` | search: 20,373 index entries (the words people use) |
| `2022_NAICS_Cross_References.xlsx` | 206,775 | `3c50c3bfa9d76862aea471cc8831fd726f0b978619d833e48c54a749d9622144` | search: 4,601 "X is classified in Y" notes (what an industry is not, and where it goes) |
| `2022_to_2017_NAICS.xlsx` | 61,577 | `76ed2d5bc8cd3443dc86f0c3225b83e6d187f3fc47d4c5696c911a3234f1716f` | the tree: every retired 2017 industry and the 2022 industries it became (from census.gov/naics/concordances) |
| `naics_manual_full.json` | 1,944,935 | `a79606a79661d815f8d65441607635845648eb8227c580d68bd60859f3a3114c` | the tree: index terms only |

`naics_manual_full.json` is a JSON conversion of the 2022 NAICS Manual published on the same site.
The conversion was not scripted, so it cannot be regenerated from census.gov as-is. About 100 of its
descriptions carry PDF page-header debris, which is why search uses the spreadsheets instead.

## Derived data (generated)

| Output | Built by | Contents |
|---|---|---|
| `naics_2022_tree.json` | `build_tree.py` | 2,126 nodes (20 sectors to 1,012 industries), titles, descriptions, index terms, the 2017 concordance |
| `naics_2022_search.json` | `build_search.py` | per industry: description, examples, index entries, exclusions, activities sent here from other industries |
| Postgres tables | `db/build` | see [db/README.md](db/README.md) |

Both builds read only the files above. `backend/tests/test_data_rebuild.py` rebuilds both from the
Census files and compares the result with the committed `naics_2022_tree.json` and
`naics_2022_search.json`, so a checked-in file can never silently drift from what the builds would
produce today.

## Benchmark data (not in the repo)

The external check in the README (JEV-57) uses the public
[ExioNAICS](https://huggingface.co/datasets/Yvnminc/ExioNAICS) dataset (company descriptions with
NAICS labels). It is third-party data, so it is not committed: the CSV, the sampled batches, the
results, and the per-case reviews live in the git-ignored `.scratch/exionaics/`. The repo carries
only the aggregate results and short paraphrased examples (`frontend/benchmark.html`), with no
company names. Labels there are noisy: 45% of its unique descriptions carry two or more codes, and the
review found 18 of 60 labels wrong.

## Refreshing

For a new NAICS edition, download the files from https://www.census.gov/naics/, update this table's
checksums, rebuild the derived data, and reload the database (the load is keyed by data version).
Then re-run every eval and re-measure every threshold before trusting the new data.
