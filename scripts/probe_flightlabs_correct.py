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

# Async job-queue → jedna trasa, submit + poll do 200 (strop 6 req: 1 submit +
# 5 pollů). Joby dozrávají ~30–80 s, proto delší poll_delay.
ORIGIN, DESTINATION = "MUC", "NRT"
DEPART = date(2026, 11, 12)
RETURN = date(2026, 11, 26)
MAX_POLLS = 5        # 1 submit + 5 pollů = strop 6 requestů
POLL_DELAY_S = 12.0


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze testovat.")
        return 1

    # max_polls>0 → submit pollne stejné parametry až do 200 (async job-queue).
    src = FlightLabsSource(settings.flightlabs_key, max_polls=MAX_POLLS,
                           poll_delay=POLL_DELAY_S)
    try:
        results = src.search(ORIGIN, DESTINATION, DEPART, return_date=RETURN,
                             route_name="diag")
    except Exception as exc:  # noqa: BLE001
        logger.error("search %s→%s selhal: %s", ORIGIN, DESTINATION, exc)
        return 0

    logger.info("SEARCH %s→%s: %d nabídek (po %d req, rate_limited=%s)",
                ORIGIN, DESTINATION, len(results), src.request_count,
                src.rate_limited)
    for r in sorted(results, key=lambda r: r.price)[:5]:
        logger.info("  %.0f € | %s→%s | %s→%s | %s",
                    r.price, r.origin, r.destination, r.depart_date,
                    r.return_date, ",".join(r.airlines) or "-")
    if not results:
        logger.warning("Žádné nabídky – job se buď nedokončil v %d pollech, nebo "
                       "viz DIAG surová těla výše pro skutečný tvar/chybu.",
                       MAX_POLLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
