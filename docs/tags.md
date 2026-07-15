# Diary tags

Scope: the diary tag vocabulary as a deterministic routing and privacy
contract — the versioned route/deny table, the `private` tier, untagged
defaults, corrections and receipts, and how the vocabulary grows.
Decided 2026-07-15
([selfos#5](https://github.com/jointsome0-lgtm/selfos/issues/5)
resolution). This is the canonical page: subsystem docs and issues
point here and do not restate it. Producers store tags as opaque
structured metadata; the cross-system meaning below is enforced by the
selfos adapters alone.

## Vocabulary v1

A closed, versioned table. A tag is contract-bearing exactly when it
has a row here; every other tag is legal and inert — personal
organization that rides as preserved metadata and carries no
cross-system meaning.

```text
tag      v1 meaning
-------  -----------------------------------------------------------
atlas    Owner authorizes the knowledge aspect of the entry for the
         atlas intake route. An optional opaque `atlas_ref` metadata
         string may accompany it and is passed through untouched;
         its meaning belongs to atlas's own intake.
private  Absolute deny tier — see below. Wins over every routing
         tag; the combination is surfaced in receipts and dry-runs.
career   RESERVED: the name is parked, no route is bound. Semantics
         arrive when exp2res's capture intake defines them
         (exp2res#77); until then the tag is inert like any other.
```

Standing rules: tags never become evidence-strength, understanding,
employment, or verification labels. Sensitivity classes
([Deletion](deletion.md)) are an orthogonal axis — purge and
export-default behavior — and are not expressed as tags in v1.

## Route/deny table v1

Adapters route by this table and never interpret free text (AGENTS.md
→ "Adapters are deterministic").

```text
entry                          atlas intake       exp2res raw-log
-----------------------------  -----------------  ----------------
private (any tags present)     deny               deny
atlas tag, non-private         ship (+atlas_ref)  ship (default)
untagged or inert tags only,   deny               ship (default)
non-private
```

The exp2res raw-log route is the single default flow: every
non-private entry ships there untagged (canonical since 2026-07-05;
gap-question answers are ordinary diary entries and ride it with no
special casing). Untagged entries never reach atlas by adapter
initiative; the recall path for forgotten tags is the explicit
`suggest-tags` stage below. Any future route is deny for untagged
until its table row explicitly allows it — a new row never inherits
the default flow.

## The private tier

`private` is the third tier of the cloud-context data boundary
(AGENTS.md): an optional boolean field on an entry, opaque to the
producer.

- **Absolute deny.** A private entry never enters any agent context —
  ambient or explicit pipeline runs alike — and is never shipped by
  an adapter to any route, including the default raw-log flow. No
  other tag overrides it.
- **One-way latch.** De-privatizing means authoring a new non-private
  record (an append-only correction with its own occurred date).
  Clearing the flag on an existing entry has no routing effect: once
  private, never shipped. The invariant is end-to-end testable and
  keeps every historical audit one-dimensional.
- **Owner-set only.** `suggest-tags` reads untagged non-private
  entries and proposes routing tags only. An LLM proposing `private`
  is explicitly deferred — a candidate for a future vocabulary
  version once agent runs are local; the stage-one cloud tradeoff is
  the accepted residual named in [Deletion](deletion.md).
- **Adapter hard-stop, two layers.** The ephemeris JSONL export stays
  a full ledger replay; the private flag rides the payload. That
  export is private-instance data readable only by deny-aware selfos
  adapters — deterministic code, which reads it and ships nothing
  private. The reserved consumer-side RawLog flag (exp2res#28) is the
  second line of defense, never the primary gate.
- **Late privatization.** When an entry shipped non-private and a
  later correction marks it private, the adapter deterministically
  delivers a content-free flag-flip to the consumer — the stored copy
  is excluded from all future LLM stages — and surfaces the decision
  to remove that copy under the deletion contract (logical delete, or
  purge by sensitivity).

## Token syntax and storage

Structured metadata is canonical: routing tags are a list of strings,
`private` a separate boolean — both already reserved in consumer
schemas. Contract tag names are lowercase ASCII kebab-case; comparison
is exact. Personal tags are any string and stay inert.

In-text `#hashtags` are prose. A capture surface may deterministically
lift them into structured metadata at capture time (its own policy;
the capture CLI takes structured fields). Adapters never read prose
after capture, so a literal hashtag in text needs no escaping and
means nothing by itself.

## Corrections and receipts

Tag changes are append-only correction events; no adapter rewrites an
old source record. Every delivery writes a content-free receipt —
entry id, route, vocabulary version, delivery id — respecting the
standing rule that adapters keep no persistent content copies
([Private instance](instance.md)). Receipts make routing idempotent: a
re-run or a later correction never duplicates a delivery.

## suggest-tags

An explicit, user-run recall pass over untagged non-private entries.
It proposes routing tags (v1: `atlas`); the owner approves; the
adapter ships deterministically with ordinary receipts — an approved
suggestion produces exactly the same route as direct owner tagging.
Never adapter initiative. An adapter dry-run may report untagged
counts as a reminder; that is output, not a queue.

## Growing the vocabulary

The table is versioned by a plain integer (this page: **v1**); every
receipt records the version it routed under. Repository provenance is
pinned by the version manifest (selfos#1): the integer is the semantic
axis, the sha the provenance axis. A machine-readable mirror of the
table appears when adapter code first exists, carrying the same
version.

Promotion is forward-only: a new row — including promoting a
previously personal tag — takes effect for entries recorded after the
version bump. Historical entries are never re-routed automatically;
backfill is an explicit owner-run pass with per-entry approval, and
receipts prevent duplicate deliveries. Every new or changed row lands
with invented fixtures ([Public hygiene](hygiene.md)) covering its
route/deny combinations.
