#!/usr/bin/env bash
# Builds the dashboard, validates, and deploys to gh-pages via git worktree.
# Usage (from repo root): ./scripts/deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
WORKTREE_DIR=$(mktemp -d)

cleanup() { git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || true; }
trap cleanup EXIT

echo "==> [1/3] Building from branch: $DEV_BRANCH"
cd "$REPO_ROOT/web"
npm install --silent
rm -rf dist
npm run build 2>&1 | grep -E "built|error"

echo ""
echo "==> [2/3] Running validation suite..."
bash "$REPO_ROOT/scripts/validate.sh" || { echo "❌ Validation failed"; exit 1; }

echo ""
echo "==> [3/3] Deploying to gh-pages via worktree..."
NEW_JS=$(grep -o 'assets/index-[^"]*\.js' dist/index.html)
NEW_CSS=$(grep -o 'assets/index-[^"]*\.css' dist/index.html)

git -C "$REPO_ROOT" worktree add "$WORKTREE_DIR" gh-pages

# Clean slate: remove everything except .git, then rebuild from dist
find "$WORKTREE_DIR" -maxdepth 1 -mindepth 1 ! -name '.git' -exec rm -rf {} +

# Ensure Jekyll is disabled (required for Pages to serve raw JS/CSS correctly)
touch "$WORKTREE_DIR/.nojekyll"

# Copy built assets and index
cp -r dist/assets "$WORKTREE_DIR/"
cp dist/index.html "$WORKTREE_DIR/"

# Sync data and config
mkdir -p "$WORKTREE_DIR/data/calendar" "$WORKTREE_DIR/data/history" "$WORKTREE_DIR/config"
cp -r "$REPO_ROOT/web/public/data/." "$WORKTREE_DIR/data/"
cp -r "$REPO_ROOT/web/public/config/." "$WORKTREE_DIR/config/"

# Final integrity check before commit
if ! grep -q "$(basename "$NEW_JS")" "$WORKTREE_DIR/index.html"; then
  echo "❌ ERROR: index.html still has wrong JS reference"
  exit 1
fi

cd "$WORKTREE_DIR"
git add -A
COMMIT_MSG=$(git -C "$REPO_ROOT" log "$DEV_BRANCH" -1 --pretty=format:'%s')
git commit -m "Deploy: $COMMIT_MSG" || echo "(nothing new to commit)"
git push origin gh-pages

echo ""
echo "✓ Deployment successful: https://medniledved.github.io/flight-watcher/"
echo "  Assets: $NEW_JS, $NEW_CSS"
