"""Enforce the public Git-layer boundary for this repository.

The policy is documented in docs/hygiene.md. This check rejects known
private-data paths, requires the matching .gitignore rules, and requires the
Vera Example marker in demo fixtures. If a legitimate public file matches a
private-data pattern, add a narrow allowlist entry in the same change that adds
the file.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GITIGNORE_PATTERNS = {
    "data/",
    "state/",
    "intake/",
    "graph/",
    "plans/",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "*.db",
    "*.jsonl",
    ".env",
    ".env.*",
    "engine.pin",
    "copies-manifest",
    "delivery-registry",
    ".claude/",
    ".codex/",
    ".agents/",
}

DENIED_PATH_PATTERNS = (
    "data/**",
    "state/**",
    "intake/**",
    "graph/**",
    "plans/**",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "*.db",
    "*.jsonl",
    ".env",
    ".env.*",
    "engine.pin",
    "copies-manifest",
    "delivery-registry",
    ".claude/**",
    ".codex/**",
    ".agents/**",
)

FIXTURE_PATH_PATTERNS = ("fixtures/**",)

DENIED_PATH_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Add only exact paths here, with a comment explaining each exception.
    }
)


def candidate_git_files() -> list[str]:
    """Return tracked and untracked-unignored repository paths."""
    raw = subprocess.check_output(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=ROOT,
    )
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in raw.split(b"\0")
        if path
    ]


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def active_gitignore_patterns() -> set[str]:
    return {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main() -> int:
    errors: list[str] = []

    try:
        candidates = candidate_git_files()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = str(exc).replace("\n", " ")
        print(f"FAIL: cannot inspect the public Git layer: {detail}", file=sys.stderr)
        return 1

    try:
        gitignore_patterns = active_gitignore_patterns()
    except OSError as exc:
        detail = str(exc).replace("\n", " ")
        print(f"FAIL: cannot read .gitignore: {detail}", file=sys.stderr)
        return 1

    for pattern in sorted(REQUIRED_GITIGNORE_PATTERNS - gitignore_patterns):
        errors.append(f".gitignore missing required pattern: {pattern}")

    for path in sorted(candidates):
        if path not in DENIED_PATH_ALLOWLIST and matches_any(
            path, DENIED_PATH_PATTERNS
        ):
            errors.append(f"denied path visible to the public Git layer: {path}")

        if matches_any(path, FIXTURE_PATH_PATTERNS):
            try:
                fixture = (ROOT / path).read_bytes()
            except OSError as exc:
                detail = str(exc).replace("\n", " ")
                errors.append(f"cannot read fixture {path}: {detail}")
            else:
                if b"Vera Example" not in fixture:
                    errors.append(
                        f"fixture lacks required marker 'Vera Example': {path}"
                    )

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("OK: public Git layer has no denied paths or unmarked demo fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
