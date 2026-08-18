# Ephemeris retro/diary → Exp2Res §19.1 adapter

`scripts/retro_adapter.py` reads an ephemeris JSONL audit export, selects
the retro-entry and diary-entry slices, and emits the §19.1
activity-domain JSONL payload that `exp2res import ephemeris <file>`
accepts
([selfos#37](https://github.com/jointsome0-lgtm/selfos/issues/37), under
the [#25](https://github.com/jointsome0-lgtm/selfos/issues/25)
integration architecture). Source contracts: ephemeris
`docs/retro-spec.md` (sec33) and `docs/diary-spec.md` (sec35). Routing
contract for the diary slice: [tags.md](tags.md) (route/deny table v1).
Target contract: exp2res `spec/19-integration-contracts.md`
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
are explicit. When an exp2res private root is configured (the explicit
`--instance PATH` flag, then `EXP2RES_WORKSPACE`, then
`instances.exp2res` in `~/.config/selfos/config.toml` — the
[instance.md](instance.md) discovery order), the root must be an
existing directory and the `-o` path must sit strictly beneath it — as
both the written name and its resolved target, so neither a file-valued
root nor a symlink parked outside the root can receive the payload:
adapters operate only on explicitly configured private-instance paths,
and a configured root that itself lies inside a public checkout is
refused — the explicit flag does not bypass that guard. With no root configured the run is refused outright — the
adapter cannot tell personal data from fixtures — unless
`--allow-unconfigured` explicitly marks it an invented-data run to a
private destination; even then an `-o` path inside any public engine
checkout the AGENTS.md map names is refused (checked as both the
pathname given and its symlink-resolved target), as is output onto the
export itself (symlink and hard-link aliases included). The export is
likewise refused as a source when it lies inside a public checkout; it
is not required to sit inside an ephemeris root, because ephemeris
delivers exports as browser downloads. The export is opened once,
validated as the opened file, and read through that same descriptor —
a source retargeted between check and read cannot substitute a
different file. The payload is written to a
fresh owner-only (`0600`) staging file (`<output>.tmp`) that atomically
replaces the destination name — an existing destination's inode is
never truncated in place, so a hard-linked alias of it never receives
the payload. The output-side checks run against a pinned descriptor of
the output's directory, and the staging write and atomic replace go
through that same descriptor — so a parent directory swapped between
check and write cannot redirect the payload. A staging file left behind
by an interrupted run is identified and refused at startup, never
silently deleted or overwritten: it may hold a payload, so the owner
inspects and deletes it before rerunning.
Once the import report is confirmed the owner deletes
the payload file — it is a transient handoff artifact, not a second
store, and nothing else retains shipped entry text outside the
receiving workspace.

## Selection

Export lines are grouped per slice — `retro_entry_*` events by
`payload.retro_uuid`, `diary_entry_*` events by `payload.diary_uuid`;
the slices never mix, and a mixed export yields both in one pass (every
retro record first, then every diary record, each slice in first-seen
export order). The latest event in file order wins (the export is
ordered by ledger append order); an entry whose latest snapshot has
`archived_at` set is excluded (`skipped: archived`). An identity
carrying any event with an unsupported `payload_version` is rejected
whole — its latest state is unreadable, so an older snapshot never
stands in for it. Any other event type is ignored and counted. A
knowledge-state payload is never either slice: it is not a
`retro_entry_*` or `diary_entry_*` event, so it never passes the
filter, and no Atlas-shaped record is ever emitted.

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
- Archival after delivery does not propagate. §19.1 declares no
  tombstone record and exp2res imports are additive, so an entry that
  was imported and later archived in ephemeris stays in exp2res as
  delivered. The adapter is stateless by the no-copy rule and cannot
  know what was delivered; it surfaces the divergence instead — the
  report's `skipped: archived` entries carry the `record_id`, which
  equals the exp2res `source_record_id` metadata, and removing
  already-delivered evidence is the owner's exp2res deletion flow.

## Diary slice

The diary slice routes under the [tags.md](tags.md) route/deny table
v1: every non-private entry ships to the exp2res raw-log route by
default; `private` is the absolute deny tier. The adapter is the
primary privacy gate — the ephemeris export is a full ledger replay,
private entries included (diary-spec sec3).

### Privacy deny, history-wide

An entry whose **any** event snapshot carries `private: true` is denied
— not just the latest. The tags.md latch is one-way (once private,
never shipped), and clearing the flag on an existing entry has no
routing effect, so a history that was ever private never ships. The
latch is read on every diary event, an unsupported `payload_version`
included, because denying is always the safe direction; `private` wins
over every other disposition (archived, unsupported version) in the
report. Denied entries appear in the run report content-free: counts
plus `diary_uuid` only, reason `private` — no `record_id` is minted for
an entry that must never become a record. A valid diary event whose
`private` value is not a JSON boolean marks the identity's privacy
state unreadable, and an entry whose privacy cannot be read is never
shipped (`rejected: invalid_snapshot`).

Late-privatization *delivery* — the content-free flag-flip to a copy
already shipped non-private (tags.md "Late privatization", exp2res#28
second line) — is out of scope for this slice. The seam is the denied
report entries: they carry the `diary_uuid` whose exp2res
`source_record_id` would be `ephemeris:diary:<diary_uuid>`, so the
owner can cross-check a previously delivered copy; the stateless
adapter (no-copy rule) cannot know what was delivered.

### Mapping table

| Source (diary snapshot) | Target (§19.1) | Rule |
|---|---|---|
| — | `source` | literal `ephemeris` |
| `diary_uuid` | `record_id` | `ephemeris:diary:<diary_uuid>` — minted once per entry, never per edit (same rule as retro) |
| — | `domain` | literal `activity` |
| `entry_date` | `occurred` | resolved by the same imported `exp2res.services.time_input.parse_occurred` in `--timezone`, at `exact_day` precision, `high` confidence — day precision, no invention. The confidence constant is the value exp2res's own day-capture path (`today_occurred`) assigns to an owner-picked exact day; the diary UI makes `entry_date` a deliberate owner choice (default today, never future). A value that is not the sec35 `YYYY-MM-DD` wire shape is `rejected: invalid_snapshot`; a well-shaped date the grammar refuses (an impossible calendar day) is `rejected: entry_date_unparsable:<code>` — never approximated. |
| — | `project` | literal `diary`. §19.1 requires a non-empty project; diary entries carry none, and adapters never interpret free text or inert tags to guess one — a single slice-level provenance label is a routing fact, not per-record invention. The owner ratifies this constant (and the confidence constant above) at PR review. |
| `text` | `text` | verbatim source voice; data only, never interpreted |
| `private` | — | routing only: the history-wide deny latch above; never emitted |
| `tags` | — | never read. §19.1 has no tags field; `atlas` routes to the atlas intake, not this slice; `career` is reserved-inert (tags.md v1); personal tags are inert by contract |
| `atlas_ref` | — | belongs to the atlas route's intake; never read here |
| `created_at`, `updated_at`, `archived_at` | — | capture/lifecycle provenance; a capture timestamp never populates `occurred` (§19.1). `archived_at` only drives the archived exclusion |
| `diary_id` | — | ephemeris-local row id; the stable identity is `diary_uuid` |

Identity and edit semantics are exactly the retro rules: edits before
first import converge adapter-side (latest event wins); edits after a
delivered import arrive as the same identity with a different content
hash and exp2res rejects them by §19.4 rule 2 — the owner's §14.4
correction flow is the only reinterpretation channel; rerunning adapter
+ import over an unchanged export converges (byte-identical output,
§19.4 `duplicate` no-ops); archival after delivery does not propagate
and is surfaced, not hidden.

Like the ratified retro slice, this slice builds no delivery-receipt
store: idempotency rests on §19.4 duplicate convergence. The tags.md
receipts ledger remains the standing gap, named in the delivering PR.

## Report reason codes

Report entries carry the slice's own uuid key (`retro_uuid` or
`diary_uuid`) plus `record_id` — except `denied` entries, which carry
`diary_uuid` only.

| Class | Reason | Meaning |
|---|---|---|
| denied | `private` | some event snapshot of this diary identity carries `private: true`; the history-wide latch denies the entry (tags.md) |
| skipped | `archived` | latest snapshot is archived |
| skipped | `no_project` | the retro entry has no project (`null` or blank) and §19.1 requires one (diary entries always carry the constant `diary`) |
| rejected | `period_unparsable:<code>` | exp2res grammar refused `period_raw` (grammar drift) |
| rejected | `entry_date_unparsable:<code>` | exp2res grammar refused a well-shaped `entry_date` (an impossible calendar day) |
| rejected | `invalid_snapshot` | snapshot field types are not the sec33/sec35 wire shape (a non-string `project`, a non-`YYYY-MM-DD` `entry_date`, or a non-boolean `private` — an unreadable privacy state never ships), or the latest event's archive transition contradicts its own snapshot (`*_archived` without `archived_at`, `*_unarchived` with it) |
| rejected | `text_not_encodable` | the record cannot be encoded as UTF-8 (e.g. an unpaired surrogate in a corrupted export) |
| rejected | `unsupported_payload_version` | some event of this identity carries a `payload_version` that is not exactly the integer `1`; the whole entry is rejected |

Three conditions refuse the whole run rather than a single record, each
named with its physical line number: a retro or diary event whose
payload carries no attributable `retro_uuid`/`diary_uuid`, any line
that is not a JSON event object at all, and any unknown `retro_entry_*`
or `diary_entry_*` event type (a newer contract than this adapter
speaks). All share one reason — the unreadable or unrecognized event
could change or hide the state of any entry, and an earlier snapshot
must never stand in for it.

Deterministic only: no model call, no network, no persistent state, and
entry text cannot alter behaviour. Requires the `exp2res` package to be
importable (installed, or its checkout on `PYTHONPATH`).
