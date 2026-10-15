"""Human-reviewed TypeSafe questions and gate thresholds.

Full NAICS 2022 (the tree walk), the search-then-pick classifier, their hybrid, and the
spelling correction used by search. Thresholds are calibrated on the model pinned in
_jev/providers.yaml; each block below says how and when.
"""

# ---- Full NAICS 2022 (JEV-48): hierarchical beam search over the Census tree ------------
# The path score is the geometric mean of the chosen edge probabilities along the path (the
# TypeSafe hierarchical-classification pattern); PROB_FULL_PATH gates the final pick among the
# finalists. Same lines as the closed agent's calibrated ones, checked 2026-09-29 on jev-1.13.0
# over evals/full_naics.json (33) and full_naics_heldout.json (6), final recorded run: 36 right,
# 1 correct hold, 2 held (vet, car wash), 0 wrong. Wrong final picks reached p 0.63-0.67, so the
# 0.70 bar alone would not always stop them; the fit Noul does the work (wrong-industry fits
# <= 0.16; the closest correct fit, tattoo, was 0.49-0.57 across three recordings, so it sits
# on the 0.50 bar). Not yet swept by tools/calibrate.py, which covers only the closed agent.
FULL_BEAM_WIDTH = 3
PROB_FULL_PATH = 0.7
NOUL_FULL_UNDERSPECIFIED = 0.7
# Hold when Jev does not think the best industry describes the business at all.
NOUL_FULL_FIT_HOLD = 0.5

# ---- Search, then Jev picks (JEV-50) -------------------------------------------------------------
# Postgres full-text search (db.search, IDF-weighted) returns SEARCH_K candidates; Jev picks one
# of only those in one call, with a fit Noul per candidate.
# SEARCH_K: measured 2026-09-29 over the evals with a gold code, no model call: the right
# industry was in the top 10 for 31 of 32 dev and 6 of 6 held-out (top 5: 28 of 32; top 50: 32).
SEARCH_K = 10
# The three gates are the tree walk's lines above (same questions: the pick's probability, the fit
# Noul, the vagueness Noul), re-checked 2026-09-29 on jev-1.13.0 over the search eval (33 dev + 6
# held-out, search_eval.py): every right pick had p >= 0.96 and fit 0.75-0.98 (lowest: moving
# company); the one miss (the right industry was not in the results) leaned at p 0.99 with fit
# 0.07, so the fit Noul, not the probability, held it. Vagueness: the vague case 0.92, the rest
# <= 0.35. That held-out set informed SEARCH_K and the ranking (gate audit), so a fresh one with
# close competitors (evals/search_heldout.json, committed before its run) was measured once: 7 of
# 8 right, 0 wrong; the retail bakery split with commercial bakeries at p 0.49 and this bar held
# it. Right picks there: p >= 0.97, fit >= 0.92. A lone search result always holds (unmeasured).
# Not yet swept by tools/calibrate.py.
# The hybrid (JEV-53, hybrid.py) adds no line of its own: its second pick, over search's results plus
# the tree walk's new finalists (up to SEARCH_K + FULL_BEAM_WIDTH), is search's pick with these gates.
# In the dev spike (all 51 dev cases run over the union of 10-12 candidates, 2026-09-29, jev-1.13.0)
# right picks had p 0.88-1.00 and fit 0.65-0.98, none wrong. In the recorded evals the fallback asked the
# union pick once (insurtech, 13 candidates: 524210 at p 1.00, fit 0.85); the retail bakery fell back but
# the walk added nothing. The fit Noul on tree-added candidates rests on that one answer, so the union
# pick is lightly measured. Only an industry the walk added can be acted on (hybrid.py).
PROB_SEARCH_PICK = PROB_FULL_PATH
NOUL_SEARCH_FIT_HOLD = NOUL_FULL_FIT_HOLD
NOUL_SEARCH_UNDERSPECIFIED = NOUL_FULL_UNDERSPECIFIED

# ---- Spelling (JEV-52): code corrects typed words before search; no model call ---------------------
# A word that is not in the Census text is corrected to the Census word with the fewest edits (at most
# SPELL_MAX_EDITS, Levenshtein), then the highest trigram similarity (at least SPELL_MIN_SIMILARITY).
# The corrected word counts at SPELL_WEIGHT of a typed word's weight, and the typed words stay in.
# Measured 2026-09-29 on the Census vocabulary (10,313 words) with the JEV-52 dev typos: real
# typos scored 0.44-0.75 at one edit ("clinc" -> clinic 0.44, "comercial" -> commercial 0.75);
# non-typos the Census lacks: "saas" 0.33, "doordash" -> door (4 edits), "kubernetes" 0.21. The
# known false friend, "meditation" -> mediation (1 edit, 0.62), is why a correction is down-weighted
# rather than trusted.
# Weights swept 2026-09-29 (gate audit) on dev only, by search recall of the gold (no model call):
# evals/full_naics.json + messy_dev.json, 50 cases with a gold code. Top 10 / mean reciprocal rank:
#   spell 0.25 / 0.5 / 1.0 at term 0.25:  50 / 0.760,  50 / 0.773,  50 / 0.793
#   term  0 / 0.25 / 0.5 / 1.0 at spell 0.5:  49 / 0.751,  50 / 0.773,  50 / 0.774,  50 / 0.743
# TERM_WEIGHT 1.0 pushed the incidental-term cases down (ranks 3, 3, 5: an app-booked plumber, a
# farm owner's podcast); 0.25 keeps all six at rank 1, and 0 loses the SaaS case. SPELL_WEIGHT 1.0
# scores a little higher on dev (MRR +0.02, same top 10), but its false friends ("seeing" -> seeding,
# "taco" -> tack) are rare in dev: 0.5 is kept as a margin against them, at that measured cost.
SPELL_MIN_SIMILARITY = 0.4
SPELL_MAX_EDITS = 2
SPELL_WEIGHT = 0.5
# A listed modern term (data/naics_terms.json, JEV-52) adds its Census wording at TERM_WEIGHT of a
# typed word: enough to find the industry the term names, not so much that an incidental mention
# ("we also host a podcast") outranks the business. Swept with SPELL_WEIGHT above.
TERM_WEIGHT = 0.25
# Which words are spelling candidates: typed words of SPELL_MIN_WORD to SPELL_MAX_WORD letters (a
# 3-letter word has too few trigrams to correct safely; Postgres levenshtein refuses over 255, and no
# typo is 40 letters long); the Census vocabulary keeps words of VOCAB_MIN_WORD letters or more.
SPELL_MIN_WORD = 4
SPELL_MAX_WORD = 40
VOCAB_MIN_WORD = 3

# ---- Real-time assist (assist.py): completeness checks as a broker types ---------------------------
# Asked together with the tree walk's first level (the sector Choice and its vagueness Noul) in one call.
# A check under its bar decides the status and the one tooltip shown (assist.py); none is a pick.
# Measured 2026-10-05 on jev-1.13.0 over evals/assist_dev.json (30 synthetic cases, recorded in
# transcripts/naics-assist.json): operations text, revenue/fee/funding-only cases 0.04-0.22 vs every other
# case 0.74-0.99; specific activity, generic one-word cases 0.03-0.17 vs clear cases 0.72-0.98 (mixed
# 0.64-0.79); one main activity, mixed cases 0.07-0.08 vs clear 0.69-0.97 (the three-part IT advisory case
# 0.47, which then fell to its pick-list anyway); customers stated 0.79-0.98 vs not stated 0.04-0.37, but
# "for shippers" read 0.34, so the customers check only picks a tooltip, never a status. 0.5 sits in each
# gap. Of the 11 clear cases 8 were coded, all right; car wash and vet held in the walk (PARTIAL), and one
# SaaS case failed closed on a malformed level answer (ERROR). Not yet checked on a held-out set.
NOUL_ASSIST_OPERATIONS = 0.5
NOUL_ASSIST_ACTIVITY = 0.5
NOUL_ASSIST_CUSTOMERS = 0.5
NOUL_ASSIST_SINGLE = 0.5
ASSIST_CONFIG_VERSION = "2026-10-05"
ASSIST_QUESTIONS = {
    "assist_operations_text": (
        "Does `ops_description` describe what the business does? Answer false if it only names a revenue amount, "
        "a fee type, a funding detail, or how the business is paid, or is unrelated text.",
        "Yes: it describes what the business does.",
        "No: it is only revenue, fees, funding, payment, or unrelated text."),
    "assist_states_activity": (
        "Does `ops_description` say what the business does or sells specifically enough to identify its industry? "
        "'Sells subscription software that schedules dental appointments' is specific. 'Software', 'services', "
        "'sales', 'consulting', or 'e-commerce' alone are not.",
        "Yes: the activity is specific enough to identify the industry.",
        "No: the activity is too general to identify the industry."),
    "assist_states_customers": (
        "Does `ops_description` say who the business's customers are, for example consumers, small businesses, "
        "hospitals, law firms, or government agencies?",
        "Yes: it says who the customers are.",
        "No: it does not say who the customers are."),
    "assist_single_activity": (
        "Does `ops_description` describe one main business activity, or say which activity is the main one? "
        "Answer false if it lists several unrelated activities without saying which is the main one.",
        "Yes: one main activity is clear.",
        "No: several unrelated activities and no main one."),
}
ASSIST_DELIVERY = {
    "own_software_product": "Licenses or subscribes customers to software or an app the business owns",
    "custom_work_per_client": "Builds, designs, or advises to each client's specification",
    "ongoing_managed_service": "Operates or monitors systems or processes for clients on an ongoing basis",
    "makes_physical_goods": "Manufactures or produces physical products",
    "resells_to_businesses": "Buys and resells goods to other businesses",
    "sells_to_consumers": "Sells goods directly to consumers",
    "in_person_service": "Performs a service in person, for example repairs, cleaning, care, or food service",
    "not_stated": "The description does not say how it is delivered",
}

# ---- Refine (refine.py): a wide pick over every industry, then a verify step -----------------------
# PROVISIONAL until the spike on the dev sets is recorded (CALIBRATION.md).
# REFINE_CHOICE_MAX: the API allows 255 options per Choice; each wide Choice holds whole sector groups.
REFINE_CHOICE_MAX = 240
# The short profile of each industry in the wide pick: its title and up to REFINE_SHORT_TERMS Census index
# entries, cut at REFINE_SHORT_CHARS. Every wide call carries all 1,012, so this sets its input tokens.
REFINE_SHORT_TERMS = 3
REFINE_SHORT_CHARS = 120
# How many of the wide pick's best industries the verify step judges.
REFINE_TOP_K = 4
# scoreb: how many of each block's own best industries the verify step judges (5 blocks, so up to 10).
REFINE_PER_BLOCK = 2
# A wide block that is malformed only holds the case when its groups carry at least this much of the group
# pick (provisional: set from the one E9 dev error, a mining/utilities block on an insurance description).
REFINE_BLOCK_MASS_MIN = 0.05
# cascadeb (E12): the Census boundary text shown for each verified candidate, at most this many items of each kind
# (what the industry is not, what belongs to it), each cut to this many characters. PROVISIONAL: sized so a
# candidate profile stays under about 1,000 characters; not tuned.
REFINE_BOUNDARY_ITEMS = 4
REFINE_BOUNDARY_CHARS = 120
# The short-text gate of /v1/refine/assist (E15). 1: only blank text is too short. The three-word rule stopped all
# 34 single- and two-word cases on the blind short/modern set (plumber, dentist, winery, SaaS), which the assist
# checks (operations, specific activity) handle: they held 11 of 12 must-hold cases. PROVISIONAL.
REFINE_MIN_WORDS = 1
# A recognised lexicon term adds its Census wording for Jev only to a description of at most this many words; in a
# longer one the user's own words are enough, and the added wording once made a soybean farm that mentions a
# podcast read as two activities (E15). Recognition is never limited. PROVISIONAL, a round number not tuned.
REFINE_EXPAND_MAX_WORDS = 6
