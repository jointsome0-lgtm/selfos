"""Diary-slice selection tests for scripts/retro_adapter.py.

Everything here runs without exp2res (issue #37, diary slice): selection —
including the history-wide private latch — is pure line handling. The
grammar and §19.1 shape are covered in test_diary_adapter_contract.py. All
data is invented for the Vera Example persona.
"""

from __future__ import annotations

import json

import pytest

from scripts import retro_adapter

from test_retro_adapter_select import export_line, snapshot as retro_snapshot


def diary_snapshot(diary_uuid: str, **overrides) -> dict:
    payload = {
        "diary_uuid": diary_uuid,
        "diary_id": 1,
        "entry_date": "2026-05-02",
        "text": "Vera Example watered the balcony tomatoes before breakfast.",
        "tags": ["garden"],
        "private": False,
        "atlas_ref": None,
        "created_at": "2026-05-02T21:00:00+02:00",
        "updated_at": None,
        "archived_at": None,
    }
    payload.update(overrides)
    return payload


def test_latest_diary_event_per_uuid_wins_in_first_seen_order():
    text = "\n".join(
        [
            export_line("diary_entry_created", diary_snapshot("uuid-a", text="Vera Example first phrasing.")),
            export_line("diary_entry_created", diary_snapshot("uuid-b")),
            export_line("diary_entry_updated", diary_snapshot("uuid-a", text="Vera Example final phrasing.")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.diary_snapshots) == ["uuid-a", "uuid-b"]
    assert (
        selection.diary_snapshots["uuid-a"]["text"]
        == "Vera Example final phrasing."
    )
    assert selection.snapshots == {}
    assert selection.diary_private == {}
    assert selection.rejected_entries == []


def test_retro_and_diary_slices_never_mix_identities():
    text = "\n".join(
        [
            export_line("retro_entry_created", retro_snapshot("uuid-a")),
            export_line("diary_entry_created", diary_snapshot("uuid-a")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.snapshots) == ["uuid-a"]
    assert list(selection.diary_snapshots) == ["uuid-a"]
    assert selection.snapshots["uuid-a"]["retro_uuid"] == "uuid-a"
    assert selection.diary_snapshots["uuid-a"]["diary_uuid"] == "uuid-a"


def test_private_creation_latches_the_identity():
    text = export_line(
        "diary_entry_created", diary_snapshot("uuid-a", private=True)
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.diary_private) == ["uuid-a"]


def test_late_privatization_latches_history_wide():
    text = "\n".join(
        [
            export_line("diary_entry_created", diary_snapshot("uuid-a")),
            export_line(
                "diary_entry_updated", diary_snapshot("uuid-a", private=True)
            ),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.diary_private) == ["uuid-a"]


def test_clearing_the_flag_after_privatization_does_not_unlatch():
    text = "\n".join(
        [
            export_line(
                "diary_entry_created", diary_snapshot("uuid-a", private=True)
            ),
            export_line(
                "diary_entry_updated", diary_snapshot("uuid-a", private=False)
            ),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.diary_private) == ["uuid-a"]


def test_private_flag_on_a_poisoned_version_still_latches():
    text = export_line(
        "diary_entry_created", diary_snapshot("uuid-a", private=True), version=2
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.diary_private) == ["uuid-a"]
    assert selection.rejected_entries == []


def test_poisoned_non_private_diary_identity_is_rejected_whole():
    text = "\n".join(
        [
            export_line("diary_entry_created", diary_snapshot("uuid-a")),
            export_line("diary_entry_updated", diary_snapshot("uuid-a"), version=2),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert selection.diary_snapshots == {}
    assert selection.rejected_entries == [
        {
            "diary_uuid": "uuid-a",
            "record_id": "ephemeris:diary:uuid-a",
            "reason": "unsupported_payload_version",
        }
    ]


def test_non_boolean_private_marks_the_privacy_state_unreadable():
    for value in (1, "true", None):
        text = export_line(
            "diary_entry_created", diary_snapshot("uuid-a", private=value)
        )
        selection = retro_adapter.select_snapshots(text)
        assert selection.diary_private == {}
        assert selection.diary_privacy_unreadable == {"uuid-a"}


def test_unknown_diary_lifecycle_type_refuses_the_whole_run():
    text = "\n".join(
        [
            export_line("diary_entry_created", diary_snapshot("uuid-a")),
            export_line("diary_entry_deleted", diary_snapshot("uuid-a")),
        ]
    )
    with pytest.raises(retro_adapter.AdapterError) as excinfo:
        retro_adapter.select_snapshots(text)
    assert "diary_entry_deleted" in str(excinfo.value)


def test_unattributable_diary_event_refuses_the_whole_run():
    for payload in ({"note": "no uuid"}, diary_snapshot(""), "not an object"):
        text = "\n".join(
            [
                export_line("diary_entry_created", diary_snapshot("uuid-a")),
                export_line("diary_entry_updated", payload),
            ]
        )
        with pytest.raises(retro_adapter.AdapterError) as excinfo:
            retro_adapter.select_snapshots(text)
        assert "diary_uuid" in str(excinfo.value)
