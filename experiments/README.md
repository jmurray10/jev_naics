# Experiments

Every test of a method or a change, with what was asked, how it was measured, and what came out. Version
controlled, so the history of a decision stays with the code that made it.

## Layout

```
experiments/
  README.md                 this file: conventions and the index
  EXPERIMENT.md             the running log: every experiment, its method, its result, and what it decided
  <name>/                   one folder per line of work (refine/, ...)
    spike.py                the harness, if any (no data inside)
    runs/<RUN-ID>.json      a run's summary: synthetic descriptions only
```

## Labels

- **Experiment ID:** `E<n>` (E1, E2, …), in the order run. An experiment is one question.
- **Run ID:** `<E-id>-<variant>-<set>-<yyyymmdd>`, for example `E9-score-dev-20261006`. One run is one
  variant on one set, on the model pinned in `backend/src/jev_naics/_jev/providers.yaml`.
- **Variant:** a short lower-case name (`wide`, `verify`, `score`, `union`, `assist-v1`).
- **Set:** `dev` (the synthetic dev evals), `refine_<name>` (a synthetic blind set, run once), or
  `exio-<size>` (the public ExioNAICS dataset). Runs on your own data stay in the git-ignored `test_data/`.

## Rules (from AGENTS.md and SECURITY.md)

- **Synthetic and public only in this folder.** Your own application data, per-case results on it, and
  reviews of it stay in the git-ignored `test_data/` (README.md, "Bring your own data"). The original
  project's private samples are not published.
- **Recorded, not paraphrased.** A live result is recorded (`JEV_DECIDER=record-missing`) to a transcript
  under `backend/transcripts/`, or quoted from a run file here.
- **Held-out sets run once.** A blind set is never tuned on; its run is reported as it came out.
- **No threshold changes from an experiment alone.** A number moves into `questions.py` only with its
  measurement written into `CALIBRATION.md`.
