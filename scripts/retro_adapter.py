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
payload and the only copy the adapter produces.

Requires the ``exp2res`` package to be importable (installed, or its
checkout on ``PYTHONPATH``). Contracts: exp2res ``spec/19-integration-
contracts.md`` §19.1/§19.4, ephemeris ``docs/retro-spec.md`` (sec33);
field mapping and decisions in ``docs/retro-adapter.md``.
"""

from __future__ import annotations

import argparse
import json
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
    rejected_lines: list[dict]
    ignored_lines: int


def select_snapshots(text: str) -> Selection:
    """Group ``retro_entry_*`` lines by ``retro_uuid``; latest line wins.

    Line numbers are physical (blank lines count, matching a text editor);
    non-retro event types are counted, never reported per line.
    """
    snapshots: dict[str, dict] = {}
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
        if event.get("payload_version") != SUPPORTED_PAYLOAD_VERSION:
            rejected.append(
                {"line": number, "reason": "unsupported_payload_version"}
            )
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            rejected.append({"line": number, "reason": "payload_not_object"})
            continue
        retro_uuid = payload.get("retro_uuid")
        if not isinstance(retro_uuid, str) or not retro_uuid:
            rejected.append({"line": number, "reason": "missing_retro_uuid"})
            continue
        snapshots[retro_uuid] = payload
    return Selection(snapshots=snapshots, rejected_lines=rejected, ignored_lines=ignored)


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
        if snapshot.get("archived_at") is not None:
            skipped.append({**identity, "reason": "archived"})
            continue
        project = snapshot.get("project")
        if not isinstance(project, str) or not project.strip():
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
        records.append(
            {
                "source": "ephemeris",
                "record_id": identity["record_id"],
                "domain": "activity",
                "occurred": occurred.model_dump(mode="json"),
                "project": project,
                "text": text,
            }
        )
        accepted.append(identity)
    return records, accepted, skipped, rejected


def run(text: str, timezone_name: str) -> tuple[list[dict], dict]:
    """Whole adapter pass: §19.1 records plus the machine-readable report."""
    selection = select_snapshots(text)
    records, accepted, skipped, entry_rejected = build_records(
        selection, timezone_name
    )
    rejected = selection.rejected_lines + entry_rejected
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
        text = Path(args.export_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read export: {exc}", file=sys.stderr)
        return 2
    try:
        records, report = run(text, args.timezone)
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    try:
        Path(args.output).write_text(body, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
