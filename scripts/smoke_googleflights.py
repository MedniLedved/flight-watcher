"""Smoke test zdroje Google Flights – JEDNO živé vyhledávání, výpis výsledků.

Ověřuje proti reálnému Googlu (proto není v pytest sadě – testy nesmí na síť):
že fast-flights stáhne a naparsuje výsledky, ceny jsou v EUR a deep link sedí.

Spuštění lokálně:  python scripts/smoke_googleflights.py [ORIGIN DEST [NOCÍ]]
V CI: workflow „Test Google Flights source" (workflow_dispatch).

Exit kód 0 = zdroj vrátil aspoň jednu nabídku v EUR; 1 = selhání.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sources.googleflights import GoogleFlightsSource  # noqa: E402


def main() -> int:
    origin = sys.argv[1] if len(sys.argv) > 1 else "MUC"
    dest = sys.argv[2] if len(sys.argv) > 2 else "NRT"
    nights = int(sys.argv[3]) if len(sys.argv) > 3 else 14
    depart = date.today() + timedelta(days=90)
    ret = depart + timedelta(days=nights)

    print(f"Hledám {origin}→{dest}, {depart} – {ret} ({nights} nocí)…")
    src = GoogleFlightsSource()
    try:
        results = src.search(origin, dest, depart, return_date=ret,
                             route_name="smoke")
    except Exception as exc:  # noqa: BLE001
        print(f"CHYBA: vyhledávání selhalo: {exc!r}")
        return 1

    if not results:
        print("CHYBA: žádné nabídky (parsing selhal, nebo Google blokuje).")
        return 1

    print(f"OK: {len(results)} nabídek (řazeno dle ceny):")
    for r in results:
        airlines = ", ".join(r.airlines) or "?"
        print(f"  {r.price:8.0f} {r.currency}  {airlines}")
    print(f"\nNejlevnější: {results[0].price:.0f} {results[0].currency}")
    print(f"Deep link:   {results[0].deep_link}")
    bad = [r for r in results if r.currency != "EUR"]
    if bad:
        print(f"CHYBA: {len(bad)} nabídek není v EUR!")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
