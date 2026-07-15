#!/usr/bin/env bash
# Portfolio-wide GitHub issue backup: dump issues + comments of every
# non-archived repo on the account to local JSON snapshots, so tracker
# history (design decisions live in issues) survives GitHub being
# unreachable or gone. Repos are discovered via `gh repo list` — new
# projects are picked up automatically, nothing to maintain.
# Idempotent full dump per run; per-repo `latest.json` symlink; keeps the
# last 30 daily snapshots per repo. Needs an authenticated `gh` + `jq`.
set -euo pipefail

OWNER="jointsome0-lgtm"
DEST_ROOT="${SELFOS_ISSUE_BACKUP_DIR:-$HOME/backups/issues}"
KEEP=30
stamp="$(date +%F)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

repos="$(gh repo list "$OWNER" --no-archived --json name,hasIssuesEnabled \
    --jq '.[] | select(.hasIssuesEnabled) | .name')"

for repo in $repos; do
    dest="$DEST_ROOT/$repo"
    mkdir -p "$dest"
    out="$dest/issues-$stamp.json"

    # --paginate concatenates JSON arrays; jq -s 'add' merges them into one.
    gh api "repos/$OWNER/$repo/issues?state=all&per_page=100" --paginate \
        | jq -s 'add // []' > "$tmp/issues.json"
    gh api "repos/$OWNER/$repo/issues/comments?per_page=100" --paginate \
        | jq -s 'add // []' > "$tmp/comments.json"

    jq -n --arg repo "$OWNER/$repo" \
        --slurpfile issues "$tmp/issues.json" --slurpfile comments "$tmp/comments.json" \
        '{repo: $repo, fetched_at: now | todate, issues: $issues[0], comments: $comments[0]}' \
        > "$out.tmp"
    mv "$out.tmp" "$out"
    ln -sfn "$out" "$dest/latest.json"

    ls -1t "$dest"/issues-*.json | tail -n "+$((KEEP + 1))" | xargs -r rm --

    echo "$repo: $(jq '.issues | length' "$out") issues, $(jq '.comments | length' "$out") comments"
done
