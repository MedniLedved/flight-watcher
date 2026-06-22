"""Minimální živý test FlightLabs přes PRODUKČNÍ zdroj (src.sources.flightlabs).

Smysl: na malém vzorku (strop 6 requestů) ověřit, že po opravě dostáváme reálné
spoje. Volá přímo FlightLabsSource.search() – tedy úplně stejný kód i parser,
jaký používá scanner v produkci (žádná paralelní reimplementace, která by se
mohla rozejít s realitou).

Oficiální kontrakt (goflightlabs Flight Prices API):
  GET /retrieveFlights?access_key=…&originIATACode=…&destinationIATACode=…
      &date=…&returnDate=…&mode=roundtrip&sortBy=best&group_by_roundtrip=true
  → synchronní 200 {"pairs":[{outbound,inbound,price}], "unpaired":[…]}

NEdělá nic destruktivního: nezapisuje historii, neposílá Telegram, necommituje.
Zdroj sám loguje surové tělo prvních 3 requestů (DIAG) → vidíme skutečný tvar.

Spuštění (v CI, kde je FLIGHTLABS_KEY): python -m scripts.probe_flightlabs_correct
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from src.config import Settings
from src.sources.flightlabs import FlightLabsSource

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs_correct")

# Pár tras Evropa→Japonsko v cestovním okně (září–prosinec 2026).
COMBOS = [("MUC", "NRT"), ("PRG", "NRT"), ("VIE", "HND")]
DEPART = date(2026, 11, 12)
RETURN = date(2026, 11, 26)
MAX_REQUESTS = 6   # tvrdý strop – ať se nespálí víc, než je schváleno


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze testovat.")
        return 1

    src = FlightLabsSource(settings.flightlabs_key)
    all_results = []

    for origin, destination in COMBOS:
        if src.request_count >= MAX_REQUESTS:
            logger.info("Dosažen strop %d requestů – končím.", MAX_REQUESTS)
            break
        try:
            results = src.search(origin, destination, DEPART,
                                 return_date=RETURN, route_name="diag")
        except Exception as exc:  # noqa: BLE001
            logger.error("search %s→%s selhal: %s", origin, destination, exc)
            continue
        logger.info("SEARCH %s→%s: %d nabídek (po %d req, rate_limited=%s)",
                    origin, destination, len(results), src.request_count,
                    src.rate_limited)
        for r in sorted(results, key=lambda r: r.price)[:3]:
            logger.info("  %.0f € | %s→%s | %s→%s | %s",
                        r.price, r.origin, r.destination, r.depart_date,
                        r.return_date, ",".join(r.airlines) or "-")
        all_results += results

    logger.info("CELKEM: %d nabídek za %d requestů.", len(all_results),
                src.request_count)
    if not all_results:
        logger.warning("Žádné nabídky – viz DIAG surová těla výše pro skutečný "
                       "tvar odpovědi / chybu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
