#!/usr/bin/env bash
# Validuje kanonická data scanneru (root data/ NEBO web/public/data/).
# Lehký, bez Node/npm — spustitelný i v CI scan jobu, kde není frontend build.
#
# Používá:
#  - .github/workflows/scan.yml: PŘED commitem denního exportu. Scan job nemá
#    dist/ ani npm, takže nemůže spustit plné validate.sh — tento skript hlídá
#    datovou vrstvu (zejména one-way pollution), aby se znečištěná data nikdy
#    nedostala na main commitem z CI.
#  - scripts/validate.sh [5b]: sdílí stejný one-way pollution guard (jeden zdroj
#    pravdy — neduplikovat jq logiku).
#
# REGRESSION-PROOFING (viz CLAUDE.md): nově objevený typ datové chyby přidej sem
# jako kontrolu s exit 1. Kontroly nikdy neodstraňuj.
#
# Usage: bash scripts/validate-data.sh [DATA_DIR]   (default: data)
set -euo pipefail

DATA_DIR="${1:-data}"

if [ ! -d "$DATA_DIR" ]; then
  echo "❌ Datový adresář neexistuje: $DATA_DIR"
  exit 1
fi

echo "== Datová validace: $DATA_DIR =="

# 1. Povinné soubory existují a jsou validní JSON.
REQUIRED=("latest.json" "stats.json")
for f in "${REQUIRED[@]}"; do
  path="$DATA_DIR/$f"
  if [ ! -f "$path" ]; then
    echo "  ❌ Chybí povinný soubor: $path"
    exit 1
  fi
  if ! jq empty "$path" 2>/dev/null; then
    echo "  ❌ Nevalidní JSON: $path"
    exit 1
  fi
  echo "  ✓ $f"
done

# 2. Volitelné soubory: pokud existují, musí být validní JSON.
OPTIONAL=("routes.json" "meta.json" "insights.json")
for f in "${OPTIONAL[@]}"; do
  path="$DATA_DIR/$f"
  if [ -f "$path" ] && ! jq empty "$path" 2>/dev/null; then
    echo "  ❌ Nevalidní JSON: $path"
    exit 1
  fi
done

# 3. history/, calendar/ a alternatives/ řady: každý soubor musí být validní JSON.
for sub in history calendar alternatives; do
  if [ -d "$DATA_DIR/$sub" ]; then
    while IFS= read -r -d '' jf; do
      if ! jq empty "$jf" 2>/dev/null; then
        echo "  ❌ Nevalidní JSON v $sub/: $jf"
        exit 1
      fi
    done < <(find "$DATA_DIR/$sub" -name '*.json' -print0)
  fi
done

# 4. One-way pollution guard: roundtrip/openjaw nabídka MUSÍ mít returnDate.
#    Regrese: travelpayouts/aviasales vracel bez return_at JEDNOSMĚRNÉ letenky,
#    které se ukládaly jako roundtrip s prázdným returnDate (podhodnocená cena
#    znečistila zpáteční data). Exporter teď one-way zahazuje; tato kontrola
#    hlídá, aby se podhodnocené one-way nabídky nikdy nedostaly do latest.json.
BAD_RT=$(jq '[.[] | select((.type == "roundtrip" or .type == "openjaw") and (.returnDate == null))] | length' "$DATA_DIR/latest.json")
if [ "$BAD_RT" -gt 0 ]; then
  echo "  ❌ $BAD_RT roundtrip/openjaw nabídek bez returnDate (one-way pollution)"
  jq -r '.[] | select((.type == "roundtrip" or .type == "openjaw") and (.returnDate == null)) | "    \(.routeKey) (\(.source))"' "$DATA_DIR/latest.json"
  exit 1
fi
echo "  ✓ Žádná one-way pollution v latest.json"

echo "== Datová validace OK =="
