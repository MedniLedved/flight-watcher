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

# Python závislosti
# --use-pep517: některé staré sdisty (sgmllib3k přes feedparser) se na systémovém
# Debian setuptools rozbijí ('install_layout'); PEP 517 build je staví v izolaci.
echo "[1/2] Python dependencies..."
pip install -r requirements.txt -q --disable-pip-version-check --use-pep517
pip install pytest -q --disable-pip-version-check
echo "  ✓ pip OK"

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
