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
    except ModuleNotFoundError as exc:
        raise AdapterError(
            "the exp2res package must be importable (install it or add its "
            "checkout to PYTHONPATH); occurred resolution uses its grammar"
        ) from exc
    return parse_occurred, workspace_zone, Exp2ResError


@dataclass
class Selection:
    """The retro slice of one export: latest snapshot per entry."""

    snapshots: dict[str, dict]
    latest_types: dict[str, str]
    rejected_lines: list[dict]
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
    snapshot may stand in for it. Line numbers are physical (blank lines
    count, matching a text editor); non-retro event types are counted,
    never reported per line.
    """
    snapshots: dict[str, dict] = {}
    latest_types: dict[str, str] = {}
    poisoned: dict[str, None] = {}
    rejected: list[dict] = []
    ignored = 0
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            rejected.append({"line": number, "reason": "line_not_json"})
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            rejected.append({"line": number, "reason": "line_not_event"})
            continue
        if event["type"] not in RETRO_EVENT_TYPES:
            ignored += 1
            continue
        version = event.get("payload_version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != SUPPORTED_PAYLOAD_VERSION
        ):
            retro_uuid = _payload_uuid(event)
            if retro_uuid is None:
                rejected.append(
                    {"line": number, "reason": "unsupported_payload_version"}
                )
            else:
                poisoned.setdefault(retro_uuid)
                snapshots.pop(retro_uuid, None)
                latest_types.pop(retro_uuid, None)
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            rejected.append({"line": number, "reason": "payload_not_object"})
            continue
        retro_uuid = payload.get("retro_uuid")
        if not isinstance(retro_uuid, str) or not retro_uuid:
            rejected.append({"line": number, "reason": "missing_retro_uuid"})
            continue
        if retro_uuid in poisoned:
            continue
        snapshots[retro_uuid] = payload
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
        rejected_lines=rejected,
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
    rejected = selection.rejected_lines + selection.rejected_entries + entry_rejected
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


def configured_private_root() -> Path | None:
    """The exp2res private root, by the docs/instance.md discovery order.

    ``EXP2RES_WORKSPACE`` first, then ``instances.exp2res`` in the user
    config. A config that exists but cannot be read or parsed refuses the
    run rather than silently weakening the output boundary.
    """
    value = os.environ.get(ENV_VAR)
    if value:
        return Path(value)
    if not CONFIG_PATH.is_file():
        return None
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError as exc:
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
    for public_root in _public_roots():
        if resolved.is_relative_to(public_root):
            raise AdapterError(
                f"{role} path {path} resolves inside the public checkout "
                f"{public_root}; a public checkout is never a data source "
                "or destination (docs/instance.md)"
            )


def refuse_unsafe_paths(output: Path, export: Path) -> None:
    """Adapters operate only on private instance paths (AGENTS.md, docs/instance.md).

    Neither the export nor the output may resolve inside a public engine
    checkout the AGENTS.md map names. When an exp2res private root is
    configured (``EXP2RES_WORKSPACE`` or ``instances.exp2res`` in the user
    config), the output must additionally resolve inside it; with no root
    configured — capture is still blocked by design, so only invented-data
    runs exist — the deny set is the whole boundary. The export is never
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
    private_root = configured_private_root()
    if private_root is not None and not resolved_output.is_relative_to(
        private_root.resolve()
    ):
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
    args = parser.parse_args(argv)
    try:
        refuse_unsafe_paths(Path(args.output), Path(args.export_path))
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
        Path(args.output).write_bytes(body)
    except (OSError, UnicodeError) as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
