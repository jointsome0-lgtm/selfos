# Private instance

This is the operational reference for private-instance layout and file
ownership, the engine pin, and instance-path discovery. Decided
2026-07-14
([selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3),
[selfos#10](https://github.com/jointsome0-lgtm/selfos/issues/10),
[selfos#11](https://github.com/jointsome0-lgtm/selfos/issues/11),
[selfos#14](https://github.com/jointsome0-lgtm/selfos/issues/14)).
Deletion semantics live in the canonical
[docs/deletion.md](deletion.md); this page does not restate them.

## Two roots

A **public engine** holds code, schemas/specs, docs, and invented demo
fixtures authored by a named synthetic persona. The selfos, atlas,
ephemeris, and exp2res engine repositories are all public. They hold
no primary data: real data never enters a public checkout, tracked or
untracked.

A **private instance** holds real curated content, journals and state,
derived outputs built from them, and the engine-revision provenance
pin. The Atlas instance is an ordinary second git repository at its
own root, with a private remote or no remote. For example,
`/home/vera/src/atlas` may be the public engine checkout and
`/home/vera/atlas-instance` its separate private instance; both paths
are fictional.

In atlas §25.1 and §25.6, **the Atlas repository** therefore means
the instance repository, not the public engine checkout. Atlas §8
defines the file families split between those roots.

This separation closes two structural exits. An accidental push
cannot publish data that never entered a public repository.
Development-agent sessions also read public checkouts ambiently, so
real data there could enter model-provider context without any push.
Engine tooling receives an explicitly configured private path; it
never infers a public checkout as a data destination.

## Ownership table

```text
File family                      Owner root              Notes
-------------------------------  ----------------------  ----------------------
selfos adapters, source          selfos public engine    Code and invented
registries, docs, fixtures                               fixtures only; no
                                                         persistent shipped-
                                                         content copies.
atlas docs/, scripts/, viewer/,  atlas public engine     Specs, tools, UI, and
demo fixtures                                            invented fixtures.
ephemeris code, schemas, docs,   ephemeris public        No ledger, backups,
demo fixtures                    engine                  exports, or real data.
exp2res code, specs, docs,       exp2res public engine   No workspace, sources,
demo fixtures                                            run state, or real data.
atlas/, plans/                   Atlas instance repo     Curated content and
                                                         plans.
intake/                          Atlas instance repo     Delivered batches, kept
                                                         as delivered.
state/                           Atlas instance repo     Append-only JSONL
                                                         journals.
state/purges.jsonl               Atlas instance repo     Purge notes.
state/intake.jsonl               Atlas instance repo     Intake receipts.
graph/ builds; exported          Atlas instance repo     Derived, gitignored,
snapshots                                                and untracked.
engine.pin                       Atlas instance root     Exact Atlas engine
                                                         commit provenance.
copies manifest; export          Atlas instance root     Tracked; copied with
delivery registry                                        the instance.
SQLite ledger, backups, exports  ephemeris private       Outside the public
                                 data root               checkout.
SQLite workspace, supplied       exp2res private         Outside the public
sources, run state, disposable   workspace               checkout.
projections
```

**Atlas.** The instance owns `atlas/`, `plans/`, `intake/`, and
`state/`. Delivered intake batches remain as delivered. The state
journals are append-only JSONL, including `state/purges.jsonl` purge
notes and `state/intake.jsonl` receipts (atlas §8, §34).

The **copies manifest** lists every durable remote, clone, and backup
medium. The **export delivery registry** records one line per snapshot
delivery. Both are instance-side tracked files and ride inside the
unit being copied, so every copy knows all registered copies. Their
templates will ship with the forthcoming
[docs/bootstrap.md](bootstrap.md) walkthrough.

**ephemeris.** Its SQLite ledger, backups, and exports live in a
private data root outside the public checkout. `ACTIVITY_DATA_DIR`
locates that root. Its backup and deletion lifecycle remains owned by
[ephemeris#17](https://github.com/jointsome0-lgtm/ephemeris/issues/17).

**exp2res.** Its private SQLite workspace, supplied sources, run
state, and disposable projections live outside the public code repo.
Their lifecycle remains owned by
[exp2res#48](https://github.com/jointsome0-lgtm/exp2res/issues/48).

**selfos.** Adapters and source registries are engine-side code with
invented fixtures only. They operate only on configured private paths
and keep no persistent content copies of shipped entries. This is the
standing adapter rule
([selfos#13](https://github.com/jointsome0-lgtm/selfos/issues/13)).

## Derived outputs

Atlas `graph/` builds and exported snapshots are instance-side but
gitignored and untracked. Recovery of a derivable file is a rebuild,
not a checkout (atlas §25.6, §31.8). Tracking builds would put every
historical graph blob into every purge rewrite set. The post-purge
rebuild is therefore mandatory (atlas §34); the purge semantics and
runbook remain in [docs/deletion.md](deletion.md).

## Engine pin

Decided 2026-07-14
([selfos#11](https://github.com/jointsome0-lgtm/selfos/issues/11)).
The Atlas instance root contains `engine.pin`. Its canonical form is
one line: the full 40-character Atlas engine commit sha, not a tag.
Tags are mutable; commit shas are not. Whitespace and a `# comment`,
such as a date and engine version, may follow:

```text
0123456789abcdef0123456789abcdef01234567 # 2026-07-14 atlas v0
```

A parser takes the first whitespace-delimited token of the first
non-empty, non-comment line. The token must be the full sha.
Comparisons use the full sha; diagnostics abbreviate each sha to 12
characters.

The pin records **provenance**: the instance was written or validated
under engine revision X. It does not enforce compatibility. Format
and schema versions enforce compatibility; atlas owns that contract
in [atlas#30](https://github.com/jointsome0-lgtm/atlas/issues/30).

When the running engine differs from the pin, reads continue with the
following warning. Substitute the running and pinned shas, abbreviated
to 12 characters:

```text
warning: running engine '0123456789ab' differs from instance pin 'fedcba987654'; reads are allowed
```

Writes stop with:

```text
refusing write: running engine '0123456789ab' differs from instance pin 'fedcba987654'; validate and sync the instance first
```

The explicit sync or upgrade step first runs the new engine's
validation against the instance. Only after successful validation
does it rewrite `engine.pin` to the new full sha. A bootstrap/sync
command in this repository will perform that sequence once
orchestration exists; until then it is manual. The friction is
deliberate: there is one real instance, while bootstrap development
instances will re-pin with that one command.

## Path discovery

Decided 2026-07-14
([selfos#14](https://github.com/jointsome0-lgtm/selfos/issues/14)).
Engine scripts and adapters resolve a private path in this order:

1. the explicit flag `--instance PATH`;
2. the subsystem environment variable;
3. the user-scope config `~/.config/selfos/config.toml`.

Atlas uses `ATLAS_INSTANCE`. Ephemeris keeps its existing
`ACTIVITY_DATA_DIR` precedent. Its current in-checkout `./data`
default is a live misalignment to be fixed in ephemeris under
[ephemeris#29](https://github.com/jointsome0-lgtm/ephemeris/issues/29),
not an exception to this rule. `EXP2RES_WORKSPACE` is reserved for
exp2res; adoption happens in that repository.

The user-scope config has an `[instances]` table and will also be read
by the selfos orchestrator once it exists. All paths below are
fictional:

```text
[instances]
atlas = "/home/vera/atlas-instance"
ephemeris = "/home/vera/ephemeris-data"
exp2res = "/home/vera/exp2res-workspace"
```

There is no default inside a checkout. With no private Atlas
destination, capture or write tooling refuses with this normative
wording:

```text
no private instance configured: pass --instance PATH, set ATLAS_INSTANCE, or set instances.atlas in ~/.config/selfos/config.toml; a public checkout is never a data destination (selfos docs/instance.md)
```

Other tools substitute their environment variable and `[instances]`
key without changing the rule. Real capture into Atlas or exp2res
remains blocked until that subsystem's private destination is
configured.

A path found through any discovery source is still refused if it is
a public engine checkout or lies inside one. An explicit flag does not
bypass that guard.
