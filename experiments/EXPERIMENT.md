# Experiment log

The running record of every test since the batch runs began (2026-10-05). Conventions and labels:
[README.md](README.md). Model: **jev-1.13.0** throughout (the pin in `_jev/providers.yaml`).

**This public log covers the synthetic eval sets and the public ExioNAICS dataset only.** Some of the
work also ran on a private sample of real insurance applications; those results, and anything derived
from them, are not published here. The scripts take your own file instead: see "Bring your own data"
in [README.md](README.md).

The architecture these test is PEAS (PEAS.md, DESIGN.md): Jev is a **sensor** that answers closed
questions; code builds every closed list, checks every answer, and owns every actuator (`auto_act`,
`hold`, and for the assist, the tooltip and the broker pick-list). An experiment changes a sensor, a
closed list, or a gate. It never gives Jev an actuator.

## Status

| ID | Question | State |
|---|---|---|
| E1–E5 | The three methods, a disagreement review, and combining methods, on a private sample | done; results not published |
| E6 | Live assist: can completeness checks gate vague text at entry? | done, calibrated |
| E7 | Live assist through the UI on hard private cases | done; results not published |
| E8 | UI flicker and revert | done |
| E9 | Refine: a wide pick plus a verify step, in several variants, against the current assist | spike done (dev; private samples not published) |
| E10 | Verify first, scoreb only when verify does not code | done: provisional default |
| E11 | Blind held-out set, refine variants v assist (run once) | done |
| E12 | Software v the core service it wraps (fintech, insurtech): where does it fail, and does Census boundary text fix it? | S0 and J1 done (J1 neutral); J2 and autoresearch need more labelled pairs |
| E13 | ExioNAICS, an external labelled set (JEV-57 data): refine v assist, small and medium, off-label judged blind | small and medium done |
| E14 | ExioNAICS large (2,000) | done: refine +59 usable codes, indefensible level (6 v 7); pick-lists 32% |
| E15 | The lexicon: modern terms, abbreviations and short text, read before Jev | built and tested; the blind measurement is blocked on credits |

---

## E1–E5: the methods on a private sample (results not published)

**Method.** `tools/run_naics_sample.py` against the running API (`/v1/classify` in each mode, then
`/v1/consensus`, `/v1/triage` and `/v1/tree-first`), one call per distinct description, on a private
sample of real applications kept outside the repo; disagreements with the applications' own labels were
reviewed by hand against the Census text.

**What it decided** (the lessons, not the data):
- An application's own NAICS label is not ground truth: many are NAICS 2017 codes, and subscription
  software is often labelled 541511 where the 2022 text for 513210 covers it. Score against reviewed
  verdicts, not labels.
- Most wrong codes came from descriptions that state revenue or billing rather than an activity, or from
  a word read literally. That led to the completeness checks in E6.
- Agreement between the hybrid and the tree walk is the strongest signal; one Choice over many close
  candidates spreads probability below the bar.

## E6: live assist (`/v1/assist`, `/v1/finalize`)

**Method.** Request A asks completeness Nouls (operations text, specific activity, customers, one
activity), a delivery-mode Choice, and the tree walk's first level, in one call; the walk continues only
if the text passes. Calibrated on `backend/evals/assist_dev.json` (30 synthetic), recorded in
`backend/transcripts/naics-assist.json`. Bars in `questions.py` and `CALIBRATION.md`.

**Result.** Revenue-only text 0.04–0.22 vs other 0.74–0.99 on the operations check; generic 0.03–0.17
vs clear 0.72–0.98 on specificity. 14 of 14 revenue/generic cases NEEDS_INFO at 1 call (~0.2 s); 8 of
11 clear coded, all right; latency 0.7–1.7 s to a code.

## E7: live assist through the UI on hard private cases (results not published)

**Method.** Chrome via Playwright, Live assist tab: type, wait, Submit, read the page, on the private
cases the earlier methods got wrong or held. **Found.** `/v1/finalize` ignored the completeness checks:
texts the meter flagged could still be coded. Refine's Submit (below) answers from its own result instead.

## E8: UI flicker and revert

**Method.** Measure the Submit button's position while typing through six stages (Playwright).
**Result.** 0 moves after the fixes (stable layout, redraw only on change, results cache, reset on
delete). No Jev change.

---

## E9: refine (in progress)

**Question.** Does a wide pick over every industry plus a verify step code more of the right industries,
and fewer wrong ones, than the current assist (E6), without breaking the hard rules?

**Why.** E1–E7 show three gaps: the tree prunes the right branch early (car wash, vet); one Choice over
many close candidates spreads probability (E5); and the option order can change a pick (jev-1.13
jaggedness notes; one dev case returned a choice that was not its most probable option).

**PEAS change.** One new sensor and one new reading; actuators unchanged.

| PEAS part | Today (E6) | E9 |
|---|---|---|
| Sensor: candidates | tree walk (3 finalists) | **wide pick**: Choice over 22 sector groups + one Choice per block of whole groups (≤ 240 each) over all 1,012 industries' short profiles, in one call |
| Sensor: fit | fit Noul per finalist | **fit Score** per candidate, 3 levels written before any answer: different / related / primary |
| Sensor: stability | — | the verify pick asked twice, options in opposite orders |
| Gate | p ≥ 0.70 and fit ≥ 0.50 | exactly one candidate at "primary" and both orders agree on it (no fitted number) |
| Actuators | emit code, hold, tooltip, pick-list | same; pick-lists can now be built from any 2–4 candidates, not only the curated pairs |

**Variants** (`backend/src/jev_naics/refine.py`, `variant=`):

| Variant | Calls | What it tests |
|---|---|---|
| `assist-v1` | 1 + ~4 | the E6 baseline |
| `wide` | 1 | the wide pick alone (gated at 0.70) |
| `verify` | 2 | wide top K + search's pick and fit Nouls |
| `score` | 2 | wide top K + fit Score + order check |
| `union` | 2 + ~4 | `score` over the wide top K plus the tree walk's finalists |

**Sets.** Dev only, all synthetic: `full_naics.json` (33), `messy_dev.json` (18), `assist_dev.json`
(30): 61 with a gold code, 20 expected holds or needs-info. The held-out sets are not used (they were
run once already).

**Measures** (per variant): coded right, **coded wrong**, held; gold in the wide top K (recall@K);
input tokens per description; calls; latency. The hard rule is checked on every case: no 6-digit code
without passing completeness checks.

**Plan.**
1. Spike: run every variant on dev, recorded (`JEV_DECIDER=record-missing`, transcript
   `backend/transcripts/naics-refine.json`); summary to `experiments/refine/runs/`.
2. Choose K and the default variant from recall@K and wrong codes; write the measurement into
   `CALIBRATION.md` before `REFINE_TOP_K` stops being provisional.
3. Implement the endpoints (`/v1/refine/assist`, `/v1/refine/finalize`; new only, nothing existing
   changes), tests (fake deciders + replay), a UI toggle, and the short-text tooltip.
4. Live API test on the dev sets.

**Log.** (newest last)

- 2026-10-06: design read (PEAS.md, DESIGN.md): the wide pick is a sensor over a closed list built in
  code; gates stay on probabilities and Score levels, never Choice confidence. Sizing: 1,012 industries,
  manufacturing 346 (over the 255 limit, so split 31/32/33), short profiles 115k characters total.
- 2026-10-06, **spike on dev** (81 synthetic cases, 61 with a gold code; runs `E9-<variant>-dev-20261006`,
  recorded in `backend/transcripts/naics-refine.json`). Coded right / coded wrong / correct industry in the
  candidates:

  | Variant | Right | Wrong | Gold in candidates | Calls | Input tokens |
  |---|---|---|---|---|---|
  | assist-v1 | 54 | 1 | n/a | 4.4 | n/a |
  | wide | 51 | 2 | 58/61 | 0.9 | 39.9k |
  | verify | 55 | 1 | 57/60 | 1.7 | 41.8k |
  | score | 54 | 1 | 58/61 | 1.7 | 42.3k |
  | **scoreb** | **57** | 1 | **61/61** | 1.7 | 45.4k |

  **Found.** (1) The wide pick lost all three veterinary descriptions: the sector-group pick files a vet
  clinic under health care (62), and weighting by P(group) buried 541940. `scoreb` drops the weighting and
  takes each block's own top 2: 61/61. (2) One ERROR: a block of unrelated sectors (mining, utilities)
  answered an insurance description malformed. Now a malformed block only fails closed when its groups carry
  `REFINE_BLOCK_MASS_MIN` (0.05) of the group pick. (3) The one wrong code (`conf-mkt`) is the same in every
  variant: a confusable case, not a retrieval gap. Dev is near ceiling, so it cannot rank the variants
  further; larger samples (private, not published here) did that.
- 2026-10-06, **live API test** of `/v1/refine/assist` (TestClient, real key, `record-missing`): 2 words →
  `TOO_SHORT`, 0 calls; car detailing → 811192 (2 calls, 2.6 s); typo'd veterinary text → 541940 (assist
  leaves it PARTIAL); "We build software for clients." → `NEEDS_INFO` (1 call). Per call about 42–49k input
  and 12.7–13.2k output tokens: the wide Choice returns a probability for every option, so output cost is
  not small. Offline: 10 new tests (`test_refine.py`), full backend suite and 45 frontend tests pass.

**State.** E9 spike finished on dev. The wide pick fixes retrieval (61/61 gold in the candidates on dev,
vets included). On larger private samples (not published) `verify` was the safe default over assist and
`scoreb` was the right tool for the cases verify does not code, but alone it asked a broker about a third
of descriptions; an `agree` variant (code only when both agree) gained nothing. Next experiment (E10):
verify first, and run scoreb only when verify does not code.

## E10: cascade (verify first, scoreb only when verify does not code)

**Question.** Can one variant keep `verify`'s coverage and safety and still give brokers a pick-list only
where the ambiguity is real?

**Method.** `refine(variant="cascade")`: one shared wide call; `verify`'s call; if it codes, stop (2 calls);
otherwise the `scoreb` verify call on the same wide call (3). Evaluated by replay on every E9 set: every
answer was already recorded, so no new Jev call. Runs `E10-cascade-<set>-20261006`.

**Result.**

| Set | assist-v1 | verify | scoreb | **cascade** |
|---|---|---|---|---|
| dev (61 gold): right / wrong | 54 / 1 | 55 / 1 | 57 / 1 | **58 / 1** |
| calls per description (dev) | 4.4 | 1.7 | 1.7 | 1.8 |

A **strict AND** rule (code only when verify and scoreb code the same industry) threw away about 40% of the
right codes on the private samples to remove a couple of wrong ones; not adopted. The wrong codes that
remained read clearly enough that every check passes; only a person catches them.

**Decided.** `cascade` is the provisional default of `/v1/refine/assist`. It has the best dev score, fewer
pick-lists than scoreb on the private samples, and 1.8 calls on dev against assist's 4.4. It is not
yet a replacement for assist: its cost is input tokens (about 44k a description), and the left-over wrong
codes are not gate-fixable. No threshold changed; `CALIBRATION.md` is unchanged.

**Open.** A blind held-out set written before the method would be the way to claim accuracy (E11).

## E11: blind held-out set (run once, not tuned)

**Method.** `backend/evals/refine_heldout.json`: 60 synthetic descriptions (28 clear, 8 filed somewhere
unexpected, 6 modern wording, 6 typos, 6 confusable pairs, 6 that must hold), gold codes checked against the
Census titles, written by a separate agent that was told not to read the classifier, the transcripts or these
results, and committed before any variant ran on it. Run once, live, recorded; `E11-<variant>-refine_heldout`.
Strict: a code on a hold case counts as wrong.

**Result.**

| Variant | Right (of 54 gold) | Wrong | Holds correct (of 6) | Calls | Gold in wide candidates |
|---|---|---|---|---|---|
| assist-v1 | 48 | 0 | 6 | 4.8 | n/a |
| verify | 49 | 0 | 6 | 1.9 | 53/54 |
| scoreb | 48 | 0 | 6 | 1.9 | 53/54 |
| **cascade** | **49** | **0** | **6** | 2.0 | 53/54 |

Cascade left 5 gold cases PARTIAL (a general-practice-style accounting case, pet boarding, crypto mining,
a typo'd general contractor) and 1 as a pick-list that did not offer the gold (dating app: 516210 v
513210); assist held the same ones. No variant coded a wrong industry or coded a hold case.

**Read.** On a set this clean the methods are close: refine is **one code better, no worse on wrong codes,
and uses 2.0 calls against 4.8**. It does not show the gain E9/E10 suggested on the larger private
samples, and it cannot: the held-out set has no real-world noise. It supports "refine is not worse and is cheaper in calls",
not "refine is more accurate". The agent flagged its less certain golds (vacation-rental manager 531311,
gig delivery driver 492210, volunteer fire department 922160, three confusable pairs); none of the misses
above are among them, so the result does not depend on them.

## UI (2026-10-06)

Live assist tab: a **Method** toggle (current assist, or refine cascade). Refine's Submit answers from the
refine result itself (a code only when CODED or the broker answered the pick-list; otherwise held with the
reason), so it never second-guesses a check the way `/finalize` did (E7). Under three words the page shows
the `too_short` copy as the tooltip (not a faint hint) in both methods. Tested in Chrome against the
rebuilt compose stack: too-short tooltip in both methods; clear text coded and Submit used the refine result
(2 calls); revenue-only text got the operations tooltip and Submit held with the reason; an ambiguous
software description showed the 513210 v 541511 pick-list, Submit without an answer held, with an answer
returned the broker's code; toggling routed to `/v1/refine/assist` and `/v1/assist`. Found and fixed: a
submitted result stayed on screen after the text was edited. 49 frontend tests.

## E12: software versus the core service it wraps (fintech, insurtech, and the like)

**Question.** When a description mixes software and a service ("monthly-fee money coaching app", "platform
that places insurance"), does the pipeline pick the industry the Census text says is the primary activity,
and if not, which stage fails: finding the right candidates, or judging between them?

**Scope.** Still description of operations to NAICS. No underwriting policy, no database of companies. The
gold is what the Census text (title, description, index entries, `excludes` "X is classified in Y",
`sent_here`) says the primary activity is. The refinement is inside the PEAS sensor design: how code builds
the closed list and the questions Jev answers. Jev never sees an option code did not build.

**Hypothesis.** The wide pick already offers both neighbours (each block's top 2 includes the software
block's 513210 and the service block's 523940), so a miss is a judging failure: the verify step shows each
candidate only `description[:400]` and none of the Census boundary text (`excludes`, `sent_here`), which is
exactly where "software publishers do not do X; X is classified in Y" lives.

**Set.** `backend/evals/refine_techenabled.json`: 48 synthetic descriptions in 24 matched pairs (software
first v the same vertical's service with an app as the channel), written blind by a separate agent from the
Census text; verticals: personal finance, insurance, lending/payments, health, real estate, education,
logistics, legal/accounting, staffing, one free. Regression: dev (61 gold) and the held-out set (E11).

**Spike (live, recorded, run once per arm).**
- **S0 baseline.** `cascade` as it is. For every miss, classify the stage: gold absent from the wide
  candidates (a recall failure), present but not chosen (a judging failure), or chosen as a pick-list that
  offers it (a one-click recovery). Measures per pair: both sides right, one side right, neither.
- **J1 boundary text.** The verify step's candidate profile adds each industry's `excludes` and `sent_here`
  (cut to a budget), nothing else changed. Cost: input tokens in the verify call only.
- **J2 what the customer receives.** A closed facet Choice in call 1 (software the customer uses themselves
  / a service performed for the customer / goods / a financial product or funds); code maps the answer to
  industry subsets built from the tree (the software and IT industries v the rest). When the facet is sure,
  the verify candidates are the subset's; otherwise both sides stay. Never removes a candidate the facet is
  unsure about (fail closed). The mapping is code-owned configuration in `questions.py`, measured before it
  is trusted.
- **J1+J2** if each helps alone.

**Measures.** Right / wrong / pick-list-offering-gold / held per kind (software, service); pair
accuracy; calls and tokens; regression on dev and E11 must not lose a right code or add a wrong one. Wrong
on a pair is the cost that matters: a software company coded as a financial advisor, or the reverse.

**Decide.** Keep an arm only if it fixes pairs without regression; a threshold or mapping moves into
`questions.py` only with its measurement in `CALIBRATION.md`. If S0 shows the failures are recall, the arms
change to the wide pick's framing instead, and this plan is revised before any code.

**State.** Plan written; waiting for the blind set (written independently, not yet seen).

## E12 (continued): software v the core service. Results so far

**S0 baseline** on `refine_techenabled` (48 synthetic, 24 matched pairs, written blind from the Census text;
gold 513210 for every software case, a service code for every service case):

| Variant | Right | Wrong | Software right (of 24) | Service right (of 24) | Gold in wide candidates |
|---|---|---|---|---|---|
| assist-v1 | 43 | 1 | 23 | 20 | n/a |
| verify | 44 | 1 | 23 | 21 | 47/48 |
| scoreb | 42 | 2 | 20 | 22 | 48/48 |
| cascade | 45 | 2 | 23 | 22 | 48/48 |

**Where it fails.** Not retrieval: the gold was among the wide candidates for all 48 pairs. The few misses are
judging: a coding-practice platform licensed to universities by subscription coded 611420 (computer
training) instead of 513210; a virtual primary-care practice coded 621491 (HMO medical centers) instead of
621111 (the gold is arguable); online talk therapy ended as a pick-list that offered the gold. The set is
written clearly, with explicit verbs, so it understates the problem on messy real text.

**J1: Census boundary text** (`cascadeb`: each verified candidate also shows its `excludes` and
`sent_here`, 4 items of 120 characters at most; found while reading the code: the verify instructions said
"what it is not" but the options carried only title and `description[:400]`). Tech-enabled: 45 right, wrong
2 → 1 (the HMO code became a pick-list); the education platform unchanged. Regression: dev 58/1 and held-out
49/0, identical to cascade. Cost +about 600 input tokens. J2 (a "what the customer receives" facet) and the
autoresearch loop are not started: 48 pairs is too few to fit or hold out.

**J1 full regression** (on the private samples too, not published). **Neutral**: it turns a few more cases into
pick-lists and fixes one wrong code on the tech-enabled set, for about 600 more input tokens.
Not adopted as the default; `cascadeb` stays available. The boundary text is Census wording, so keeping the
verify instructions and the options consistent is still the right design; it simply is not where the
remaining errors are.

## E13: ExioNAICS, an external labelled set (the JEV-57 data)

**Data.** Hugging Face `Yvnminc/ExioNAICS` (public, apache-2.0): 20,535 rows, 13,669 distinct descriptions
with a 2022 NAICS code (7,521 with one code, 5,986 with two or more). Local only, in the git-ignored
`.scratch/exionaics/`, with its samples (`make_exio.py`: nested 100 / 500 / 2,000, seed 11) and per-row
results; only summaries are tracked. The labels are noisy (45% of descriptions carry two or more codes;
JEV-57 found about 30% of labels wrong), so a case matches if the coded industry is **any** of the
description's labelled codes, and an off-label code is judged blind before it is called wrong. As in JEV-57,
nothing is tuned on it: it is a test set.

**Small (100).** Off-label codes judged blind by a separate reviewer from the description and the Census text
(27 distinct cases: 19 defensible, 8 arguable, **0 indefensible**; the reviewer read the full Census text for
only a few codes and judged the rest from titles, index terms and general NAICS knowledge, so this is a
lighter review than JEV-57's).

| Variant | Coded | Label match | Off-label: defensible / arguable | Usable (match + defensible) | Pick-lists containing a label | Calls | Input tok |
|---|---|---|---|---|---|---|---|
| assist-v1 | 53 | 30 | 16 / 7 | 46 | 1 | 5.2 | 0 |
| cascade | 55 | 34 | 17 / 4 | 51 | 14 | 2.3 | 47.5k |
| cascadeb | 56 | 34 | 18 / 4 | 52 | 14 | 2.3 | 48.6k |

When both assist and cascade coded (47), they chose the same code in 46. No variant coded an indefensible
industry. Cascade codes 8 that assist did not and misses 6 that assist coded, and offers the labelled code
in 14 pick-lists (assist 1).

**Medium (500 ExioNAICS descriptions, `E13-<variant>-exio-medium-20261006`, local per-row files).**
Off-label codes (152 distinct descriptions with a code the dataset does not list) judged blind by **two
independent reviewers**, each given the full Census text for every proposed code (title, description, index
entries, what the industry is not), told to check a rival code, not told the method or the dataset label.
15 cases were given to both: **identical verdict on 15 of 16 codes** (one defensible v arguable) and **the same
best code on 15 of 15**, so the review is reliable. Where they overlapped, the stricter verdict is used (the
lenient reading changes at most one code).

| Variant | Coded | Label match | Off-label: defensible / arguable / **indefensible** | Usable (match + defensible) | **Indefensible, % of coded** | Pick-lists containing a label | Errors | Calls | Input tok |
|---|---|---|---|---|---|---|---|---|---|
| assist-v1 | 264 | 144 | 83 / 31 / **6** | 227 (86%) | **2.3%** | 3 | 4 | 5.1 | 0 |
| cascade | 288 | 159 | 89 / 38 / **2** | 248 (86%) | **0.7%** | 81 | 2 | 2.3 | 47.6k |
| cascadeb | 286 | 160 | 87 / 37 / **2** | 247 (86%) | **0.7%** | 82 | 3 | 2.3 | 49.0k |

**The indefensible codes (6 cases).** Assist coded all 6. Cascade coded 2 of them (a drug store coded as
wholesale 424210, best 456110; a factory homebuilder coded 236117, best 321991) and avoided the other 4: on
two scale/telecom-equipment cases it coded the reviewers' best code (333998, 334210) where assist did not, and
on the news publisher (513110 → 516210) it offered a pick-list. Cascadeb also coded the reviewers' best code
on the third scale case.

**Read.** On 500 random external descriptions refine codes **24 more** than assist (288 v 264), matches **15
more labels**, makes **a third as many indefensible codes** (2 v 6 of about 270), uses **under half the
calls** (2.3 v 5.1), and offers the labelled code in 81 pick-lists where assist offers it in 3. About 86% of
the codes either method assigns are label matches or defensible; the remaining 14% are arguable
(reasonable neighbours). Cascadeb is indistinguishable from cascade.

**Limits.** Indefensible counts are small (6 and 2), so the gap in wrong codes is suggestive, not
statistically firm. The reviewers are models reading the Census text, not underwriters. They agree with each
other, which shows consistency, not that an underwriter would agree. The dataset labels are noisy, so label
match is a floor. A pick-list is not a code: **169 of 500 (34%)** ended in one (88 whose options did not include a labelled code and 81 whose options did; an earlier version of this line reported only the 88), and nobody has tested whether users
answer them correctly. 2,000-description run not done.

## E14: ExioNAICS large (2,000): first attempt failed on credits; completed 2026-10-07 (result at the end of this file)

Started 2026-10-06 for `cascade` and the assist baseline (`exio_large`, nested over the 500 already run; per-row files
in `.scratch/exionaics/`, transcripts `naics-refine-exio-{cascade,assist}.json`, which keep every completed call).
The first attempt returned ERROR for about 1,450 of 2,000 from case 62 on: the cause was **HTTP 402 from
TypeSafe: "Your organization has no available TypeSafe API credits."** Those two result files were error-polluted
and were removed; they are not results. The harness now retries with backoff and prints the real error.
A re-run of `cascade` got past case 620 before credits ran out again. Everything is resumable at the cost of the
remainder only. Nothing from E14 may be quoted until both variants finish the 2,000 and the off-label codes are
reviewed blind.

## E15: the lexicon (how people describe a business, before Jev)

**Question.** People write "SaaS", "HVAC", "dropshipping"; the Census text does not. A large share of the distinct
words in real descriptions are not in the Census text, "SaaS" is common, and many descriptions are under
three words. The reviewed term list
(27 terms, JEV-52) feeds only the Postgres search; the refine pipeline, the tree walk and the assist call never
use it, and the three-word gate ignores it. Does reading the text against a lexicon before Jev (a) let a
recognised short term be checked and (b) help Jev when the Census wording is added, without choosing a code?

**Design (PEAS).** A sensor stage in code before the Jev call. A lexicon entry maps a term to Census wording,
never to a code; a term with no industry ("b2b", "fintech", "esports") is recognised and adds nothing. Under
three words is `TOO_SHORT` only when no listed term is recognised. What Jev sees is the user's own words, then
`[Census wording: ...]`. Source: a reviewed JSON file now; a table in its **own database** (`lexicon.sql`: term,
census, why, reviewed_by, reviewed_on, active, added_from; never the Census tables) once the measurement earns it.

**Built.** `lexicon.py` (read, from_json, from_db), `lexicon.sql`, `refine(lexicon=...)` and the harness's
`--lexicon/--lexicon-mode`; 8 tests (matching, bounded expansion, gate, what Jev is asked, frozen-list
validation, the SQL round trip against a `*_test` database). Text with no listed term is asked exactly as before.

**Frozen list** `experiments/refine/lexicon_v1.json`, sha256 prefix `f0656ef3b12b6807`: the 27 JEV-52 terms plus 33 drafted
from the out-of-vocabulary analysis (hvac, cpa, bnpl, robo-advisor, mga, neobank, dropshipping, print on demand,
e-bike, micromobility, food truck, boba, last mile delivery, freight broker, 3pl, urgent care, crossfit, yoga
studio, influencer, content creator, youtuber, coworking, reit, cybersecurity, devops, plus recognised-only
insurtech, proptech, edtech, healthtech, regtech, esports). Every Census word in an entry is checked against
the Census text. **Not reviewed by a person.** Written before the blind set was opened.
*Disclosure:* the agent that wrote the blind set named some of its terms in its hand-back report (HVAC, CPA, PaaS,
IaaS, rideshare, coworking, PEO, doula, MRI, crypto, staffing, esports) before the list was frozen. HVAC, CPA,
coworking and esports were already in the draft from the vocabulary analysis; PEO, doula and MRI were not added.

**Blind set** `backend/evals/refine_shortmodern.json`: 72 synthetic cases written by a separate agent that was
told not to read the lexicon or the classifier (20 single-word, 14 two-word, 14 modern multi-word, 12 that must
hold, 12 plain controls).

**Plan (not yet run: credits).** Arms on the blind set, run once: (1) cascade, no lexicon; (2) lexicon in `gate`
mode (recognised terms pass the gate; no Census wording added); (3) lexicon in `full` mode; current assist for
reference. Measures: right / wrong / pick-list-with-gold / held, per kind; the controls must not change.
Regression on dev, held-out and the larger samples (text with no term must be identical to before).
Decide: adopt `full` or `gate` only if it fixes cases without adding a wrong code; move the lexicon into its own
database only after the list is reviewed by a person.

### E15 results (2026-10-06; lexicon frozen at sha256 prefix `f0656ef3b12b6807`; run once on the blind set)

**Credit note.** Runs hit HTTP 402 (no TypeSafe credits) twice, and for a time looked like they were failing for
that reason when my own command had been aborted by a zsh glob (`rm -f x*` with no match aborts the whole line).
Harness fixes: retry with backoff and the real error printed. Results below are from complete runs with zero
retries and zero errors, except the one assist case that returned ERROR.

**Blind short/modern set (72 synthetic; `E15-*-refine_shortmodern-*`).** Right / wrong (a code on a hold case counts
as wrong), by kind:

| Arm | single-word (20) | two-word (14) | modern multi-word (14) | must hold (12) | plain controls (12) | ALL right / wrong |
|---|---|---|---|---|---|---|
| cascade, 3-word gate (today) | 0 | 0 | 11 | 12 held | 12 | 23 / 2 |
| assist-v1 (no gate) | 13 | 9 | 9 | 11 held, 1 wrong | 12 | 43 / 4 |
| lexicon, gate on (recognised terms only) | 7 | 1 | 12 | 9 held + 1 pick-list, 1 wrong | 12 | 32 / 2 |
| gate off, no lexicon | 14 | 13 | 11 | 11 held, 1 wrong | 12 | 50 / 4 |
| **gate off + lexicon (full)** | **18** | **13** | **12** | 10 held + 1 pick-list, 1 wrong | 12 | **55 / 3** |

The 3-word gate stopped all 34 single/two-word cases, including plain trade words the old assist codes
correctly (plumber, dentist, winery, brewery, car wash, pest control, tax preparation). The completeness checks,
not a word count, are what held 11 of 12 must-hold cases. The lexicon adds 4 single-word and 1 modern-term case
on top of gate-off (SaaS, PaaS, IaaS, CPA terms). The 3 wrong codes are all cases the writer flagged as
arguable: a crypto exchange (523210 for 523160), an esports club (the Census indexes it under two codes), and
a pickleball club (713940 for 713990). Controls unchanged in every arm.

**Regression of gate-off + lexicon against the plain cascade** (`compare_lex.py`; counts):

| Set | Cases | Changed | Right | Wrong / off-label |
|---|---|---|---|---|
| dev (81, 61 gold) | | 7 | 58 → 58 | 1 → 2 |
| held-out (60) | | **0** | 49 → 49 | 0 → 0 |
| tech-enabled (48) | | **0** | 45 → 45 | 2 → 2 |
| ExioNAICS medium (500) | | 3 | 159 → 159 | 129 → 130 |

Dev changes: **CPA firm 541211 now right (was PARTIAL); a soybean farm that hosts a small podcast regressed
from correct 111110 to NEEDS_INFO** (the podcast Census wording was added to a text whose primary activity was
clear, and it made the text read as two activities); "SaaS" alone is now coded 513210 where the old fixture
expected a hold (the old fixture predates the decision that single-word terms like SaaS are valid). On the
private samples the common change was a SaaS description moving from a pick-list or NEEDS_INFO to a 513210
code, plus short texts now being checked; the changed cases were reviewed blind (not published). On
ExioNAICS 1% to 2% of descriptions contain a lexicon term.

**Decision (2026-10-07), provisional:** gate off (`REFINE_MIN_WORDS` = 1: only blank is too short), the lexicon
recognises terms at any length, and the Census wording is appended only to descriptions of **6 words or fewer**
(`REFINE_EXPAND_MAX_WORDS`). Same arm on the blind set: 54 right / 4 wrong (full expansion 55 / 3; today 23 / 2);
single-word 18 of 20, two-word 13 of 14, plain controls 12 of 12. Regression: held-out 0 changes, tech-enabled 0,
ExioNAICS medium 0 (it was 3), dev 5 (was 7, podcast farm restored; the CPA-firm gain is lost because that text is 15 words). Not adopted: expansion on long text, until there
are more labelled long cases showing it helps.

**In the product.** `lexicon.py` + packaged `data/naics_lexicon.json` (the frozen v1 list, a draft no person has
reviewed) + `lexicon.sql` (a separate database, not yet created); `POST /v1/refine/assist` takes `lexicon` (default true)
and returns `reading`; the Live assist tab's refine method has no word-count gate (any text of 2 or more
characters is checked after the pause), shows "Read "saas" as: ..." when a term was read, and keeps today's method on
the three-word rule. Tests: 4 API tests (`test_refine_api.py`), 9 lexicon tests, 3 new frontend tests; browser-tested.

## E14 result: ExioNAICS large (2,000), 2026-10-07

**Run.** `cascade` with the lexicon (expansion on texts of 6 words or fewer, word gate off; ExioNAICS texts are formal
blurbs, so the lexicon changed almost nothing: 26 of 2,000 contain a term) and the assist baseline, both on the same
2,000 distinct descriptions (the first 500 are E13's, and `cascade` reproduced them exactly, 500 of 500). Retries: two
10-second API timeouts that recovered. Errors: assist 11, cascade 4. The harness now records through
`_FastRecordMissing` (the product's record-missing decider rewrites the whole 34 MB transcript on every call).

**Review.** The 676 distinct descriptions where either method coded an industry the dataset does not list were
judged blind by four independent reviewers (about 180 items each, 15 shared), each shown the full Census text for
every proposed code and told to judge the described activity only. Caveats: three of the four reviewers said they
opened the full Census entry for only some codes and judged the rest from titles, index terms and their own NAICS
recall, so this is a lighter review than asked for; their strictness differs (defensible 55% to 68%, arguable
31% to 42%, indefensible 1.1% to 2.7%), though items were shuffled so each method meets all four. On the 15
shared items they agreed on 87% of verdict pairs and chose the same best code on 10 of 15 (medium: 15 of 15).

| Method | Coded | Label match | Off-label: defensible / arguable / **indefensible** | Usable (match + defensible) | Indefensible, % of coded | Errors | Calls |
|---|---|---|---|---|---|---|---|
| assist-v1 | 1,138 | 579 | 378 / 174 / **7** | 957 (47.9% of all 2,000) | 0.62% | 11 | 5.1 |
| cascade | 1,192 | 625 | 391 / 170 / **6** | **1,016 (50.8%)** | 0.50% | 4 | 2.3 |

**Corrections to earlier claims.** (1) On the 500 sample refine made a third as many indefensible codes (2 v 6); at
2,000 they are level (6 v 7, 0.50% v 0.62% of coded), so that earlier gap was largely chance. (2) Pick-lists are
**640 of 2,000 (32%)**, not 18% (E13 and my summary under-reported them): 320 offer a labelled code, 320 do not. Of
those 640, assist produced no code at all for 481 (PARTIAL), coded an off-label industry for 90, coded a
labelled industry for 32, and itself offered a pick-list for 27. Both methods agree on the code in 980 cases and
differ in 26; refine coded 186 that assist did not, and assist coded 132 that refine did not.

**Read.** Refine codes 4.7% more descriptions, with 46 more label matches and 59 more usable codes, the same
very low indefensible rate (about 0.5% of coded), fewer errors, and 2.3 calls against 5.1. It asks a question on
a third of formal company descriptions: mostly where the old assist stopped at a partial level with no code, so the
user gets a code to confirm in place of a dead end, but also in 32 cases where assist had coded a labelled
industry. Nobody has tested whether users answer those questions correctly. The reviewers are models; no
underwriter has checked any of this.

## Audit of the testing (2026-10-07), against PEAS.md, CLAUDE.md and CALIBRATION.md

Prompted by "looking good" having been said without a definition. The project's own bar: PEAS.md's performance table
(right code on an auto-act = the gold NAICS 2022 industry; correct hold; **wrong auto-act: never**; calls per
description) and its rule that blind held-out sets are "written by a separate agent, committed before the method they
test, and run once"; CLAUDE.md's hard requirements; CALIBRATION.md's "thresholds are measured".

| Check | Finding |
|---|---|
| Wrong auto-act "never" | **Not met by either system, and never testable as stated.** ExioNAICS 2,000: 6 of 1,192 refine codes and 7 of 1,138 assist codes judged indefensible (0.50% and 0.62%; 95% CI 0.23%-1.09% and 0.30%-1.26%), plus 14% arguable (170 and 174). Dev 1 wrong, tech-enabled 2. Refine is not worse than assist; neither meets "never". The honest claim is a rate with an interval, not "good". |
| "Right code" means gold | **Only the synthetic sets have gold.** On ExioNAICS "usable" = matches a dataset label (about 30% wrong, JEV-57) or a model reviewer judged it defensible; "arguable" codes (14%) are neither right nor wrong. |
| Blind sets "run once" | **Violated.** held-out: 7 run files; tech-enabled: 7; short/modern: 8. They were also used to choose the final configuration (gate off, expansion limit of 6 words), so none is blind for it. The final configuration has no blind result. None was committed before the method either (nothing is committed). |
| Reviewer quality | **Weaker than specified.** Models, not underwriters; three of four large-run reviewers read the full Census entry for only some codes; strictness ranged 55% to 68% defensible; 10 of 15 shared best codes agreed (15 of 15 on the medium run). |
| Hidden errors | **Not looked for.** Only off-label codes were reviewed. Label-matching codes (could be wrong for a wrong label), holds and pick-lists were not sampled (the E16 audit below samples them on ExioNAICS). |
| Thresholds measured (req. 4) | **Not for refine.** The refine verify gate reuses 0.70 / 0.50 / 0.50 (`PROB_SEARCH_PICK`, `NOUL_SEARCH_FIT_HOLD`, `NOUL_ASSIST_SINGLE`), measured for search and the assist on different candidate sets; the 6-word limit and `REFINE_MIN_WORDS` came from one set of 72. All provisional. |
| Gates fail closed (req. 2) | **One gap, fixed.** A Score answer whose probabilities do not sum to 1 (0.5 or 2.7) was accepted and ended in a pick-list; it is now ERROR (`_level`, tested; 0 of 189 recorded synthetic cases changed). NaN was already ERROR. |
| Closed lists, Hold never an option (req. 1) | Met by construction (tests: pick-list options come from the candidates; hold is code-owned). |
| Nothing invents evidence (req. 3) | Met, with a note: the lexicon appends Census wording to what Jev reads. It comes from a reviewed list, code that ran, and is reported in `reading`; it is a design choice that changes the evidence, and the list is an unreviewed draft. |
| Secrets and third-party data local (req. 6) | Met for the repo: the tracked refine transcript holds 2,344 entries, all synthetic descriptions from committed evals; private data, ExioNAICS text and per-row results are git-ignored. |
| Replay never invents (req. 7) | Met: no transcript was hand-edited; the harness's `_FastRecordMissing` writes the same keys and entries. |
| New behaviour gets a failing test first (req. 5) | **Mostly not followed:** most refine and lexicon tests were written after the code. The fail-closed gap was reproduced by a script before its test and fix. |
| Spec and docs | **Out of date.** PEAS.md, DESIGN.md, `agent.yaml`, SECURITY.md, README and CHANGELOG do not mention refine, the lexicon or the new endpoint. |
| Calls per description | Met and improved: 2.3 v 5.1; but about 44-48k input and 13k output tokens per call. |
| Users | **Not tested.** A third of formal company descriptions end in a pick-list; no broker has answered one. |
| Linear / issue process | Not followed: no JEV issue, no comment, nothing committed. |

**What can be claimed:** on 2,000 external descriptions refine matches or beats assist on every measure (59 more usable
codes, similar indefensible rate, fewer errors, under half the calls). **What cannot:** that either meets the PEAS
"never"; that the final configuration passed a blind test; that the pick-list rate is acceptable.

## E16: the fresh blind set for the final configuration (frozen 2026-10-07, before any run on it)

**Why.** The audit above found that the earlier blind sets were run many times and used to choose the final configuration.
The final configuration is: refine cascade, no word-count gate (`REFINE_MIN_WORDS` = 1), the packaged lexicon with Census wording
appended to descriptions of 6 words or fewer, the verify gate re-measured at 0.70 / 0.50 / 0.50.

**Set.** `backend/evals/refine_v2.json`: 119 synthetic descriptions, from two independent writers (60 each, different themes:
A everyday applicant wording across 19 sectors; B technology and platform wording), neither shown the lexicon, the classifier,
the transcripts or any result. Gold is the Census primary-activity text; where it does not settle the case the case is a hold.
A third agent, shown only the descriptions (no gold), re-derived every answer from the Census text with a program-checked quote.
It agreed with the writers on 119 of 120 cases; the one disagreement (a unisex hair salon the writer meant as a hold, the verifier
read as 812112) was excluded before any run. Mix: 104 gold codes, 15 holds; kinds plain 20, messy 10, short 18, modern 18,
confusable 8, hold 15, sw_vs_service 16, health_prof 8, industrial 6. Caveat: writers and verifier are one model family and
may share blind spots.

**Frozen:** `refine_v2.json` sha256 prefix `1cec8e92ff2d6ea2`; lexicon `f0656ef3b12b6807`. It was **not committed** (nothing is, pending the
branch decision), so this hash is the freeze. **Rule: run once** with the final configuration and the assist baseline, report
as it comes out, change nothing after seeing it. Scoring is `experiments/refine/score_v2.py`, fixed before the run: a code on a
hold case is wrong; a hold on a gold case is a miss; a pick-list that offers the gold code is reported separately.

## E16 audit results: what the earlier reviews never looked at (2026-10-07)

**Method.** From the refine (cascade) results on the 2,000 ExioNAICS descriptions, a random sample of 280 decisions the earlier
reviews did not cover (`make_audit_pack.py`): A) 120 codes that match a dataset label; B) 80 holds (PARTIAL / NEEDS_INFO); C) 80
pick-lists (40 whose options include a labelled code, 40 whose do not). Three independent reviewers (103 to 104 items each, 15
shared), each shown the Census text of every code and required to give an exact Census quote for their best code; a program
checked every quote (`check_audit.py`). **All 310 quotes are real Census text** (254 from the best code's own entry; 56 real
phrases from another named code, mostly the proposed one), so unlike the earlier reviews this one demonstrably read the text.
Agreement on the shared items: A 4 of 5, B 2 of 3, C 6 of 7 (too few to trust as a rate).

| Type | Sample | Result |
|---|---|---|
| A: codes that match a dataset label | 120 | **108 defensible, 12 arguable, 0 indefensible.** The label match did not hide wrong codes; with 0 of 120, the rate is below about 2.5% (95%). |
| B: holds | 80 | 51 the text does not settle a code (the hold was right), **15 maybe, 14 yes** (a code was supported: 17.5% of sampled holds, about 1.4% of all descriptions, missed). |
| C: pick-lists | 80 | Options contain the best industry in **63 (79%)**: 37 of 40 when an option was a labelled code, 26 of 40 when none was. **19 (24%)** had one clearly best option, so no question was needed; 61 (76%) were a real question. |

**Read.** The headline accuracy of the codes refine does give is supported from a second angle: among label-matching codes there
was no indefensible one in the sample. The weaknesses are on the other side of the gate: about 1 in 6 holds could have been a
code, about 1 in 5 pick-lists omit the best industry, and about 1 in 4 asks a question that did not need asking. None of this
is an underwriter's judgement.

## E16 blind run: BLOCKED on TypeSafe credits (2026-10-07)

The one-time run of the frozen set (`refine_v2.json`, sha256 prefix `1cec8e92ff2d6ea2`) was started and stopped after about one
case on HTTP 402 ("no available TypeSafe API credits") before any result file was written; nothing from it has been looked
at. Completed calls are recorded, so resuming costs only the remainder. It must still be run once, as it comes out.

**The 402s, diagnosed (2026-10-07).** Hypothesis from the user: API limits. Probe: 8 live calls, 2 seconds apart, one process, nothing else
running: 8 of 8 returned HTTP 402 "no available credits", with no rate-limit, retry or balance headers (request ids kept for
support). A rate limit would let some calls through and say when to retry. Spend: distinct recorded live calls 12,354 (95M input,
22M output tokens) when the user read "$3 left of $5", 23,534 (185M, 45M) now; about 11,200 more calls, the same again as the
first roughly $2. Conclusion: consistent with the balance running out, not with a rate limit. Open: whether the balance the API
counts matches the console, and whether the key belongs to the console's organization. Remaining work that needs the API is small:
the frozen set is 119 cases, about 900 calls for refine plus the assist baseline.
