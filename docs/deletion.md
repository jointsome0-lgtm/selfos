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
derived rows; rows with other live bases survive whole with dangling,
non-descriptive refs; intake receipts and the content-free purge note
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
   (date, destination). Classed content leaves an instance only by
   explicit per-export choice, but owner-declared purges hit
   unclassed content that may sit in any ordinary export — so every
   delivery registers.
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

1. **Scope.** List the records; batch pending redactions. Compute
   the closure per the store's contract (atlas §34.2) and review
   it — the owner extends the set by declaration: surviving free
   text that paraphrases the purged content; a telling id extends
   the set to its surviving refs.
2. **Quiesce.** Everything pushed, every clone idle; the rewrite
   runs in exactly one place.
3. **Append the purge note** — content-free (date + classes): the
   anchor for export invalidation and the explanation of later
   dangling refs.
4. **Rewrite.** filter-repo content redaction over the closure, in
   the one site.
5. **Verify.** Grep the full rewritten history for every purged id;
   a hit means the closure was incomplete — extend and repeat.
6. **Rebuild.** Derived outputs are untracked by design; the
   post-purge rebuild is mandatory.
7. **Fresh backup first.** Take a post-rewrite backup before
   destroying anything: the instance never lives in a single copy.
8. **Walk the manifest.** Every remote replaced (delete / re-init /
   push), every clone destroyed and re-cloned — never pull or fetch
   over rewritten history. Then destroy all pre-rewrite backup
   generations on every registered medium (decommission rule).
   Rejected and why: aging pre-rewrite backups out by retention —
   "the purge ran but the data lives another N months in backups"
   is exactly the dishonest middle state this page exists to forbid.
9. **Walk the delivery registry.** Delete each delivered file and
   re-export fresh over it; consumers tolerate vanished ids by
   construction; consumer-internal cleanup is the consumer's own
   deletion lifecycle, invoked from here. Invalidation is
   date-based, anchored on the purge note: exports before that date
   are superseded.
10. **Sweep.** Transient same-machine copies; filesystem snapshots
    covering instance paths.
11. **Mark every line.** The purge is complete only when every
    manifest and registry line is marked. An unreachable clone or
    medium leaves the purge explicitly incomplete with a named
    waiting line — never a silent "almost done".

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

What a purge is **not**: not a secure-erase of media, not a
provider-side guarantee, not retroactive recall from LLM logs.

## Manifest and registry formats

Instance-side data; copy-paste templates ship with the bootstrap
walkthrough
([selfos#17](https://github.com/jointsome0-lgtm/selfos/issues/17)).
The shapes:

```text
# copies-manifest — one line per durable copy
name      kind           location                created     last-verified
laptop    working-clone  (this machine)          2026-07-01  —
nas-bare  bare-remote    nas:/srv/git/instance   2026-07-01  2026-07-10
usb-b1    borg-repo      usb-blue (keyfile)      2026-07-05  2026-07-05
```

```text
# delivery-registry — one line per export delivery
date        destination
2026-07-08  exp2res intake (state snapshot)
```
