"""Regression coverage for the already-shipped repository and root checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import doctor

from conftest import run_git


def check_by_id(checks: list[doctor.Check], check_id: str) -> doctor.Check:
    """Make failures identify the stable contract key under test."""
    return next(check for check in checks if check.id == check_id)


def test_missing_sibling_is_blocked(isolated_doctor: Path) -> None:
    checks = doctor.repo_checks("ephemeris", "0" * 40, False)
    assert [check.status for check in checks] == [
        "blocked",
        "not_applicable",
        "not_applicable",
    ]


def test_unknown_pin_is_warning(isolated_doctor: Path, make_repo) -> None:
    make_repo(isolated_doctor.parent / "ephemeris")
    checks = doctor.repo_checks("ephemeris", "f" * 40, False)
    assert check_by_id(checks, "repo.revision_matches_pin").status == "warning"


def test_non_commit_pin_is_warning(isolated_doctor: Path, make_repo) -> None:
    repo = isolated_doctor.parent / "ephemeris"
    make_repo(repo)
    blob = run_git(repo, "hash-object", "-w", "--stdin", input_text="not a commit")
    checks = doctor.repo_checks("ephemeris", blob, False)
    revision = check_by_id(checks, "repo.revision_matches_pin")
    assert revision.status == "warning"
    assert "blob object" in revision.detail


def test_wrong_commit_and_dirty_repo_warn(isolated_doctor: Path, make_repo) -> None:
    repo = isolated_doctor.parent / "ephemeris"
    pin = make_repo(repo)
    (repo / "second.txt").write_text("Vera Example second commit\n", encoding="utf-8")
    run_git(repo, "add", "second.txt")
    run_git(repo, "commit", "-q", "-m", "second")
    (repo / "dirty.txt").write_text("Vera Example dirty\n", encoding="utf-8")

    checks = doctor.repo_checks("ephemeris", pin, False)
    assert check_by_id(checks, "repo.revision_matches_pin").status == "warning"
    assert check_by_id(checks, "repo.worktree_clean").status == "warning"


def test_private_root_inside_public_repo_is_blocked(isolated_doctor: Path) -> None:
    private = isolated_doctor / "private-marker"
    checks = doctor.instance_checks("atlas", private, "test", False)
    containment = check_by_id(checks, "instance.root_outside_public")
    assert containment.status == "blocked"
    assert "private-marker" not in containment.detail


def test_instances_loader_keeps_runtime_separate(
    isolated_doctor: Path, monkeypatch
) -> None:
    config = isolated_doctor.parent / "config.toml"
    config.write_text(
        '[instances]\natlas = "/tmp/vera-atlas"\n'
        '[runtime]\noverride_path = "/tmp/vera-override"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "CONFIG_PATH", config)
    assert doctor.load_config_instances() == {"atlas": "/tmp/vera-atlas"}
    assert doctor.load_user_config()[1] == "/tmp/vera-override"


def test_rejected_public_roots_make_all_subsystem_checks_not_applicable(
    isolated_doctor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ephemeris_root = isolated_doctor / "rejected-ephemeris-instance"
    atlas_root = isolated_doctor / "rejected-atlas-instance"
    monkeypatch.setenv("ACTIVITY_DATA_DIR", str(ephemeris_root))
    monkeypatch.setenv("ATLAS_INSTANCE", str(atlas_root))

    def reject_inspection(*_args, **_kwargs):
        pytest.fail("a rejected public root must not be inspected")

    original_lstat = Path.lstat

    def guarded_lstat(path, *args, **kwargs):
        if any(path.is_relative_to(root) for root in (ephemeris_root, atlas_root)):
            pytest.fail("a rejected public root must not be stat-walked")
        return original_lstat(path, *args, **kwargs)

    def safe_runner_path(roots):
        assert ephemeris_root not in roots
        assert atlas_root not in roots
        return "", {}

    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    monkeypatch.setattr(doctor.sqlite3, "connect", reject_inspection)
    monkeypatch.setattr(doctor, "_open_directory_no_follow", reject_inspection)
    monkeypatch.setattr(doctor, "_safe_runner_path", safe_runner_path)
    checks = doctor.collect_checks(False)

    for label in ("ephemeris", "atlas"):
        containment = next(
            check
            for check in checks
            if check.id == "instance.root_outside_public" and check.label == label
        )
        assert containment.status == "blocked"
        subsystem = [
            check
            for check in checks
            if check.label == label and check.id.startswith("subsystem.")
        ]
        assert subsystem
        assert {check.status for check in subsystem} == {"not_applicable"}
        assert all(
            "inspection is refused across the public-instance boundary"
            in check.detail
            for check in subsystem
        )


def test_unknown_user_instance_path_is_reported_without_expansion_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTIVITY_DATA_DIR", "~missing-user/data")
    root, source = doctor.discover_instance("ephemeris", {})
    assert root is None
    checks = doctor.instance_checks("ephemeris", root, source, False)
    assert check_by_id(checks, "instance.root_configured").status == "warning"
    assert check_by_id(checks, "instance.root_outside_public").status == (
        "not_applicable"
    )


def test_looping_configured_root_is_reported_without_aborting_other_checks(
    isolated_doctor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = isolated_doctor.parent / "instance-loop-first"
    second = isolated_doctor.parent / "instance-loop-second"
    first.symlink_to(second)
    second.symlink_to(first)
    monkeypatch.setenv("ATLAS_INSTANCE", str(first))

    checks = doctor.collect_checks(False)

    configured = next(
        check
        for check in checks
        if check.id == "instance.root_configured" and check.label == "atlas"
    )
    outside = next(
        check
        for check in checks
        if check.id == "instance.root_outside_public" and check.label == "atlas"
    )
    assert configured.status == "warning"
    assert "invalid or unreadable" in configured.detail
    assert outside.status == "not_applicable"
    assert any(check.label != "atlas" for check in checks)


def test_nul_instance_path_is_reported_without_crashing() -> None:
    root, source = doctor.discover_instance("atlas", {"atlas": "\x00"})

    assert root is None
    assert source == "invalid path from user config instances.atlas"
    checks = doctor.instance_checks("atlas", root, source, False)
    assert check_by_id(checks, "instance.root_configured").status == "warning"
