"""Probe živých goflightlabs endpointů pro daný FLIGHTLABS_KEY.

Migrace na www.goflightlabs.com/retrieveAirport vrátila 410 Gone (endpoint
odstaven). Tento skript jednorázově oťuká matici kandidátních endpointů (host
app vs www, různé cesty) a vypíše HTTP status + začátek těla, ať se z actions
logu pozná, KTERÝ endpoint je pro tenhle klíč/plán živý.

Read-only, ~tucet requestů. Spuštění: python -m scripts.probe_flightlabs
"""
from __future__ import annotations

import logging
import sys

import requests

from src.config import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs")

# (popis, URL, extra query params bez access_key)
CANDIDATES = [
    # Ověření platnosti klíče – vlajkový real-time tracker goflightlabs.
    ("tracker www/flights", "https://www.goflightlabs.com/flights", {"limit": "1"}),
    ("tracker app/flights", "https://app.goflightlabs.com/flights", {"limit": "1"}),
    # Skyscanner airport lookup – různé hosty/cesty.
    ("www/retrieveAirport", "https://www.goflightlabs.com/retrieveAirport", {"query": "MUC"}),
    ("app/retrieveAirport", "https://app.goflightlabs.com/retrieveAirport", {"query": "MUC"}),
    ("www/retrieve-airport", "https://www.goflightlabs.com/retrieve-airport", {"query": "MUC"}),
    ("www/searchAirport", "https://www.goflightlabs.com/searchAirport", {"query": "MUC"}),
    # Skyscanner flight search – různé hosty/cesty.
    ("www/retrieveFlights", "https://www.goflightlabs.com/retrieveFlights",
     {"originSkyId": "MUC", "destinationSkyId": "NRT", "date": "2026-09-10"}),
    ("app/retrieveFlights", "https://app.goflightlabs.com/retrieveFlights",
     {"originSkyId": "MUC", "destinationSkyId": "NRT", "date": "2026-09-10"}),
    # Starý (odstavený) cheapest-flights endpoint – pro srovnání statusu.
    ("app/retrieve-cheapest-flights", "https://app.goflightlabs.com/retrieve-cheapest-flights",
     {"origin": "MUC", "destination": "NRT", "departureDate": "2026-09-10"}),
]


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze probovat.")
        return 1

    session = requests.Session()
    for desc, url, params in CANDIDATES:
        full = {**params, "access_key": settings.flightlabs_key}
        try:
            resp = session.get(url, params=full, timeout=30)
        except requests.RequestException as exc:
            logger.info("%-32s EXC: %s", desc, exc)
            continue
        body = resp.text.replace("\n", " ")[:250]
        logger.info("%-32s HTTP %s | %s", desc, resp.status_code, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
