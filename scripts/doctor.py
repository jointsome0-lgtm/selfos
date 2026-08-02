"""Diagnose whether the selfos workspace is locally coherent.

This staged issue #7 implementation checks repository and private-instance
topology, ephemeris and Atlas subjects, and the approved agent runtimes. It is
read-only, offline, never invokes a model, and never clones, fetches, repairs,
or otherwise mutates a checkout or instance. Because doctor checks all
subsystems together, private roots are discovered from each subsystem
environment variable and then ``~/.config/selfos/config.toml``; doctor has no
per-subsystem ``--instance`` flag. Requires Python 3.11+ (tomllib).

SQLite inspection always uses immutable read-only mode so it cannot create or
modify sidecars. A bounded WAL-header probe recognizes a complete pending WAL
frame; when one exists, schema and integrity results are explicitly unverified
because immutable inspection cannot see the WAL tail.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import math
import os
import re
import select
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
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
SCHEMA_VERSION_LINE = re.compile(r"^\s*SCHEMA_VERSION\s*=\s*(\d+)\s*(?:#.*)?$")
VERIFIED_LINE = re.compile(r"^Last verified: (\d{4}-\d{2}-\d{2})(?:\s|$)")
STATUS_RANK = {"not_applicable": 0, "ok": 0, "warning": 1, "blocked": 2}
APPROVED_RUNNERS = ("codex", "claude")
BACKUP_RECENT_DAYS = 7
EPHEMERIS_MAX_BACKUP_ENTRIES = 4096
OVERRIDE_RECENT_DAYS = 90
RUNNER_VERSION_TIMEOUT_SECONDS = 3
RUNNER_VERSION_MAX_BYTES = 4 * 1024
SQLITE_WAL_HEADER_BYTES = 32
SQLITE_WAL_FRAME_HEADER_BYTES = 24
SQLITE_WAL_MAGIC = {0x377F0682, 0x377F0683}
ATLAS_MAX_STATE_ENTRIES = 1024
ATLAS_MAX_JOURNALS = 256
ATLAS_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
ATLAS_MAX_RECEIPT_BYTES = 32 * 1024 * 1024
ATLAS_MAX_TOTAL_JOURNAL_BYTES = 64 * 1024 * 1024
ATLAS_MAX_RECEIPT_KEYS = 100_000


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


@dataclass(frozen=True)
class ReceiptState:
    """The latest marker plus any invalid transition already seen for one key."""

    latest_marker: str
    invalid_transition: bool = False
    processed_without_open: bool = False


@dataclass(frozen=True)
class AtlasJournalScan:
    """Carry only bounded folded receipt state after safe journal inspection."""

    check: Check
    receipts_present: bool = False
    receipt_latest: dict[str, ReceiptState] | None = None
    receipt_shape_valid: bool = True


def load_pins(path: Path | None = None) -> dict[str, str]:
    """Resolve the manifest at call time so isolated callers cannot hit the host."""
    path = PINS_PATH if path is None else path
    try:
        with path.open("rb") as manifest:
            data = tomllib.load(manifest)
    except FileNotFoundError as exc:
        raise ManifestError("pins.toml is missing") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("cannot read pins.toml") from exc

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


def pin_object_type(repo: Path, pin: str) -> str | None:
    """Return the pin's local object type, or None when it is absent.

    The type must be checked, not just ``^{commit}`` resolvability: an
    annotated tag's own SHA peels to a commit, so a tag pin would pass
    a mere existence check while sync could never verify it as HEAD.
    """
    result = run_git(repo, "cat-file", "-t", pin)
    return result.stdout.strip() if result.returncode == 0 else None


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
    elif (pin_type := pin_object_type(repo, pin)) is None:
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
    elif pin_type != "commit":
        checks.append(
            Check(
                "repo.revision_matches_pin",
                label,
                "warning",
                f"pin {abbreviated(pin)} names a {pin_type} object, "
                "not a commit",
                "Re-pin to the commit SHA in pins.toml; sync refuses "
                "non-commit pins.",
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


def load_user_config() -> tuple[dict[str, str], str | None]:
    """Keep config failures explicit so doctor never guesses around bad policy."""
    try:
        with CONFIG_PATH.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError:
        return {}, None
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

    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict):
        raise DoctorInternalError("the user-scope [runtime] value is not a table")
    override_path = runtime.get("override_path")
    if override_path is not None and not isinstance(override_path, str):
        raise DoctorInternalError(
            "the user-scope runtime.override_path value is not a string"
        )
    return configured, override_path or None


def load_config_instances() -> dict[str, str]:
    """Preserve the shipped instances-only loader for existing callers."""
    instances, _override_path = load_user_config()
    return instances


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
        try:
            root = Path(os.path.abspath(Path(value).expanduser()))
        except RuntimeError:
            return None, f"invalid path from environment variable {env_var}"
        return root, f"environment variable {env_var}"
    if value := config_instances.get(label):
        try:
            root = Path(os.path.abspath(Path(value).expanduser()))
        except RuntimeError:
            return None, f"invalid path from user config instances.{label}"
        return root, f"user config instances.{label}"
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
    public_label: str | None = None,
    containment_checked: bool = False,
) -> list[Check]:
    """Build the two stable private-root checks for one subsystem."""
    env_var = ENV_VARS[label]
    remediation = (
        f"Set {env_var} or user config instances.{label}; see docs/instance.md."
    )
    if root is None:
        if source:
            return [
                Check(
                    "instance.root_configured",
                    label,
                    "warning",
                    "configured private root path is invalid or unreadable",
                    remediation,
                ),
                Check(
                    "instance.root_outside_public",
                    label,
                    "not_applicable",
                    "public-checkout containment cannot be checked for an invalid path",
                ),
            ]
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
    if not containment_checked:
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


def _age_days(timestamp: float) -> int:
    """Clamp future mtimes so clock skew cannot produce a misleading age."""
    return max(0, int((time.time() - timestamp) // 86400))


def _open_directory_no_follow(path: Path) -> int:
    """Bind every directory component so inspection cannot escape via symlink."""
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    directory_fd = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            info = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _sqlite_is_busy(exc: sqlite3.Error) -> bool:
    """Separate transient writer contention from stable database damage."""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    lowered = str(exc).lower()
    return "locked" in lowered or "busy" in lowered


def _sqlite_has_pending_wal(wal: Path) -> bool | None:
    """Recognize a pending WAL, distinguishing absence from unreadability."""
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    file_fd: int | None = None
    try:
        file_fd = os.open(wal, flags)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        header = os.read(file_fd, SQLITE_WAL_HEADER_BYTES)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
    if len(header) != SQLITE_WAL_HEADER_BYTES:
        return False
    magic = int.from_bytes(header[:4], "big")
    page_size = int.from_bytes(header[8:12], "big")
    valid_page_size = (
        512 <= page_size <= 65536 and page_size & (page_size - 1) == 0
    )
    return (
        magic in SQLITE_WAL_MAGIC
        and valid_page_size
        and info.st_size
        >= SQLITE_WAL_HEADER_BYTES + SQLITE_WAL_FRAME_HEADER_BYTES + page_size
    )


def _ephemeris_schema_version() -> int | None:
    """Read the engine constant as text to avoid import-time app configuration."""
    source = ROOT.parent / "ephemeris" / "app" / "db.py"
    try:
        with source.open("r", encoding="utf-8") as stream:
            for line in stream:
                if match := SCHEMA_VERSION_LINE.fullmatch(line.rstrip("\n")):
                    return int(match.group(1))
    except (OSError, UnicodeError):
        return None
    return None


def _ephemeris_backup_check(root: Path, show_paths: bool) -> Check:
    """Use mtimes only so backup health never opens or names private snapshots."""
    backups = root / "backups"
    remediation = "Run python -m scripts.backup_db in ephemeris."
    try:
        directory_fd = _open_directory_no_follow(backups)
    except (FileNotFoundError, NotADirectoryError):
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            "no backup directory is available" + path_suffix(backups, show_paths),
            remediation,
        )
    except OSError:
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            "backup recency could not be checked now"
            + path_suffix(backups, show_paths),
            remediation,
        )

    newest: float | None = None
    try:
        with os.scandir(directory_fd) as entries:
            for index, entry in enumerate(entries):
                if index >= EPHEMERIS_MAX_BACKUP_ENTRIES:
                    return Check(
                        "subsystem.ephemeris.backup_recent",
                        "ephemeris",
                        "warning",
                        "backup recency was not fully checked because a safety "
                        "cap was exceeded",
                        remediation,
                    )
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    return Check(
                        "subsystem.ephemeris.backup_recent",
                        "ephemeris",
                        "warning",
                        "backup recency could not be checked now",
                        remediation,
                    )
                if not stat.S_ISREG(info.st_mode):
                    continue
                modified = info.st_mtime
                newest = modified if newest is None else max(newest, modified)
    except OSError:
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            "backup recency could not be checked now",
            remediation,
        )
    finally:
        os.close(directory_fd)

    if newest is None:
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            "no backups are available",
            remediation,
        )
    if newest > time.time():
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            "backup recency is unverified because a backup timestamp is in the future",
            "Check the system clock and backup metadata, then run doctor again.",
        )
    age = _age_days(newest)
    if age > BACKUP_RECENT_DAYS:
        return Check(
            "subsystem.ephemeris.backup_recent",
            "ephemeris",
            "warning",
            f"newest backup is {age} whole days old",
            remediation,
        )
    return Check(
        "subsystem.ephemeris.backup_recent",
        "ephemeris",
        "ok",
        f"newest backup is {age} whole days old",
    )


def ephemeris_checks(
    root: Path | None,
    show_paths: bool,
    root_accepted: bool = True,
) -> list[Check]:
    """Keep one read-only connection so the three database views agree."""
    if not root_accepted:
        reason = "inspection is refused across the public-instance boundary"
        return [
            Check(check_id, "ephemeris", "not_applicable", reason)
            for check_id in (
                "subsystem.ephemeris.database_readable",
                "subsystem.ephemeris.schema_compatible",
                "subsystem.ephemeris.integrity_check",
                "subsystem.ephemeris.backup_recent",
            )
        ]
    if root is None:
        reason = "ephemeris private root is not configured"
        return [
            Check(
                "subsystem.ephemeris.database_readable",
                "ephemeris",
                "not_applicable",
                reason,
            ),
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                reason,
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                reason,
            ),
            Check(
                "subsystem.ephemeris.backup_recent",
                "ephemeris",
                "not_applicable",
                reason,
            ),
        ]

    override = os.environ.get("ACTIVITY_DB")
    try:
        database = (
            Path(os.path.abspath(Path(override).expanduser()))
            if override
            else root / "activity.sqlite"
        )
    except RuntimeError:
        reason = "configured ledger database path is invalid or unreadable"
        return [
            Check(
                "subsystem.ephemeris.database_readable",
                "ephemeris",
                "blocked",
                reason,
                "Restore a valid ledger path; doctor will not repair it.",
            ),
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                "schema cannot be checked because the database path is invalid",
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                "integrity cannot be checked because the database path is invalid",
            ),
            _ephemeris_backup_check(root, show_paths),
        ]
    # Ephemeris deliberately permits ACTIVITY_DB to be a free-standing path;
    # app/settings.py uses <data_dir>/activity.sqlite only as its unset default.
    # Requiring containment under ACTIVITY_DATA_DIR would reject supported,
    # deliberate layouts. The public-checkout boundary is the invariant here.
    public_label = containing_public_root(database)
    if public_label is not None:
        boundary = "configured ledger database is inside the " + (
            f"{public_label} public checkout"
        )
        return [
            Check(
                "subsystem.ephemeris.database_readable",
                "ephemeris",
                "blocked",
                boundary + path_suffix(database, show_paths),
                "Move or reconfigure the ephemeris database outside every "
                "public checkout; see docs/instance.md.",
            ),
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                "schema cannot be checked across the public-instance boundary",
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                "integrity cannot be checked across the public-instance boundary",
            ),
            _ephemeris_backup_check(root, show_paths),
        ]
    try:
        database.lstat()
    except FileNotFoundError:
        absent = "ledger database is absent; real capture remains blocked by design"
        return [
            Check(
                "subsystem.ephemeris.database_readable",
                "ephemeris",
                "not_applicable",
                absent,
            ),
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                absent,
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                absent,
            ),
            _ephemeris_backup_check(root, show_paths),
        ]
    except OSError:
        database_is_regular = False
    else:
        try:
            effective_database = database.resolve(strict=True)
            database_is_regular = stat.S_ISREG(effective_database.stat().st_mode)
        except (OSError, RuntimeError):
            database_is_regular = False

    if not database_is_regular:
        unreadable = Check(
            "subsystem.ephemeris.database_readable",
            "ephemeris",
            "blocked",
            "ledger database is present but cannot be inspected"
            + path_suffix(database, show_paths),
            "Restore readable database storage; doctor will not repair it.",
        )
        return [
            unreadable,
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                "schema cannot be checked because the database is unreadable",
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                "integrity cannot be checked because the database is unreadable",
            ),
            _ephemeris_backup_check(root, show_paths),
        ]

    wal = effective_database.with_name(effective_database.name + "-wal")
    wal_pending = _sqlite_has_pending_wal(wal)
    wal_unverified = wal_pending is not False
    wal_reason = (
        "the WAL tail is not visible"
        if wal_pending is True
        else "the WAL file could not be inspected"
    )
    wal_remediation = (
        "Checkpoint the ledger WAL explicitly, then run doctor again."
        if wal_pending is True
        else "Restore readable WAL storage or checkpoint it explicitly, then run "
        "doctor again."
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            effective_database.as_uri() + "?mode=ro&immutable=1",
            uri=True,
            timeout=0.2,
        )
        row = connection.execute("PRAGMA user_version").fetchone()
        if row is None or not isinstance(row[0], int):
            raise sqlite3.DatabaseError("invalid user_version result")
        actual_version = row[0]
    except sqlite3.Error as exc:
        transient = _sqlite_is_busy(exc)
        status = "warning" if transient else "blocked"
        if transient:
            detail = "ledger database could not be checked now"
        else:
            detail = "ledger database is present but cannot be opened read-only"
        readability = Check(
            "subsystem.ephemeris.database_readable",
            "ephemeris",
            status,
            detail + path_suffix(database, show_paths),
            (
                "Try again after the active database operation finishes."
                if transient
                else "Restore a readable SQLite ledger; doctor will not repair it."
            ),
        )
        if connection is not None:
            connection.close()
        return [
            readability,
            Check(
                "subsystem.ephemeris.schema_compatible",
                "ephemeris",
                "not_applicable",
                "schema cannot be checked because the database did not open",
            ),
            Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "not_applicable",
                "integrity cannot be checked because the database did not open",
            ),
            _ephemeris_backup_check(root, show_paths),
        ]

    readability = Check(
        "subsystem.ephemeris.database_readable",
        "ephemeris",
        "ok",
        "ledger database opens through an immutable read-only SQLite connection"
        + path_suffix(database, show_paths),
    )
    expected_version = _ephemeris_schema_version()
    if wal_unverified:
        schema = Check(
            "subsystem.ephemeris.schema_compatible",
            "ephemeris",
            "warning",
            f"schema compatibility is unverified because {wal_reason}",
            wal_remediation,
        )
    elif expected_version is None:
        schema = Check(
            "subsystem.ephemeris.schema_compatible",
            "ephemeris",
            "warning",
            "engine schema version is unavailable from the sibling checkout",
            "Restore an inspectable ephemeris sibling checkout, then run doctor again.",
        )
    elif actual_version > expected_version:
        schema = Check(
            "subsystem.ephemeris.schema_compatible",
            "ephemeris",
            "blocked",
            f"database schema {actual_version} is newer than engine schema "
            f"{expected_version}",
            "Use an ephemeris engine that supports this database schema.",
        )
    elif actual_version < expected_version:
        schema = Check(
            "subsystem.ephemeris.schema_compatible",
            "ephemeris",
            "warning",
            f"database schema {actual_version} is older than engine schema "
            f"{expected_version}",
            "Run the ephemeris migration explicitly; doctor will not run it.",
        )
    else:
        schema = Check(
            "subsystem.ephemeris.schema_compatible",
            "ephemeris",
            "ok",
            f"database and engine schema versions match at {actual_version}",
        )

    try:
        quick_rows = connection.execute("PRAGMA quick_check(1)").fetchall()
    except sqlite3.Error as exc:
        if wal_unverified:
            integrity = Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "warning",
                f"integrity is unverified because {wal_reason}",
                wal_remediation,
            )
        elif _sqlite_is_busy(exc):
            integrity = Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "warning",
                "database integrity could not be checked now",
                "Try again after the active database operation finishes.",
            )
        else:
            integrity = Check(
                "subsystem.ephemeris.integrity_check",
                "ephemeris",
                "blocked",
                "database integrity check failed",
                "Restore the ledger from a known-good backup.",
            )
    else:
        passed = quick_rows == [("ok",)]
        status = "warning" if wal_unverified else ("ok" if passed else "blocked")
        integrity = Check(
            "subsystem.ephemeris.integrity_check",
            "ephemeris",
            status,
            f"integrity is unverified because {wal_reason}"
            if wal_unverified
            else "database integrity check passed"
            if passed
            else (
                "database integrity check failed"
            ),
            ""
            if passed and not wal_unverified
            else wal_remediation
            if wal_unverified
            else "Restore the ledger from a known-good backup.",
        )
    finally:
        connection.close()
    return [readability, schema, integrity, _ephemeris_backup_check(root, show_paths)]


def _safe_file_name(name: str) -> str:
    """Keep the required filename diagnostic on one printable output line."""
    return "".join(character if character.isprintable() else "?" for character in name)


def _strict_json_loads(text: str) -> object:
    """Match Atlas's duplicate-key and finite-number JSON discipline locally."""
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Reject ambiguity rather than silently choosing the last value."""
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def finite_number(_value: str) -> object:
        """Keep non-standard numeric constants out of strict JSON."""
        raise ValueError("non-finite number")

    def finite_float(value: str) -> float:
        """Reject standard numeric tokens whose conversion overflows to infinity."""
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite number")
        return parsed

    return json.loads(
        text,
        object_pairs_hook=unique_object,
        parse_constant=finite_number,
        parse_float=finite_float,
    )


def _atlas_layout_check(root: Path | None, show_paths: bool) -> tuple[Check, bool]:
    """Prove the canonical four-directory skeleton without following symlinks."""
    if root is None:
        return Check(
            "subsystem.atlas.instance_layout",
            "atlas",
            "not_applicable",
            "Atlas private instance is not configured",
        ), False
    opened: list[int] = []
    try:
        for name in ("atlas", "plans", "intake", "state"):
            opened.append(_open_directory_no_follow(root / name))
    except OSError:
        return Check(
            "subsystem.atlas.instance_layout",
            "atlas",
            "blocked",
            "configured root lacks safe atlas/, plans/, intake/, and state/ "
            "directories"
            + path_suffix(root, show_paths),
            "Choose or restore a valid Atlas instance; doctor will not create it.",
        ), False
    finally:
        for directory_fd in opened:
            os.close(directory_fd)
    return Check(
        "subsystem.atlas.instance_layout",
        "atlas",
        "ok",
        "configured root has safe atlas/, plans/, intake/, and state/ directories"
        + path_suffix(root, show_paths),
    ), True


def _parse_jsonl_bytes(data: bytes) -> tuple[tuple[object, ...] | None, int | None]:
    """Return only parsed values or a content-free one-based failing row."""
    if not data:
        return (), None
    parts = data.split(b"\n")
    if parts[-1] != b"":
        return None, len(parts)
    rows: list[object] = []
    for number, raw in enumerate(parts[:-1], start=1):
        if not raw or b"\r" in raw:
            return None, number
        try:
            text = raw.decode("utf-8", errors="strict")
            rows.append(_strict_json_loads(text))
        except (UnicodeError, ValueError, RecursionError):
            return None, number
    return tuple(rows), None


def _atlas_journal_scan(root: Path, show_paths: bool) -> AtlasJournalScan:
    """Bound enumeration and reads so malformed instance size cannot stall doctor."""
    try:
        state_fd = _open_directory_no_follow(root / "state")
    except OSError:
        return AtlasJournalScan(
            Check(
                "subsystem.atlas.journals_parse",
                "atlas",
                "warning",
                "state journals could not be checked now"
                + path_suffix(root / "state", show_paths),
            )
        )

    capped = False
    enumeration_capped = False
    receipt_enumeration_incomplete = False
    journal_names: list[tuple[str | None, str]] = []
    try:
        try:
            with os.scandir(state_fd) as iterator:
                entries = []
                for entry in iterator:
                    if len(entries) >= ATLAS_MAX_STATE_ENTRIES:
                        capped = True
                        enumeration_capped = True
                        break
                    entries.append(entry)
        except OSError:
            return AtlasJournalScan(Check(
                "subsystem.atlas.journals_parse",
                "atlas",
                "warning",
                "state journals could not be checked now",
            ))

        entries.sort(key=lambda entry: (entry.name != "receipts", entry.name))
        enumerated_entries = len(entries)
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NONBLOCK", 0)
        for entry in entries:
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                return AtlasJournalScan(Check(
                    "subsystem.atlas.journals_parse",
                    "atlas",
                    "warning",
                    "state journals could not be checked now",
                ))
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                return AtlasJournalScan(Check(
                    "subsystem.atlas.journals_parse",
                    "atlas",
                    "blocked",
                    f"state entry {_safe_file_name(entry.name)} is a symlink "
                    "or special file",
                    "Replace it with an lstat-confirmed regular file or directory.",
                ))
            if stat.S_ISREG(mode) and entry.name.endswith(".jsonl"):
                journal_names.append((None, entry.name))
            elif stat.S_ISDIR(mode):
                directory_fd: int | None = None
                try:
                    directory_fd = os.open(
                        entry.name,
                        directory_flags,
                        dir_fd=state_fd,
                    )
                    with os.scandir(directory_fd) as iterator:
                        for rotated_entry in iterator:
                            if enumerated_entries >= ATLAS_MAX_STATE_ENTRIES:
                                capped = True
                                enumeration_capped = True
                                break
                            enumerated_entries += 1
                            try:
                                rotated_mode = rotated_entry.stat(
                                    follow_symlinks=False
                                ).st_mode
                            except OSError:
                                return AtlasJournalScan(Check(
                                    "subsystem.atlas.journals_parse",
                                    "atlas",
                                    "warning",
                                    "state journals could not be checked now",
                                ))
                            if stat.S_ISLNK(rotated_mode) or not (
                                stat.S_ISREG(rotated_mode)
                                or stat.S_ISDIR(rotated_mode)
                            ):
                                return AtlasJournalScan(Check(
                                    "subsystem.atlas.journals_parse",
                                    "atlas",
                                    "blocked",
                                    f"state entry "
                                    f"{_safe_file_name(rotated_entry.name)} "
                                    "is a symlink or special file",
                                    "Replace it with an lstat-confirmed regular "
                                    "file or directory.",
                                ))
                            if (
                                stat.S_ISREG(rotated_mode)
                                and rotated_entry.name.endswith(".jsonl")
                            ):
                                journal_names.append(
                                    (entry.name, rotated_entry.name)
                                )
                            elif stat.S_ISDIR(rotated_mode):
                                capped = True
                                if entry.name == "receipts":
                                    receipt_enumeration_incomplete = True
                except OSError:
                    return AtlasJournalScan(Check(
                        "subsystem.atlas.journals_parse",
                        "atlas",
                        "warning",
                        "state journals could not be checked now",
                    ))
                finally:
                    if directory_fd is not None:
                        os.close(directory_fd)

        def journal_order(item: tuple[str | None, str]) -> tuple[bool, str, bool, str]:
            directory, name = item
            stem = directory if directory is not None else Path(name).stem
            return stem != "receipts", stem, directory is None, name

        journal_names.sort(key=journal_order)
        # Atlas engine authority is spec/08-repository-layout.md,
        # spec/schemas/journal-receipt.schema.json, scripts/atlas_io.py, and
        # validate_atlas.py:547-553: receipts.jsonl plus receipts/ are receipts.
        # docs/instance.md is stale here and remains for its owner to correct.
        receipt_files = sum(
            directory == "receipts" or (directory is None and name == "receipts.jsonl")
            for directory, name in journal_names
        )
        if len(journal_names) > ATLAS_MAX_JOURNALS:
            capped = True
            journal_names = journal_names[:ATLAS_MAX_JOURNALS]

        receipts_present = receipt_files > 0
        receipt_latest: dict[str, ReceiptState] = {}
        receipt_shape_valid = True
        receipt_bytes = 0
        journal_bytes = 0
        receipt_budget_exceeded = False
        receipts_complete = (
            not enumeration_capped
            and not receipt_enumeration_incomplete
            and receipt_files
            == sum(
                directory == "receipts"
                or (directory is None and name == "receipts.jsonl")
                for directory, name in journal_names
            )
        )
        file_flags = os.O_RDONLY
        file_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NONBLOCK", 0)
        for index, (directory, name) in enumerate(journal_names):
            is_receipt = directory == "receipts" or (
                directory is None and name == "receipts.jsonl"
            )
            if is_receipt and receipt_budget_exceeded:
                continue
            parent_fd = state_fd
            try:
                if directory is not None:
                    parent_fd = os.open(
                        directory,
                        directory_flags,
                        dir_fd=state_fd,
                    )
                file_fd = os.open(name, file_flags, dir_fd=parent_fd)
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    os.close(file_fd)
                    return AtlasJournalScan(Check(
                        "subsystem.atlas.journals_parse",
                        "atlas",
                        "blocked",
                        f"state entry {_safe_file_name(name)} is a symlink "
                        "or special file",
                        "Replace it with an lstat-confirmed regular file.",
                    ))
                if journal_bytes + info.st_size > ATLAS_MAX_TOTAL_JOURNAL_BYTES:
                    os.close(file_fd)
                    capped = True
                    if any(
                        remaining_directory == "receipts"
                        or (
                            remaining_directory is None
                            and remaining_name == "receipts.jsonl"
                        )
                        for remaining_directory, remaining_name in journal_names[index:]
                    ):
                        receipts_complete = False
                    break
                if (
                    is_receipt
                    and receipt_bytes + info.st_size > ATLAS_MAX_RECEIPT_BYTES
                ):
                    os.close(file_fd)
                    capped = True
                    receipts_complete = False
                    receipt_budget_exceeded = True
                    continue
                if info.st_size > ATLAS_MAX_JOURNAL_BYTES:
                    os.close(file_fd)
                    capped = True
                    if is_receipt:
                        receipts_complete = False
                    continue
                remaining_bytes = ATLAS_MAX_TOTAL_JOURNAL_BYTES - journal_bytes
                read_limit = min(ATLAS_MAX_JOURNAL_BYTES, remaining_bytes)
                with os.fdopen(file_fd, "rb") as stream:
                    data = stream.read(read_limit + 1)
            except OSError:
                return AtlasJournalScan(Check(
                    "subsystem.atlas.journals_parse",
                    "atlas",
                    "warning",
                    "state journals could not be checked now",
                ))
            finally:
                if parent_fd != state_fd:
                    os.close(parent_fd)
            if len(data) > remaining_bytes:
                capped = True
                if any(
                    remaining_directory == "receipts"
                    or (
                        remaining_directory is None
                        and remaining_name == "receipts.jsonl"
                    )
                    for remaining_directory, remaining_name in journal_names[index:]
                ):
                    receipts_complete = False
                break
            if len(data) > ATLAS_MAX_JOURNAL_BYTES:
                capped = True
                if is_receipt:
                    receipts_complete = False
                continue
            if (
                is_receipt
                and receipt_bytes + len(data) > ATLAS_MAX_RECEIPT_BYTES
            ):
                capped = True
                receipts_complete = False
                receipt_budget_exceeded = True
                continue
            journal_bytes += len(data)
            rows, bad_row = _parse_jsonl_bytes(data)
            if bad_row is not None:
                return AtlasJournalScan(
                    Check(
                        "subsystem.atlas.journals_parse",
                        "atlas",
                        "blocked",
                        f"journal {_safe_file_name(name)} has a malformed row "
                        f"{bad_row}",
                        "Repair the journal explicitly from a known-good source.",
                    ),
                    receipts_present=receipts_present,
                )
            if is_receipt:
                assert rows is not None
                receipt_bytes += len(data)
                for row in rows:
                    if not receipt_shape_valid:
                        continue
                    if not isinstance(row, dict):
                        receipt_shape_valid = False
                        continue
                    marker = row.get("marker")
                    key = row.get("intake")
                    if not isinstance(key, str) or marker not in {
                        "opened",
                        "processed",
                    }:
                        receipt_shape_valid = False
                        continue
                    previous = receipt_latest.get(key)
                    if previous is None and len(receipt_latest) >= (
                        ATLAS_MAX_RECEIPT_KEYS
                    ):
                        capped = True
                        receipts_complete = False
                        receipt_budget_exceeded = True
                        break
                    invalid_transition = (
                        previous.invalid_transition if previous else False
                    )
                    processed_without_open = (
                        previous.processed_without_open if previous else False
                    )
                    if marker == "opened":
                        invalid_transition |= previous is not None
                    else:
                        missing_open = (
                            previous is None or previous.latest_marker != "opened"
                        )
                        invalid_transition |= missing_open
                        processed_without_open |= missing_open
                    receipt_latest[key] = ReceiptState(
                        marker,
                        invalid_transition,
                        processed_without_open,
                    )

        if capped:
            return AtlasJournalScan(
                Check(
                    "subsystem.atlas.journals_parse",
                    "atlas",
                    "warning",
                    "state journals were not fully checked because a safety "
                    "cap was exceeded",
                ),
                receipts_present=receipts_present,
                receipt_latest=receipt_latest if receipts_complete else None,
                receipt_shape_valid=receipt_shape_valid,
            )
        return AtlasJournalScan(
            Check(
                "subsystem.atlas.journals_parse",
                "atlas",
                "ok",
                "all bounded state journals contain strict UTF-8 "
                "LF-terminated JSON rows",
            ),
            receipts_present=receipts_present,
            receipt_latest=receipt_latest,
            receipt_shape_valid=receipt_shape_valid,
        )
    finally:
        os.close(state_fd)


def _atlas_lock_check(root: Path) -> Check:
    """Inspect lock metadata only because process liveness is unknowable."""
    try:
        root_fd = _open_directory_no_follow(root)
        try:
            info = os.stat(".atlas-lock", dir_fd=root_fd, follow_symlinks=False)
        finally:
            os.close(root_fd)
    except FileNotFoundError:
        return Check(
            "subsystem.atlas.writer_lock",
            "atlas",
            "ok",
            "no writer lock is present",
        )
    except OSError:
        return Check(
            "subsystem.atlas.writer_lock",
            "atlas",
            "warning",
            "writer lock age could not be checked now",
            "Inspect it manually; stale locks are removed only by hand.",
        )
    return Check(
        "subsystem.atlas.writer_lock",
        "atlas",
        "warning",
        f"writer lock is present and is {_age_days(info.st_mtime)} whole days "
        "old; writer liveness is unknown",
        "Inspect it manually; stale locks are removed only by hand.",
    )


def _atlas_receipts_check(scan: AtlasJournalScan) -> Check:
    """Report counts from bounded latest receipt state without revealing keys."""
    if scan.check.status == "blocked":
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "not_applicable",
            "receipt completeness is covered by the journal parse finding",
        )
    if not scan.receipts_present:
        detail = (
            "receipt journal was not fully checked"
            if scan.check.status == "warning"
            else "state/receipts.jsonl and its rotated prefix are absent"
        )
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "not_applicable",
            detail,
        )
    if scan.receipt_latest is None:
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "not_applicable",
            "receipt journal was not fully checked",
        )
    if not scan.receipt_shape_valid:
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "not_applicable",
            "receipt rows do not expose the expected content-free "
            "bookkeeping shape",
        )

    interrupted = sum(
        state.latest_marker == "opened" for state in scan.receipt_latest.values()
    )
    if interrupted:
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "warning",
            f"{interrupted} intake keys have an opened receipt without a "
            "processed receipt",
            "Resume or reconcile the interrupted intake explicitly.",
        )
    orphan_processed = sum(
        state.processed_without_open
        for state in scan.receipt_latest.values()
    )
    if orphan_processed:
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "warning",
            f"{orphan_processed} intake keys have a processed receipt without "
            "a preceding opened receipt",
            "Reconcile the inconsistent receipt history explicitly.",
        )
    invalid_transitions = sum(
        state.invalid_transition for state in scan.receipt_latest.values()
    )
    if invalid_transitions:
        return Check(
            "subsystem.atlas.receipts_complete",
            "atlas",
            "warning",
            f"{invalid_transitions} intake keys have invalid receipt transitions",
            "Reconcile the inconsistent receipt history explicitly.",
        )
    return Check(
        "subsystem.atlas.receipts_complete",
        "atlas",
        "ok",
        "every opened intake receipt has a matching processed receipt",
    )


def atlas_checks(
    root: Path | None,
    show_paths: bool,
    root_accepted: bool = True,
) -> list[Check]:
    """Keep Atlas findings contiguous while suppressing cascades from bad layout."""
    if not root_accepted:
        reason = "inspection is refused across the public-instance boundary"
        return [
            Check(check_id, "atlas", "not_applicable", reason)
            for check_id in (
                "subsystem.atlas.instance_layout",
                "subsystem.atlas.journals_parse",
                "subsystem.atlas.writer_lock",
                "subsystem.atlas.receipts_complete",
            )
        ]
    layout, usable = _atlas_layout_check(root, show_paths)
    if not usable:
        reason = (
            "Atlas private instance is not configured"
            if root is None
            else "Atlas instance layout is not usable"
        )
        return [
            layout,
            Check("subsystem.atlas.journals_parse", "atlas", "not_applicable", reason),
            Check("subsystem.atlas.writer_lock", "atlas", "not_applicable", reason),
            Check(
                "subsystem.atlas.receipts_complete",
                "atlas",
                "not_applicable",
                reason,
            ),
        ]
    assert root is not None
    scan = _atlas_journal_scan(root, show_paths)
    return [layout, scan.check, _atlas_lock_check(root), _atlas_receipts_check(scan)]


def _is_within(path: Path, roots: list[Path]) -> bool:
    """Reject runner candidates from any inspected code or data root."""
    try:
        candidates = {path, path.resolve(strict=True)}
    except (OSError, RuntimeError):
        return True
    for root in roots:
        try:
            root_candidates = {root, root.resolve(strict=True)}
        except FileNotFoundError:
            try:
                root_candidates = {root, root.resolve(strict=False)}
            except (OSError, RuntimeError):
                return True
        except (OSError, RuntimeError):
            return True
        if any(
            candidate.is_relative_to(base)
            for candidate in candidates
            for base in root_candidates
        ):
            return True
    return False


def _safe_runner_path(instance_roots: list[Path]) -> tuple[str, dict[str, Path]]:
    """Remove repository and instance entries before executable resolution."""
    forbidden = [
        ROOT,
        *(ROOT.parent / name for name in EXPECTED_REPOS),
        *instance_roots,
    ]
    entries: list[str] = []
    for raw in os.get_exec_path():
        try:
            entry = Path(os.path.abspath(raw or os.curdir)).expanduser()
        except RuntimeError:
            continue
        if not _is_within(entry, forbidden):
            entries.append(str(entry))
    search_path = os.pathsep.join(entries)
    found: dict[str, Path] = {}
    for runner in APPROVED_RUNNERS:
        resolved = shutil.which(runner, path=search_path)
        if resolved is None:
            continue
        candidate = Path(os.path.abspath(resolved))
        if not _is_within(candidate, forbidden):
            found[runner] = candidate
    return search_path, found


def _parse_version(runner: str, output: str) -> str | None:
    """Whitelist a short version line so third-party output cannot leak paths."""
    line = output.strip()
    if "\n" in line or "\r" in line or len(line) > 120:
        return None
    allowed = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+()\-]*", line)
    if runner not in line.lower() or allowed is None:
        return None
    return line


def _bounded_runner_version(
    executable: Path,
    environment: dict[str, str],
) -> tuple[int, str] | None:
    """Read one bounded version prefix and always reap the runner process."""
    process = subprocess.Popen(
        (str(executable), "--version"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        cwd="/",
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + RUNNER_VERSION_TIMEOUT_SECONDS
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(
                process.stdout.fileno(),
                min(4096, RUNNER_VERSION_MAX_BYTES + 1 - len(output)),
            )
            if not chunk:
                try:
                    return_code = process.wait(
                        timeout=max(0.0, deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired:
                    return None
                return return_code, output.decode("utf-8", errors="strict")
            output.extend(chunk)
            if len(output) > RUNNER_VERSION_MAX_BYTES:
                return None
    finally:
        process.stdout.close()
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        process.wait()


def runtime_runner_checks(instance_roots: list[Path]) -> list[Check]:
    """Treat an entirely absent optional runtime as an absent subject, not damage."""
    search_path, found = _safe_runner_path(instance_roots)
    if not found:
        return [
            Check(
                "runtime.runner_present",
                "selfos",
                "not_applicable",
                "no approved agent runner is installed",
            ),
            Check(
                "runtime.runner_version",
                "selfos",
                "not_applicable",
                "no installed runner has a version to check",
            ),
        ]

    missing = [runner for runner in APPROVED_RUNNERS if runner not in found]
    present_detail = "available runners: " + ", ".join(found)
    if missing:
        present = Check(
            "runtime.runner_present",
            "selfos",
            "warning",
            present_detail + "; missing runners: " + ", ".join(missing),
            "Install a missing runner only if an explicit agent run needs it.",
        )
    else:
        present = Check("runtime.runner_present", "selfos", "ok", present_detail)

    environment = {
        "PATH": search_path,
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
    }
    versions: list[str] = []
    failed = False
    for runner in APPROVED_RUNNERS:
        executable = found.get(runner)
        if executable is None:
            continue
        try:
            result = _bounded_runner_version(executable, environment)
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            failed = True
            continue
        parsed = (
            _parse_version(runner, result[1])
            if result is not None and result[0] == 0
            else None
        )
        if parsed is None:
            failed = True
        else:
            versions.append(parsed)
    detail = "; ".join(versions)
    if failed:
        if detail:
            detail += "; "
        detail += "one or more installed runner versions could not be verified"
        version = Check(
            "runtime.runner_version",
            "selfos",
            "warning",
            detail,
            "Run the affected executable with --version manually.",
        )
    else:
        version = Check("runtime.runner_version", "selfos", "ok", detail)
    return [present, version]


def runtime_override_check(override_value: str | None, show_paths: bool) -> Check:
    """Read only the dated marker so the live override body never enters output."""
    remediation = "Configure runtime.override_path; see docs/model-override.example.md."
    if override_value is None:
        return Check(
            "runtime.override_verified",
            "selfos",
            "not_applicable",
            "no runtime override path is configured",
            remediation,
        )
    try:
        path = Path(os.path.abspath(Path(override_value).expanduser()))
    except RuntimeError:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            "configured runtime override is missing or unreadable",
            remediation,
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        file_fd = os.open(path, flags)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise OSError
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = -1
                data = stream.read(64 * 1024 + 1)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    except OSError:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            "configured runtime override is missing or unreadable"
            + path_suffix(path, show_paths),
            remediation,
        )
    if len(data) > 64 * 1024:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            "runtime override marker was not found within the bounded read",
            remediation,
        )
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        lines = []
    match = next(
        (
            VERIFIED_LINE.match(line)
            for line in lines
            if VERIFIED_LINE.match(line)
        ),
        None,
    )
    if match is None:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            "runtime override has no parseable Last verified date",
            remediation,
        )
    rendered_date = match.group(1)
    try:
        verified = dt.date.fromisoformat(rendered_date)
    except ValueError:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            "runtime override has no parseable Last verified date",
            remediation,
        )
    age = (dt.date.today() - verified).days
    if age < 0 or age > OVERRIDE_RECENT_DAYS:
        return Check(
            "runtime.override_verified",
            "selfos",
            "warning",
            f"runtime override was last verified {rendered_date}; age is {age} days",
            "Re-verify the override against current runner behavior.",
        )
    return Check(
        "runtime.override_verified",
        "selfos",
        "ok",
        f"runtime override was last verified {rendered_date}; age is {age} days",
    )


def collect_checks(show_paths: bool) -> list[Check]:
    """Append new groups without disturbing the shipped presentation order."""
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

    config_instances, runtime_override = load_user_config()
    instance_roots: dict[str, Path | None] = {}
    root_accepted: dict[str, bool] = {}
    for label in EXPECTED_REPOS:
        root, source = discover_instance(label, config_instances)
        instance_roots[label] = root
        public_label = containing_public_root(root) if root is not None else None
        accepted = root is None or public_label is None
        root_accepted[label] = accepted
        checks.extend(
            instance_checks(
                label,
                root,
                source,
                show_paths,
                public_label,
                containment_checked=True,
            )
        )

    checks.extend(
        ephemeris_checks(
            instance_roots["ephemeris"], show_paths, root_accepted["ephemeris"]
        )
    )
    checks.extend(
        atlas_checks(instance_roots["atlas"], show_paths, root_accepted["atlas"])
    )
    configured_roots = [
        root
        for label, root in instance_roots.items()
        if root is not None and root_accepted[label]
    ]
    checks.extend(runtime_runner_checks(configured_roots))
    checks.append(runtime_override_check(runtime_override, show_paths))
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
    except (DoctorInternalError, OSError, RuntimeError):
        print(
            "doctor internal error: local state could not be inspected",
            file=sys.stderr,
        )
        return 2

    if args.json:
        render_json(checks, state)
    else:
        render_human(checks, state)
    return 1 if state == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
