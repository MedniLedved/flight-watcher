"""Izolovaný diagnostický běh FlightLabs (2-fázový submit/collect).

NEdělá nic destruktivního: nezapisuje historii, neposílá Telegram, necommituje.
Submitne pár kombinací (retrieveFlights je async → většinou 202), pak několik
kol re-dotáže pending joby (collect) s prodlevou, ať se v actions logu ukáže,
jestli a za jak dlouho job dokončí a že parser sedí.

Spuštění: python -m scripts.test_flightlabs
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date

from src.config import Settings
from src.sources.flightlabs import FlightLabsSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_flightlabs")

TEST_COMBOS = [("MUC", "KIX"), ("PRG", "NRT"), ("VIE", "HND")]
DEPART = date(2026, 11, 12)
RETURN = date(2026, 11, 26)
COLLECT_ROUNDS = 5
COLLECT_DELAY_S = 8.0


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze testovat.")
        return 1

    src = FlightLabsSource(settings.flightlabs_key)
    all_results = []
    pending = []

    # Fáze 1 – submit (krátký poll chytí už nacachované joby).
    for origin, destination in TEST_COMBOS:
        results, pend = src.submit(origin, destination, DEPART, return_date=RETURN,
                                   route_name="diag")
        logger.info("SUBMIT %s→%s: %d výsledků, pending=%s",
                    origin, destination, len(results), pend is not None)
        all_results += results
        if pend is not None:
            pending.append(pend)

    # Fáze 2 – collect: re-dotáže pending joby v několika kolech s prodlevou.
    for rnd in range(1, COLLECT_ROUNDS + 1):
        if not pending:
            break
        time.sleep(COLLECT_DELAY_S)
        still = []
        for job in pending:
            results, done = src.collect(job)
            o, d = job.get("originIATACode"), job.get("destinationIATACode")
            if results:
                logger.info("COLLECT[%d] %s→%s: %d výsledků (hotovo)",
                            rnd, o, d, len(results))
                all_results += results
            elif not done:
                still.append(job)
            else:
                logger.info("COLLECT[%d] %s→%s: hotovo bez výsledků/chyba",
                            rnd, o, d)
        pending = still
        logger.info("COLLECT kolo %d: zbývá %d pending", rnd, len(pending))

    logger.info("CELKEM: %d nabídek, %d requestů, %d nedokončených pending",
                len(all_results), src.request_count, len(pending))
    for r in sorted(all_results, key=lambda r: r.price)[:5]:
        logger.info("  %.0f € | %s→%s | %s→%s | %s",
                    r.price, r.origin, r.destination, r.depart_date, r.return_date,
                    ",".join(r.airlines) or "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
