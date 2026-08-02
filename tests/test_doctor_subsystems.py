"""Runtime-built private fixtures exercise subsystem checks without real data."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from scripts import doctor


def by_id(checks: list[doctor.Check], check_id: str) -> doctor.Check:
    """Keep assertions coupled to public IDs instead of list offsets."""
    return next(check for check in checks if check.id == check_id)


def make_ephemeris_engine(public_root: Path, version: int) -> None:
    """Expose only the text constant doctor is allowed to extract."""
    source = public_root.parent / "ephemeris" / "app" / "db.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"SCHEMA_VERSION = {version}\n", encoding="utf-8")


def make_database(root: Path, version: int) -> Path:
    """Build a disposable SQLite ledger through the standard library."""
    root.mkdir(parents=True, exist_ok=True)
    database = root / "activity.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA user_version = {version}")
    connection.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return database


def fresh_backup(root: Path) -> None:
    """Supply one content-free mtime target without a committed fixture."""
    backups = root / "backups"
    backups.mkdir()
    (backups / "synthetic.backup").write_bytes(b"")


def make_atlas(root: Path) -> Path:
    """Create the required directories and a healthy real-layout receipt tail."""
    (root / "atlas").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"fixture/source#1","marker":"opened"}\n'
        '{"intake":"fixture/source#1","marker":"processed"}\n',
        encoding="utf-8",
    )
    return root


def test_corrupt_database_is_blocked_without_creating_sidecars(
    isolated_doctor: Path,
) -> None:
    root = isolated_doctor.parent / "private-corrupt-marker"
    root.mkdir()
    database = root / "activity.sqlite"
    database.write_bytes(b"not sqlite and no private content")

    checks = doctor.ephemeris_checks(root, False)
    assert by_id(checks, "subsystem.ephemeris.database_readable").status == "blocked"
    assert by_id(checks, "subsystem.ephemeris.schema_compatible").status == "not_applicable"
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()


def test_unopenable_database_error_is_summarised_without_path(
    isolated_doctor: Path, monkeypatch
) -> None:
    root = isolated_doctor.parent / "private-unopenable-marker"
    database = make_database(root, 1)
    fresh_backup(root)

    def refuse_open(*_args, **_kwargs):
        raise sqlite3.OperationalError(f"unable to open database file {database}")

    monkeypatch.setattr(doctor.sqlite3, "connect", refuse_open)
    finding = by_id(
        doctor.ephemeris_checks(root, False),
        "subsystem.ephemeris.database_readable",
    )
    assert finding.status == "blocked"
    assert "private-unopenable-marker" not in finding.detail


def test_busy_database_is_transient_warning(isolated_doctor: Path) -> None:
    root = isolated_doctor.parent / "private-busy"
    database = make_database(root, 1)
    fresh_backup(root)
    writer = sqlite3.connect(database)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        checks = doctor.ephemeris_checks(root, False)
    finally:
        writer.rollback()
        writer.close()
    readable = by_id(checks, "subsystem.ephemeris.database_readable")
    assert readable.status == "warning"
    assert "could not be checked now" in readable.detail


def test_newer_and_older_database_versions_have_required_severity(
    isolated_doctor: Path,
) -> None:
    make_ephemeris_engine(isolated_doctor, 5)
    newer = isolated_doctor.parent / "private-newer"
    make_database(newer, 6)
    fresh_backup(newer)
    older = isolated_doctor.parent / "private-older"
    make_database(older, 4)
    fresh_backup(older)

    assert by_id(
        doctor.ephemeris_checks(newer, False),
        "subsystem.ephemeris.schema_compatible",
    ).status == "blocked"
    assert by_id(
        doctor.ephemeris_checks(older, False),
        "subsystem.ephemeris.schema_compatible",
    ).status == "warning"


def test_healthy_database_and_recent_backup_are_ok(isolated_doctor: Path) -> None:
    make_ephemeris_engine(isolated_doctor, 5)
    root = isolated_doctor.parent / "private-healthy"
    make_database(root, 5)
    fresh_backup(root)
    checks = doctor.ephemeris_checks(root, False)
    assert [check.status for check in checks] == ["ok", "ok", "ok", "ok"]


def test_old_or_absent_backup_warns(isolated_doctor: Path) -> None:
    root = isolated_doctor.parent / "private-backup"
    root.mkdir()
    no_backup = doctor._ephemeris_backup_check(root, False)
    assert no_backup.status == "warning"

    backups = root / "backups"
    backups.mkdir()
    snapshot = backups / "synthetic.backup"
    snapshot.write_bytes(b"")
    old = time.time() - 9 * 86400
    os.utime(snapshot, (old, old))
    assert doctor._ephemeris_backup_check(root, False).status == "warning"


def test_atlas_writer_lock_is_warning_and_is_not_removed(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-private")
    lock = root / ".atlas-lock"
    lock.write_text('{"pid": 123, "started_at": "Vera Example"}\n', encoding="utf-8")
    checks = doctor.atlas_checks(root, False)
    assert by_id(checks, "subsystem.atlas.writer_lock").status == "warning"
    assert lock.exists()


def test_malformed_journal_reports_only_name_and_row(isolated_doctor: Path) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-secret-root-marker")
    journal = root / "state" / "events.jsonl"
    journal.write_text('{"safe": true}\nPRIVATE-ROW-CONTENT\n', encoding="utf-8")
    checks = doctor.atlas_checks(root, False)
    finding = by_id(checks, "subsystem.atlas.journals_parse")
    assert finding.status == "blocked"
    assert "events.jsonl" in finding.detail
    assert "row 2" in finding.detail
    assert "PRIVATE-ROW-CONTENT" not in finding.detail
    assert "atlas-secret-root-marker" not in finding.detail
    assert by_id(checks, "subsystem.atlas.receipts_complete").status == "not_applicable"


def test_healthy_receipts_are_complete(isolated_doctor: Path) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-healthy")
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "ok"


def test_interrupted_intake_reports_count_without_key(isolated_doctor: Path) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-intake")
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"private-key-one","marker":"opened"}\n'
        '{"intake":"private-key-two","marker":"opened"}\n'
        '{"intake":"private-key-two","marker":"processed"}\n',
        encoding="utf-8",
    )
    checks = doctor.atlas_checks(root, False)
    finding = by_id(
        checks,
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "warning"
    assert "1 intake keys" in finding.detail
    assert "private-key" not in finding.detail
    assert sum(check.status == "warning" for check in checks) == 1


def test_guessed_receipt_field_names_are_not_applicable(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-receipt-shape")
    (root / "state" / "receipts.jsonl").write_text(
        '{"key":"private-guessed-key","status":"opened"}\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "not_applicable"
    assert "expected content-free bookkeeping shape" in finding.detail
    assert "private-guessed-key" not in finding.detail


def test_rotated_opened_and_direct_processed_are_complete(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-rotated-receipts")
    rotated = root / "state" / "receipts"
    rotated.mkdir()
    (rotated / "0001.jsonl").write_text(
        '{"intake":"private-rotated-key","marker":"opened"}\n',
        encoding="utf-8",
    )
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"private-rotated-key","marker":"processed"}\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "ok"
    assert "private-rotated-key" not in finding.detail


def test_malformed_rotated_journal_reports_only_name_and_row(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-rotated-secret-root-marker")
    rotated = root / "state" / "events"
    rotated.mkdir()
    (rotated / "0001.jsonl").write_text(
        '{"safe":true}\nPRIVATE-ROTATED-ROW-CONTENT\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.journals_parse",
    )
    assert finding.status == "blocked"
    assert "0001.jsonl" in finding.detail
    assert "row 2" in finding.detail
    assert "PRIVATE-ROTATED-ROW-CONTENT" not in finding.detail
    assert "atlas-rotated-secret-root-marker" not in finding.detail


def test_symlinked_state_entry_is_blocked_without_following(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-symlink")
    outside = isolated_doctor.parent / "outside-private"
    outside.write_text("PRIVATE-OUTSIDE-CONTENT", encoding="utf-8")
    (root / "state" / "unsafe.jsonl").symlink_to(outside)
    finding = by_id(doctor.atlas_checks(root, False), "subsystem.atlas.journals_parse")
    assert finding.status == "blocked"
    assert "PRIVATE-OUTSIDE-CONTENT" not in finding.detail


def test_symlinked_rotated_journal_is_blocked_without_following(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-rotated-symlink")
    rotated = root / "state" / "events"
    rotated.mkdir()
    outside = isolated_doctor.parent / "outside-rotated-private"
    outside.write_text("PRIVATE-ROTATED-OUTSIDE-CONTENT", encoding="utf-8")
    (rotated / "0001.jsonl").symlink_to(outside)
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.journals_parse",
    )
    assert finding.status == "blocked"
    assert "PRIVATE-ROTATED-OUTSIDE-CONTENT" not in finding.detail


def test_journal_byte_cap_is_warning_not_failure(
    isolated_doctor: Path, monkeypatch
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-bounded")
    (root / "state" / "events.jsonl").write_text('{"ok":true}\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "ATLAS_MAX_JOURNAL_BYTES", 4)
    finding = by_id(doctor.atlas_checks(root, False), "subsystem.atlas.journals_parse")
    assert finding.status == "warning"
    assert "not fully checked" in finding.detail


def test_absent_subsystem_subjects_are_all_not_applicable() -> None:
    checks = doctor.ephemeris_checks(None, False) + doctor.atlas_checks(None, False)
    assert {check.status for check in checks} == {"not_applicable"}
