"""Ephemeris retro/diary export -> Exp2Res §19.1 JSONL adapter (issue #37).

Reads an ephemeris audit-export JSONL file, selects the retro-entry slice
(``retro_entry_*`` events, grouped by ``retro_uuid``) and the diary-entry
slice (``diary_entry_*`` events, grouped by ``diary_uuid``) — latest event
in file order wins, entries whose latest snapshot is archived excluded —
and emits one closed §19.1 activity-domain record per surviving entry,
ready for ``exp2res import ephemeris``:

    python scripts/retro_adapter.py <export.jsonl> --timezone <IANA> -o <out.jsonl>

The adapter is the primary privacy gate of the docs/tags.md route/deny
table: a diary entry whose *any* event snapshot carries ``private: true``
is denied history-wide (once private, never shipped — the one-way latch),
reported content-free by ``diary_uuid`` only, and never written to the
output.

Deterministic only: no model call, no network, no state, and entry text is
data that never alters behaviour. Retro ``occurred`` is resolved from the
owner-typed ``period_raw`` + ``precision`` + ``confidence`` with exp2res's
own grammar (``exp2res.services.time_input.parse_occurred``), so acceptance
is equal by construction; ``period_start``/``period_end`` are
ephemeris-local display derivations and are never read. Diary ``occurred``
resolves the owner-picked ``entry_date`` through the same grammar at
``exact_day`` precision. A value the grammar refuses rejects that record
with a reason — never an approximation.

The run report (stdout, one JSON object) carries counts and per-record
reason codes only, never entry text. The output file is the delivery
payload and the only copy the adapter produces: it must live on a private
instance path (inside the configured exp2res root when one is set; never
inside a public engine checkout), and the owner deletes it once the
import report is confirmed.

Requires the ``exp2res`` package to be importable (installed, or its
checkout on ``PYTHONPATH``). Contracts: exp2res ``spec/19-integration-
contracts.md`` §19.1/§19.4, ephemeris ``docs/retro-spec.md`` (sec33) and
``docs/diary-spec.md`` (sec35), selfos ``docs/tags.md`` (route/deny v1);
field mapping and decisions in ``docs/retro-adapter.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
DIARY_EVENT_TYPES = frozenset(
    (
        "diary_entry_created",
        "diary_entry_updated",
        "diary_entry_archived",
        "diary_entry_unarchived",
    )
)
SUPPORTED_PAYLOAD_VERSION = 1
RECORD_ID_PREFIX = "ephemeris:retro:"
DIARY_RECORD_ID_PREFIX = "ephemeris:diary:"
DIARY_PROJECT = "diary"
DIARY_CONFIDENCE = "high"
ENTRY_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
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
    """The retro and diary slices of one export: latest snapshot per entry."""

    snapshots: dict[str, dict]
    latest_types: dict[str, str]
    rejected_entries: list[dict]
    ignored_lines: int
    diary_snapshots: dict[str, dict]
    diary_latest_types: dict[str, str]
    diary_private: dict[str, None]
    diary_privacy_unreadable: frozenset[str]


def _payload_uuid(event: dict, key: str) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def select_snapshots(text: str) -> Selection:
    """Group lifecycle lines by entry uuid per slice; latest line wins.

    ``retro_entry_*`` events group by ``retro_uuid``, ``diary_entry_*``
    events by ``diary_uuid``; the slices never mix. An identity that
    carries any event with an unsupported payload version is rejected
    whole: its latest state is unreadable, so no earlier snapshot may
    stand in for it. A lifecycle event with no attributable uuid refuses
    the whole run for the same reason, with no identity to pin the damage
    to — and so does any line that is not a JSON event object at all,
    because a corrupted line cannot be proven outside both slices and
    could hide an edit or archive, and any unknown ``retro_entry_*`` or
    ``diary_entry_*`` type, because a newer lifecycle event could change
    the selected state of any entry. Line numbers are physical (blank
    lines count, matching a text editor); event types outside both slices
    are counted, never reported per line.

    The diary ``private`` latch is history-wide and read before the
    version gate: any diary event whose payload says ``private: true`` —
    a poisoned version included, because denying is always the safe
    direction — latches its identity as denied. A valid diary event whose
    ``private`` value is not a JSON boolean marks the identity's privacy
    state unreadable, and an entry whose privacy cannot be read is never
    shipped.
    """
    snapshots: dict[str, dict] = {}
    latest_types: dict[str, str] = {}
    poisoned: dict[str, None] = {}
    diary_snapshots: dict[str, dict] = {}
    diary_latest_types: dict[str, str] = {}
    diary_poisoned: dict[str, None] = {}
    diary_private: dict[str, None] = {}
    diary_unreadable: set[str] = set()
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
                "non-diary and could hide an edit or archive of some entry"
            ) from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise AdapterError(
                f"line {number} is not an event object; refusing the whole "
                "export, because an unreadable line cannot be proven "
                "non-retro and non-diary and could hide an edit or archive "
                "of some entry"
            )
        event_type = event["type"]
        if event_type in RETRO_EVENT_TYPES:
            slice_name, uuid_key = "retro", "retro_uuid"
            slice_snapshots, slice_types, slice_poisoned = (
                snapshots,
                latest_types,
                poisoned,
            )
        elif event_type in DIARY_EVENT_TYPES:
            slice_name, uuid_key = "diary", "diary_uuid"
            slice_snapshots, slice_types, slice_poisoned = (
                diary_snapshots,
                diary_latest_types,
                diary_poisoned,
            )
        else:
            if event_type.startswith(("retro_entry_", "diary_entry_")):
                family = (
                    "retro" if event_type.startswith("retro_entry_") else "diary"
                )
                raise AdapterError(
                    f"line {number}: unknown {family} lifecycle event type "
                    f"{event_type!r}; the export speaks a newer {family} "
                    "contract than this adapter supports, and an unknown "
                    "lifecycle event could change the selected state of "
                    "any entry"
                )
            ignored += 1
            continue
        entry_uuid = _payload_uuid(event, uuid_key)
        if entry_uuid is None:
            raise AdapterError(
                f"line {number}: a {slice_name} event carries no attributable "
                f"{uuid_key}; refusing the whole export, because an "
                "unattributable lifecycle event could hide an edit or "
                "archive of some entry"
            )
        if slice_name == "diary" and event["payload"].get("private") is True:
            diary_private.setdefault(entry_uuid)
        version = event.get("payload_version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != SUPPORTED_PAYLOAD_VERSION
        ):
            slice_poisoned.setdefault(entry_uuid)
            slice_snapshots.pop(entry_uuid, None)
            slice_types.pop(entry_uuid, None)
            continue
        if entry_uuid in slice_poisoned:
            continue
        if slice_name == "diary" and not isinstance(
            event["payload"].get("private"), bool
        ):
            diary_unreadable.add(entry_uuid)
        slice_snapshots[entry_uuid] = event["payload"]
        slice_types[entry_uuid] = event_type
    rejected_entries = [
        {
            "retro_uuid": retro_uuid,
            "record_id": RECORD_ID_PREFIX + retro_uuid,
            "reason": "unsupported_payload_version",
        }
        for retro_uuid in poisoned
    ] + [
        {
            "diary_uuid": diary_uuid,
            "record_id": DIARY_RECORD_ID_PREFIX + diary_uuid,
            "reason": "unsupported_payload_version",
        }
        for diary_uuid in diary_poisoned
        if diary_uuid not in diary_private
    ]
    return Selection(
        snapshots=snapshots,
        latest_types=latest_types,
        rejected_entries=rejected_entries,
        ignored_lines=ignored,
        diary_snapshots=diary_snapshots,
        diary_latest_types=diary_latest_types,
        diary_private=diary_private,
        diary_privacy_unreadable=frozenset(diary_unreadable),
    )


def build_records(
    selection: Selection, timezone_name: str
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Map surviving snapshots to §19.1 records; report the rest by reason.

    Output order is deterministic: every retro record first, then every
    diary record, each slice in first-seen export order — a rerun over an
    unchanged export is byte-identical.
    """
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
    denied: list[dict] = [
        {"diary_uuid": diary_uuid, "reason": "private"}
        for diary_uuid in selection.diary_private
    ]
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
    for diary_uuid, snapshot in selection.diary_snapshots.items():
        if diary_uuid in selection.diary_private:
            continue
        identity = {
            "diary_uuid": diary_uuid,
            "record_id": DIARY_RECORD_ID_PREFIX + diary_uuid,
        }
        if diary_uuid in selection.diary_privacy_unreadable:
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        archived_at = snapshot.get("archived_at")
        if archived_at is not None and not isinstance(archived_at, str):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        archived = archived_at is not None
        event_type = selection.diary_latest_types[diary_uuid]
        if (event_type == "diary_entry_archived" and not archived) or (
            event_type == "diary_entry_unarchived" and archived
        ):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        if archived:
            skipped.append({**identity, "reason": "archived"})
            continue
        text = snapshot.get("text")
        entry_date = snapshot.get("entry_date")
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(entry_date, str)
            or not ENTRY_DATE_PATTERN.fullmatch(entry_date)
        ):
            rejected.append({**identity, "reason": "invalid_snapshot"})
            continue
        try:
            occurred = parse_occurred(
                period=entry_date,
                precision="exact_day",
                confidence=DIARY_CONFIDENCE,
                timezone_name=timezone_name,
            )
        except Exp2ResError as exc:
            code = getattr(exc, "diagnostic_class", "invalid_input")
            rejected.append(
                {**identity, "reason": f"entry_date_unparsable:{code}"}
            )
            continue
        record = {
            "source": "ephemeris",
            "record_id": identity["record_id"],
            "domain": "activity",
            "occurred": occurred.model_dump(mode="json"),
            "project": DIARY_PROJECT,
            "text": text,
        }
        try:
            json.dumps(record, ensure_ascii=False).encode("utf-8")
        except UnicodeError:
            rejected.append({**identity, "reason": "text_not_encodable"})
            continue
        records.append(record)
        accepted.append(identity)
    return records, accepted, skipped, rejected, denied


def run(text: str, timezone_name: str) -> tuple[list[dict], dict]:
    """Whole adapter pass: §19.1 records plus the machine-readable report."""
    selection = select_snapshots(text)
    records, accepted, skipped, entry_rejected, denied = build_records(
        selection, timezone_name
    )
    rejected = selection.rejected_entries + entry_rejected
    report = {
        "counts": {
            "accepted": len(accepted),
            "skipped": len(skipped),
            "rejected": len(rejected),
            "denied": len(denied),
            "ignored_lines": selection.ignored_lines,
        },
        "accepted": accepted,
        "skipped": skipped,
        "rejected": rejected,
        "denied": denied,
    }
    return records, report


def configured_private_root(explicit: str | None = None) -> Path | None:
    """The exp2res private root, by the docs/instance.md discovery order.

    The explicit ``--instance`` flag first, then ``EXP2RES_WORKSPACE``,
    then ``instances.exp2res`` in the user config. Every source is
    ``~``-expanded, matching doctor's discovery. A config that exists
    but cannot be read or parsed refuses the run rather than silently
    weakening the output boundary.
    """
    if explicit:
        return Path(explicit).expanduser()
    value = os.environ.get(ENV_VAR)
    if value:
        return Path(value).expanduser()
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
    return Path(configured).expanduser()


def _public_roots() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    roots = [root]
    for name in PUBLIC_SIBLINGS:
        sibling = root.parent / name
        if sibling.is_dir():
            roots.append(sibling.resolve())
    return roots


def _refuse_inside_public(path: Path, resolved: Path, role: str) -> None:
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
) -> tuple[int, int]:
    """Adapters operate only on private instance paths (AGENTS.md, docs/instance.md).

    Returns ``(export_fd, dir_fd)``: an open descriptor of the export
    and one of the output's directory. Every check runs against the
    descriptors' actual identities, and the caller reads the export and
    writes the payload through these same descriptors — so neither a
    source retargeted nor a parent component swapped between check and
    use can substitute a path. The caller owns closing both.

    Neither the export nor the output may lie inside a public engine
    checkout the AGENTS.md map names. When an exp2res private root is
    configured (``--instance``, then ``EXP2RES_WORKSPACE``, then
    ``instances.exp2res`` in the user config), it must be an existing
    directory and the output must sit strictly beneath it — as both the
    written name and its resolved target, since the atomic replace lands
    the payload at the name, not through a symlink. The root itself is
    refused when it lies inside a public checkout — an explicit flag
    does not bypass that guard (docs/instance.md). A leftover staging
    file (``<output>.tmp``) from an interrupted run is identified and
    refused, never silently deleted or overwritten. With no root configured the run is
    refused unless ``allow_unconfigured`` explicitly marks it an
    invented-data run to a private destination. The export is never
    required to sit inside an ephemeris root: ephemeris delivers exports
    as browser downloads. The export is also refused as the output
    destination, through symlink and hard-link aliases alike, so a typo
    cannot truncate the source.
    """
    _refuse_inside_public(export, export.resolve(), "export")
    try:
        export_fd = os.open(export, os.O_RDONLY)
    except OSError as exc:
        raise AdapterError(f"cannot open export {export}: {exc}") from exc
    try:
        export_stat = os.fstat(export_fd)
        try:
            pinned_export = Path(os.readlink(f"/proc/self/fd/{export_fd}"))
        except OSError:
            pinned_export = export.resolve()
            if not os.path.samestat(export_stat, os.stat(pinned_export)):
                raise AdapterError(
                    f"the export {export} changed while being verified; "
                    "refusing to read"
                ) from None
        _refuse_inside_public(export, pinned_export, "export")
        resolved_output = output.resolve()
        same_file = resolved_output == pinned_export
        if not same_file and output.exists():
            same_file = os.path.samestat(export_stat, os.stat(output))
        if same_file:
            raise AdapterError(
                f"output path {output} is the export itself; refusing to "
                "overwrite the source"
            )
        dir_fd = _pin_output_directory(
            output,
            resolved_output,
            instance=instance,
            allow_unconfigured=allow_unconfigured,
        )
    except BaseException:
        os.close(export_fd)
        raise
    return export_fd, dir_fd


def _pin_output_directory(
    output: Path,
    resolved_output: Path,
    *,
    instance: str | None,
    allow_unconfigured: bool,
) -> int:
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
        resolved_root = private_root.resolve()
        if not resolved_root.is_dir():
            raise AdapterError(
                f"configured exp2res private root {private_root} is not an "
                "existing directory; a root that names a file would itself "
                "be overwritten by the payload (docs/instance.md)"
            )
    parent = Path(os.path.abspath(output)).parent
    try:
        dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise AdapterError(
            f"cannot open the output directory {parent}: {exc}"
        ) from exc
    try:
        try:
            pinned_parent = Path(os.readlink(f"/proc/self/fd/{dir_fd}"))
        except OSError:
            pinned_parent = parent.resolve()
            if not os.path.samestat(os.fstat(dir_fd), os.stat(pinned_parent)):
                raise AdapterError(
                    f"the output directory {parent} changed while being "
                    "verified; refusing to write"
                ) from None
        pinned_output = pinned_parent / output.name
        if private_root is not None:
            resolved_root = private_root.resolve()
            if (
                resolved_output == resolved_root
                or not resolved_output.is_relative_to(resolved_root)
                or not pinned_parent.is_relative_to(resolved_root)
            ):
                raise AdapterError(
                    f"output path {output} is not strictly beneath the "
                    f"configured exp2res private root {private_root}, as "
                    "both the written name and its resolved target must "
                    "be; write the payload there (docs/instance.md)"
                )
        _refuse_inside_public(output, resolved_output, "output")
        _refuse_inside_public(output, pinned_output, "output")
        staging_name = output.name + ".tmp"
        try:
            os.stat(staging_name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise AdapterError(
                f"cannot probe the staging path "
                f"{pinned_parent / staging_name}: {exc}"
            ) from exc
        else:
            raise AdapterError(
                f"staging file {pinned_parent / staging_name} already "
                "exists — likely a payload left by an interrupted run; "
                "inspect and delete it before rerunning"
            )
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map an ephemeris JSONL audit export's retro and diary entries "
            "to exp2res §19.1 activity records."
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
        export_fd, dir_fd = refuse_unsafe_paths(
            Path(args.output),
            Path(args.export_path),
            instance=args.instance,
            allow_unconfigured=args.allow_unconfigured,
        )
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            with os.fdopen(export_fd, "rb") as handle:
                text = handle.read().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            print(f"error: cannot read export: {exc}", file=sys.stderr)
            return 2
        try:
            records, report = run(text, args.timezone)
        except AdapterError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        out_name = Path(args.output).name
        staging_name = out_name + ".tmp"
        try:
            body = "".join(
                json.dumps(r, ensure_ascii=False) + "\n" for r in records
            ).encode("utf-8")
            fd = os.open(
                staging_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dir_fd,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(body)
                os.replace(
                    staging_name,
                    out_name,
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                )
            except BaseException:
                try:
                    os.unlink(staging_name, dir_fd=dir_fd)
                except OSError:
                    pass
                raise
        except (OSError, UnicodeError) as exc:
            print(f"error: cannot write output: {exc}", file=sys.stderr)
            return 2
    finally:
        os.close(dir_fd)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
