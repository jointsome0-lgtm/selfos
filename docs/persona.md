# Synthetic persona

Vera Example is the one ecosystem-wide synthetic persona for public
demo fixtures. Every public demo fixture is authored by this named
identity; fixtures are invented from it, never sanitized from real
data. Decided 2026-07-14
([selfos#12](https://github.com/jointsome0-lgtm/selfos/issues/12),
following the resolution of
[selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3)).

The identity must remain obviously fictional and uniquely greppable.
The literal string `Vera Example` is the provenance marker that CI
hygiene gates use to recognize demo fixtures. Self-documenting template
shape takes priority over a pretty name: the identity itself says that
the material is an example.

## Canonical fact sheet

This fact sheet is deliberately small. Every fact here constrains every
future fixture.

- **Name:** Vera Example.
- **Fixture marker:** the exact, case-sensitive string `Vera Example`.
- **Date of birth:** 2000-01-01.
- **Residence:** Exampleburg.
- **Email:** `vera@example.com`; `example.com` is the RFC 2606 example
  domain.
- **Occupation:** technical writer.
- **Learning interests:** Kubernetes operations, human anatomy,
  strength-training basics, and backend distributed systems in Python.
- **GitHub identity:** login `vera-example`; her one public repository
  is `vera-example/k8s-playbook` (fictional, like every entity here).
- **Personal projects:**
  - *K8s Playbook* — a public documentation project: runbook-style
    notes on Kubernetes operations, kept in
    `vera-example/k8s-playbook`.
  - *Strength Basics* — a personal beginner strength-training routine
    with anatomy notes; no repository, tracked only in her own logs.
- **Tone:** mundane and small; this is demo data, not fiction writing.

## One identity

This page is the single source for Vera Example's core identity.
Identities must not fork. Demo fixtures in every public engine repository
draw from this page: Atlas example plans, ephemeris demo entries, and
exp2res demo evidence. The exp2res fixture work will consume the same
persona under
[exp2res#78](https://github.com/jointsome0-lgtm/exp2res/issues/78).

A fixture that needs a new biographical fact adds it here first, then
uses it. Fixtures may extend the identity only through this page and
must never contradict it. Local additions in a fixture or subsystem
are not another source of identity.

Renaming is cheap only until fixtures have been generated across the
engine repositories. After that, `Vera Example` is load-bearing as the
ecosystem-wide grep marker.

## Fixture placement

The literal marker `Vera Example` must appear verbatim in every demo
fixture file. It may be an author field, a comment, or fixture content,
as the format permits.

Fixtures live only in the designated fixture paths of the public engine
repositories. No fixture contains real personal data, whether copied,
redacted, or sanitized. Sanitization is not a privacy boundary; public
fixtures are invented instead, as decided in
[selfos#3](https://github.com/jointsome0-lgtm/selfos/issues/3).
