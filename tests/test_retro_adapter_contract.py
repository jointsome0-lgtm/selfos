"""End-to-end adapter tests against the real exp2res grammar and model.

These need the exp2res package (issue #37 resolves ``occurred`` with
exp2res's own ``parse_occurred`` and validates output against its
``EphemerisRecord``); without it the whole module skips cleanly. All data
is invented for the Vera Example persona.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("exp2res")

from scripts import retro_adapter

from test_retro_adapter_select import export_line, snapshot


TIMEZONE = "Europe/Berlin"


def run(*lines: str) -> tuple[list[dict], dict]:
    return retro_adapter.run("\n".join(lines), TIMEZONE)


def test_accepted_record_matches_the_191_shape_and_grammar():
    from exp2res.services.time_input import parse_occurred

    records, report = run(export_line("retro_entry_created", snapshot("uuid-a")))
    assert report["counts"] == {
        "accepted": 1,
        "skipped": 0,
        "rejected": 0,
        "ignored_lines": 0,
    }
    expected = parse_occurred(
        period="2026-05",
        precision="month",
        confidence="medium",
        timezone_name=TIMEZONE,
    )
    assert records == [
        {
            "source": "ephemeris",
            "record_id": "ephemeris:retro:uuid-a",
            "domain": "activity",
            "occurred": expected.model_dump(mode="json"),
            "project": "Vera Garden",
            "text": "Vera Example repotted the balcony tomatoes.",
        }
    ]


def test_archived_entry_is_skipped_and_unarchive_restores():
    records, report = run(
        export_line("retro_entry_created", snapshot("uuid-a")),
        export_line(
            "retro_entry_archived",
            snapshot("uuid-a", archived_at="2026-05-03T09:00:00+02:00"),
        ),
        export_line("retro_entry_created", snapshot("uuid-b")),
        export_line(
            "retro_entry_archived",
            snapshot("uuid-b", archived_at="2026-05-03T09:00:00+02:00"),
        ),
        export_line("retro_entry_unarchived", snapshot("uuid-b")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:retro:uuid-b"]
    assert report["skipped"] == [
        {
            "retro_uuid": "uuid-a",
            "record_id": "ephemeris:retro:uuid-a",
            "reason": "archived",
        }
    ]


def test_edits_converge_to_the_latest_snapshot_under_one_identity():
    lines = (
        export_line("retro_entry_created", snapshot("uuid-a", text="Vera Example draft.")),
        export_line("retro_entry_updated", snapshot("uuid-a", text="Vera Example final.")),
    )
    records, _ = run(*lines)
    assert [r["record_id"] for r in records] == ["ephemeris:retro:uuid-a"]
    assert records[0]["text"] == "Vera Example final."
    rerun_records, rerun_report = run(*lines)
    assert rerun_records == records
    assert rerun_report["counts"]["accepted"] == 1


def test_unparsable_period_is_rejected_never_approximated():
    records, report = run(
        export_line(
            "retro_entry_created",
            snapshot("uuid-a", period_raw="Q5 2026", precision="quarter"),
        ),
        export_line("retro_entry_created", snapshot("uuid-b")),
    )
    assert [r["record_id"] for r in records] == ["ephemeris:retro:uuid-b"]
    assert len(report["rejected"]) == 1
    entry = report["rejected"][0]
    assert entry["retro_uuid"] == "uuid-a"
    assert entry["reason"].startswith("period_unparsable:")


def test_entry_without_project_is_skipped_never_invented():
    records, report = run(
        export_line("retro_entry_created", snapshot("uuid-a", project=None))
    )
    assert records == []
    assert report["skipped"] == [
        {
            "retro_uuid": "uuid-a",
            "record_id": "ephemeris:retro:uuid-a",
            "reason": "no_project",
        }
    ]


def test_unknown_precision_resolves_to_an_open_occurred():
    records, _ = run(
        export_line(
            "retro_entry_created",
            snapshot(
                "uuid-a",
                period_raw=None,
                precision="unknown",
                confidence="unknown",
                period_start=None,
            ),
        )
    )
    assert records[0]["occurred"] == {
        "start": None,
        "end": None,
        "precision": "unknown",
        "confidence": "unknown",
    }


def test_every_emitted_record_validates_as_a_real_ephemeris_record():
    from exp2res.integrations.ephemeris import EphemerisRecord

    records, _ = run(
        export_line("retro_entry_created", snapshot("uuid-a")),
        export_line(
            "retro_entry_created",
            snapshot(
                "uuid-b",
                period_raw="2026-05-01T09:00:00+02:00/2026-06-15T18:00:00+02:00",
                precision="approximate_range",
                confidence="low",
                project="Vera Balcony",
                text="Vera Example rebuilt the planter boxes over six weeks.",
            ),
        ),
    )
    assert len(records) == 2
    for record in records:
        validated = EphemerisRecord.model_validate_json(
            json.dumps(record, ensure_ascii=False)
        )
        assert validated.source_identity == record["record_id"]


def test_bad_timezone_refuses_the_whole_run():
    with pytest.raises(retro_adapter.AdapterError):
        retro_adapter.run(
            export_line("retro_entry_created", snapshot("uuid-a")),
            "Vera/Nowhere",
        )


def test_report_carries_reason_codes_never_entry_text():
    secret = "Vera Example wrote something meant only for the raw log."
    _, report = run(
        export_line("retro_entry_created", snapshot("uuid-a", text=secret)),
        export_line(
            "retro_entry_created",
            snapshot("uuid-b", text=secret, project=None),
        ),
        export_line(
            "retro_entry_created",
            snapshot("uuid-c", text=secret, period_raw="not a period"),
        ),
    )
    assert secret not in json.dumps(report)


def test_cli_writes_payload_and_prints_report(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "retro-191.jsonl"
    exit_code = retro_adapter.main(
        [str(export), "--timezone", TIMEZONE, "-o", str(output)]
    )
    assert exit_code == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["record_id"] for line in lines] == [
        "ephemeris:retro:uuid-a"
    ]
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["accepted"] == 1
