#!/bin/bash
# deploy_pages.sh - Deploy dashboard and data to GitHub Pages (gh-pages branch)
#
# Uses a throwaway git worktree so the gh-pages branch is updated without ever
# touching your working tree. Creates the branch on the remote if it does not
# exist yet, so no manual bootstrap is needed.

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Inherited from run.sh; fall back to reading config.py when run standalone.
POOL="${POOL:-$(${PYTHON:-python3} -c 'import config; print(config.POOL_NAME)')}"
DATA_FILE="data_${POOL}.json"
WORKTREE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/brux-librarian-ghp.XXXXXX")"

cleanup() {
    cd "$DIR" 2>/dev/null || true
    git -C "$DIR" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"
    git -C "$DIR" worktree prune 2>/dev/null || true
    if [ -n "${TMP_BRANCH:-}" ]; then
        git -C "$DIR" branch -D "$TMP_BRANCH" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# --- Preconditions, with errors that say what to do ---
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: $DIR is not a git repository."
    echo "       git init && git add . && git commit -m 'Initial commit'"
    exit 1
fi
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "ERROR: no 'origin' remote configured."
    echo "       git remote add origin git@github.com:YOUR_USERNAME/brux_librarian.git"
    exit 1
fi
if [ ! -f "$DIR/$DATA_FILE" ]; then
    echo "ERROR: $DATA_FILE not found. Run ./run.sh first."
    exit 1
fi

# --- Check out gh-pages into the temp worktree, creating it if absent ---
git worktree prune 2>/dev/null || true      # clear stale worktrees from failed runs
git fetch origin gh-pages 2>/dev/null || true

rmdir "$WORKTREE_DIR" 2>/dev/null || true   # git wants to create this itself

# A throwaway branch name, so a leftover local "gh-pages" branch from an earlier
# attempt can never collide with us. The push below names the real branch.
TMP_BRANCH="_ghpages_deploy_$$"

if git show-ref --verify --quiet refs/remotes/origin/gh-pages; then
    # Only the published files get checked out here - small and fast.
    git worktree add --detach "$WORKTREE_DIR" origin/gh-pages >/dev/null
    cd "$WORKTREE_DIR"
else
    echo "gh-pages does not exist on origin yet; creating it"
    # --no-checkout: never materialize the main branch's files. Without it a repo
    # that has a committed venv/ would copy thousands of files just to delete them.
    git worktree add --detach --no-checkout "$WORKTREE_DIR" >/dev/null
    cd "$WORKTREE_DIR"
    # stderr deliberately NOT swallowed: with `set -e` a silent failure here
    # aborts the whole script with no explanation.
    git checkout --orphan "$TMP_BRANCH"
    # Empty the index outright. `git rm --cached .` does not reliably clear it in
    # a --no-checkout worktree, and a partial clear would publish the whole repo.
    git read-tree --empty
fi

# --- Publish ---
cp "$DIR/dashboard/index.html" "$WORKTREE_DIR/"
cp "$DIR/$DATA_FILE" "$WORKTREE_DIR/"

# Stage only what we publish, so nothing from the main branch can leak in.
git add -- index.html "$DATA_FILE"
if git diff --cached --quiet; then
    echo "No changes to deploy"
    exit 0
fi

git commit -q -m "Update dashboard data $(date '+%Y-%m-%d %H:%M')"
NEW_SHA="$(git rev-parse HEAD)"

# Push from the repo directory, not the temp worktree: they share an object
# store, so the commit is already visible there, and this keeps a relatively
# specified remote (or any cwd-sensitive config) working. Pushing an explicit
# SHA creates the remote branch on first push, detached HEAD or not.
git -C "$DIR" push -q origin "${NEW_SHA}:refs/heads/gh-pages"
echo "Deployed $DATA_FILE + index.html to gh-pages"
