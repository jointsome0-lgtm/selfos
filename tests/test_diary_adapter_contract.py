"""Diary-slice end-to-end tests against the real exp2res grammar and model.

These need the exp2res package (issue #37, diary slice: ``occurred``
resolves ``entry_date`` with exp2res's own ``parse_occurred`` and output
validates against its ``EphemerisRecord``); without it the whole module
skips cleanly. All data is invented for the Vera Example persona.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("exp2res")

from scripts import retro_adapter

from test_diary_adapter_select import diary_snapshot
from test_retro_adapter_select import export_line, snapshot as retro_snapshot


TIMEZONE = "Europe/Berlin"


def run(*lines: str) -> tuple[list[dict], dict]:
    return retro_adapter.run("\n".join(lines), TIMEZONE)


def test_non_private_entry_ships_as_a_191_diary_record():
    from exp2res.services.time_input import parse_occurred

    records, report = run(export_line("diary_entry_created", diary_snapshot("uuid-a")))
    assert report["counts"] == {
        "accepted": 1,
        "skipped": 0,
        "rejected": 0,
        "denied": 0,
        "ignored_lines": 0,
    }
    expected = parse_occurred(
        period="2026-05-02",
        precision="exact_day",
        confidence="high",
        timezone_name=TIMEZONE,
    )
    assert records == [
        {
            "source": "ephemeris",
            "record_id": "ephemeris:diary:uuid-a",
            "domain": "activity",
            "occurred": expected.model_dump(mode="json"),
            "project": "diary",
            "text": "Vera Example watered the balcony tomatoes before breakfast.",
        }
    ]


def test_private_creation_is_denied_content_free():
    secret = "Vera Example wrote a thought meant for no other system."
    records, report = run(
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-a", private=True, text=secret),
        )
    )
    assert records == []
    assert report["denied"] == [{"diary_uuid": "uuid-a", "reason": "private"}]
    assert report["counts"]["denied"] == 1
    assert secret not in json.dumps(report)


def test_late_privatization_denies_the_whole_history():
    secret = "Vera Example second thoughts, marked private after the fact."
    records, report = run(
        export_line("diary_entry_created", diary_snapshot("uuid-a", text=secret)),
        export_line(
            "diary_entry_updated",
            diary_snapshot("uuid-a", text=secret, private=True),
        ),
        export_line("diary_entry_created", diary_snapshot("uuid-b")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:diary:uuid-b"]
    assert report["denied"] == [{"diary_uuid": "uuid-a", "reason": "private"}]
    assert secret not in json.dumps(report)
    assert secret not in json.dumps(records)


def test_private_wins_over_the_archived_exclusion():
    records, report = run(
        export_line(
            "diary_entry_archived",
            diary_snapshot(
                "uuid-a",
                private=True,
                archived_at="2026-05-03T09:00:00+02:00",
            ),
        )
    )
    assert records == []
    assert report["denied"] == [{"diary_uuid": "uuid-a", "reason": "private"}]
    assert report["skipped"] == []


def test_archived_entry_is_skipped_and_unarchive_restores():
    records, report = run(
        export_line("diary_entry_created", diary_snapshot("uuid-a")),
        export_line(
            "diary_entry_archived",
            diary_snapshot("uuid-a", archived_at="2026-05-03T09:00:00+02:00"),
        ),
        export_line("diary_entry_created", diary_snapshot("uuid-b")),
        export_line(
            "diary_entry_archived",
            diary_snapshot("uuid-b", archived_at="2026-05-03T09:00:00+02:00"),
        ),
        export_line("diary_entry_unarchived", diary_snapshot("uuid-b")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:diary:uuid-b"]
    assert report["skipped"] == [
        {
            "diary_uuid": "uuid-a",
            "record_id": "ephemeris:diary:uuid-a",
            "reason": "archived",
        }
    ]


def test_edits_converge_to_the_latest_snapshot_under_one_identity():
    lines = (
        export_line("diary_entry_created", diary_snapshot("uuid-a", text="Vera Example draft.")),
        export_line("diary_entry_updated", diary_snapshot("uuid-a", text="Vera Example final.")),
    )
    records, _ = run(*lines)
    assert [r["record_id"] for r in records] == ["ephemeris:diary:uuid-a"]
    assert records[0]["text"] == "Vera Example final."
    rerun_records, rerun_report = run(*lines)
    assert rerun_records == records
    assert rerun_report["counts"]["accepted"] == 1


def test_mixed_export_yields_both_slices_without_cross_talk():
    records, report = run(
        export_line("diary_entry_created", diary_snapshot("uuid-d")),
        export_line("retro_entry_created", retro_snapshot("uuid-r")),
        export_line("habit_created", {"habit_id": 7}),
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-p", private=True, text="Vera Example private."),
        ),
    )
    assert [r["record_id"] for r in records] == [
        "ephemeris:retro:uuid-r",
        "ephemeris:diary:uuid-d",
    ]
    retro_record, diary_record = records
    assert retro_record["project"] == "Vera Garden"
    assert diary_record["project"] == "diary"
    assert report["counts"] == {
        "accepted": 2,
        "skipped": 0,
        "rejected": 0,
        "denied": 1,
        "ignored_lines": 1,
    }
    rerun_records, _ = run(
        export_line("diary_entry_created", diary_snapshot("uuid-d")),
        export_line("retro_entry_created", retro_snapshot("uuid-r")),
        export_line("habit_created", {"habit_id": 7}),
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-p", private=True, text="Vera Example private."),
        ),
    )
    assert rerun_records == records


def test_malformed_entry_date_is_schema_corruption():
    records, report = run(
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-a", entry_date="May 2nd 2026"),
        ),
        export_line(
            "diary_entry_created", diary_snapshot("uuid-b", entry_date=None)
        ),
    )
    assert records == []
    assert [
        (entry["diary_uuid"], entry["reason"]) for entry in report["rejected"]
    ] == [("uuid-a", "invalid_snapshot"), ("uuid-b", "invalid_snapshot")]


def test_impossible_calendar_date_is_rejected_never_approximated():
    records, report = run(
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-a", entry_date="2026-02-31"),
        ),
        export_line("diary_entry_created", diary_snapshot("uuid-b")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:diary:uuid-b"]
    entry = report["rejected"][0]
    assert entry["diary_uuid"] == "uuid-a"
    assert entry["reason"].startswith("entry_date_unparsable:")


def test_unreadable_privacy_state_never_ships():
    records, report = run(
        export_line(
            "diary_entry_created", diary_snapshot("uuid-a", private="yes")
        )
    )
    assert records == []
    assert report["rejected"] == [
        {
            "diary_uuid": "uuid-a",
            "record_id": "ephemeris:diary:uuid-a",
            "reason": "invalid_snapshot",
        }
    ]


def test_contradictory_archive_transition_is_rejected():
    records, report = run(
        export_line("diary_entry_archived", diary_snapshot("uuid-a")),
        export_line(
            "diary_entry_unarchived",
            diary_snapshot("uuid-b", archived_at="2026-05-03T09:00:00+02:00"),
        ),
        export_line("diary_entry_created", diary_snapshot("uuid-c")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:diary:uuid-c"]
    assert [
        (entry["diary_uuid"], entry["reason"]) for entry in report["rejected"]
    ] == [("uuid-a", "invalid_snapshot"), ("uuid-b", "invalid_snapshot")]


def test_every_emitted_record_validates_as_a_real_ephemeris_record():
    from exp2res.integrations.ephemeris import EphemerisRecord

    records, _ = run(
        export_line("retro_entry_created", retro_snapshot("uuid-r")),
        export_line("diary_entry_created", diary_snapshot("uuid-d")),
    )
    assert len(records) == 2
    for record in records:
        validated = EphemerisRecord.model_validate_json(
            json.dumps(record, ensure_ascii=False)
        )
        assert validated.source_identity == record["record_id"]


def test_report_carries_reason_codes_never_entry_text():
    secret = "Vera Example wrote something meant only for the raw log."
    _, report = run(
        export_line("diary_entry_created", diary_snapshot("uuid-a", text=secret)),
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-b", text=secret, private=True),
        ),
        export_line(
            "diary_entry_created",
            diary_snapshot("uuid-c", text=secret, entry_date="2026-02-31"),
        ),
        export_line(
            "diary_entry_archived",
            diary_snapshot(
                "uuid-d", text=secret, archived_at="2026-05-03T09:00:00+02:00"
            ),
        ),
    )
    assert secret not in json.dumps(report)


def test_cli_never_writes_a_private_entry_to_the_payload(tmp_path, capsys):
    secret = "Vera Example private reflection that must never ship."
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        "\n".join(
            [
                export_line("diary_entry_created", diary_snapshot("uuid-a")),
                export_line(
                    "diary_entry_created",
                    diary_snapshot("uuid-p", private=True, text=secret),
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "vera-workspace"
    workspace.mkdir()
    output = workspace / "diary-191.jsonl"
    exit_code = retro_adapter.main(
        [
            str(export),
            "--timezone",
            TIMEZONE,
            "-o",
            str(output),
            "--instance",
            str(workspace),
        ]
    )
    assert exit_code == 0
    payload = output.read_text(encoding="utf-8")
    assert "ephemeris:diary:uuid-a" in payload
    assert "uuid-p" not in payload
    assert secret not in payload
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["denied"] == 1
    assert report["denied"] == [{"diary_uuid": "uuid-p", "reason": "private"}]
