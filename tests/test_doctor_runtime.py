"""Agent-runtime checks stay optional, bounded, and content-safe."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from scripts import doctor


SAFE_RUNNER_PATH = doctor._safe_runner_path


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

    def fake_version(path, environment):
        calls.append((path, environment))
        return 0, "codex-cli 1.2.3\n"

    monkeypatch.setattr(doctor, "_bounded_runner_version", fake_version)
    checks = doctor.runtime_runner_checks([])
    assert by_id(checks, "runtime.runner_present").status == "warning"
    assert by_id(checks, "runtime.runner_version").status == "ok"
    assert calls[0][0] == executable
    assert set(calls[0][1]) == {"PATH", "LANG", "LC_ALL", "NO_COLOR"}


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
        doctor,
        "_bounded_runner_version",
        lambda _path, _environment: (0, secret),
    )
    finding = by_id(doctor.runtime_runner_checks([]), "runtime.runner_version")
    assert finding.status == "warning"
    assert secret not in finding.detail


def test_runner_inside_sibling_checkout_is_not_resolved(
    isolated_doctor: Path, monkeypatch
) -> None:
    executable = isolated_doctor.parent / "atlas" / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(doctor, "_safe_runner_path", SAFE_RUNNER_PATH)
    monkeypatch.setattr(
        doctor.os,
        "get_exec_path",
        lambda: [str(executable.parent)],
    )

    search_path, found = doctor._safe_runner_path([])
    assert search_path == ""
    assert found == {}


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


def test_unknown_user_override_path_warns_without_expansion_crash() -> None:
    finding = doctor.runtime_override_check(
        "~missing-user/override.md",
        False,
    )
    assert finding.status == "warning"
    assert finding.detail == "configured runtime override is missing or unreadable"
    assert "missing-user" not in finding.detail


@pytest.mark.parametrize("failure", ["budget", "timeout"])
def test_runner_version_budget_and_timeout_reap_child(
    isolated_doctor: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    executable = isolated_doctor.parent / f"runner-{failure}"
    pid_file = isolated_doctor.parent / f"runner-{failure}.pid"
    action = (
        "os.write(1, b'x' * 65); time.sleep(30)"
        if failure == "budget"
        else "time.sleep(30)"
    )
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"{action}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(doctor, "RUNNER_VERSION_MAX_BYTES", 64)
    monkeypatch.setattr(doctor, "RUNNER_VERSION_TIMEOUT_SECONDS", 0.2)

    result = doctor._bounded_runner_version(
        executable,
        {"PATH": "/usr/bin", "LANG": "C", "LC_ALL": "C", "NO_COLOR": "1"},
    )

    assert result is None
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
