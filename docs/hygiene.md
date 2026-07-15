# Public hygiene

Scope: the shared public-git hygiene convention for every public engine
repository in the ecosystem. selfos owns the convention; each
repository implements it locally. The gate was decided 2026-07-14 in
[selfos#15](https://github.com/jointsome0-lgtm/selfos/issues/15).

This is the [selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3)
acceptance item: tests and hygiene checks fail when a known
private-data path or non-demo fixture is staged in a public repository.

## Both layers

Every public repository must run the same local checker through both a
pre-commit hook and a CI job. Both layers are required, not
alternatives. CI is the backstop when the hook is bypassed or not
installed.

The hook mechanism (decided 2026-07-15) is a committed
`.githooks/pre-commit` script that runs the checker, enabled once per
clone with `git config core.hooksPath .githooks`. No hook framework.

## Denied paths

The ownership table in [Private instance](instance.md) defines the
instance-shaped directories and private file kinds. They must never be
visible to the public Git layer, whether tracked or staged, or
untracked but unignored. The checker scans both tracked and
untracked-unignored candidates.

Every checker fails when a denied path is visible. It also fails when
the repository's `.gitignore` lacks any required protection. A
repository extends this shared core with its own private-data paths:

```text
atlas/  data/  state/  intake/  graph/  plans/
*.sqlite*  *.db*  *.jsonl
.env  .env.*
engine.pin  copies-manifest  delivery-registry
.claude/  .codex/  .agents/
```

A repository that legitimately tracks a file matching a core pattern
must add a narrow, commented allowlist entry in the same change that
adds the file. Broad exceptions are not permitted.

## Fixture provenance

Each repository declares its designated demo-fixture paths in its
checker. Every file under those paths must contain the literal string
`Vera Example`, as defined by [Synthetic persona](persona.md). A
fixture without the marker fails the gate. The marker records invented
fixture provenance; it does not make copied or sanitized real data
public-safe.

## Local adoption

ephemeris already runs this shape through
`scripts/check_public_hygiene.py` and a CI step. The convention
generalizes that precedent. Subsystem adoption is tracked on the
cross-repo checklist in
[selfos#16](https://github.com/jointsome0-lgtm/selfos/issues/16).
