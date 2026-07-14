# Architecture

Scope: the public-engine/private-instance topology, adapter boundary,
derived outputs, and location of the deletion guarantee. Adopted
2026-07-14
([selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3)
resolution).

```text
PUBLIC ENGINE REPOS                          ┃ PRIVATE INSTANCE ROOTS
(code, specs, docs, invented fixtures)       ┃ (real data; configured paths)
                                             ┃
┌──────────────────────────────────────┐     ┃
│ selfos                               │     ┃
│ ├─ deterministic adapter code ───────┼─────╂──► reads/writes explicit
│ └─ docs/deletion.md (guarantee)      │     ┃    private-instance paths only
├──────────────────────────────────────┤     ┃
│ atlas engine                         │     ┃  ┌──────────────────────────┐
├──────────────────────────────────────┤     ┃  │ Atlas instance repo      │
│ ephemeris engine                     │     ┃  │ atlas/, plans/, intake/, │
├──────────────────────────────────────┤     ┃  │ state/                   │
│ exp2res engine                       │     ┃  │ graph/ builds, exported  │
└──────────────────────────────────────┘     ┃  │ snapshots (untracked)    │
                                             ┃  └──────────────────────────┘
                                             ┃  ┌──────────────────────────┐
                                             ┃  │ ephemeris data root      │
                                             ┃  │ SQLite ledger            │
                                             ┃  └──────────────────────────┘
                                             ┃  ┌──────────────────────────┐
                                             ┃  │ exp2res workspace        │
                                             ┃  │ evidence and run state   │
                                             ┃  └──────────────────────────┘
                                             ┃
                                             ┃  PRIVATE DATA FLOW
                                             ┃  ephemeris ledger
                                             ┃    ├─ adapter ─► Atlas intake/
                                             ┃    └─ adapter ─► exp2res evidence
                                             ┃  Atlas exported snapshot
                                             ┃    └─ adapter ─► exp2res evidence
                                             ┃  (adapter code lives in selfos)
```

## Two roots

Public engine repositories hold code, specs, docs, and invented demo
fixtures. Private instances hold real content, state, and derived
outputs. The ownership table, engine pin, and path-discovery contract
live in [Private instance](instance.md); this page does not restate
them.

## Adapters

Adapters are deterministic engine-side code. They operate only on
explicitly configured private-instance paths and never infer a public
checkout as a data root. They keep no persistent content copies of
shipped entries, the standing rule from
[selfos#13](https://github.com/jointsome0-lgtm/selfos/issues/13).

## Derived outputs

Atlas `graph/` builds and exported snapshots are instance-side and
untracked: recovery is a rebuild, not a checkout. See
[Private instance](instance.md).

## Deletion

Logical deletion removes content from current state while private
history may retain it. Purge removes content from current state,
history, and every registered copy. The guarantee, residual inventory,
and runbook live only in the canonical [Deletion](deletion.md) page.

## Demo data

Every public demo fixture is invented and authored by
[Vera Example](persona.md). The literal name is its provenance marker;
the gates that enforce it are defined in [Public hygiene](hygiene.md).

## Real capture

Real capture stays blocked until a private destination is configured.
Enforcement belongs to the capture tools.
