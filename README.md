# selfos

Personal state platform: three independent local-first systems, one honest picture of self.

| System | Question it answers | Status |
|---|---|---|
| [ephemeris](https://github.com/jointsome0-lgtm/ephemeris) | What do I do? — activity ledger: routines, tasks, focus | v0 feature-complete |
| [atlas](https://github.com/jointsome0-lgtm/atlas) | What do I know? — graph-first knowledge-state system | SDD draft |
| [exp2res](https://github.com/jointsome0-lgtm/exp2res) | What have I lived? — evidence-backed self-assessment | SDD draft |

Shared infrastructure: [tollgate](https://github.com/jointsome0-lgtm/tollgate) — local LLM gateway (Go) with a TypeScript spend dashboard: one OpenAI-compatible endpoint metering token spend and routing across free daily quotas and cheap providers. Learning-lane repo (owner-built core); SDD draft.

This is the integration repository: the cross-system map, shared conventions, and — once the subsystems mature — pinned versions (git submodules) and orchestration to run everything together.

## Integration model

The subsystems stay mutually blind: none of them knows the others exist. Each defines only generic boundaries at its own edge (exports and source-agnostic imports); everything cross-system lives here, in the integration layer.

```text
ephemeris JSONL ──adapter──► atlas    (knowledge domain: encounters, artifacts, plans)
atlas state     ──adapter──► exp2res  (knowledge-state snapshot, ingested as evidence)
ephemeris JSONL ──adapter──► exp2res  (activity domain: diary, notes, focus/time)
GitHub ─────────────────────► exp2res  (imported by exp2res itself)
```

Routing is by domain, not by source: knowledge flows through atlas; activity and words flow straight to exp2res. One event may split — a learning session's knowledge aspect goes to atlas, its time aspect to exp2res.

The UI composes the same way: ephemeris is the main shell, while the atlas viewer and exp2res views remain independent apps with their own design, embedded by URL from orchestrator config. Coupling is configuration, not code.

Each subsystem is developed in its own repository with its own issues, PRs and agent guide.
