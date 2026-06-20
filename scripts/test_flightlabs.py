"""Izolovaný diagnostický běh FlightLabs (goflightlabs Skyscanner API).

NEdělá nic destruktivního: nezapisuje price_history, neposílá Telegram,
necommituje data. Jen vezme FLIGHTLABS_KEY z prostředí, spustí pár dotazů
přes nový endpoint (retrieveAirport → retrieveFlights) a vypíše výsledek/chyby
do stdout, ať se z actions logu pozná, jestli zdroj po migraci funguje.

Spuštění: python -m scripts.test_flightlabs
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from src.config import Settings
from src.sources.flightlabs import FlightLabsSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_flightlabs")

# Pár reprezentativních tras + jeden termín v cestovním okně (zář–pro 2026).
# Krátký seznam → pár requestů, nezatíží měsíční kvótu.
TEST_COMBOS = [
    ("MUC", "KIX"),
    ("PRG", "NRT"),
    ("VIE", "HND"),
]
DEPART = date(2026, 9, 10)
RETURN = date(2026, 9, 24)


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze testovat.")
        return 1

    src = FlightLabsSource(settings.flightlabs_key)

    total = 0
    for origin, destination in TEST_COMBOS:
        logger.info("=== TEST %s→%s %s/%s ===", origin, destination, DEPART, RETURN)
        try:
            results = src.search(
                origin=origin, destination=destination,
                departure_date=DEPART, return_date=RETURN,
                return_origin=origin, return_destination=destination,
                route_name="diag",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("%s→%s VÝJIMKA: %s", origin, destination, exc)
            continue

        total += len(results)
        logger.info("%s→%s: %d nabídek", origin, destination, len(results))
        for r in results[:3]:
            logger.info(
                "  %.0f € | %s→%s | %s→%s | %s | %s",
                r.price, r.origin, r.destination,
                r.depart_date, r.return_date,
                ",".join(r.airlines) or "-", r.source,
            )

    logger.info("CELKEM: %d nabídek, %d requestů", total, src.request_count)
    if total == 0:
        logger.warning(
            "FlightLabs vrátil 0 nabídek – viz DIAG logy výše (HTTP status / "
            "tvar payloadu) pro příčinu."
        )
        # Nevrací nenulový exit – 0 nabídek může být legitimní (drahé termíny),
        # cílem je diagnostika v logu, ne fail CI.
    return 0


if __name__ == "__main__":
    sys.exit(main())
