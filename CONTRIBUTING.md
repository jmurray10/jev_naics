# Contributing

jev_naics is MIT-licensed and contributions are welcome. Its value is that every number it reports
ships with the recording and the command that reproduce it, so the most useful thing you can send is
evidence that one of them does not hold.

## Worth sending

- **A wrong code.** A description of operations where the classifier auto-acts on an industry the
  Census text does not support. This is the failure the whole design exists to prevent, so it is the
  most serious bug here. Include the description (synthetic or public, never a real client's), the
  method, the code it gave, and the code you expected.
- **A hold that should have acted.** Less serious than a wrong code, but worth an issue, especially
  with a pattern behind it (a kind of business, a kind of wording).
- **A number that does not reproduce.** Every figure in README.md and CALIBRATION.md comes from a
  replayable run. If `python -m jev_naics --hybrid-eval` (or `--search-eval`) prints something
  different, say which figure and what you got.
- **A modern term search misses.** The reviewed list in `backend/src/jev_naics/data/naics_terms.json`
  maps words the Census text never uses to Census wording, never to a code. Propose an entry with
  the descriptions that motivate it.
- **A different model.** If a newer Jev version, or another decider, answers the same recorded
  questions materially differently, that is a finding about the thresholds.
- **Overclaiming in the prose.** If a sentence says more than its evidence supports, that is a bug.

## Probably declined

- **A free-text or "closest match" path.** Jev only ever picks from a closed list built in code, and
  none-of-the-above is a hold that code decides. A change that offers the model a code it was not
  given, or a "hold" option, breaks the design.
- **Hand-edited transcripts or canned answers.** Offline runs replay answers recorded on the real
  model; a missing recording raises. Nothing invents a third mode.
- **A threshold change without a re-measurement.** CALIBRATION.md has the rule.

## The rules a change has to keep

The full list is in [AGENTS.md](AGENTS.md) (written for coding agents, and it applies to people too).
In short:

1. Every Choice is over a closed set built in code; hold is code-owned and never a Choice option.
2. Gates fail closed and gate on the picked industry's probability and fit, never on confidence. Use
   `jev_naics._jev.gate` rather than re-implementing the checks.
3. Nothing invents evidence or scores: every probability and `reason` comes from the model's actual
   answer or from code that ran.
4. Thresholds live in `backend/src/jev_naics/questions.py` with a comment saying how they were
   measured, on the model pinned in `backend/src/jev_naics/_jev/providers.yaml`.
5. Evals are strict: a hold passes only when the fixture expects one. New behaviour gets a failing
   test first.
6. Evals and transcripts hold only synthetic descriptions and public Census text. No secrets, no
   client data, no third-party data.

## Before opening a pull request

With the `dev` extra installed (`pip install -e "backend[api,dev]"` from the repo root):

```
cd backend && JEV_DECIDER=replay python -m pytest -q   # offline; the Postgres tests skip without a *_test database
cd .. && python -m pytest -q frontend/tests            # the page, from the repo root
```

If you changed the API, regenerate the committed OpenAPI:
`cd backend && python -m jev_naics --openapi > openapi.json` (the committed file is
`backend/openapi.json`). If you recorded new transcripts, say which model and
date in the pull request; recording sends only the synthetic eval text to TypeSafe.
