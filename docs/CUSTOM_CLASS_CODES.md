# Custom class codes: a second Jev step after NAICS (future)

**Status: a design note.** Nothing here is built. It records how the original 30-code classifier
(the first version of this project, which is not carried into this repo) becomes a classifier for a
carrier's own class codes.

## Why

An insurance application often needs more than NAICS: a general liability class, a workers'
compensation class code, or a carrier's own appetite class. These are closed lists, just like
NAICS, and the same approach fits: find candidates in code, have Jev judge only those, and let code
decide or hold.

## What the 30-code classifier did, and what changes

It asked one Jev Choice over a hard-coded table of 30 NAICS codes, plus two Nouls: "too vague" and
"no listed code fits". Code held when either Noul fired, or when the pick was under the probability
bar. Three things change for class codes:

| 30-code classifier | Class codes |
|---|---|
| The table was hard-coded in `questions.py` | The table is data: a file per carrier or line of business, loaded into Postgres |
| All 30 codes went into every question | NAICS narrows the table first, so Jev sees a handful, not hundreds |
| "No listed code" was a Noul carrying the table | None-of-the-above is a code-owned hold after a low fit on every candidate (as in NAICS search) |

## How to adapt it

1. **Define the table.** One row per class code: `code`, `title`, `description`, `includes`,
   `excludes`, and optional `examples` (wording applicants use). Keep the carrier's official text. Load
   it into a `class_code` table with a weighted `tsvector`, following the same pattern as
   `naics_industry` (see [../db/README.md](../db/README.md)).
2. **Add a crosswalk.** `naics_class_code(naics, class_code)` lists which class codes can apply to
   each NAICS industry. Carriers often have one already. A code missing from the crosswalk is never
   offered for that NAICS.
3. **Find the candidates in code.**
   - With a NAICS code (written by the applicant or confirmed) and a crosswalk entry: the crosswalk's
     class codes.
   - With no crosswalk entry: full-text search over the class-code text, top 10.
   - Either way, at most about 10 candidates reach Jev.
4. **Have Jev judge (one call).** Ask three things, using the same question shapes as NAICS search:
   - a Choice over the candidates, each shown with its description, includes and excludes;
   - a fit Noul per candidate;
   - a vagueness Noul.
5. **Let code decide.** Act only when the pick's probability, its fit and the vagueness check all
   pass. Otherwise hold with a reason. Never write free text or a code outside the candidates.
6. **Measure before acting.** Each table needs its own eval set, including a blind held-out set
   written by someone else and run once. Its thresholds go in `questions.py`, with a comment
   saying how they were calibrated on the pinned model. Until it is measured, a table may only
   *suggest* codes for a person to confirm, never act.

## As a secondary API call

```
POST /v1/class-codes/classify
{
  "description": "Mobile car detailing at customers' homes",
  "naics": "811192",          // optional: written, confirmed, or from /v1/classify
  "table": "acme-gl-2026"     // which class-code table to use
}
```

The response has the same shape as `/v1/classify`: `gate`, `code`, `title`, `probability`,
`fit_noul`, `hold_reason`, `candidates`, `calls`. It adds `source`, which says whether the candidates
came from the crosswalk or from search.

A typical application flow:
1. `/v1/classify` for NAICS: auto-act, or a person confirms.
2. `/v1/class-codes/classify` with that NAICS: one more Jev call over a short list.
3. Review: a person sees both, with their reasons, before anything is written to the application.

## Open questions for when this is built

- Where carrier tables come from (files in the repo, or uploaded through the API), and how they are
  versioned.
- Whether one description may need several class codes (a primary plus secondary operations), which
  would mean Nouls per candidate rather than a single Choice.
- Licensing of carrier or rating-bureau class-code text. Unlike the Census data, it is usually not
  public.
