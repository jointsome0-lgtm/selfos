# selfos — Agent Operating Guide

This is the integration level of selfos. The three subsystems are independent repositories, expected to be checked out as siblings of this repo:

- `../tick-like` — activity ledger (FastAPI + SQLite, v0 feature-complete)
- `../atlas` — knowledge-state graph (SDD stage, no code yet)
- `../exp2res` — evidence-backed self-assessment (SDD stage, no code yet)

## Task routing

- A task that concerns a single subsystem belongs in that subsystem's repository: work in its directory, follow its own `AGENTS.md`, file issues and PRs there.
- This repository owns only cross-system concerns: the map, contracts between subsystems, shared conventions, integration/orchestration, and umbrella-level docs.

## Current stage

No submodules and no orchestration yet — atlas and exp2res are specs under active refinement. When they gain code, this repo will pin subsystem versions via git submodules and provide the run-everything entry point.

## Public data boundary

This repository is public. Same policy as tick-like: never commit real personal data (ledger exports, notes, evidence files, screenshots with real entries), credentials, `.env`, or local agent/tool state. Invented demo data only.
