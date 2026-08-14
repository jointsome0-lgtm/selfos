# Ephemeris retro → Exp2Res §19.1 adapter

`scripts/retro_adapter.py` reads an ephemeris JSONL audit export, selects
the retro-entry slice, and emits the §19.1 activity-domain JSONL payload
that `exp2res import ephemeris <file>` accepts
([selfos#37](https://github.com/jointsome0-lgtm/selfos/issues/37), under
the [#25](https://github.com/jointsome0-lgtm/selfos/issues/25)
integration architecture). Source contract: ephemeris `docs/retro-spec.md`
(sec33). Target contract: exp2res `spec/19-integration-contracts.md`
§19.1 body, §19.4 envelope semantics.

```
python scripts/retro_adapter.py <export.jsonl> --timezone <IANA> -o <out.jsonl>
exp2res import ephemeris <out.jsonl>
```

Delivery stays two explicit commands; the adapter never touches an
exp2res database. `--timezone` is the receiving exp2res workspace's IANA
zone: ephemeris ships the owner-typed period verbatim and exp2res
re-resolves it in workspace time (retro-spec sec2), so the zone is an
explicit argument, never guessed. The run report (stdout, one JSON
object) carries counts and per-record reason codes only — never entry
text.

The output file is the delivery payload and the only content copy the
adapter produces (deletion-contract rule, #25). Both ends of the handoff
are explicit: the adapter refuses an `-o` path that resolves inside a
public engine checkout (adapters operate only on private instance paths,
[instance.md](instance.md)), and once the import report is confirmed the
owner deletes the payload file — it is a transient handoff artifact, not
a second store, and nothing else retains shipped entry text outside the
receiving workspace.

## Selection

Export lines are grouped by `payload.retro_uuid`; the latest
`retro_entry_*` event in file order wins (the export is ordered by ledger
append order); an entry whose latest snapshot has `archived_at` set is
excluded (`skipped: archived`). An identity carrying any event with an
unsupported `payload_version` is rejected whole — its latest state is
unreadable, so an older snapshot never stands in for it. Any other event
type is ignored and counted — this event-type filter is the seam where diary events
(ephemeris#2) would be admitted later, and nothing else is built for
them. A knowledge-state payload is never this slice: it is not a
`retro_entry_*` event, so it never passes the filter, and no
Atlas-shaped record is ever emitted.

## Mapping table

| Source (retro snapshot) | Target (§19.1) | Rule |
|---|---|---|
| — | `source` | literal `ephemeris` |
| `retro_uuid` | `record_id` | `ephemeris:retro:<retro_uuid>` |
| — | `domain` | literal `activity` |
| `period_raw` + `precision` + `confidence` | `occurred` | resolved by exp2res's own `services.time_input.parse_occurred` in `--timezone`; grammar equality by construction. An unparsable period rejects the record (`period_unparsable:<code>`), never approximates. |
| `project` | `project` | verbatim; §19.1 requires it non-empty, so an entry without one is `skipped: no_project` — the adapter never invents a label |
| `text` | `text` | verbatim source voice; data only, never interpreted |
| `period_start`, `period_end` | — | ephemeris-local display derivations (retro-spec sec2); never read |
| `created_at`, `updated_at`, `archived_at` | — | capture/lifecycle provenance; a capture timestamp never populates `occurred` (§19.1). `archived_at` only drives the archived exclusion above |
| `retro_id` | — | ephemeris-local row id; the stable identity is `retro_uuid` |

The importer itself assigns `entry_type=ephemeris_event`,
`source_type=imported_event`, `strength=imported_activity_event`; the
§19.1 record is closed and the adapter adds nothing.

## Strength decision

A retro entry maps to §19.1 as-is, arriving as
`imported_activity_event` evidence. It is owner-authored memory, but it
is relayed verbatim through a local, owner-controlled capture surface —
the same trust root as direct exp2res capture — and §9.4's `high` fact
ceiling opens from an imported root only through the owner's §14.4
correction flow, which is deliberate owner authority (exp2res's "trust
but verify" canon). So no second evidence class is invented for it; the
owner ratifies this mapping at PR review.

## Identity and edits

`record_id = ephemeris:retro:<retro_uuid>` — minted once per entry,
never per edit.

- Edits **before** first import converge adapter-side: latest event
  wins, one record per `retro_uuid`.
- Edits **after** a delivered import arrive as the same identity with a
  different content hash, and exp2res rejects them by §19.4 rule 2; the
  rejection is visible in the import report. The owner's exp2res §14.4
  correction flow is the only reinterpretation channel — the adapter
  never mints a new identity for an edit.
- Rerunning adapter + import over an unchanged export converges: the
  adapter output is byte-identical and §19.4 counts every record as a
  `duplicate` no-op.

## Report reason codes

| Class | Reason | Meaning |
|---|---|---|
| skipped | `archived` | latest snapshot is archived |
| skipped | `no_project` | §19.1 requires a project label |
| rejected | `period_unparsable:<code>` | exp2res grammar refused `period_raw` (grammar drift) |
| rejected | `invalid_snapshot` | snapshot field types are not the sec33 wire shape |
| rejected | `unsupported_payload_version` | some event of this identity carries an unknown `payload_version`; the whole entry is rejected (per line only when the line names no identity) |
| rejected | `payload_not_object`, `missing_retro_uuid`, `line_not_json`, `line_not_event` | malformed export line (reported with its physical line number) |

Deterministic only: no model call, no network, no persistent state, and
entry text cannot alter behaviour. Requires the `exp2res` package to be
importable (installed, or its checkout on `PYTHONPATH`).
