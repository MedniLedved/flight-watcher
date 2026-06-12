#!/usr/bin/env bash
# Builds the dashboard, validates, and deploys to gh-pages via git worktree.
# Usage (from repo root): ./scripts/deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
WORKTREE_DIR=$(mktemp -d)

cleanup() { git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || true; }
trap cleanup EXIT

echo "==> [1/4] Syncing scanner data (root data/ -> web/public/data/)"
# Ruční deploy musí vidět stejná čerstvá data jako CI (deploy.yml). Bez tohoto
# kroku by se nasadil starý snapshot z web/public/data a živá data na gh-pages
# by se přepsala. Guardy „[ -f ] && cp || true" kopírují JEN co existuje, takže
# chybějící zdroj nikdy nevymaže už nasazenou historii/kalendář.
mkdir -p "$REPO_ROOT/web/public/data/calendar" "$REPO_ROOT/web/public/data/history" "$REPO_ROOT/web/public/config"
for f in latest.json stats.json routes.json meta.json insights.json; do
  [ -f "$REPO_ROOT/data/$f" ] && cp "$REPO_ROOT/data/$f" "$REPO_ROOT/web/public/data/$f" || true
done
[ -d "$REPO_ROOT/data/history" ] && cp -r "$REPO_ROOT/data/history/." "$REPO_ROOT/web/public/data/history/" || true
[ -d "$REPO_ROOT/data/calendar" ] && cp -r "$REPO_ROOT/data/calendar/." "$REPO_ROOT/web/public/data/calendar/" || true
[ -f "$REPO_ROOT/config/agent.json" ] && cp "$REPO_ROOT/config/agent.json" "$REPO_ROOT/web/public/config/agent.json" || true

echo ""
echo "==> [2/4] Building from branch: $DEV_BRANCH"
cd "$REPO_ROOT/web"
npm install --silent
rm -rf dist
npm run build 2>&1 | grep -E "built|error"

echo ""
echo "==> [3/4] Running validation suite..."
bash "$REPO_ROOT/scripts/validate.sh" || { echo "❌ Validation failed"; exit 1; }

echo ""
echo "==> [4/4] Deploying to gh-pages via worktree..."
NEW_JS=$(grep -o 'assets/index-[^"]*\.js' dist/index.html)
NEW_CSS=$(grep -o 'assets/index-[^"]*\.css' dist/index.html)

git -C "$REPO_ROOT" worktree add "$WORKTREE_DIR" gh-pages

# Vyčisti jen build artefakty. data/ a config/ ZÁMĚRNĚ NEMAŽEME – jsou
# append-only (historie/kalendář) a na gh-pages mohou být soubory, které
# lokálně nemáme. Níže je jen přepíšeme/doplníme (overlay), nikdy cizí nemažeme.
find "$WORKTREE_DIR" -maxdepth 1 -mindepth 1 \
  ! -name '.git' ! -name 'data' ! -name 'config' -exec rm -rf {} +

# Ensure Jekyll is disabled (required for Pages to serve raw JS/CSS correctly)
touch "$WORKTREE_DIR/.nojekyll"

# Copy built assets and index
cp -r dist/assets "$WORKTREE_DIR/"
cp dist/index.html "$WORKTREE_DIR/"

# Overlay data a config. `cp` přepíše stejnojmenné soubory, ale NEMAŽE ty, co
# jsou na gh-pages navíc → nasazená append-only historie/kalendář se neztratí.
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
git push --force-with-lease origin gh-pages

echo ""
echo "✓ Deployment successful: https://medniledved.github.io/flight-watcher/"
echo "  Assets: $NEW_JS, $NEW_CSS"
