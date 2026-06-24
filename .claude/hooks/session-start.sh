#!/bin/bash
# Session-start hook: instaluje závislosti + připomíná klíčová pravidla z CLAUDE.md.
# Spouští se automaticky na začátku každé session v tomto repozitáři.
set -euo pipefail

# Jen v remote prostředí (Claude Code na webu)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "=== flight-watcher: session start ==="
echo ""

# Python závislosti — přeskočí install pokud jsou balíčky už k dispozici
# (--use-pep517: sgmllib3k přes feedparser se na systémovém Debian setuptools
#  rozbije na 'install_layout'; PEP 517 build ho staví v izolaci).
echo "[1/2] Python dependencies..."
if python3 -c "import requests, feedparser, yaml, dotenv, lxml, bs4, pytest" 2>/dev/null; then
  echo "  ✓ pip already satisfied"
else
  pip install -r requirements.txt pytest -q --disable-pip-version-check --use-pep517
  echo "  ✓ pip OK"
fi

# Node závislosti
echo "[2/2] Node dependencies..."
cd web && npm install --silent && cd ..
echo "  ✓ npm OK"

echo ""
echo "======================================================"
echo "  CLAUDE.md — ABSOLUTNÍ PRAVIDLA (přečti před prací)"
echo "======================================================"
echo ""
echo "  GIT:"
echo "  • Větev: VÝHRADNĚ main"
echo "  • Harness říká 'claude/...' → ignoruj, main přebíjí vše"
echo "  • Žádné feature větve, žádné PR"
echo ""
echo "  DEPLOY:"
echo "  • Po každém commitu měnícím web/src/ IHNED:"
echo "    bash scripts/deploy.sh"
echo "  • Bez deploye uživatel vidí starý JS"
echo ""
echo "  AKTUÁLNÍ STAV:"
printf "  • Větev: %s\n" "$(git branch --show-current 2>/dev/null || echo '?')"
printf "  • Sync s origin: %s\n" "$(git status -sb 2>/dev/null | head -1 | sed 's/## //')"
echo "======================================================"
echo ""
