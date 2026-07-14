# Deletion

How data leaves this ecosystem: the two deletion tiers, the purge
runbook, and — honestly — what remains afterwards. This is the
canonical page: subsystem docs point here and do not restate it.
Scope: all personal data lives in private instances; the public
engine repos hold code, specs, and invented fixtures only — they
never carry anything to delete. Decided 2026-07-14
([selfos#13](https://github.com/jointsome0-lgtm/selfos/issues/13),
[atlas#35](https://github.com/jointsome0-lgtm/atlas/issues/35)).

## The guarantee, stated honestly

Deletion here is an **inventory guarantee, not a physics guarantee**:
a purge removes content from every *registered* copy. The registry of
copies is owner discipline backed by standing rules — not a mechanism
that can see unregistered copies. Every claim below is scoped by that
sentence.

## Two tiers

**Logical deletion** — the everyday default for every class of data.
Content leaves current state (a file edit, a journal-row removal);
history keeps it in the owner's private copies; derived consumers
tolerate the vanished id by construction. No external guarantee is
claimed.

**Purge** — content leaves current state, git history (filter-repo
content redaction), and every registered copy. Triggered by
sensitivity, never by path or data kind:

- **class-default** — a record carrying a sensitivity class (today
  exactly `medical`) is purged when deleted; logical deletion of a
  classed record is the explicit exception.
- **owner-declared** — anything the owner decides must be
  unrecoverable, class or no class: accidental captures —
  credentials, third-party personal data, text in the wrong file.

Urgency: accidental entry → immediate; planned retirement of a
legitimately-lived record → the owner's schedule; several redactions
may batch into one rewrite — a rewrite invalidates every copy and is
expensive.

What a purge removes at the record level — originals plus sole-basis
derived rows and generated files; rows with other live bases survive
whole with dangling, non-descriptive refs (and with their persisted
sensitivity class — a named residual, see the inventory below);
intake receipts and the content-free purge note
survive deliberately — is each store's own contract: atlas §34 for
the graph instance; ephemeris and exp2res define theirs against this
page ([ephemeris#17](https://github.com/jointsome0-lgtm/ephemeris/issues/17),
[exp2res#48](https://github.com/jointsome0-lgtm/exp2res/issues/48)).

## Standing preconditions

These hold at all times — they are what makes revocation finite:

1. **Copies manifest** — an instance-side file listing every durable
   copy: remotes, clones, backup media (name, kind, location,
   created, last-verified). Making a copy = registering it. The
   manifest rides inside the unit being copied, so every copy knows
   all copies. Transient same-machine copies are covered by a
   runbook sweep step instead.
2. **Delivery registry** — one line per snapshot/export delivery
   (delivery id, destination, supersession ack). Classed content
   leaves an instance only by explicit per-export choice, but
   owner-declared purges hit unclassed content that may sit in any
   ordinary export — so every delivery registers.
3. **Plaintext never leaves hardware under the owner's control.**
   The control boundary is the risk boundary. This excludes hosted
   git (GitHub et al.) and unencrypted third-party storage in one
   stroke.
4. **Full-disk encryption on every machine holding an instance** —
   honestly scoped: FDE covers powered-off media (theft, disposal,
   repair), not a live mounted system.
5. **Media decommission rule** — a manifest line closes only by
   verified wipe or destruction; for LUKS media, header erase.
6. **Adapters keep no persistent content copies** — a standing rule
   of the selfos adapter contract.

## Storage configurations and their revocation moves

- **local-only** — no revocation step.
- **bare git server on hardware under the owner's control** —
  delete the bare directory, re-init, push the rewritten history.
  A rented VPS is not controlled hardware: the hoster's disk
  snapshots are someone else's risk.
- **object storage (S3 and kin) — client-side-encrypted artifacts
  only**, key never near the data: borg keyfile mode or age-encrypted
  bundles preferred; restic-repokey (wrapped key inside the repo)
  only as a conscious exception, password entropy the bound.
  Revocation: delete objects **and all versions** (bucket versioning
  retains overwritten syncs), `forget` + `prune` pre-rewrite
  generations; provider-retained ciphertext is bounded by
  crypto-erasure.

Revocation never uses force-push: a rewrite propagates by
**replacement** — delete / re-init / push; destroy / re-clone —
never by pushing over live history. Reflogs and local branches keep
purged objects alive, and a later push re-canonizes them.

## The purge runbook

1. **Scope.** List the records; batch pending redactions. A
   closure computed now is advisory only, for sizing the work —
   the authoritative closure waits for the freeze (step 3).
2. **Quiesce.** Everything pushed, every clone idle; the rewrite
   runs in exactly one place. Writes stay frozen until the
   rewrite lands.
3. **Closure, bound to the frozen head.** Compute the closure per
   the store's contract (atlas §34.2) against the exact head and
   ref set the rewrite will run on, and review it — the owner
   adjusts by declaration: surviving free text that paraphrases
   the purged content; a telling id extends the set to its
   surviving refs. Produce ephemeral verification predicates per
   target: ids where they exist, plus paths, byte- or
   pattern-selectors, object ids, and message selectors for
   owner-declared content that has no id. Predicates are content —
   never stored, they die with the run. A commit arriving after
   this step invalidates the closure: abort and recompute.
4. **Append the purge note** — content-free (date + classes +
   gen, atlas §34.3). `gen` names this purge for every mark
   below; the note anchors export invalidation and explains later
   dangling refs.
5. **Rewrite.** filter-repo content redaction over the closure, in
   the one site.
6. **Verify — content predicates over the full git object
   universe.** That universe, named: commit and tag messages,
   notes, all refs including stash and replace refs, reflogs,
   unreachable objects and packs. Expire reflogs, drop stashes,
   replace refs, and notes that hit, gc and prune, then verify at
   the object level — all before any backup (step 8). A blanket
   history grep applies only to ids that must vanish entirely
   (telling owner-declared ids whose surviving refs joined the
   set); a record with deliberate survivors (atlas §34.2
   multi-basis rows) is verified by the absence of its original
   and its content — a surviving dangling ref is the contract,
   not a failure. A predicate hit means the closure was
   incomplete — extend and repeat from step 3.
7. **Rebuild.** Derived outputs are untracked by design; the
   post-purge rebuild is mandatory.
8. **Sanitize the site, then take the fresh backup.** Before any
   post-rewrite backup, sweep the instance directory for residue
   the rewrite never touches — ignored files and stray exports,
   editor backups and histories, leftover worktrees, reports and
   caches (step 6 already scrubbed the git stores). Only then
   take the post-rewrite backup — the instance never lives in a
   single copy — and mark its manifest line with this gen. Residue
   found by any later step invalidates the backup: recreate it,
   never patch it.
9. **Walk the manifest.** Every remote replaced (delete / re-init /
   push), every clone destroyed and re-cloned — never pull or fetch
   over rewritten history; every pre-rewrite backup generation
   destroyed on every registered medium (decommission rule). Mark
   each line `purged-through: gen` as its move completes; a copy
   made from rewritten history is born current. Rejected and why:
   aging pre-rewrite backups out by retention —
   "the purge ran but the data lives another N months in backups"
   is exactly the dishonest middle state this page exists to forbid.
10. **Walk the delivery registry.** Every line without this gen's
    ack: delete the delivered file, re-export fresh over it, mark
    `superseded-by: gen`. Consumers tolerate vanished ids by
    construction; consumer-internal cleanup is the consumer's own
    deletion lifecycle, invoked from here. Invalidation is the
    per-line ack, never a date comparison — the note's date stays
    the human anchor, the ack decides.
11. **Sweep the rest.** Transient same-machine copies elsewhere;
    filesystem snapshots covering instance paths; local logical
    host artifacts — editor histories outside the instance,
    terminal scrollback and logs, local CI caches. Residue under
    an already-backed-up path sends step 8's backup back for
    recreation.
12. **Completion — one transition, readable after a crash.** The
    purge is complete only when every manifest line reads
    `purged-through ≥ gen` and every registry line carries its
    ack. On restart, the note's gen plus the unmarked lines are
    the exact remaining work — retry is idempotent per line. An
    unreachable clone or medium leaves the purge explicitly
    incomplete: its line, stuck below gen, is the named waiting
    line — never a silent "almost done".

Honest cost, stated openly: pre-rewrite recovery points die for the
whole instance; the history-as-recovery clock restarts at the purge
point.

## What remains after a purge — the residual inventory

1. **Ciphertext tails** in provider retention until crypto-erasure
   or expiry — undecryptable by key locality.
2. **Cloud agent context** — whatever entered agent sessions lives
   in provider logs under provider policy; a purge has zero
   retroactive reach there, and retention policy is policy, not
   physics (a litigation hold can suspend it). This is the accepted
   stage-one tradeoff (AGENTS.md); a local-runtime switch shrinks
   this class forward, never backward; upstream defaults — class
   exclusion from exports, diary out-by-default — keep classed
   content out of it in the first place.
3. **Physical remanence on owner media** — SSD wear-leveling,
   filesystem snapshots, swap. Bounded by the standing
   preconditions: FDE (powered-off media) and the media
   decommission rule.
4. **Unregistered copies** — outside the guarantee by definition;
   manifest completeness is owner discipline, not a mechanism, and
   no softer wording would make that untrue.
5. **Metadata residues inside the instance** — the persisted
   sensitivity class on multi-basis survivor rows ("classed
   provenance existed at this row"), and the correlation surface:
   a dangling date-serial id, the purge note's date and classes,
   and the grouped build report together bound "a classed record
   existed that day, at least N of them" (atlas §34.2–§34.3). By
   design, and instance-side only: the class keeps survivors out
   of default export, and none of the correlating artifacts is
   exported.

What a purge is **not**: not a secure-erase of media, not a
provider-side guarantee, not retroactive recall from LLM logs.

## Manifest and registry formats

Instance-side data; copy-paste templates ship with the bootstrap
walkthrough
([selfos#17](https://github.com/jointsome0-lgtm/selfos/issues/17)).
The shapes:

```text
# copies-manifest — one line per durable copy
name      kind           location                created     last-verified  purged-through
laptop    working-clone  (this machine)          2026-07-01  —              2
nas-bare  bare-remote    nas:/srv/git/instance   2026-07-01  2026-07-10     2
usb-b1    borg-repo      usb-blue (keyfile)      2026-07-05  2026-07-05     1
```

`purged-through` is the purge generation (the note's `gen`, atlas
§34.3) this copy has been brought up to — `—` before the first
purge. A line below the latest gen is an incomplete purge's named
waiting line; a copy created from rewritten history is born
current.

```text
# delivery-registry — one line per export delivery
id                destination                      superseded-by
d-2026-07-08-001  exp2res-intake (state snapshot)  2
d-2026-07-08-002  exp2res-intake (state snapshot)  —
```

`id` is date-serial — type, date, ordinal, the atlas §34.6
pattern — so two same-day deliveries stay distinct.
`superseded-by` is the purge generation whose walk revoked and
re-exported the delivery: the explicit per-line ack that replaces
date comparison. A destination expected to receive classed exports
takes a neutral operational label (`dest-1`), mirroring the
adapter source-slug rule (atlas §34.6); ordinary destinations stay
readable.
