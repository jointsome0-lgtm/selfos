"""Retro-slice selection and CLI-boundary tests for scripts/retro_adapter.py.

Everything here runs without exp2res (issue #37): selection is pure line
handling, and the CLI refusals fire before the temporal grammar loads. The
grammar and §19.1 shape are covered in test_retro_adapter_contract.py. All
data is invented for the Vera Example persona.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

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
    assert selection.rejected_entries == []
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
    assert selection.rejected_entries == []


def test_malformed_lines_refuse_the_whole_run_with_the_physical_line():
    for bad_line in ("{not json", json.dumps(["not", "an", "event"])):
        text = "\n".join(
            [
                export_line("retro_entry_created", snapshot("uuid-a")),
                "",
                bad_line,
            ]
        )
        with pytest.raises(retro_adapter.AdapterError) as excinfo:
            retro_adapter.select_snapshots(text)
        assert "line 3" in str(excinfo.value)


def test_unsupported_payload_version_rejects_the_whole_identity():
    text = export_line("retro_entry_created", snapshot("uuid-a"), version=2)
    selection = retro_adapter.select_snapshots(text)
    assert selection.snapshots == {}
    assert selection.rejected_entries == [
        {
            "retro_uuid": "uuid-a",
            "record_id": "ephemeris:retro:uuid-a",
            "reason": "unsupported_payload_version",
        }
    ]


def test_unsupported_later_event_never_falls_back_to_a_stale_snapshot():
    text = "\n".join(
        [
            export_line("retro_entry_created", snapshot("uuid-a")),
            export_line(
                "retro_entry_archived",
                snapshot("uuid-a", archived_at="2026-05-03T09:00:00+02:00"),
                version=2,
            ),
            export_line("retro_entry_created", snapshot("uuid-b")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert list(selection.snapshots) == ["uuid-b"]
    assert [entry["retro_uuid"] for entry in selection.rejected_entries] == ["uuid-a"]


def test_poisoned_identity_stays_rejected_after_a_later_valid_event():
    text = "\n".join(
        [
            export_line("retro_entry_created", snapshot("uuid-a"), version=2),
            export_line("retro_entry_updated", snapshot("uuid-a")),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert selection.snapshots == {}
    assert [entry["retro_uuid"] for entry in selection.rejected_entries] == ["uuid-a"]


def test_version_gate_requires_the_exact_integer_wire_type():
    text = "\n".join(
        [
            export_line("retro_entry_created", snapshot("uuid-a"), version=True),
            export_line("retro_entry_created", snapshot("uuid-b"), version=1.0),
        ]
    )
    selection = retro_adapter.select_snapshots(text)
    assert selection.snapshots == {}
    assert [entry["retro_uuid"] for entry in selection.rejected_entries] == [
        "uuid-a",
        "uuid-b",
    ]


def test_unattributable_retro_event_refuses_the_whole_run():
    for payload in ({"note": "no uuid"}, snapshot(""), "not an object"):
        text = "\n".join(
            [
                export_line("retro_entry_created", snapshot("uuid-a")),
                export_line("retro_entry_updated", payload),
            ]
        )
        with pytest.raises(retro_adapter.AdapterError):
            retro_adapter.select_snapshots(text)


def test_cli_refuses_output_inside_a_public_checkout(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    public_root = Path(retro_adapter.__file__).resolve().parents[1]
    output = public_root / "vera-payload.jsonl"
    exit_code = retro_adapter.main(
        [
            str(export),
            "--timezone",
            "Europe/Berlin",
            "-o",
            str(output),
            "--allow-unconfigured",
        ]
    )
    assert exit_code == 2
    assert not output.exists()
    assert "public checkout" in capsys.readouterr().err


def test_configured_private_root_becomes_the_only_allowed_destination(
    tmp_path, capsys, monkeypatch
):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "vera-workspace"
    workspace.mkdir()
    monkeypatch.setenv("EXP2RES_WORKSPACE", str(workspace))
    outside = tmp_path / "elsewhere" / "vera-out.jsonl"
    outside.parent.mkdir()
    exit_code = retro_adapter.main(
        [str(export), "--timezone", "Europe/Berlin", "-o", str(outside)]
    )
    assert exit_code == 2
    assert "configured exp2res private root" in capsys.readouterr().err
    assert not outside.exists()


def test_config_file_root_is_honored(tmp_path, monkeypatch):
    workspace = tmp_path / "vera-workspace"
    workspace.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[instances]\nexp2res = "{workspace}"\n', encoding="utf-8"
    )
    monkeypatch.setattr(retro_adapter, "CONFIG_PATH", config)
    assert retro_adapter.configured_private_root() == workspace
    config.write_text("not = valid = toml", encoding="utf-8")
    with pytest.raises(retro_adapter.AdapterError):
        retro_adapter.configured_private_root()


def test_every_discovery_source_expands_a_home_relative_root(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "vera-workspace"
    assert (
        retro_adapter.configured_private_root("~/vera-workspace") == workspace
    )
    monkeypatch.setenv("EXP2RES_WORKSPACE", "~/vera-workspace")
    assert retro_adapter.configured_private_root() == workspace
    monkeypatch.delenv("EXP2RES_WORKSPACE")
    config = tmp_path / "config.toml"
    config.write_text(
        '[instances]\nexp2res = "~/vera-workspace"\n', encoding="utf-8"
    )
    monkeypatch.setattr(retro_adapter, "CONFIG_PATH", config)
    assert retro_adapter.configured_private_root() == workspace


def test_cli_refuses_overwriting_the_export(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(export)
    exit_code = retro_adapter.main(
        [str(export), "--timezone", "Europe/Berlin", "-o", str(alias)]
    )
    assert exit_code == 2
    assert "refusing to overwrite the source" in capsys.readouterr().err
    assert "retro_entry_created" in export.read_text(encoding="utf-8")


def test_cli_refuses_a_hard_linked_alias_of_the_export(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    alias = tmp_path / "hard-alias.jsonl"
    os.link(export, alias)
    exit_code = retro_adapter.main(
        [str(export), "--timezone", "Europe/Berlin", "-o", str(alias)]
    )
    assert exit_code == 2
    assert "refusing to overwrite the source" in capsys.readouterr().err
    assert "retro_entry_created" in export.read_text(encoding="utf-8")


def test_cli_refuses_an_export_inside_a_public_checkout(tmp_path, capsys):
    public_root = Path(retro_adapter.__file__).resolve().parents[1]
    exit_code = retro_adapter.main(
        [
            str(public_root / "vera-export.jsonl"),
            "--timezone",
            "Europe/Berlin",
            "-o",
            str(tmp_path / "vera-out.jsonl"),
        ]
    )
    assert exit_code == 2
    assert "public checkout" in capsys.readouterr().err


def test_cli_refuses_an_undecodable_export(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_bytes(b'{"type": "retro_entry_created"\xff\xfe}\n')
    exit_code = retro_adapter.main(
        [
            str(export),
            "--timezone",
            "Europe/Berlin",
            "-o",
            str(tmp_path / "vera-out.jsonl"),
            "--allow-unconfigured",
        ]
    )
    assert exit_code == 2
    assert "cannot read export" in capsys.readouterr().err


def test_cli_refuses_an_unconfigured_run_without_the_optin_flag(
    tmp_path, capsys
):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "vera-out.jsonl"
    exit_code = retro_adapter.main(
        [str(export), "--timezone", "Europe/Berlin", "-o", str(output)]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--instance" in err
    assert "EXP2RES_WORKSPACE" in err
    assert not output.exists()


def test_cli_refuses_an_output_alias_through_a_public_checkout(
    tmp_path, capsys, monkeypatch
):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    private_dir = tmp_path / "vera-private"
    private_dir.mkdir()
    public_root = Path(retro_adapter.__file__).resolve().parents[1]
    alias = public_root / "vera-private-link"
    alias.symlink_to(private_dir)
    try:
        monkeypatch.setenv("EXP2RES_WORKSPACE", str(alias))
        exit_code = retro_adapter.main(
            [
                str(export),
                "--timezone",
                "Europe/Berlin",
                "-o",
                str(alias / "vera-out.jsonl"),
            ]
        )
    finally:
        alias.unlink()
    assert exit_code == 2
    assert "public checkout" in capsys.readouterr().err
    assert not (private_dir / "vera-out.jsonl").exists()


def test_instance_flag_outranks_the_environment_root(
    tmp_path, capsys, monkeypatch
):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    flag_root = tmp_path / "vera-flag-root"
    flag_root.mkdir()
    env_root = tmp_path / "vera-env-root"
    env_root.mkdir()
    monkeypatch.setenv("EXP2RES_WORKSPACE", str(env_root))
    output = env_root / "vera-out.jsonl"
    exit_code = retro_adapter.main(
        [
            str(export),
            "--timezone",
            "Europe/Berlin",
            "-o",
            str(output),
            "--instance",
            str(flag_root),
        ]
    )
    assert exit_code == 2
    assert "configured exp2res private root" in capsys.readouterr().err
    assert not output.exists()


def test_instance_flag_does_not_bypass_the_public_guard(tmp_path, capsys):
    export = tmp_path / "events-export.jsonl"
    export.write_text(
        export_line("retro_entry_created", snapshot("uuid-a")) + "\n",
        encoding="utf-8",
    )
    public_root = Path(retro_adapter.__file__).resolve().parents[1]
    exit_code = retro_adapter.main(
        [
            str(export),
            "--timezone",
            "Europe/Berlin",
            "-o",
            str(tmp_path / "vera-out.jsonl"),
            "--instance",
            str(public_root),
        ]
    )
    assert exit_code == 2
    assert "public checkout" in capsys.readouterr().err
