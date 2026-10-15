# Agent notes: backend

The rules for this whole repo, including this package, are in the repo-root
[`AGENTS.md`](../AGENTS.md) (and [`CLAUDE.md`](../CLAUDE.md) for Claude Code specifics). Read
those first.

Package-specific reminders:

- Questions and thresholds: `src/jev_naics/questions.py` only, mirrored in
  [`../CALIBRATION.md`](../CALIBRATION.md).
- `src/jev_naics/_jev/` is copied from `jev_peas` (`jev-common`/`jev-runtime`), not imported —
  each file's header names the exact commit and source path. Keep a change in sync by hand and
  note it in [`../CHANGELOG.md`](../CHANGELOG.md); do not reintroduce a dependency on
  `jev-common` or `jev-runtime`.
- `TYPESAFE_API_KEY` belongs in the repo-root `.env` only (`jev_naics.env.load_typesafe_api_key`
  reads the repo root `.env` first, then falls back to `backend/.env` for any key the first file
  did not set).
