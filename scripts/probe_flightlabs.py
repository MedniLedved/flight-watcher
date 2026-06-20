"""Probe přesného kontraktu goflightlabs /retrieveFlights.

Předchozí probe zjistil: /retrieveFlights je ŽIVÝ (HTTP 422, ne 404/410) a chce
IATA kód přímo ("The origin i a t a code field is required") – žádný skyId/
entityId lookup. Tenhle probe iterativně posílá kandidátní názvy parametrů a
loguje plné tělo odpovědi, ať se z 422/200 pozná přesný kontrakt (názvy polí +
tvar úspěšné odpovědi).

Read-only. Spuštění: python -m scripts.probe_flightlabs
"""
from __future__ import annotations

import logging
import sys

import requests

from src.config import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs")

URL = "https://www.goflightlabs.com/retrieveFlights"

# Postupně bohatší sady parametrů – z 422 hlášek zjistíme, co ještě chybí /
# jak se pole jmenují. Posíláme víc variant názvů naráz (neznámé API ignoruje).
PARAM_SETS = [
    ("camel iata + date", {
        "originIata": "MUC", "destinationIata": "NRT",
        "date": "2026-09-10", "adults": "1", "currency": "EUR",
    }),
    ("camel iata + departureDate/returnDate", {
        "originIata": "MUC", "destinationIata": "NRT",
        "departureDate": "2026-09-10", "returnDate": "2026-09-24",
        "adults": "1", "currency": "EUR", "cabinClass": "economy",
    }),
    ("snake iata", {
        "origin_iata": "MUC", "destination_iata": "NRT",
        "departure_date": "2026-09-10", "return_date": "2026-09-24",
        "adults": "1", "currency": "EUR",
    }),
    ("wide net (vše naráz)", {
        "originIata": "MUC", "origin_iata": "MUC", "origin": "MUC",
        "destinationIata": "NRT", "destination_iata": "NRT", "destination": "NRT",
        "date": "2026-09-10", "departureDate": "2026-09-10",
        "departure_date": "2026-09-10", "returnDate": "2026-09-24",
        "return_date": "2026-09-24", "adults": "1", "currency": "EUR",
        "cabinClass": "economy", "cabin_class": "economy",
    }),
]


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze probovat.")
        return 1

    session = requests.Session()
    for desc, params in PARAM_SETS:
        full = {**params, "access_key": settings.flightlabs_key}
        try:
            resp = session.get(URL, params=full, timeout=30)
        except requests.RequestException as exc:
            logger.info("%-38s EXC: %s", desc, exc)
            continue
        body = resp.text.replace("\n", " ")[:600]
        logger.info("%-38s HTTP %s | %s", desc, resp.status_code, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
