#!/usr/bin/env bash
# Builds the dashboard and deploys to gh-pages via git worktree.
# Usage (from repo root): ./scripts/deploy.sh
# Must be run from the dev branch (claude/funny-hamilton-8eodbf).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
WORKTREE_DIR=$(mktemp -d)

cleanup() { git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || true; }
trap cleanup EXIT

echo "==> Building from branch: $DEV_BRANCH"
cd "$REPO_ROOT/web"
npm install --silent
rm -rf dist
npm run build 2>&1 | grep -E "built|error"

# Verify heatmap code is in bundle
JS_FILE=$(ls dist/assets/index-*.js)
if ! grep -q "Kalendář dostupných" "$JS_FILE"; then
  echo "ERROR: heatmap code missing from bundle!" && exit 1
fi

NEW_INDEX="dist/index.html"
NEW_JS=$(grep -o 'assets/index-[^"]*\.js' "$NEW_INDEX")
NEW_CSS=$(grep -o 'assets/index-[^"]*\.css' "$NEW_INDEX")
echo "==> Bundle OK — $NEW_JS | $NEW_CSS"

echo "==> Checking out gh-pages into worktree..."
git -C "$REPO_ROOT" worktree add "$WORKTREE_DIR" gh-pages

# Replace assets and index.html
rm -rf "$WORKTREE_DIR/assets"
cp -r dist/assets "$WORKTREE_DIR/"
cp dist/index.html "$WORKTREE_DIR/"

# Sync public data (calendar, history, config)
cp -r "$REPO_ROOT/web/public/data/." "$WORKTREE_DIR/data/"
cp -r "$REPO_ROOT/web/public/config/." "$WORKTREE_DIR/config/"

# Verify index.html was updated
if ! grep -q "$(basename "$JS_FILE")" "$WORKTREE_DIR/index.html"; then
  echo "ERROR: index.html in worktree still points to old JS!" && exit 1
fi
echo "==> index.html OK — $(cat "$WORKTREE_DIR/index.html" | grep -o 'index-[^"]*\.js')"

cd "$WORKTREE_DIR"
git add -A
COMMIT_MSG=$(git -C "$REPO_ROOT" log "$DEV_BRANCH" -1 --pretty=format:'%s')
git commit -m "Deploy: $COMMIT_MSG" || echo "(nothing new to commit)"
git push origin gh-pages

echo "==> Done. https://medniledved.github.io/flight-watcher/"
