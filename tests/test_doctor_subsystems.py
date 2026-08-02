"""Runtime-built private fixtures exercise subsystem checks without real data."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

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


def sidecar_metadata(database: Path) -> dict[str, tuple[int, int] | None]:
    """Snapshot only mutation-relevant metadata for the two SQLite sidecars."""
    result: dict[str, tuple[int, int] | None] = {}
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        try:
            info = sidecar.stat()
        except FileNotFoundError:
            result[suffix] = None
        else:
            result[suffix] = (info.st_size, info.st_mtime_ns)
    return result


def synthetic_pending_wal(page_size: int = 512) -> bytes:
    """Build a header and one full frame for doctor's bounded WAL probe."""
    header = (
        (0x377F0682).to_bytes(4, "big")
        + (3_007_000).to_bytes(4, "big")
        + page_size.to_bytes(4, "big")
        + bytes(20)
    )
    return header + bytes(doctor.SQLITE_WAL_FRAME_HEADER_BYTES + page_size)


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


def test_busy_database_does_not_block_immutable_inspection(
    isolated_doctor: Path,
) -> None:
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
    assert readable.status != "blocked"


def test_wal_and_shm_make_schema_and_integrity_unverified_without_mutation(
    isolated_doctor: Path,
) -> None:
    make_ephemeris_engine(isolated_doctor, 2)
    source = isolated_doctor.parent / "wal-source"
    source_database = make_database(source, 1)
    writer = sqlite3.connect(source_database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("PRAGMA user_version = 2")
    writer.execute("INSERT INTO fixture DEFAULT VALUES")
    writer.commit()
    try:
        root = isolated_doctor.parent / "private-wal-copy"
        root.mkdir()
        database = root / "activity.sqlite"
        wal = database.with_name(database.name + "-wal")
        shm = database.with_name(database.name + "-shm")
        database.write_bytes(source_database.read_bytes())
        wal.write_bytes(
            source_database.with_name(source_database.name + "-wal").read_bytes()
        )
        shm.write_bytes(
            source_database.with_name(source_database.name + "-shm").read_bytes()
        )
        fresh_backup(root)
        before = {entry.name for entry in root.iterdir()}
        sidecars_before = sidecar_metadata(database)

        checks = doctor.ephemeris_checks(root, False)

        assert {entry.name for entry in root.iterdir()} == before
        assert sidecar_metadata(database) == sidecars_before
        readable = by_id(checks, "subsystem.ephemeris.database_readable")
        assert readable.status == "ok"
        assert "immutable read-only" in readable.detail
        for check_id in (
            "subsystem.ephemeris.schema_compatible",
            "subsystem.ephemeris.integrity_check",
        ):
            finding = by_id(checks, check_id)
            assert finding.status == "warning"
            assert "unverified" in finding.detail
            assert "WAL tail is not visible" in finding.detail
    finally:
        writer.close()


def test_wal_without_shm_makes_schema_and_integrity_unverified_without_mutation(
    isolated_doctor: Path,
) -> None:
    make_ephemeris_engine(isolated_doctor, 2)
    source = isolated_doctor.parent / "wal-source-without-shm"
    source_database = make_database(source, 1)
    writer = sqlite3.connect(source_database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("PRAGMA user_version = 2")
    writer.execute("INSERT INTO fixture DEFAULT VALUES")
    writer.commit()
    try:
        root = isolated_doctor.parent / "private-wal-without-shm"
        root.mkdir()
        database = root / "activity.sqlite"
        wal = database.with_name(database.name + "-wal")
        shm = database.with_name(database.name + "-shm")
        database.write_bytes(source_database.read_bytes())
        wal.write_bytes(
            source_database.with_name(source_database.name + "-wal").read_bytes()
        )
        fresh_backup(root)
        before = {entry.name for entry in root.iterdir()}
        sidecars_before = sidecar_metadata(database)

        checks = doctor.ephemeris_checks(root, False)

        assert {entry.name for entry in root.iterdir()} == before
        assert sidecar_metadata(database) == sidecars_before
        for check_id in (
            "subsystem.ephemeris.schema_compatible",
            "subsystem.ephemeris.integrity_check",
        ):
            finding = by_id(checks, check_id)
            assert finding.status == "warning"
            assert "unverified" in finding.detail
            assert "WAL tail is not visible" in finding.detail
    finally:
        writer.close()


def test_junk_wal_does_not_soften_corrupt_main_file_or_mutate_sidecars(
    isolated_doctor: Path,
) -> None:
    make_ephemeris_engine(isolated_doctor, 1)
    root = isolated_doctor.parent / "private-empty-wal-corrupt-main"
    database = make_database(root, 1)
    connection = sqlite3.connect(database)
    connection.executemany(
        "INSERT INTO fixture DEFAULT VALUES",
        [() for _ in range(200)],
    )
    connection.commit()
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    connection.close()
    contents = bytearray(database.read_bytes())
    contents[page_size : 2 * page_size] = b"X" * page_size
    database.write_bytes(contents)
    wal = database.with_name(database.name + "-wal")
    shm = database.with_name(database.name + "-shm")
    wal.write_bytes(b"J" * doctor.SQLITE_WAL_HEADER_BYTES)
    fresh_backup(root)
    before = {entry.name for entry in root.iterdir()}
    sidecars_before = sidecar_metadata(database)

    checks = doctor.ephemeris_checks(root, False)

    assert by_id(checks, "subsystem.ephemeris.integrity_check").status == "blocked"
    assert {entry.name for entry in root.iterdir()} == before
    assert sidecar_metadata(database) == sidecars_before


def test_valid_wal_header_with_full_frame_always_uses_immutable_connection(
    isolated_doctor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = isolated_doctor.parent / "private-readonly-recovery"
    database = make_database(root, 1)
    wal = database.with_name(database.name + "-wal")
    shm = database.with_name(database.name + "-shm")
    wal.write_bytes(synthetic_pending_wal())
    shm.write_bytes(b"S" * 32)
    fresh_backup(root)
    before = {entry.name for entry in root.iterdir()}
    sidecars_before = sidecar_metadata(database)

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class QuickCheckRefused:
        def execute(self, statement):
            if statement == "PRAGMA user_version":
                return Result((1,))
            raise sqlite3.DatabaseError("fixture quick-check refusal")

        def close(self):
            return None

    uris: list[str] = []

    def connect(database_uri, **_kwargs):
        uris.append(database_uri)
        return QuickCheckRefused()

    monkeypatch.setattr(doctor.sqlite3, "connect", connect)
    checks = doctor.ephemeris_checks(root, False)

    assert by_id(checks, "subsystem.ephemeris.database_readable").status == "ok"
    assert by_id(checks, "subsystem.ephemeris.schema_compatible").status == "warning"
    assert by_id(checks, "subsystem.ephemeris.integrity_check").status == "warning"
    assert uris[0].endswith("?mode=ro&immutable=1")
    assert {entry.name for entry in root.iterdir()} == before
    assert sidecar_metadata(database) == sidecars_before


def test_failed_integrity_with_wal_is_warning(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = isolated_doctor.parent / "private-pending-writes"
    database = make_database(root, 1)
    database.with_name(database.name + "-wal").write_bytes(synthetic_pending_wal())
    fresh_backup(root)
    sidecars_before = sidecar_metadata(database)

    class UserVersion:
        def fetchone(self):
            return (1,)

    class FailingQuickCheck:
        def execute(self, statement):
            if statement == "PRAGMA user_version":
                return UserVersion()
            raise sqlite3.DatabaseError("fixture failure")

        def close(self):
            return None

    uris: list[str] = []

    def connect(database_uri, **_kwargs):
        uris.append(database_uri)
        return FailingQuickCheck()

    monkeypatch.setattr(doctor.sqlite3, "connect", connect)
    checks = doctor.ephemeris_checks(root, False)
    integrity = by_id(checks, "subsystem.ephemeris.integrity_check")
    assert integrity.status == "warning"
    assert integrity.detail == "integrity is unverified because the WAL tail is not visible"
    assert uris[0].endswith("?mode=ro&immutable=1")
    assert sidecar_metadata(database) == sidecars_before


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


def test_backup_freshness_ignores_directories_fifos_and_symlinks(
    isolated_doctor: Path,
) -> None:
    root = isolated_doctor.parent / "private-special-backups"
    backups = root / "backups"
    backups.mkdir(parents=True)
    stale = backups / "stale.backup"
    stale.write_bytes(b"")
    old = time.time() - 9 * 86400
    os.utime(stale, (old, old))
    (backups / "new-directory").mkdir()
    os.mkfifo(backups / "new-fifo")
    target = root / "new-target"
    target.write_bytes(b"")
    (backups / "new-symlink").symlink_to(target)

    stale_finding = doctor._ephemeris_backup_check(root, False)
    assert stale_finding.status == "warning"
    assert "9 whole days old" in stale_finding.detail

    stale.unlink()
    absent_finding = doctor._ephemeris_backup_check(root, False)
    assert absent_finding.status == "warning"
    assert absent_finding.detail == "no backups are available"


def test_activity_db_override_inside_public_checkout_is_not_opened(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = isolated_doctor.parent / "private-activity-root"
    root.mkdir()
    public_database = make_database(isolated_doctor / "fixture", 1)
    monkeypatch.setenv("ACTIVITY_DB", str(public_database))

    def reject_open(*_args, **_kwargs):
        pytest.fail("public-checkout database must not be opened")

    monkeypatch.setattr(doctor.sqlite3, "connect", reject_open)
    checks = doctor.ephemeris_checks(root, False)
    readable = by_id(checks, "subsystem.ephemeris.database_readable")
    assert readable.status == "blocked"
    assert "public checkout" in readable.detail
    assert "fixture" not in readable.detail
    assert by_id(checks, "subsystem.ephemeris.schema_compatible").status == (
        "not_applicable"
    )
    assert by_id(checks, "subsystem.ephemeris.integrity_check").status == (
        "not_applicable"
    )


def test_default_activity_db_symlink_into_public_checkout_is_not_opened(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = isolated_doctor.parent / "private-default-activity-root"
    root.mkdir()
    public_database = make_database(isolated_doctor / "fixture", 1)
    (root / "activity.sqlite").symlink_to(public_database)

    def reject_open(*_args, **_kwargs):
        pytest.fail("public-checkout database target must not be opened")

    monkeypatch.setattr(doctor.sqlite3, "connect", reject_open)
    checks = doctor.ephemeris_checks(root, False)

    readable = by_id(checks, "subsystem.ephemeris.database_readable")
    assert readable.status == "blocked"
    assert "public checkout" in readable.detail
    assert "fixture" not in readable.detail
    assert by_id(checks, "subsystem.ephemeris.schema_compatible").status == (
        "not_applicable"
    )
    assert by_id(checks, "subsystem.ephemeris.integrity_check").status == (
        "not_applicable"
    )


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


@pytest.mark.parametrize("number", ["1e999", "-1e999"])
def test_overflowed_json_number_is_malformed(
    isolated_doctor: Path,
    number: str,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-overflow")
    (root / "state" / "events.jsonl").write_text(
        f'{{"value":{number}}}\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.journals_parse",
    )
    assert finding.status == "blocked"
    assert "row 1" in finding.detail
    assert number not in finding.detail


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


def test_later_opened_receipt_supersedes_earlier_processed(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-latest-receipt")
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"private-latest-key","marker":"processed"}\n'
        '{"intake":"private-latest-key","marker":"opened"}\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "warning"
    assert "1 intake keys" in finding.detail
    assert "private-latest-key" not in finding.detail


def test_processed_receipt_without_prior_opened_is_not_silently_clean(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-orphan-processed")
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"private-orphan-key","marker":"processed"}\n',
        encoding="utf-8",
    )
    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.receipts_complete",
    )
    assert finding.status == "warning"
    assert "1 intake keys" in finding.detail
    assert "without a preceding opened receipt" in finding.detail
    assert "private-orphan-key" not in finding.detail


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


def test_nested_rotation_directory_marks_journal_scan_incomplete(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-nested-rotation")
    nested = root / "state" / "events" / "nested"
    nested.mkdir(parents=True)
    (nested / "ignored.jsonl").write_text('{"safe":true}\n', encoding="utf-8")

    checks = doctor.atlas_checks(root, False)

    finding = by_id(checks, "subsystem.atlas.journals_parse")
    assert finding.status == "warning"
    assert "not fully checked" in finding.detail


def test_nested_receipt_rotation_makes_completeness_not_applicable(
    isolated_doctor: Path,
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-nested-receipts")
    nested = root / "state" / "receipts" / "nested"
    nested.mkdir(parents=True)
    (nested / "ignored.jsonl").write_text(
        '{"intake":"fixture-key","marker":"opened"}\n',
        encoding="utf-8",
    )

    checks = doctor.atlas_checks(root, False)

    assert by_id(checks, "subsystem.atlas.journals_parse").status == "warning"
    receipts = by_id(checks, "subsystem.atlas.receipts_complete")
    assert receipts.status == "not_applicable"
    assert "not fully checked" in receipts.detail


def test_journal_byte_cap_is_warning_not_failure(
    isolated_doctor: Path, monkeypatch
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-bounded")
    (root / "state" / "events.jsonl").write_text('{"ok":true}\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "ATLAS_MAX_JOURNAL_BYTES", 4)
    finding = by_id(doctor.atlas_checks(root, False), "subsystem.atlas.journals_parse")
    assert finding.status == "warning"
    assert "not fully checked" in finding.detail


def test_aggregate_receipt_byte_cap_warns_and_skips_completeness(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-receipt-byte-budget")
    rotated = root / "state" / "receipts"
    rotated.mkdir()
    first = b'{"intake":"fixture-key","marker":"opened"}\n'
    tail = b'{"intake":"fixture-key","marker":"processed"}\n'
    (rotated / "0001.jsonl").write_bytes(first)
    (root / "state" / "receipts.jsonl").write_bytes(tail)
    monkeypatch.setattr(
        doctor,
        "ATLAS_MAX_RECEIPT_BYTES",
        max(len(first), len(tail)),
    )

    checks = doctor.atlas_checks(root, False)
    assert by_id(checks, "subsystem.atlas.journals_parse").status == "warning"
    assert by_id(checks, "subsystem.atlas.receipts_complete").status == (
        "not_applicable"
    )


def test_aggregate_all_journal_byte_cap_warns(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-all-journal-byte-budget")
    (root / "state" / "receipts.jsonl").write_bytes(b"")
    journal = b'{"safe":true}\n'
    (root / "state" / "alpha.jsonl").write_bytes(journal)
    (root / "state" / "beta.jsonl").write_bytes(journal)
    monkeypatch.setattr(doctor, "ATLAS_MAX_TOTAL_JOURNAL_BYTES", len(journal))

    finding = by_id(
        doctor.atlas_checks(root, False),
        "subsystem.atlas.journals_parse",
    )

    assert finding.status == "warning"
    assert "not fully checked" in finding.detail


def test_aggregate_receipt_key_cap_warns_and_skips_completeness(
    isolated_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_atlas(isolated_doctor.parent / "atlas-receipt-key-budget")
    (root / "state" / "receipts.jsonl").write_text(
        '{"intake":"fixture-key-one","marker":"opened"}\n'
        '{"intake":"fixture-key-two","marker":"opened"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "ATLAS_MAX_RECEIPT_KEYS", 1)

    checks = doctor.atlas_checks(root, False)
    assert by_id(checks, "subsystem.atlas.journals_parse").status == "warning"
    assert by_id(checks, "subsystem.atlas.receipts_complete").status == (
        "not_applicable"
    )


def test_absent_subsystem_subjects_are_all_not_applicable() -> None:
    checks = doctor.ephemeris_checks(None, False) + doctor.atlas_checks(None, False)
    assert {check.status for check in checks} == {"not_applicable"}
