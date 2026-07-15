"""Diagnose whether the selfos repository and private roots are coherent.

This first staged slice implements issue #7's repository and private-instance
topology checks. It is read-only, offline, never invokes a model, and never
clones, fetches, repairs, or otherwise mutates a checkout or instance. Because
doctor checks all subsystems together, private roots are discovered from each
subsystem environment variable and then ``~/.config/selfos/config.toml``;
doctor has no per-subsystem ``--instance`` flag. Requires Python 3.11+
(tomllib).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "pins.toml"
CONFIG_PATH = Path.home() / ".config" / "selfos" / "config.toml"
EXPECTED_REPOS = ("ephemeris", "atlas", "exp2res")
ENV_VARS = {
    "ephemeris": "ACTIVITY_DATA_DIR",
    "atlas": "ATLAS_INSTANCE",
    "exp2res": "EXP2RES_WORKSPACE",
}
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
STATUS_RANK = {"not_applicable": 0, "ok": 0, "warning": 1, "blocked": 2}


class ManifestError(ValueError):
    """The pins manifest is missing or does not match its strict contract."""


class DoctorInternalError(RuntimeError):
    """Doctor could not safely interpret local configuration or Git state."""


@dataclass(frozen=True)
class Check:
    """One stable, machine-readable doctor result."""

    id: str
    label: str
    status: str
    detail: str
    remediation: str = ""


def load_pins(path: Path = PINS_PATH) -> dict[str, str]:
    """Load exactly one full commit SHA for each expected sibling."""
    try:
        with path.open("rb") as manifest:
            data = tomllib.load(manifest)
    except FileNotFoundError as exc:
        raise ManifestError("pins.toml is missing") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read pins.toml: {exc}") from exc

    keys = set(data)
    expected = set(EXPECTED_REPOS)
    if keys != expected:
        parts = []
        if missing := sorted(expected - keys):
            parts.append(f"missing keys: {', '.join(missing)}")
        if unexpected := sorted(keys - expected):
            parts.append(f"unknown keys: {', '.join(unexpected)}")
        raise ManifestError("invalid pins.toml (" + "; ".join(parts) + ")")

    pins: dict[str, str] = {}
    for name in EXPECTED_REPOS:
        value = data[name]
        if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
            raise ManifestError(
                f"invalid pins.toml ({name} must be a full 40-character SHA)"
            )
        pins[name] = value.lower()
    return pins


@functools.cache
def git_env() -> dict[str, str]:
    """Build a caller-independent environment for Git in a sibling repo.

    Repo-local variables (GIT_DIR, GIT_WORK_TREE, ...) exported by a
    caller such as a Git hook would override ``git -C`` and point the
    query at the wrong repository, so everything git itself lists as
    local is removed. GIT_NO_LAZY_FETCH keeps a partial/promisor clone
    from fetching missing objects on demand; GIT_NO_REPLACE_OBJECTS
    keeps refs/replace/* from silently substituting the pinned commit.
    """
    env = os.environ.copy()
    listed = subprocess.run(
        ("git", "rev-parse", "--local-env-vars"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.split()
    fallback = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
    for var in (*listed, *fallback):
        env.pop(var, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only local Git query in a caller-independent environment."""
    env = git_env()
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def is_git_repo(path: Path) -> bool:
    """Return whether path is the root of a working-tree Git repository."""
    if not path.is_dir():
        return False
    result = run_git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return False
    return Path(result.stdout.strip()).resolve() == path.resolve()


def git_output(repo: Path, *args: str) -> str:
    """Return stripped Git stdout or raise an internal inspection error."""
    result = run_git(repo, *args)
    if result.returncode != 0:
        raise DoctorInternalError("a local Git inspection command failed")
    return result.stdout.strip()


def pin_is_available(repo: Path, pin: str) -> bool:
    """Return whether pin resolves to a commit in the local object store."""
    return run_git(repo, "cat-file", "-e", f"{pin}^{{commit}}").returncode == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Test commit ancestry, distinguishing false from an inspection error."""
    result = run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise DoctorInternalError("a local Git ancestry command failed")


def revision_state(repo: Path, head: str, pin: str) -> str:
    """Classify HEAD relative to a locally available pin."""
    if head == pin:
        return "match"
    if is_ancestor(repo, pin, head):
        return "ahead"
    if is_ancestor(repo, head, pin):
        return "behind"
    return "diverged"


def abbreviated(sha: str) -> str:
    """Use the repository-wide diagnostic SHA convention."""
    return sha[:12]


def path_suffix(path: Path, show_paths: bool) -> str:
    """Reveal a resolved path only when the caller explicitly requested it."""
    return f" at {path.resolve()}" if show_paths else ""


def repo_checks(
    label: str,
    pin: str | None,
    show_paths: bool,
) -> list[Check]:
    """Build the three stable repository checks for one sibling."""
    repo = ROOT.parent / label
    if not is_git_repo(repo):
        return [
            Check(
                "repo.sibling_present",
                label,
                "blocked",
                "expected sibling is missing or is not a Git repository"
                + path_suffix(repo, show_paths),
                "Create or restore the expected sibling checkout; "
                "doctor will not clone it.",
            ),
            Check(
                "repo.revision_matches_pin",
                label,
                "not_applicable",
                "revision cannot be checked because the sibling is absent",
            ),
            Check(
                "repo.worktree_clean",
                label,
                "not_applicable",
                "working tree cannot be checked because the sibling is absent",
            ),
        ]

    checks = [
        Check(
            "repo.sibling_present",
            label,
            "ok",
            "expected sibling is a Git repository" + path_suffix(repo, show_paths),
        )
    ]
    head = git_output(repo, "rev-parse", "--verify", "HEAD^{commit}")

    if pin is None:
        checks.append(
            Check(
                "repo.revision_matches_pin",
                label,
                "not_applicable",
                "revision cannot be checked because pins.toml is unavailable",
                "Restore a valid pins.toml and run doctor again.",
            )
        )
    elif not pin_is_available(repo, pin):
        checks.append(
            Check(
                "repo.revision_matches_pin",
                label,
                "warning",
                f"pin {abbreviated(pin)} is unknown in the local object store; "
                f"HEAD is {abbreviated(head)}",
                f"Run git fetch manually in {label}, then run doctor again.",
            )
        )
    else:
        state = revision_state(repo, head, pin)
        if state == "match":
            checks.append(
                Check(
                    "repo.revision_matches_pin",
                    label,
                    "ok",
                    f"HEAD {abbreviated(head)} matches pin {abbreviated(pin)}",
                )
            )
        else:
            checks.append(
                Check(
                    "repo.revision_matches_pin",
                    label,
                    "warning",
                    f"HEAD {abbreviated(head)} is {state} pin {abbreviated(pin)}",
                    "Keep the deliberate local revision or run scripts/sync.py "
                    "when ready.",
                )
            )

    # --untracked-files=all overrides a status.showUntrackedFiles=no
    # repository config, which would otherwise hide untracked files.
    dirty = bool(git_output(repo, "status", "--porcelain", "--untracked-files=all"))
    if dirty:
        checks.append(
            Check(
                "repo.worktree_clean",
                label,
                "warning",
                "working tree has staged, unstaged, or untracked changes",
                f"Review the local changes in {label}; doctor will not modify them.",
            )
        )
    else:
        checks.append(
            Check(
                "repo.worktree_clean",
                label,
                "ok",
                "working tree is clean",
            )
        )
    return checks


def load_config_instances() -> dict[str, str]:
    """Read configured instance values, treating a missing config as empty."""
    try:
        with CONFIG_PATH.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DoctorInternalError("cannot parse the user-scope selfos config") from exc

    instances = data.get("instances", {})
    if not isinstance(instances, dict):
        raise DoctorInternalError("the user-scope [instances] value is not a table")

    configured: dict[str, str] = {}
    for label in EXPECTED_REPOS:
        if label not in instances:
            continue
        value = instances[label]
        if not isinstance(value, str):
            raise DoctorInternalError(
                f"the user-scope instances.{label} value is not a string"
            )
        if value:
            configured[label] = value
    return configured


def discover_instance(
    label: str,
    config_instances: dict[str, str],
) -> tuple[Path | None, str]:
    """Apply doctor's environment-then-config discovery precedence.

    The returned path is absolute but deliberately unresolved: the
    containment check must also see the path as configured, not only
    its symlink target.
    """
    env_var = ENV_VARS[label]
    if value := os.environ.get(env_var):
        return Path(os.path.abspath(Path(value).expanduser())), (
            f"environment variable {env_var}"
        )
    if value := config_instances.get(label):
        return Path(os.path.abspath(Path(value).expanduser())), (
            f"user config instances.{label}"
        )
    return None, ""


def containing_public_root(root: Path) -> str | None:
    """Return the logical public checkout containing root, if any.

    Both the path as configured and its fully resolved target are
    tested: a symlink living inside a public checkout is refused even
    when it points outside (a public-checkout alias to private data),
    and a symlink living outside is refused when its target is inside.
    """
    public_roots = {"selfos": ROOT}
    public_roots.update({name: ROOT.parent / name for name in EXPECTED_REPOS})
    candidates = {root, root.resolve()}
    for label, public_root in public_roots.items():
        resolved_public = public_root.resolve()
        for candidate in candidates:
            if candidate.is_relative_to(resolved_public):
                return label
    return None


def instance_checks(
    label: str,
    root: Path | None,
    source: str,
    show_paths: bool,
) -> list[Check]:
    """Build the two stable private-root checks for one subsystem."""
    env_var = ENV_VARS[label]
    remediation = (
        f"Set {env_var} or user config instances.{label}; see docs/instance.md."
    )
    if root is None:
        return [
            Check(
                "instance.root_configured",
                label,
                "not_applicable",
                "no private root is configured; real capture remains blocked by design",
                remediation,
            ),
            Check(
                "instance.root_outside_public",
                label,
                "not_applicable",
                "public-checkout containment cannot be checked without a private root",
            ),
        ]

    checks = [
        Check(
            "instance.root_configured",
            label,
            "ok",
            f"private root is configured via {source}"
            + path_suffix(root, show_paths),
        )
    ]
    public_label = containing_public_root(root)
    if public_label is None:
        checks.append(
            Check(
                "instance.root_outside_public",
                label,
                "ok",
                "configured root is outside all public engine checkouts"
                + path_suffix(root, show_paths),
            )
        )
    else:
        checks.append(
            Check(
                "instance.root_outside_public",
                label,
                "blocked",
                "configured root equals or lies inside the "
                f"{public_label} public checkout"
                + path_suffix(root, show_paths),
                f"Move or reconfigure the {label} private root outside every "
                "public checkout; "
                "see docs/instance.md.",
            )
        )
    return checks


def collect_checks(show_paths: bool) -> list[Check]:
    """Collect this slice's checks in stable presentation order."""
    try:
        pins = load_pins()
    except ManifestError as exc:
        pins = None
        manifest_check = Check(
            "repo.pins_manifest",
            "selfos",
            "blocked",
            str(exc),
            "Restore pins.toml with exactly one full SHA for each expected sibling.",
        )
    else:
        manifest_check = Check(
            "repo.pins_manifest",
            "selfos",
            "ok",
            "pins.toml contains full revisions for all expected siblings",
        )

    checks = [manifest_check]
    for label in EXPECTED_REPOS:
        pin = pins[label] if pins is not None else None
        checks.extend(repo_checks(label, pin, show_paths))

    config_instances = load_config_instances()
    for label in EXPECTED_REPOS:
        root, source = discover_instance(label, config_instances)
        checks.extend(instance_checks(label, root, source, show_paths))
    return checks


def overall_state(checks: list[Check]) -> str:
    """Return the worst applicable status in the stable state vocabulary."""
    worst = max((STATUS_RANK[check.status] for check in checks), default=0)
    return {0: "ok", 1: "warning", 2: "blocked"}[worst]


def render_human(checks: list[Check], state: str) -> None:
    """Print one privacy-preserving line per check plus overall state."""
    prefixes = {
        "ok": "OK",
        "warning": "WARN",
        "blocked": "BLOCKED",
        "not_applicable": "n/a",
    }
    for check in checks:
        line = f"{prefixes[check.status]} {check.id} {check.label}: {check.detail}"
        if check.remediation:
            line += f" | remediation: {check.remediation}"
        print(line)
    print(f"OVERALL {state}")


def render_json(checks: list[Check], state: str) -> None:
    """Print the version-one machine-readable doctor envelope."""
    rendered_checks = []
    for check in checks:
        rendered = asdict(check)
        if not rendered["remediation"]:
            del rendered["remediation"]
        rendered_checks.append(rendered)
    print(json.dumps({"version": 1, "state": state, "checks": rendered_checks}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose selfos workspace topology.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the versioned machine-readable envelope",
    )
    parser.add_argument(
        "--show-paths",
        action="store_true",
        help="include resolved filesystem paths in diagnostic details",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        checks = collect_checks(args.show_paths)
        state = overall_state(checks)
    except (DoctorInternalError, OSError) as exc:
        detail = str(exc) if args.show_paths else "local state could not be inspected"
        print(f"doctor internal error: {detail}", file=sys.stderr)
        return 2

    if args.json:
        render_json(checks, state)
    else:
        render_human(checks, state)
    return 1 if state == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
