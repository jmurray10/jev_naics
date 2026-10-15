# Calibration

Every numeric UPPERCASE constant in `backend/src/jev_naics/questions.py`, why it has that value, and
when it was measured. `backend/tests/test_docs.py` checks that every one of these names appears here.

**Model:** every measurement below is on the pinned Jev model, **jev-1.13.0**, the only version named
in `backend/src/jev_naics/_jev/providers.yaml`. **Date:** 2026-09-29, unless a line says otherwise.

## Full NAICS 2022 (JEV-48): the tree walk

| Constant | Value |
|---|---|
| `FULL_BEAM_WIDTH` | 3 |
| `PROB_FULL_PATH` | 0.7 |
| `NOUL_FULL_UNDERSPECIFIED` | 0.7 |
| `NOUL_FULL_FIT_HOLD` | 0.5 |

Justification, copied from the comment above these lines in `questions.py`:

> The path score is the geometric mean of the chosen edge probabilities along the path (the
> TypeSafe hierarchical-classification pattern); PROB_FULL_PATH gates the final pick among the
> finalists. Same lines as the closed agent's calibrated ones, checked 2026-09-29 on jev-1.13.0
> over evals/full_naics.json (33) and full_naics_heldout.json (6), final recorded run: 36 right,
> 1 correct hold, 2 held (vet, car wash), 0 wrong. Wrong final picks reached p 0.63-0.67, so the
> 0.70 bar alone would not always stop them; the fit Noul does the work (wrong-industry fits
> <= 0.16; the closest correct fit, tattoo, was 0.49-0.57 across three recordings, so it sits
> on the 0.50 bar). Not yet swept by tools/calibrate.py, which covers only the closed agent.

("The closed agent" is history: the 30-code `closed` mode, dropped before this repo existed — see
CHANGELOG.md.)

## Search, then Jev picks (JEV-50)

| Constant | Value |
|---|---|
| `SEARCH_K` | 10 |
| `PROB_SEARCH_PICK` | 0.7 (= `PROB_FULL_PATH`) |
| `NOUL_SEARCH_FIT_HOLD` | 0.5 (= `NOUL_FULL_FIT_HOLD`) |
| `NOUL_SEARCH_UNDERSPECIFIED` | 0.7 (= `NOUL_FULL_UNDERSPECIFIED`) |

Justification, copied from the comment above these lines in `questions.py`:

> Postgres full-text search (db.search, IDF-weighted) returns SEARCH_K candidates; Jev picks one
> of only those in one call, with a fit Noul per candidate.
> SEARCH_K: measured 2026-09-29 over the evals with a gold code, no model call: the right
> industry was in the top 10 for 31 of 32 dev and 6 of 6 held-out (top 5: 28 of 32; top 50: 32).
> The three gates are the tree walk's lines above (same questions: the pick's probability, the fit
> Noul, the vagueness Noul), re-checked 2026-09-29 on jev-1.13.0 over the search eval (33 dev + 6
> held-out, search_eval.py): every right pick had p >= 0.96 and fit 0.75-0.98 (lowest: moving
> company); the one miss (the right industry was not in the results) leaned at p 0.99 with fit
> 0.07, so the fit Noul, not the probability, held it. Vagueness: the vague case 0.92, the rest
> <= 0.35. That held-out set informed SEARCH_K and the ranking (gate audit), so a fresh one with
> close competitors (evals/search_heldout.json, committed before its run) was measured once: 7 of
> 8 right, 0 wrong; the retail bakery split with commercial bakeries at p 0.49 and this bar held
> it. Right picks there: p >= 0.97, fit >= 0.92. A lone search result always holds (unmeasured).
> Not yet swept by tools/calibrate.py.
> The hybrid (JEV-53, hybrid.py) adds no line of its own: its second pick, over search's results plus
> the tree walk's new finalists (up to SEARCH_K + FULL_BEAM_WIDTH), is search's pick with these gates.
> In the dev spike (all 51 dev cases run over the union of 10-12 candidates, 2026-09-29, jev-1.13.0)
> right picks had p 0.88-1.00 and fit 0.65-0.98, none wrong. In the recorded evals the fallback asked the
> union pick once (insurtech, 13 candidates: 524210 at p 1.00, fit 0.85); the retail bakery fell back but
> the walk added nothing. The fit Noul on tree-added candidates rests on that one answer, so the union
> pick is lightly measured. Only an industry the walk added can be acted on (hybrid.py).

## Spelling (JEV-52): code corrects typed words before search; no model call

| Constant | Value |
|---|---|
| `SPELL_MIN_SIMILARITY` | 0.4 |
| `SPELL_MAX_EDITS` | 2 |
| `SPELL_WEIGHT` | 0.5 |
| `TERM_WEIGHT` | 0.25 |
| `SPELL_MIN_WORD` | 4 |
| `SPELL_MAX_WORD` | 40 |
| `VOCAB_MIN_WORD` | 3 |

Justification, copied from the comment above these lines in `questions.py`:

> A word that is not in the Census text is corrected to the Census word with the fewest edits (at most
> SPELL_MAX_EDITS, Levenshtein), then the highest trigram similarity (at least SPELL_MIN_SIMILARITY).
> The corrected word counts at SPELL_WEIGHT of a typed word's weight, and the typed words stay in.
> Measured 2026-09-29 on the Census vocabulary (10,313 words) with the JEV-52 dev typos: real
> typos scored 0.44-0.75 at one edit ("clinc" -> clinic 0.44, "comercial" -> commercial 0.75);
> non-typos the Census lacks: "saas" 0.33, "doordash" -> door (4 edits), "kubernetes" 0.21. The
> known false friend, "meditation" -> mediation (1 edit, 0.62), is why a correction is down-weighted
> rather than trusted.
> Weights swept 2026-09-29 (gate audit) on dev only, by search recall of the gold (no model call):
> evals/full_naics.json + messy_dev.json, 50 cases with a gold code. Top 10 / mean reciprocal rank:
>   spell 0.25 / 0.5 / 1.0 at term 0.25:  50 / 0.760,  50 / 0.773,  50 / 0.793
>   term  0 / 0.25 / 0.5 / 1.0 at spell 0.5:  49 / 0.751,  50 / 0.773,  50 / 0.774,  50 / 0.743
> TERM_WEIGHT 1.0 pushed the incidental-term cases down (ranks 3, 3, 5: an app-booked plumber, a
> farm owner's podcast); 0.25 keeps all six at rank 1, and 0 loses the SaaS case. SPELL_WEIGHT 1.0
> scores a little higher on dev (MRR +0.02, same top 10), but its false friends ("seeing" -> seeding,
> "taco" -> tack) are rare in dev: 0.5 is kept as a margin against them, at that measured cost.
> Which words are spelling candidates: typed words of SPELL_MIN_WORD to SPELL_MAX_WORD letters (a
> 3-letter word has too few trigrams to correct safely; Postgres levenshtein refuses over 255, and no
> typo is 40 letters long); the Census vocabulary keeps words of VOCAB_MIN_WORD letters or more.

## Real-time assist (2026-10-05): completeness checks as a broker types

| Constant | Value |
|---|---|
| `NOUL_ASSIST_OPERATIONS` | 0.5 |
| `NOUL_ASSIST_ACTIVITY` | 0.5 |
| `NOUL_ASSIST_CUSTOMERS` | 0.5 (tooltip only, never a status) |
| `NOUL_ASSIST_SINGLE` | 0.5 |

Justification, copied from the comment above these lines in `questions.py`:

> Measured 2026-10-05 on jev-1.13.0 over evals/assist_dev.json (30 synthetic cases, recorded in
> transcripts/naics-assist.json): operations text, revenue/fee/funding-only cases 0.04-0.22 vs every other
> case 0.74-0.99; specific activity, generic one-word cases 0.03-0.17 vs clear cases 0.72-0.98 (mixed
> 0.64-0.79); one main activity, mixed cases 0.07-0.08 vs clear 0.69-0.97 (the three-part IT advisory case
> 0.47, which then fell to its pick-list anyway); customers stated 0.79-0.98 vs not stated 0.04-0.37, but
> "for shippers" read 0.34, so the customers check only picks a tooltip, never a status. 0.5 sits in each
> gap. Of the 11 clear cases 8 were coded, all right; car wash and vet held in the walk (PARTIAL), and one
> SaaS case failed closed on a malformed level answer (ERROR). Not yet checked on a held-out set.

The assist adds no other line. CODED is the tree walk's own act (`PROB_FULL_PATH`, `NOUL_FULL_FIT_HOLD`);
NEEDS_INFO also fires on the walk's vagueness Noul (`NOUL_FULL_UNDERSPECIFIED`); the sector shown on the
meter, and the PARTIAL level (the deepest node shared by the finalists covering the final pick's
probability), both reuse `PROB_FULL_PATH`. Those reuses are not separately measured. Latency on the same
run: NEEDS_INFO 0.16-0.30 s (1 call); a walk to a code 0.68-1.68 s (4-6 calls).

## Refine (2026-10-06, E9): wide pick and verify. PROVISIONAL

| Constant | Value |
|---|---|
| `REFINE_CHOICE_MAX` | 240 |
| `REFINE_SHORT_TERMS` | 3 |
| `REFINE_SHORT_CHARS` | 120 |
| `REFINE_TOP_K` | 4 |
| `REFINE_PER_BLOCK` | 2 |
| `REFINE_BLOCK_MASS_MIN` | 0.05 |
| `REFINE_BOUNDARY_ITEMS` | 4 |
| `REFINE_BOUNDARY_CHARS` | 120 |
| `REFINE_MIN_WORDS` | 1 |
| `REFINE_EXPAND_MAX_WORDS` | 6 |

Measured 2026-10-06 on jev-1.13.0 (experiments/EXPERIMENT.md, E9; recorded in
`transcripts/naics-refine.json`). `REFINE_CHOICE_MAX` is a structural limit (the API allows 255 options per
Choice), not a fitted number. `REFINE_PER_BLOCK`: with the group-weighted top 4, all three veterinary dev
descriptions missed (58 of 61 gold codes in the candidates); each block's own top 2 put 61 of 61 in.
`REFINE_BLOCK_MASS_MIN`: one dev description failed closed because a block of mining and utilities answered
malformed; a malformed block now holds only when its groups carry at least this much of the group pick.
Set from that one case, not yet checked on a held-out set. `REFINE_TOP_K` is used by the `wide`, `verify`,
`score` and `union` variants only. `REFINE_SHORT_TERMS` and `REFINE_SHORT_CHARS` set the wide pick's input
size (about 40k tokens per description) and were not tuned. `REFINE_BOUNDARY_ITEMS` and `REFINE_BOUNDARY_CHARS` (E12, `cascadeb`) size the Census boundary text shown for
each verified candidate (what an industry is not, and what belongs to it) to under about 1,000 characters a
candidate; they are provisional and not tuned. `REFINE_MIN_WORDS` (E15): the three-word gate stopped all 34 single- and two-word cases on the blind short/modern set
(72 cases, jev-1.13.0, 2026-10-07); with the gate off 50 were right (55 with the lexicon) against 23, and the assist
checks held 11 of 12 must-hold cases. `REFINE_EXPAND_MAX_WORDS`: Census wording for a recognised term is appended only
to descriptions of this many words or fewer. With no limit the blind set gave 55 right / 3 wrong, with 6 words
54 right / 4 wrong (one modern case lost), but long descriptions stopped changing: ExioNAICS medium 3 changes
to 0, a soybean farm that mentions a podcast stays correct, and long SaaS descriptions stay pick-lists. Both are
provisional and set from one blind set of 72; the lexicon itself (`data/naics_lexicon.json`) is a draft no person has
reviewed. The refine gate has no fitted number: one
candidate at the top Score level, and both option orders picking it (the entity-alignment pattern).

## Refine verify gate, re-measured (2026-10-07, E16; replay of recorded jev-1.13.0 answers, no new calls)

The refine gate (`refine.py`) codes the best verify candidate when its probability is at least `PROB_SEARCH_PICK` (0.70),
its fit at least `NOUL_SEARCH_FIT_HOLD` (0.50) and the "one main activity" check at least `NOUL_ASSIST_SINGLE` (0.50).
Those values were measured for search and the assist on other candidate lists; this is their first measurement on the refine
verify step. Set: every gold-labelled synthetic case that reached the verify step, 228 cases from six sets
(`full_naics`, `messy_dev`, `assist_dev`, `refine_heldout`, `refine_techenabled`, `refine_shortmodern`; 209 would be right
if coded, 19 wrong, a code on a case that must hold counting as wrong). These sets have been used to choose the
configuration, so this is a calibration set and not a test; the fresh blind set (E16) is the test.

| Setting (others at 0.70 / 0.50 / 0.50) | Coded | Right | Wrong |
|---|---|---|---|
| **current** | **208** | **201** | **7** |
| p at least 0.50 or 0.60 | 210 | 202 | 8 |
| p at least 0.80 | 207 | 201 | 6 |
| p at least 0.90 | 202 | 197 | 5 |
| p at least 0.95 | 197 | 193 | 4 |
| fit at least 0.35 | 211 | 202 | 9 |
| fit at least 0.65 | 202 | 197 | 5 |
| fit at least 0.80 | 187 | 185 | 2 |
| single at least 0.20 | 213 | 206 | 7 |
| single at least 0.80 | 189 | 182 | 7 |

Reading: the three bars sit on a flat part of the curve. The "one main activity" check does not separate right from wrong
at any value (7 wrong throughout). Raising the probability or fit bar buys fewer wrong codes only by giving up right ones:
p 0.95 trades 8 right for 3 wrong; fit 0.80 trades 16 right for 5 wrong. The 7 wrong codes at the current setting are
mostly confident (probability 0.77 to 1.00, fit 0.43 to 0.92), so no threshold removes them. **Decision: keep 0.70 / 0.50 /
0.50**; they are now measured on the refine verify step, and the cost of tightening is quoted above. Not a claim that wrong
codes are controlled by the gate: they are not.

## The eval sets these thresholds are checked against

Quoted from `backend/README.md`'s history sections (JEV-48, JEV-50, JEV-52, JEV-53), all live on
jev-1.13.0, 2026-09-29 unless noted:

**JEV-48 (tree walk):**

| Set | Result |
|---|---|
| Dev (33): the 23 JEV-2 descriptions plus 10 synthetic ones written for an intake app (JEV-47) | **31/33**: 30 right, 1 correct hold (vague), 2 unexpected holds |
| Held-out (6, committed on its own before the later changes, never tuned on) | **6/6** |
| Wrong codes | **0** |

**JEV-50 (search, then Jev picks):**

| | Search then Jev pick | Tree walk |
|---|---|---|
| Right industry in the top 10 of search | 31/32 dev, 6/6 held-out (no IDF: 29/32) | - |
| Dev (33) | **31 right**, 1 correct hold, 1 hold | 30 right, 1 correct hold, 2 holds |
| JEV-48 held-out (6) | 6/6 (it informed K and ranking) | 6/6 |
| Fresh held-out (8, close competitors, committed before its run) | **7 right, 1 hold** | - |
| Wrong codes | 0 | 0 |
| Jev calls per description | 1 | about 5 |

**JEV-52 (spelling and modern terms):**

| Set | Before JEV-52 | After |
|---|---|---|
| Messy dev: the first 12 (typos, jargon, several activities) | 10 right, 2 held (1) | **12 right** |
| Messy dev: 6 more where a listed term is incidental (added for the weight sweep) | - | **6 right** |
| Messy blind held-out (12, written and committed by a separate agent) (2) | - | **11 right, 1 held** |
| Dev (33) | 31 right, 1 correct hold, 1 hold | **32 right, 1 correct hold** ("SaaS" fixed) |
| JEV-48 and fresh held-out (14) | 13 right, 1 held | unchanged |
| Wrong codes | 0 | 0 |

1. Measured live before any change and recorded to a scratch transcript outside the repo (the
   before-state has no replay here); the output is quoted on Linear JEV-52.
2. Run twice, never to choose anything: with TERM_WEIGHT 1.0, then again after a dev-only sweep
   (gate audit) set it to 0.25. Both runs gave the same result.

**JEV-53 (search first, the tree walk as a fallback):**

| Set | Search alone | Tree walk alone | **Hybrid** | Hybrid fallbacks |
|---|---|---|---|---|
| **Blind held-out** (16, written and committed by a separate agent before the hybrid existed; run once) | 8 right, 8 held | 14 right, 2 held | **15 right, 1 held** | 8 |
| Dev (33) | 32 right, 1 correct hold | 30 right, 1 correct hold, 2 held | 32 right, 1 correct hold | 0 |
| Messy dev (18) | 18 right | 17 right, 1 held | 18 right | 0 |
| Messy blind held-out (12, seen: it motivated JEV-53) | 11 right, 1 held | 12 right | 12 right | 1 |
| Fresh held-out (8, seen) | 7 right, 1 held | - | 7 right, 1 held | 1 |
| Wrong codes | 0 | 0 | 0 | |

The blind held-out result above is also checked in code, on the recorded transcript, in
`backend/tests/test_hybrid_replay.py::test_the_jev53_blind_held_out_run_once`: hybrid 15 right / 1
held (`hy-quirk-vet`, the veterinary clinic neither search nor the walk offered); search alone on the
same set 8 right / 8 held; the tree walk alone 14 right / 2 held; 0 wrong in any of the three.

## External check, not calibration (JEV-57, 2026-10-01)

60 random unique descriptions from the public ExioNAICS dataset, run live once through the hybrid
and judged blind (README, "External check"): 0 indefensible codes, 20 of 60 held. **No constant in
this file was changed for it**, and none should be tuned on it. It is a test set, and its labels are
noisy. Items it raised for a later, measured calibration pass:
- 4 of the 5 holds that should have acted had leaned to the right code.
- A clearly described freight forwarder was held as `too_vague`.
- Franchisor vs operator (533110 vs the operated industry) is a recurring arguable call.

## The rule

**Re-measure before changing `backend/src/jev_naics/_jev/providers.yaml` (the model pin) or any
threshold above.** `tools/calibrate.py` did not move into this repo — it swept only the 30-code
`closed` agent, which was dropped (CHANGELOG.md); it still exists in `jev_peas`, tied to that
agent, and is not a command in this repo. Re-measuring here means a live run against the pinned
model over the eval sets above, recorded with `JEV_DECIDER=record` (or `record-missing` for only
the new cases), before trusting a changed number.
