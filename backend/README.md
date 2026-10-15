# backend: the jev_naics library and API

This package is documented at the repo root, not here — see:

- [../README.md](../README.md): what it does, how it classifies, how to run it
- [../DESIGN.md](../DESIGN.md): architecture, the three methods, gates, failure modes
- [../PEAS.md](../PEAS.md): performance, environment, actuators, sensors
- [../CALIBRATION.md](../CALIBRATION.md): every threshold in `src/jev_naics/questions.py`, its
  value, and how it was measured
- [../DATA.md](../DATA.md): the Census source files and what is derived from them
- [../db/README.md](../db/README.md): building the Postgres search database
- [../SECURITY.md](../SECURITY.md): keys, service auth (`NAICS_API_KEYS`, Bearer), what evals and transcripts may hold, the leak scan
- [../CHANGELOG.md](../CHANGELOG.md): what moved here from `jev_peas` and why
- [../AGENTS.md](../AGENTS.md) / [../CLAUDE.md](../CLAUDE.md): the rules for working in this repo

Inside this package: `src/jev_naics/` (the library and the FastAPI app under `api/`),
`src/jev_naics/_jev/` (the few jev_peas helpers this package needs, copied in — each file's
header names its source), `tests/`, `evals/`, `transcripts/`, `agents/naics/agent.yaml` (the PEAS
spec).
