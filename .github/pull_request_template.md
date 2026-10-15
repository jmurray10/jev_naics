**What changed and why**

**Evidence** (quote the output, do not paraphrase it)

- [ ] `cd backend && JEV_DECIDER=replay python -m pytest -q` passes
- [ ] `python -m pytest -q frontend/tests` passes (from the repo root)
- [ ] New behaviour has a test that failed before the change
- [ ] No threshold changed without a re-measurement recorded in CALIBRATION.md
- [ ] No transcript hand-edited; any new recording names its model and date here
- [ ] Only synthetic descriptions and public Census text added to evals or transcripts; no key, no `.env`
- [ ] `openapi.json` regenerated if the API changed
