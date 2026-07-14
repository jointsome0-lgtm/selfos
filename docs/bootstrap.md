# Bootstrap a private instance

This manual copy-paste walkthrough creates an invented Atlas private
instance for [Vera Example](persona.md). It fulfills the bootstrap
acceptance item from the public/private split. Decided 2026-07-14
([selfos#17](https://github.com/jointsome0-lgtm/selfos/issues/17),
following
[selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3)).

There is no orchestration code yet. A later bootstrap command in this
repository will automate exactly these steps.

Never use a public checkout as a working root or write destination. The
engine-pin step reads its revision without modifying it; every write
goes to the separate instance. This walkthrough uses the fictional
public checkout `/home/vera/src/atlas` and private instance
`/home/vera/atlas-instance`. Every name and path shown is fictional. A
reader running the walkthrough substitutes a private root outside every
public checkout.

The operational contract remains in
[Private instance](instance.md); deletion and copy accounting remain in
[Deletion](deletion.md). This page applies those contracts without
restating them.

## 1. Choose a private root

Every machine holding the instance must use full-disk encryption, and
plaintext must never leave hardware under the owner's control. Hosted
Git, including GitHub, is not a private-instance remote. These are
standing preconditions of the [deletion contract](deletion.md).

Create and enter the separate root:

```text
mkdir -p /home/vera/atlas-instance
cd /home/vera/atlas-instance
```

## 2. Initialize the repository

An Atlas instance is an ordinary Git repository:

```text
git init -b main
```

## 3. Create the skeleton

Create the instance-owned directories and empty state journals. The
placeholder files keep otherwise empty directories in the first
commit; remove a placeholder when that directory gains content.

```text
mkdir -p atlas plans intake state
touch atlas/.gitkeep plans/.gitkeep intake/.gitkeep
touch state/artifacts.jsonl state/encounters.jsonl
touch state/questions.jsonl state/decisions.jsonl
touch state/intake.jsonl state/purges.jsonl
```

Copy this into `.gitignore`. Atlas graph builds and exported snapshots
are derived instance-side outputs under `graph/`; they stay untracked.

```text
graph/
```

The ownership and recovery rules are canonical in
[Private instance](instance.md).

## 4. Record the engine pin

Write the full 40-character commit sha of the Atlas engine checkout to
`engine.pin` at the instance root:

```text
git -C /home/vera/src/atlas rev-parse HEAD > /home/vera/atlas-instance/engine.pin
```

An optional `# comment` may follow the sha on the same line. Preserve
the sha produced by the command; this is the accepted shape:

```text
0123456789abcdef0123456789abcdef01234567 # 2026-07-14 atlas v0
```

The pin records engine provenance, not compatibility. Its parsing and
write-gate mechanics remain in [Private instance](instance.md).

## 5. Create the copy records

Copy this block verbatim into `copies-manifest` at the instance root.
Replace example dates when copies are created. The working clone exists
now, so its line is active. The bare-remote line stays commented until
that copy exists.

```text
# copies-manifest — one line per durable copy; making a copy = registering it
name        kind           location                                   created     last-verified  purged-through
vera-atlas  working-clone  /home/vera/atlas-instance                  2026-07-14  —              —
# vera-nas  bare-remote    vera@nas.example.com:/home/vera/atlas.git  2026-07-14  —              —
```

`name` identifies the copy. `kind` is `working-clone`, `bare-remote`,
`borg-repo`, `age-bundle`, or `other`. `location` identifies where the
copy lives; `created` is its creation date; `last-verified` is a date
or `—`. `purged-through` is the purge generation this copy has been
brought up to — `—` until the first purge; its mechanics are the
[deletion contract](deletion.md)'s.

Copy this block verbatim into `delivery-registry` at the instance root.
The example stays commented until that delivery exists.

```text
# delivery-registry — one line per export delivery; making a delivery = registering it
id                  destination                                              superseded-by
# d-2026-07-14-001  /home/vera/exp2res-workspace/intake/atlas-snapshot.json  —
```

`id` is date-serial (`d-<date>-<NNN>`), so two same-day deliveries
stay distinct; `superseded-by` is `—` until a purge walk revokes and
re-exports the delivery ([deletion contract](deletion.md)). A
destination expected to receive classed exports takes a neutral
operational label. Add a line when an export is delivered, not later.

## 6. Configure path discovery

For the current shell, point Atlas at the invented instance:

```text
export ATLAS_INSTANCE=/home/vera/atlas-instance
```

Alternatively, set the user-scope configuration in
`~/.config/selfos/config.toml`:

```text
[instances]
atlas = "/home/vera/atlas-instance"
```

Until one of the discovery paths is configured, capture and write
tooling refuses by design. The full discovery order and the guard
against public-checkout destinations remain in
[Private instance](instance.md). This configuration permits invented
demo writes only; it is not a destination for real capture.

## 7. Make the first commit

Commit the skeleton, pin, manifest, and registry:

```text
git add .gitignore atlas plans intake state engine.pin copies-manifest delivery-registry
git commit -m "Bootstrap Vera Example Atlas instance"
```

## 8. Optionally add a private remote

A private remote is optional and may exist only on hardware under the
owner's control. For example, initialize a bare repository on Vera's
fictional owned machine:

```text
ssh vera@nas.example.com git init --bare /home/vera/atlas.git
```

The moment the bare repository exists, uncomment its line in
`copies-manifest` and replace the dates. Making a copy means registering
it. Commit that registration, then add the remote and make the first
push. The remote receives the complete manifest:

```text
git add copies-manifest
git commit -m "Register Vera Atlas private remote"
git remote add origin vera@nas.example.com:/home/vera/atlas.git
git push -u origin main
```

## 9. Keep demo and real instances separate

Anything written into this walkthrough's example instance is invented
data authored by [Vera Example](persona.md). It never contains real or
sanitized personal data.

After the walkthrough, remove the Vera discovery setting before real
capture. Real capture stays blocked until a separate real private
destination is configured. A real instance is never a demo instance.

For a shell-only setting:

```text
unset ATLAS_INSTANCE
```

If the example path was placed in `config.toml`, remove it there
instead.
