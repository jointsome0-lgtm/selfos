"""Regression coverage for the already-shipped repository and root checks."""

from __future__ import annotations

from pathlib import Path

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
