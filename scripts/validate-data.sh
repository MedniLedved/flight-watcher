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

# 5. One-way pollution guard v dlouhodobých řadách (history/) a price_history.json.
#    Stejná regrese jako [4], ale na KANONICKÝCH datech, ze kterých se počítají
#    statistiky (airport_stats → insights.json). latest.json one-way zahazuje, ale
#    historie ho dřív obsahovala (travelpayouts bez return_date uložený pod klíč
#    "-roundtrip" → ~2× nižší cena zkreslila deal-rate per letiště). Kontrola [4]
#    sama tento typ chyby v historii NEODCHYTÍ, proto je tady navíc.
#    Pole: history/*.json používá camelCase `returnDate`, price_history.json
#    snake_case `return_date`. Roundtrip/openjaw se pozná z názvu řady / route_key.
bad_hist=0
if [ -d "$DATA_DIR/history" ]; then
  while IFS= read -r -d '' jf; do
    base="$(basename "$jf" .json)"
    case "$base" in
      *-roundtrip|*-openjaw)
        bad=$(jq '[.[] | select(.returnDate == null)] | length' "$jf")
        if [ "$bad" -gt 0 ]; then
          echo "  ❌ history/$base.json: $bad záznamů bez returnDate (one-way pollution)"
          bad_hist=$((bad_hist + bad))
        fi
        ;;
    esac
  done < <(find "$DATA_DIR/history" -name '*.json' -print0)
fi
if [ "$bad_hist" -gt 0 ]; then
  exit 1
fi
echo "  ✓ Žádná one-way pollution v history/"

# price_history.json je jen v root data/ (ne ve web/public/data) → guard s [ -f ].
if [ -f "$DATA_DIR/price_history.json" ]; then
  bad_ph=$(jq '[ to_entries[]
                 | select(.key != "_meta" and (.key | test("-(roundtrip|openjaw)$")))
                 | .value.history // []
                 | .[]
                 | select(.return_date == null) ] | length' \
              "$DATA_DIR/price_history.json")
  if [ "$bad_ph" -gt 0 ]; then
    echo "  ❌ price_history.json: $bad_ph roundtrip/openjaw záznamů bez return_date (one-way pollution)"
    jq -r 'to_entries[]
           | select(.key != "_meta" and (.key | test("-(roundtrip|openjaw)$")))
           | select([.value.history // [] | .[] | select(.return_date == null)] | length > 0)
           | "    \(.key)"' "$DATA_DIR/price_history.json" | sort -u
    exit 1
  fi
  echo "  ✓ Žádná one-way pollution v price_history.json"
fi

echo "== Datová validace OK =="
