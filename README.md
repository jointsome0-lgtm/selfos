# selfos

Selfos is the integration repository for three independent local-first systems — ephemeris (activity), atlas (knowledge), and exp2res (experience). Each subsystem owns its own data and semantics; selfos owns what crosses their boundaries: contracts, deterministic adapters, compatible-version pins, shared configuration, and UI composition. Subsystem-owned private instances remain canonical.

| Repository | Role | Readiness |
|---|---|---|
| selfos | Cross-system map, contracts, and integration | Implementation-ready for named integration slices |
| [ephemeris](https://github.com/jointsome0-lgtm/ephemeris) | What do I do? — activity ledger: routines, tasks, focus | Implementation repository; v0 feature-complete |
| [atlas](https://github.com/jointsome0-lgtm/atlas) | What do I know? — graph-first knowledge-state system | Partial freeze; knowledge vertical active, Body Atlas frozen |
| [exp2res](https://github.com/jointsome0-lgtm/exp2res) | What have I lived? — evidence-backed self-assessment | Implementation-ready with controlled amendments; SDD v0.3 binding |
| [tollgate](https://github.com/jointsome0-lgtm/tollgate) | Shared local LLM gateway and spend dashboard | Refinement / Track B learning lane |

The concise [readiness matrix](docs/readiness.md) is canonical for what may proceed, its issue gates, how implementation friction is handled, and what changes each state next.

This is the integration repository: it owns the cross-system map, shared conventions, version selection, and named integration slices. `pins.toml` records exact subsystem commit SHAs; `python scripts/sync.py --status` reports drift in the subsystem sibling checkouts, and `python scripts/sync.py` checks out locally available pins while refusing dirty or unsafe checkouts. A generic run-everything orchestrator remains deferred until at least two subsystems are genuinely runnable with known startup and configuration contracts.

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
