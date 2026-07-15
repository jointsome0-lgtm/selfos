"""Synchronize sibling public-engine repositories to the pinned revisions.

The policy is decided in issue #1. Sibling checkouts are resolved relative to
this repository, never cloned or fetched, and a dirty checkout is never
changed. ``--status`` is fully read-only; normal mode performs only the
explicit ``git checkout <sha>`` needed to reach a locally available pin.
Requires Python 3.11+ (tomllib).
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "pins.toml"
EXPECTED_REPOS = ("ephemeris", "atlas", "exp2res")
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")


class ManifestError(ValueError):
    """The pins manifest is missing or does not match its strict contract."""


class GitInspectionError(RuntimeError):
    """A local Git repository could not be inspected reliably."""


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
    command at the wrong repository, so everything git itself lists as
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
    """Run a local-only Git command in a caller-independent environment."""
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
    """Return stripped stdout or raise a concise inspection error."""
    result = run_git(repo, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or "Git command failed"
        raise GitInspectionError(detail.replace("\n", " "))
    return result.stdout.strip()


def head_sha(repo: Path) -> str:
    """Return the full commit SHA at HEAD."""
    return git_output(repo, "rev-parse", "--verify", "HEAD^{commit}")


def worktree_is_dirty(repo: Path) -> bool:
    """Include staged, unstaged, and untracked paths in the dirty check.

    --untracked-files=all overrides a status.showUntrackedFiles=no
    repository config, which would otherwise hide untracked files.
    """
    return bool(git_output(repo, "status", "--porcelain", "--untracked-files=all"))


def pin_object_type(repo: Path, pin: str) -> str | None:
    """Return the pin's local object type, or None when it is absent.

    The type must be checked, not just ``^{commit}`` resolvability: an
    annotated tag's own SHA peels to a commit, and a checkout of the
    peeled commit would move the tree before verification could fail.
    """
    result = run_git(repo, "cat-file", "-t", pin)
    return result.stdout.strip() if result.returncode == 0 else None


def path_ancestors(path: str) -> set[str]:
    """Return every proper directory prefix of a slash-separated path."""
    parts = path.split("/")
    return {"/".join(parts[:i]) for i in range(1, len(parts))}


def clobber_candidates(repo: Path, pin: str) -> list[str]:
    """Return local untracked files (ignored included) the checkout may hit.

    ``git status --porcelain`` omits ignored files, and ``git checkout``
    overwrites ignored files by default when the target commit tracks the
    same path — so this must be checked separately before any checkout.
    Comparison is prefix-aware in both directions: a pin entry ``data``
    collides with a local ignored ``data/secret``, and a pin entry
    ``data/secret`` collides with a local ignored file ``data``. Two
    different files merely sharing a directory do not collide.
    """
    tracked_in_pin = set(
        git_output(repo, "ls-tree", "-r", "--name-only", f"{pin}^{{commit}}").splitlines()
    )
    tracked_ancestors = set().union(
        *(path_ancestors(path) for path in tracked_in_pin), set()
    )
    # A nested repository is listed as a bare directory marker
    # ("subrepo/", no recursion); strip the slash so prefix comparison
    # still collides it with the pin's "subrepo/..." entries.
    present_untracked = [
        line.rstrip("/")
        for line in git_output(repo, "ls-files", "--others").splitlines()
    ]
    return sorted(
        path
        for path in present_untracked
        if path in tracked_in_pin
        or path in tracked_ancestors
        or not path_ancestors(path).isdisjoint(tracked_in_pin)
    )


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Test commit ancestry, distinguishing false from an inspection error."""
    result = run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or "git merge-base failed"
    raise GitInspectionError(detail.replace("\n", " "))


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


def report_status(pins: dict[str, str]) -> int:
    """Report pin and cleanliness state without changing any checkout."""
    all_match = True
    for name in EXPECTED_REPOS:
        repo = ROOT.parent / name
        pin = pins[name]
        if not is_git_repo(repo):
            print(f"{name}: absent")
            all_match = False
            continue

        try:
            head = head_sha(repo)
            dirty = worktree_is_dirty(repo)
        except GitInspectionError as exc:
            print(f"{name}: absent ({exc})")
            all_match = False
            continue

        marker = " dirty" if dirty else ""
        pin_type = pin_object_type(repo, pin)
        if pin_type is None:
            print(
                f"{name}: unknown-pin head={abbreviated(head)} "
                f"pin={abbreviated(pin)}{marker}"
            )
            all_match = False
            continue
        if pin_type != "commit":
            print(
                f"{name}: bad-pin ({pin_type} object, not a commit) "
                f"pin={abbreviated(pin)}{marker}"
            )
            all_match = False
            continue

        try:
            state = revision_state(repo, head, pin)
        except GitInspectionError as exc:
            print(f"{name}: diverged ({exc}){marker}")
            all_match = False
            continue

        print(
            f"{name}: {state} head={abbreviated(head)} "
            f"pin={abbreviated(pin)}{marker}"
        )
        if state != "match" or dirty:
            all_match = False
    return 0 if all_match else 1


def synchronize(pins: dict[str, str]) -> int:
    """Checkout each locally available pin, refusing every unsafe case."""
    all_synced = True
    for name in EXPECTED_REPOS:
        repo = ROOT.parent / name
        pin = pins[name]
        if not is_git_repo(repo):
            print(f"{name}: error: sibling is absent or not a Git repository")
            all_synced = False
            continue

        try:
            if worktree_is_dirty(repo):
                print(f"{name}: refused: working tree is dirty")
                all_synced = False
                continue
            head = head_sha(repo)
        except GitInspectionError as exc:
            print(f"{name}: error: cannot inspect repository: {exc}")
            all_synced = False
            continue

        pin_type = pin_object_type(repo, pin)
        if pin_type is None:
            print(
                f"{name}: error: pin {abbreviated(pin)} is not available "
                f"locally; run git fetch manually in {name}"
            )
            all_synced = False
            continue
        if pin_type != "commit":
            print(
                f"{name}: error: pin {abbreviated(pin)} is a {pin_type} "
                f"object, not a commit; re-pin to the commit SHA"
            )
            all_synced = False
            continue

        if head == pin:
            print(f"{name}: already at pin {abbreviated(pin)}")
            continue

        try:
            clobbered = clobber_candidates(repo, pin)
        except GitInspectionError as exc:
            print(f"{name}: error: cannot inspect repository: {exc}")
            all_synced = False
            continue
        if clobbered:
            print(
                f"{name}: refused: checkout would overwrite "
                f"{len(clobbered)} ignored local file(s) tracked at the pin"
            )
            all_synced = False
            continue

        # An empty hooks path keeps a local post-checkout hook from
        # running arbitrary code mid-sync (fetching, dirtying the tree,
        # or turning a completed checkout into a reported failure).
        # --detach plus the ^{commit} rev form pins the checkout to the
        # object itself: a stray branch named like the 40-hex SHA would
        # otherwise win the single-argument <branch> resolution.
        result = run_git(
            repo,
            "-c",
            f"core.hooksPath={os.devnull}",
            "checkout",
            "--detach",
            f"{pin}^{{commit}}",
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "git checkout failed"
            print(f"{name}: error: {detail.replace(chr(10), ' ')}")
            all_synced = False
            continue

        # Verify HEAD only: a pre-existing local file can legitimately be
        # ignored at the old HEAD but untracked at the pin, so a
        # dirty-after check would misreport a correct checkout.
        try:
            final_head = head_sha(repo)
        except GitInspectionError as exc:
            print(f"{name}: error: cannot verify checkout: {exc}")
            all_synced = False
            continue
        if final_head != pin:
            print(f"{name}: error: checkout verification failed: HEAD missed the pin")
            all_synced = False
            continue
        print(f"{name}: checked out pin {abbreviated(pin)}")

    return 0 if all_synced else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize sibling repositories to pins.toml."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="report revision drift and cleanliness without changing anything",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        pins = load_pins()
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.status:
            return report_status(pins)
        return synchronize(pins)
    except OSError as exc:
        print(f"error: cannot run local Git command: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
