# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first: it is the shared agent contract for this
repository (the subsystem map, design intent, task routing, the integration
model and its data boundaries, current stage, public-data policy) and applies
to Claude Code in full. The public shape of the ecosystem lives in
[README.md](README.md).

## Security reviews go to an independent external reviewer

Claude-only rule — in AGENTS.md it would just tell the reviewer to
delegate to itself. Ecosystem-wide; the full version lives in
ephemeris's CLAUDE.md.

Adversarial security / threat-model reviews — of cross-system
contracts, integration surfaces, or any subsystem code — are
**delegated to an independent external reviewer**: a different
vendor's agent CLI driven by a self-contained prompt (current tool
and model: dated local override). Never run by Claude in the first
person.

- Durable reason, so nobody "fixes" this later: (1) independence —
  an adversarial pass from a second, unaffiliated toolchain is
  evidence the author's own harness cannot produce about itself;
  (2) continuity — assistant-side dual-use safeguards can reroute
  or switch models mid-task on security framing, silently dropping
  the thread. Dated instance (2026-07): Fable's safeguards are
  documented (anthropic.com, Fable 5 announcement) to fall back to
  Claude Opus 4.8 on cybersecurity framing; the Codex CLI is
  unaffected.
- Claude's role is the correctness half (consistency, invariants,
  plan alignment) and converging the external reviewer's findings
  with its own.
- Routing rule, not a license to ignore security: a concern noticed
  in passing still gets surfaced plainly — the adversarial probing
  is what goes to the external reviewer.

## Picking models for workflows and subagents

Route by capability, not by name. The current model roster — names,
rankings, harness mechanics, effort defaults, fallback order,
last-verified date — lives in the dated local override (user-scope
~/.claude/CLAUDE.md; bootstrap example:
docs/model-override.example.md). This section stays valid when
every alias changes.

Capability axes (match task shape to the roster's current best fit):

- bulk/mechanical work with a clear spec;
- architecture & interconnection review;
- independent defect finding (critic);
- user-facing taste: UI, copy, API design;
- adversarial security review → independent external reviewer (see
  section above);
- unsupervised autonomy needs intelligence headroom, not just skill
  match.

Standing rules, model-independent:

- Judge the output, not the price: escalate without asking when a
  cheaper model's output misses the bar. Intelligence > taste >
  cost for anything that ships; cost is a tie-breaker only.
- Respect the override's capability floor — never delegate below it.
- When a preferred model is unavailable, degrade along the
  override's fallback order — never below the floor, and never by
  weakening review independence or widening data access.
- Delegated claims (file:line, "tests are green", "done") are
  unverified until checked against artifacts.
- Write delegate prompts yourself: outcome-first, self-contained.
  Relay wrappers pass them verbatim — never reformulate; no
  intermediary layers.
- On long solo work, call an independent critic at checkpoints, not
  only at the end.
- Review output states the selected runtime and the override's date.
