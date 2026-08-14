"""Retro-slice selection tests for scripts/retro_adapter.py (issue #37).

Selection is pure line handling and needs no exp2res; the temporal grammar
and §19.1 shape are covered in test_retro_adapter_contract.py. All data is
invented for the Vera Example persona.
"""

from __future__ import annotations

import json

from scripts import retro_adapter


def export_line(event_type: str, payload: dict, version: int = 1) -> str:
    return json.dumps(
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "timestamp": "2026-05-02T10:00:00+02:00",
            "type": event_type,
            "payload_version": version,
            "payload": payload,
        },
        ensure_ascii=False,
    )


def snapshot(retro_uuid: str, **overrides) -> dict:
    payload = {
        "retro_uuid": retro_uuid,
        "retro_id": 1,
        "period_raw": "2026-05",
        "precision": "month",
        "confidence": "medium",
        "period_start": "2026-05-01T00:00:00+02:00",
        "period_end": None,
        "project": "Vera Garden",
        "text": "Vera Example repotted the balcony tomatoes.",
        "created_at": "2026-05-02T10:00:00+02:00",
        "updated_at": None,
        "archived_at": None,
    }
    payload.update(overrides)
    return payload


def test_latest_event_per_uuid_wins_in_first_seen_order():
    text = "\n".join(
        [
            export_line("retro_entry_created", snapshot("uuid-a", text="Vera Example first phrasing.")),
            export_line("retro_entry_created", snapshot("uuid-b")),
            export_line("retro_entry_updated", snapshot("uuid-a", text="Vera Example final phrasing.")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.snapshots) == ["uuid-a", "uuid-b"]
    assert selection.snapshots["uuid-a"]["text"] == "Vera Example final phrasing."
    assert selection.rejected_lines == []
    assert selection.ignored_lines == 0


def test_non_retro_event_types_are_ignored_not_reported():
    text = "\n".join(
        [
            export_line("habit_created", {"habit_id": 7}),
            export_line("retro_entry_created", snapshot("uuid-a")),
            export_line("calendar_event_series", {"id": 3, "title": "Vera Example demo"}),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.snapshots) == ["uuid-a"]
    assert selection.ignored_lines == 2
    assert selection.rejected_lines == []


def test_malformed_lines_are_rejected_with_physical_line_numbers():
    text = "\n".join(
        [
            export_line("retro_entry_created", snapshot("uuid-a")),
            "",
            "{not json",
            json.dumps(["not", "an", "event"]),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.snapshots) == ["uuid-a"]
    assert selection.rejected_lines == [
        {"line": 3, "reason": "line_not_json"},
        {"line": 4, "reason": "line_not_event"},
    ]


def test_unsupported_payload_version_is_rejected():
    text = export_line("retro_entry_created", snapshot("uuid-a"), version=2)
    selection = retro_adapter.select_snapshots(text)
    assert selection.snapshots == {}
    assert selection.rejected_lines == [
        {"line": 1, "reason": "unsupported_payload_version"}
    ]


def test_retro_event_without_retro_uuid_is_rejected():
    payload = snapshot("uuid-a")
    del payload["retro_uuid"]
    text = "\n".join(
        [
            export_line("retro_entry_created", payload),
            export_line("retro_entry_created", snapshot("")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert selection.snapshots == {}
    assert [entry["reason"] for entry in selection.rejected_lines] == [
        "missing_retro_uuid",
        "missing_retro_uuid",
    ]
