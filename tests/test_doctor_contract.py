"""End-to-end contract tests pin ordering, exit behavior, JSON, and privacy."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import doctor


EXISTING_SEQUENCE = [
    ("repo.pins_manifest", "selfos"),
    ("repo.sibling_present", "ephemeris"),
    ("repo.revision_matches_pin", "ephemeris"),
    ("repo.worktree_clean", "ephemeris"),
    ("repo.sibling_present", "atlas"),
    ("repo.revision_matches_pin", "atlas"),
    ("repo.worktree_clean", "atlas"),
    ("repo.sibling_present", "exp2res"),
    ("repo.revision_matches_pin", "exp2res"),
    ("repo.worktree_clean", "exp2res"),
    ("instance.root_configured", "ephemeris"),
    ("instance.root_outside_public", "ephemeris"),
    ("instance.root_configured", "atlas"),
    ("instance.root_outside_public", "atlas"),
    ("instance.root_configured", "exp2res"),
    ("instance.root_outside_public", "exp2res"),
]

NEW_IDS = [
    "subsystem.ephemeris.database_readable",
    "subsystem.ephemeris.schema_compatible",
    "subsystem.ephemeris.integrity_check",
    "subsystem.ephemeris.backup_recent",
    "subsystem.atlas.instance_layout",
    "subsystem.atlas.journals_parse",
    "subsystem.atlas.writer_lock",
    "subsystem.atlas.receipts_complete",
    "runtime.runner_present",
    "runtime.runner_version",
    "runtime.override_verified",
]


def test_not_applicable_does_not_change_overall_and_only_blocked_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert doctor.overall_state([doctor.Check("x", "x", "not_applicable", "x")]) == "ok"
    for status, expected in [
        ("not_applicable", 0),
        ("ok", 0),
        ("warning", 0),
        ("blocked", 1),
    ]:
        monkeypatch.setattr(
            doctor,
            "collect_checks",
            lambda _show, value=status: [doctor.Check("x", "x", value, "detail")],
        )
        monkeypatch.setattr(sys, "argv", ["doctor.py", "--json"])
        assert doctor.main() == expected


def test_main_catches_runtime_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        doctor,
        "collect_checks",
        lambda _show: (_ for _ in ()).throw(RuntimeError("private config value")),
    )
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--json"])
    assert doctor.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "doctor internal error: local state could not be inspected\n"
    assert "Traceback" not in captured.err
    assert "private config value" not in captured.err


def test_json_envelope_and_stable_order(
    isolated_doctor: Path, write_pins, capsys
) -> None:
    write_pins(isolated_doctor, {name: "0" * 40 for name in doctor.EXPECTED_REPOS})
    checks = doctor.collect_checks(False)
    doctor.render_json(checks, doctor.overall_state(checks))
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["version"] == 1
    assert set(envelope) == {"version", "state", "checks"}
    pairs = [(check["id"], check["label"]) for check in envelope["checks"]]
    assert pairs[: len(EXISTING_SEQUENCE)] == EXISTING_SEQUENCE
    assert [check["id"] for check in envelope["checks"][-len(NEW_IDS) :]] == NEW_IDS
    assert len(pairs) == len(set(pairs))
    assert len(NEW_IDS) == len(set(NEW_IDS))


def test_default_output_hides_paths_and_show_paths_reveals_them(
    isolated_doctor: Path, write_pins, monkeypatch, capsys
) -> None:
    marker = "RECOGNISABLE-PRIVATE-PATH-MARKER"
    marked_root = isolated_doctor.parent / marker / "selfos"
    marked_root.mkdir(parents=True)
    monkeypatch.setattr(doctor, "ROOT", marked_root)
    monkeypatch.setattr(doctor, "PINS_PATH", marked_root / "pins.toml")
    write_pins(marked_root, {name: "0" * 40 for name in doctor.EXPECTED_REPOS})

    monkeypatch.setattr(sys, "argv", ["doctor.py"])
    assert doctor.main() == 1
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err

    monkeypatch.setattr(sys, "argv", ["doctor.py", "--show-paths"])
    assert doctor.main() == 1
    captured = capsys.readouterr()
    assert marker in captured.out
    assert captured.err == ""


def test_healthy_partial_installation_has_mixed_ok_and_na_and_exit_zero(
    isolated_doctor: Path,
    make_repo,
    write_pins,
    monkeypatch,
    capsys,
) -> None:
    pins = {}
    for name in doctor.EXPECTED_REPOS:
        files = {"README.md": "Vera Example fixture\n"}
        if name == "ephemeris":
            files["app/db.py"] = "SCHEMA_VERSION = 3\n"
        pins[name] = make_repo(isolated_doctor.parent / name, files)
    write_pins(isolated_doctor, pins)

    private = isolated_doctor.parent / "private-ephemeris"
    private.mkdir()
    connection = sqlite3.connect(private / "activity.sqlite")
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    backups = private / "backups"
    backups.mkdir()
    (backups / "synthetic.backup").write_bytes(b"")
    monkeypatch.setenv("ACTIVITY_DATA_DIR", str(private))

    checks = doctor.collect_checks(False)
    statuses = {check.status for check in checks}
    assert "ok" in statuses
    assert "not_applicable" in statuses
    assert "blocked" not in statuses
    assert doctor.overall_state(checks) == "ok", [
        (check.id, check.label, check.detail)
        for check in checks
        if check.status == "warning"
    ]
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--json"])
    assert doctor.main() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ok"
