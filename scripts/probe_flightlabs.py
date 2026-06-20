"""Probe goflightlabs /retrieveFlights AŽ DO DOKONČENÍ async jobu.

Kontrakt zjištěn: params originIATACode/destinationIATACode/date; první volání
vrátí 202 {"status":"processing","jobId":...}; výsledky se získají opakovaným
voláním STEJNÝCH parametrů. Tenhle probe pollne až do 200 a vypíše PLNÉ tělo,
ať se pozná tvar dokončené odpovědi (itineráře/price/legs) + jestli vrací
roundtrip (2 legs) při zadaném returnDate.

Read-only. Spuštění: python -m scripts.probe_flightlabs
"""
from __future__ import annotations

import logging
import sys
import time

import requests

from src.config import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs")

URL = "https://www.goflightlabs.com/retrieveFlights"
PARAMS = {
    "originIATACode": "MUC",
    "destinationIATACode": "NRT",
    "date": "2026-09-10",
    "returnDate": "2026-09-24",   # ověř, zda vrací roundtrip (2 legs)
    "adults": "1",
    "currency": "EUR",
    "cabinClass": "economy",
}
MAX_POLLS = 10
POLL_DELAY_S = 3.0


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze probovat.")
        return 1

    session = requests.Session()
    full = {**PARAMS, "access_key": settings.flightlabs_key}

    for attempt in range(1, MAX_POLLS + 1):
        try:
            resp = session.get(URL, params=full, timeout=30)
        except requests.RequestException as exc:
            logger.info("poll %d EXC: %s", attempt, exc)
            time.sleep(POLL_DELAY_S)
            continue

        status = resp.status_code
        if status == 202:
            logger.info("poll %d: HTTP 202 processing (%.120s)", attempt,
                        resp.text.replace("\n", " "))
            time.sleep(POLL_DELAY_S)
            continue

        # 200 nebo cokoli jiného → dump plného těla a konec.
        logger.info("poll %d: HTTP %s | FULL BODY:\n%s", attempt, status,
                    resp.text[:4000])
        return 0

    logger.warning("Job se nedokončil ani po %d pollech.", MAX_POLLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
