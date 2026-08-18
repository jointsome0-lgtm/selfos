# Operational scripts

Day-to-day maintenance commands in `scripts/`. Each script's header comment
is its full documentation; this page is just the index.

| Script | What it does |
|---|---|
| `sync.py` | Pin sync/status for sibling subsystem checkouts against `pins.toml` (decided in [#1](https://github.com/jointsome0-lgtm/selfos/issues/1)). |
| `doctor.py` | Staged `selfos doctor` — repository, instance, ephemeris, Atlas, and agent-runtime health checks ([#7](https://github.com/jointsome0-lgtm/selfos/issues/7)). |
| `check_public_hygiene.py` | Public-layer hygiene gate; contract in [hygiene.md](hygiene.md). Runs in pre-commit + CI. |
| `retro_adapter.py` | Ephemeris retro/diary export → Exp2Res §19.1 JSONL payload; mapping and decisions in [retro-adapter.md](retro-adapter.md) ([#37](https://github.com/jointsome0-lgtm/selfos/issues/37)). |
| `backup-issues.sh` | Snapshots issues + comments of every non-archived repo on the account to `~/backups/issues/<repo>/` (local only, never committed). Design history lives in the trackers; if GitHub is unreachable, read `latest.json` there. Intended to run daily. |
