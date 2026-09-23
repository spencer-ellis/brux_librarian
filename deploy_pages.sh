#!/bin/bash
# deploy_pages.sh - Deploy dashboard and data to GitHub Pages (gh-pages branch)
# Uses a git worktree to update gh-pages without touching the main working tree.

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Inherited from run.sh; fall back to reading config.py when run standalone.
POOL="${POOL:-$(${PYTHON:-python3} -c 'import config; print(config.POOL_NAME)')}"
WORKTREE_DIR="/tmp/brux-librarian-gh-pages-$$"

cleanup() { git worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"; }
trap cleanup EXIT

git fetch origin gh-pages 2>/dev/null || true

git worktree add "$WORKTREE_DIR" gh-pages 2>/dev/null || {
    git worktree add --orphan -b gh-pages "$WORKTREE_DIR" 2>/dev/null || {
        git worktree add --detach "$WORKTREE_DIR"
        cd "$WORKTREE_DIR"
        git checkout --orphan gh-pages
        git rm -rf . 2>/dev/null || true
        cd "$DIR"
    }
}

cp "$DIR/dashboard/index.html" "$WORKTREE_DIR/"
cp "$DIR/data_${POOL}.json" "$WORKTREE_DIR/"

cd "$WORKTREE_DIR"
git add -A
if ! git diff --cached --quiet; then
    git commit -m "Update dashboard data $(date '+%Y-%m-%d %H:%M')"
    git push origin gh-pages
else
    echo "No changes to deploy"
fi
