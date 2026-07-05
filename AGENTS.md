# selfos — Agent Operating Guide

This is the integration level of selfos. The three subsystems are independent repositories, expected to be checked out as siblings of this repo:

- `../tick-like` — activity ledger (FastAPI + SQLite, v0 feature-complete)
- `../atlas` — knowledge-state graph (SDD stage, no code yet)
- `../exp2res` — evidence-backed self-assessment (SDD stage, no code yet)

## Shared agent skills

Cross-repo Claude Code skills live in the sibling `selfos-skills` repository (`../selfos-skills`; GitHub: `jointsome0-lgtm/selfos-skills`) — a Claude Code plugin marketplace. Install once at user scope: `/plugin marketplace add jointsome0-lgtm/selfos-skills` (or the local checkout path), then `/plugin install sdd@selfos` (the marketplace inside is named `selfos`). The `sdd` plugin ships `grill-sdd` (grill a spec section by section; outcomes land as SDD edits + issues). Repo-specific skills stay in each repo's `.claude/skills/`; if a needed skill is missing in a session, ask the user to install/update the plugin.

## Task routing

- A task that concerns a single subsystem belongs in that subsystem's repository: work in its directory, follow its own `AGENTS.md`, file issues and PRs there.
- This repository owns only cross-system concerns: the map, contracts between subsystems, shared conventions, integration/orchestration, and umbrella-level docs.

## Integration model

Decided 2026-07-05. The README states the public shape; the working rules for agents:

- **Mutual blindness.** No subsystem knows the others exist. Each defines only generic boundaries at its own edge: exports (tick-like JSONL ledger replay, atlas state snapshot) and source-agnostic imports (atlas encounter/plan intake, exp2res evidence intake with per-source strength).
- **Adapters live here.** Format mapping (e.g. tick-like `timestamp` vs exp2res `occurred_at`/`recorded_at`), source registries, versioning against tick-like `payload_version` — all in this repo, with invented fixtures only (public-data boundary).
- **Adapters are deterministic.** They map formats and route by event type or explicit user-set tags — never interpret free text. Interpretation is the receiving system's own agent stage, inside its invariants: exp2res's extract/verify pipeline (its §13, §15, §16), atlas's hybrid importer (its §21) behind §14's review gates. Corollary for the diary: every entry reaches exp2res as a raw log, but only entries the user explicitly tagged for atlas are shipped to its intake — untagged free text never enters another system (or an agent context) by adapter initiative.
- **Domain routing.** Knowledge flows through atlas: tick-like Learn → adapter → atlas → adapter → exp2res. Activity and verbal material flow directly: tick-like diary/notes/focus time → adapter → exp2res. Atlas's outbound "assessment" is a knowledge-state snapshot on its own scales — never a mastery score (atlas §4/§31.1); exp2res ingests it as evidence, never as ready-made claims (exp2res §25.5).
- **UI composition.** tick-like is the main shell; the atlas viewer and exp2res views stay separate apps with their own design, embedded/linked by URL from orchestrator config. Coupling is configuration, not code.
- **Agent runtime.** Specialized pipeline agents (exp2res extractors/verifiers per its §15, the Learn tutor, future atlas roles per its §17–§18) run as Codex sessions / `codex exec` under the user's ChatGPT OAuth subscription — the economical path, and the one the subscription terms sanction (Codex tooling, not a hand-rolled API client). Starting an agent is always an explicit user act with a user-chosen provider (atlas §24's rule, applied ecosystem-wide).
- **Cloud-context data boundary.** Atlas §24's ignore-paths principle extends to all subsystem data: what may enter a cloud agent's context is an explicit per-flow decision. Diary content is the most sensitive and stays out of agent context by default. With the stage-one runtime fully cloud (ChatGPT subscription), "out by default" bars *ambient* inclusion — tutors, dev agents, adapter initiative — not pipeline stages the user runs explicitly (e.g. exp2res `extract` over diary raw logs is the sanctioned, user-initiated path).
- **Two development tracks.** Track A: generated baselines, end-to-end, to validate the specs — agents write the code. Track B: the learning lane — user-written code, agents act as reviewers only; each repo declares its lane (precedent: tick-like `app/agent/`). Never mix the tracks in one change.

## Current stage

No submodules and no orchestration yet — atlas and exp2res are specs under active refinement. When they gain code, this repo will pin subsystem versions via git submodules, provide the run-everything entry point, and grow the adapters described above.

## Public data boundary

This repository is public. Same policy as tick-like: never commit real personal data (ledger exports, notes, evidence files, screenshots with real entries), credentials, `.env`, or local agent/tool state. Invented demo data only.
