# Model runtime override — example

Copy into `~/.claude/CLAUDE.md` and keep dated; no credentials belong in this
file.

This is a dated snapshot of the owner's live override, taken 2026-08-02, kept
here so a fresh machine can bootstrap the runtime layer that
[CLAUDE.md](../CLAUDE.md) deliberately does not name. It is an example, not
canon: the live file wins, and every name, number, and date below goes stale by
design. The override's own last-verified line is part of the snapshot.

---

# Model runtime override

Dated local override for the capability-routing canon in each repo's
CLAUDE.md ("Picking models for workflows and subagents"). Keep dated;
no credentials belong in this file.

Last verified: 2026-07-29 (owner, manual).

Effort ceiling (owner, 2026-07-30): low/medium/high is the whole working
scale for every project and every model; xhigh/max only for live novel
design discussion with the owner — never for build, review, or
verification runs.

## Roster (cost = what I actually pay)

Rankings on a 0–10 scale, higher = better. Intelligence is how hard a problem you can
hand the model unsupervised. Taste covers UI/UX, code quality, API
design, and copy.

| model       | cost | intelligence | taste |
|-------------|------|--------------|-------|
| gpt-5.6     | 9    | 8.9          | 7     |
| sol-pro-web | 9    | 9            | 7     |
| opus-5      | 3    | 8.5          | 8.5   |
| fable-5     | 2    | 9            | 9     |

sol-pro-web = gpt-5.6-sol served in `reasoning.mode: pro`; stronger
than our CLI sol at xhigh, still behind fable-5 on deep analysis.
Ratings = bench-based guess (2026-07-29), adjust from experience.
sonnet-5 dropped 2026-07-29 (only role was the codex relay wrapper,
never used).

Capability floor: never Haiku-class.
Fallback order (owner decision 2026-07-24): fable-5 → opus-5 → gpt-5.6.
Never below the floor. opus-4.8 is retired from the roster and is still
not a fallback; opus-5 replaces it and, unlike 4.8, is a legitimate
fallback. Role inversion when gpt-5.6 holds the pen: independent review
of that work goes to a Claude session — fable-5, or opus-5 when Fable
limits are out, so review independence no longer waits on a limit reset.
Degrading never weakens review independence.

The top two split by shape, not rank: fable-5 is stronger on
architecture and interconnections; gpt-5.6 on driving a goal to
completion and finding defects. Pick by task shape, not the raw
intelligence number.

## Role assignments

- bulk/mechanical (clear-spec implementation, data analysis,
  migrations): gpt-5.6 — effectively free
- architecture & interconnection review: fable-5
- independent critic / defect finding: gpt-5.6
- user-facing taste ≥ 7: fable-5 / opus-5
- reviews of plans/implementations: fable-5 or opus-5, optionally
  gpt-5.6 as an extra independent perspective
- turning-point whole-picture passes (spec consistency, plan
  convergence, experiment adjudication): sol-pro-web — best available
  defect-finder; architecture stays with fable-5
- security reviewer (external, independent vendor): Codex CLI

## Harness mechanics

- gpt-5.6 is only reachable through the Codex CLI: `codex exec` /
  `codex review`; `~/.codex/config.toml` defaults to `gpt-5.6-sol` at
  xhigh effort.
- Run `codex exec` directly via Bash with a self-contained prompt you
  wrote: `-s read-only` for pure reading/analysis; `-s
  workspace-write` when it must edit files OR run tests/builds — test
  runs write caches and temp state, so read-only makes them fail or
  stall (this produced a false "verify.py hangs" finding once).
- Effort sizing (2026-07-19, capped by the 2026-07-30 ceiling above):
  every run passes `-c model_reasoning_effort=...` explicitly, because
  the config default sits above the ceiling. Full adversarial/design
  passes and scoped real work — implementing from a clear spec,
  diagnosing a named bug, reviewing a medium diff, prep/measurement
  tasks — get `high`. Routine bounded checks — verifying a small diff,
  fidelity/gate checks, health checks — get medium (trivial/relay:
  low); xhigh on a 42-line diff wastes ~10× wall-time for no extra
  findings.
- Parallel codex execs are fragile here (S8 2026-07-16: a run hung
  ~35 min behind parallel sessions) — prefer one lighter run over a
  fan-out; whole-diff consistency doesn't decompose per-finding anyway.
- Health check: `codex --version` plus a trivial exec.
- sol-pro-web is manual-only: ChatGPT web, no filesystem/tests/commits;
  reads public repos via the GitHub connector and can comment on issues
  (findings = ONE aggregate comment on the master issue). Nothing
  private goes there. Reserve for turning points, not per-PR review.
- Claude models (opus-5, fable-5) run via the Agent/Workflow model
  parameter.
