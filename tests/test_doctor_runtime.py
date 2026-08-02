"""Agent-runtime checks stay optional, bounded, and content-safe."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from scripts import doctor


def by_id(checks: list[doctor.Check], check_id: str) -> doctor.Check:
    """Select the single runtime result promised by each public ID."""
    return next(check for check in checks if check.id == check_id)


def test_no_runners_is_not_applicable() -> None:
    checks = doctor.runtime_runner_checks([])
    assert [check.status for check in checks] == ["not_applicable", "not_applicable"]


def test_one_missing_runner_warns_and_present_runner_uses_version_only(
    isolated_doctor: Path, monkeypatch
) -> None:
    executable = isolated_doctor.parent / "bin" / "codex"
    executable.parent.mkdir()
    executable.write_text("Vera Example placeholder\n", encoding="utf-8")
    monkeypatch.setattr(
        doctor,
        "_safe_runner_path",
        lambda _roots: (str(executable.parent), {"codex": executable}),
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "codex-cli 1.2.3\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    checks = doctor.runtime_runner_checks([])
    assert by_id(checks, "runtime.runner_present").status == "warning"
    assert by_id(checks, "runtime.runner_version").status == "ok"
    assert calls[0][0] == (str(executable), "--version")
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["cwd"] == "/"
    assert set(calls[0][1]["env"]) == {"PATH", "LANG", "LC_ALL", "NO_COLOR"}


def test_unparseable_runner_output_warns_without_echo(
    isolated_doctor: Path, monkeypatch
) -> None:
    executable = isolated_doctor.parent / "bin" / "codex"
    executable.parent.mkdir()
    executable.write_text("Vera Example placeholder\n", encoding="utf-8")
    monkeypatch.setattr(
        doctor,
        "_safe_runner_path",
        lambda _roots: (str(executable.parent), {"codex": executable}),
    )
    secret = "/private/path/SHOULD-NOT-LEAK"
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, secret, ""),
    )
    finding = by_id(doctor.runtime_runner_checks([]), "runtime.runner_version")
    assert finding.status == "warning"
    assert secret not in finding.detail


def test_stale_override_reports_only_date_and_age(isolated_doctor: Path) -> None:
    override = isolated_doctor.parent / "private-override-marker.md"
    verified = dt.date.today() - dt.timedelta(days=91)
    override.write_text(
        "secret line MUST-NOT-LEAK\n"
        f"Last verified: {verified.isoformat()} (Vera Example)\n"
        "another private line\n",
        encoding="utf-8",
    )
    finding = doctor.runtime_override_check(str(override), False)
    assert finding.status == "warning"
    assert verified.isoformat() in finding.detail
    assert "91 days" in finding.detail
    assert "MUST-NOT-LEAK" not in finding.detail
    assert "private-override-marker" not in finding.detail


def test_unset_override_is_not_applicable() -> None:
    finding = doctor.runtime_override_check(None, False)
    assert finding.status == "not_applicable"
    assert "docs/model-override.example.md" in finding.remediation
