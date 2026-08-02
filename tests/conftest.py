"""Keep doctor tests hermetic from the developer's actual workspace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import doctor  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_doctor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect every discovery seam before a test can observe user state."""
    public_root = tmp_path / "selfos"
    public_root.mkdir()
    monkeypatch.setattr(doctor, "ROOT", public_root)
    monkeypatch.setattr(doctor, "PINS_PATH", public_root / "pins.toml")
    monkeypatch.setattr(doctor, "CONFIG_PATH", tmp_path / "config.toml")
    for variable in (*doctor.ENV_VARS.values(), "ACTIVITY_DB"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(doctor, "_safe_runner_path", lambda _roots: ("", {}))
    doctor.git_env.cache_clear()
    yield public_root
    doctor.git_env.cache_clear()


def run_git(repo: Path, *args: str, input_text: str | None = None) -> str:
    """Create only local fixture history with deterministic author metadata."""
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Vera Example",
            "GIT_AUTHOR_EMAIL": "vera@example.invalid",
            "GIT_COMMITTER_NAME": "Vera Example",
            "GIT_COMMITTER_EMAIL": "vera@example.invalid",
        }
    )
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        input=input_text,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    return result.stdout.strip()


@pytest.fixture
def make_repo():
    """Give tests small real repositories so Git behavior is not mocked."""
    def build(path: Path, files: dict[str, str] | None = None) -> str:
        path.mkdir(parents=True)
        run_git(path, "init", "-q")
        material = files or {"README.md": "Vera Example fixture\n"}
        for relative, content in material.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        run_git(path, "add", ".")
        run_git(path, "commit", "-q", "-m", "fixture")
        return run_git(path, "rev-parse", "HEAD")

    return build


@pytest.fixture
def write_pins():
    """Write only synthetic full SHAs into the temporary public root."""
    def write(root: Path, pins: dict[str, str]) -> None:
        body = "".join(f'{name} = "{pins[name]}"\n' for name in doctor.EXPECTED_REPOS)
        (root / "pins.toml").write_text(body, encoding="utf-8")

    return write
