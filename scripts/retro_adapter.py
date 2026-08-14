"""Ephemeris retro export -> Exp2Res §19.1 JSONL adapter (issue #37).

Reads an ephemeris audit-export JSONL file, selects the retro-entry slice
(``retro_entry_*`` events, grouped by ``retro_uuid``, latest event in file
order wins, entries whose latest snapshot is archived excluded), and emits
one closed §19.1 activity-domain record per surviving entry, ready for
``exp2res import ephemeris``:

    python scripts/retro_adapter.py <export.jsonl> --timezone <IANA> -o <out.jsonl>

Deterministic only: no model call, no network, no state, and entry text is
data that never alters behaviour. ``occurred`` is resolved from the
owner-typed ``period_raw`` + ``precision`` + ``confidence`` with exp2res's
own grammar (``exp2res.services.time_input.parse_occurred``), so acceptance
is equal by construction; ``period_start``/``period_end`` are
ephemeris-local display derivations and are never read. A period the
grammar refuses rejects that record with a reason — never an approximation.

The run report (stdout, one JSON object) carries counts and per-record
reason codes only, never entry text. The output file is the delivery
payload and the only copy the adapter produces: it must live on a private
instance path (inside the configured exp2res root when one is set; never
inside a public engine checkout), and the owner deletes it once the
import report is confirmed.

Requires the ``exp2res`` package to be importable (installed, or its
checkout on ``PYTHONPATH``). Contracts: exp2res ``spec/19-integration-
contracts.md`` §19.1/§19.4, ephemeris ``docs/retro-spec.md`` (sec33);
field mapping and decisions in ``docs/retro-adapter.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


RETRO_EVENT_TYPES = frozenset(
    (
        "retro_entry_created",
        "retro_entry_updated",
        "retro_entry_archived",
        "retro_entry_unarchived",
    )
)
SUPPORTED_PAYLOAD_VERSION = 1
RECORD_ID_PREFIX = "ephemeris:retro:"
PUBLIC_SIBLINGS = ("ephemeris", "atlas", "exp2res", "tollgate", "selfos-skills")
ENV_VAR = "EXP2RES_WORKSPACE"
CONFIG_PATH = Path.home() / ".config" / "selfos" / "config.toml"


class AdapterError(RuntimeError):
    """The whole run is refused; individual records are reported instead."""


def _time_grammar():
    try:
        from exp2res.errors import Exp2ResError
        from exp2res.services.time_input import parse_occurred, workspace_zone
    except ImportError as exc:
        raise AdapterError(
            "the exp2res package must be importable and expose its time "
            "grammar (install a compatible revision or add its checkout to "
            "PYTHONPATH); occurred resolution uses that grammar"
        ) from exc
    return parse_occurred, workspace_zone, Exp2ResError


@dataclass
class Selection:
    """The retro slice of one export: latest snapshot per entry."""

    snapshots: dict[str, dict]
    latest_types: dict[str, str]
    rejected_entries: list[dict]
    ignored_lines: int


def _payload_uuid(event: dict) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    retro_uuid = payload.get("retro_uuid")
    if not isinstance(retro_uuid, str) or not retro_uuid:
        return None
    return retro_uuid


def select_snapshots(text: str) -> Selection:
    """Group ``retro_entry_*`` lines by ``retro_uuid``; latest line wins.

    An identity that carries any event with an unsupported payload version
    is rejected whole: its latest state is unreadable, so no earlier
    snapshot may stand in for it. A retro event with no attributable
    ``retro_uuid`` refuses the whole run for the same reason, with no
    identity to pin the damage to — and so does any line that is not a
    JSON event object at all, because a corrupted line cannot be proven
    non-retro and could hide an edit or archive. Line numbers are physical
    (blank lines count, matching a text editor); non-retro event types are
    counted, never reported per line.
    """
    snapshots: dict[str, dict] = {}
    latest_types: dict[str, str] = {}
    poisoned: dict[str, None] = {}
    ignored = 0
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise AdapterError(
                f"line {number} is not JSON; refusing the whole export, "
                "because a corrupted line cannot be proven non-retro and "
                "could hide an edit or archive of some entry"
            ) from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise AdapterError(
                f"line {number} is not an event object; refusing the whole "
                "export, because an unreadable line cannot be proven "
                "non-retro and could hide an edit or archive of some entry"
            )
        if event["type"] not in RETRO_EVENT_TYPES:
            ignored += 1
            continue
        retro_uuid = _payload_uuid(event)
        if retro_uuid is None:
            raise AdapterError(
                f"line {number}: a retro event carries no attributable "
                "retro_uuid; refusing the whole export, because an "
                "unattributable lifecycle event could hide an edit or "
                "archive of some entry"
            )
        version = event.get("payload_version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != SUPPORTED_PAYLOAD_VERSION
        ):
            poisoned.setdefault(retro_uuid)
            snapshots.pop(retro_uuid, None)
            latest_types.pop(retro_uuid, None)
            continue
        if retro_uuid in poisoned:
            continue
        snapshots[retro_uuid] = event["payload"]
        latest_types[retro_uuid] = event["type"]
    rejected_entries = [
        {
            "retro_uuid": retro_uuid,
            "record_id": RECORD_ID_PREFIX + retro_uuid,
            "reason": "unsupported_payload_version",
        }
        for retro_uuid in poisoned
    ]
    return Selection(
        snapshots=snapshots,
        latest_types=latest_types,
        rejected_entries=rejected_entries,
        ignored_lines=ignored,
    )


def build_records(
    selection: Selection, timezone_name: str
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Map surviving snapshots to §19.1 records; report the rest by reason."""
    parse_occurred, workspace_zone, Exp2ResError = _time_grammar()
    try:
        workspace_zone(timezone_name)
    except Exp2ResError as exc:
        raise AdapterError(
            f"--timezone must be a usable IANA zone, got {timezone_name!r}"
        ) from exc

    records: list[dict] = []
    accepted: list[dict] = []
    skipped: list[dict] = []
    rejected: list[dict] = []
    for retro_uuid, snapshot in selection.snapshots.items():
        identity = {
            "retro_uuid": retro_uuid,
            "record_id": RECORD_ID_PREFIX + retro_uuid,
        }
        archived_at = snapshot.get("archived_at")
        if archived_at is not None and not isinstance(archived_at, str):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        archived = archived_at is not None
        event_type = selection.latest_types[retro_uuid]
        if (event_type == "retro_entry_archived" and not archived) or (
            event_type == "retro_entry_unarchived" and archived
        ):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        if archived:
            skipped.append({**identity, "reason": "archived"})
            continue
        project = snapshot.get("project")
        if project is not None and not isinstance(project, str):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        if project is None or not project.strip():
            skipped.append({**identity, "reason": "no_project"})
            continue
        text = snapshot.get("text")
        period = snapshot.get("period_raw")
        precision = snapshot.get("precision")
        confidence = snapshot.get("confidence")
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(precision, str)
            or not isinstance(confidence, str)
            or not (period is None or isinstance(period, str))
        ):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        try:
            occurred = parse_occurred(
                period=period,
                precision=precision,
                confidence=confidence,
                timezone_name=timezone_name,
            )
        except Exp2ResError as exc:
            code = getattr(exc, "diagnostic_class", "invalid_input")
            rejected.append({**identity, "reason": f"period_unparsable:{code}"})
            continue
        record = {
            "source": "ephemeris",
            "record_id": identity["record_id"],
            "domain": "activity",
            "occurred": occurred.model_dump(mode="json"),
            "project": project,
            "text": text,
        }
        try:
            json.dumps(record, ensure_ascii=False).encode("utf-8")
        except UnicodeError:
            rejected.append({**identity, "reason": "text_not_encodable"})
            continue
        records.append(record)
        accepted.append(identity)
    return records, accepted, skipped, rejected


def run(text: str, timezone_name: str) -> tuple[list[dict], dict]:
    """Whole adapter pass: §19.1 records plus the machine-readable report."""
    selection = select_snapshots(text)
    records, accepted, skipped, entry_rejected = build_records(
        selection, timezone_name
    )
    rejected = selection.rejected_entries + entry_rejected
    report = {
        "counts": {
            "accepted": len(accepted),
            "skipped": len(skipped),
            "rejected": len(rejected),
            "ignored_lines": selection.ignored_lines,
        },
        "accepted": accepted,
        "skipped": skipped,
        "rejected": rejected,
    }
    return records, report


def configured_private_root(explicit: str | None = None) -> Path | None:
    """The exp2res private root, by the docs/instance.md discovery order.

    The explicit ``--instance`` flag first, then ``EXP2RES_WORKSPACE``,
    then ``instances.exp2res`` in the user config. A config that exists
    but cannot be read or parsed refuses the run rather than silently
    weakening the output boundary.
    """
    if explicit:
        return Path(explicit)
    value = os.environ.get(ENV_VAR)
    if value:
        return Path(value)
    if not CONFIG_PATH.is_file():
        return None
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError as exc:
            raise AdapterError(
                f"{CONFIG_PATH} exists but no TOML parser is available; "
                "run under Python 3.11+ or install tomli"
            ) from exc
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise AdapterError(f"cannot read {CONFIG_PATH}: {exc}") from exc
    instances = data.get("instances")
    if not isinstance(instances, dict):
        return None
    configured = instances.get("exp2res")
    if not isinstance(configured, str) or not configured:
        return None
    return Path(configured)


def _public_roots() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    roots = [root]
    for name in PUBLIC_SIBLINGS:
        sibling = root.parent / name
        if sibling.is_dir():
            roots.append(sibling.resolve())
    return roots


def _refuse_inside_public(path: Path, resolved: Path, role: str) -> None:
    # Both representations are checked: the symlink-resolved target and
    # the pathname as given, so a payload is never reachable through a
    # name inside a public checkout even when its target is private.
    lexical = Path(os.path.abspath(path))
    for public_root in _public_roots():
        for candidate in (lexical, resolved):
            if candidate.is_relative_to(public_root):
                raise AdapterError(
                    f"{role} path {path} lies inside the public checkout "
                    f"{public_root}; a public checkout is never a data "
                    "source or destination (docs/instance.md)"
                )


def refuse_unsafe_paths(
    output: Path,
    export: Path,
    *,
    instance: str | None = None,
    allow_unconfigured: bool = False,
) -> None:
    """Adapters operate only on private instance paths (AGENTS.md, docs/instance.md).

    Neither the export nor the output may lie inside a public engine
    checkout the AGENTS.md map names. When an exp2res private root is
    configured (``--instance``, then ``EXP2RES_WORKSPACE``, then
    ``instances.exp2res`` in the user config), the output must
    additionally resolve inside it, and the root itself is refused when
    it lies inside a public checkout — an explicit flag does not bypass
    that guard (docs/instance.md). With no root configured the run is
    refused unless ``allow_unconfigured`` explicitly marks it an
    invented-data run to a private destination. The export is never
    required to sit inside an ephemeris root: ephemeris delivers exports
    as browser downloads. The export is also refused as the output
    destination, through symlink and hard-link aliases alike, so a typo
    cannot truncate the source.
    """
    resolved_output = output.resolve()
    resolved_export = export.resolve()
    same_file = resolved_output == resolved_export
    if not same_file and output.exists() and export.exists():
        same_file = os.path.samefile(output, export)
    if same_file:
        raise AdapterError(
            f"output path {output} is the export itself; refusing to "
            "overwrite the source"
        )
    _refuse_inside_public(export, resolved_export, "export")
    private_root = configured_private_root(instance)
    if private_root is None:
        if not allow_unconfigured:
            raise AdapterError(
                "no private instance configured: pass --instance PATH, "
                "set EXP2RES_WORKSPACE, or set instances.exp2res in "
                "~/.config/selfos/config.toml; a public checkout is never "
                "a data destination (selfos docs/instance.md) — or pass "
                "--allow-unconfigured only for an invented-data run to a "
                "private destination"
            )
    else:
        _refuse_inside_public(
            private_root, private_root.resolve(), "configured private root"
        )
        if not resolved_output.is_relative_to(private_root.resolve()):
            raise AdapterError(
                f"output path {output} is outside the configured exp2res "
                f"private root {private_root}; write the payload there "
                "(docs/instance.md)"
            )
    _refuse_inside_public(output, resolved_output, "output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map an ephemeris JSONL audit export's retro entries to "
            "exp2res §19.1 activity records."
        )
    )
    parser.add_argument("export_path", help="ephemeris JSONL audit export")
    parser.add_argument(
        "--timezone",
        required=True,
        help="IANA timezone of the receiving exp2res workspace",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="destination for the §19.1 JSONL payload",
    )
    parser.add_argument(
        "--instance",
        help=(
            "exp2res private root for this run; highest-precedence "
            "discovery source (docs/instance.md), before "
            "EXP2RES_WORKSPACE and the user config"
        ),
    )
    parser.add_argument(
        "--allow-unconfigured",
        action="store_true",
        help=(
            "run without a configured exp2res private root; only for "
            "invented-data runs writing to a private destination"
        ),
    )
    args = parser.parse_args(argv)
    try:
        refuse_unsafe_paths(
            Path(args.output),
            Path(args.export_path),
            instance=args.instance,
            allow_unconfigured=args.allow_unconfigured,
        )
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        text = Path(args.export_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"error: cannot read export: {exc}", file=sys.stderr)
        return 2
    try:
        records, report = run(text, args.timezone)
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        body = "".join(
            json.dumps(r, ensure_ascii=False) + "\n" for r in records
        ).encode("utf-8")
        fd = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.chmod(args.output, 0o600)
    except (OSError, UnicodeError) as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
