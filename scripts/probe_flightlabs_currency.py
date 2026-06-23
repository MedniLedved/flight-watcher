"""Cílený test: jaký parametr/kombinace donutí goflightlabs vrátit ceny v EUR.

Hypotéza (z retrieve-countries dokumentace): měnu řídí kombinace
currency + market (locale, např. de-DE) + countryCode (DE), ne samotné
currency=EUR. Tenhle probe pošle JEDEN čerstvý job (nová data, ať se netrefí
do nacachovaného USD jobu) s currency=EUR&market=de-DE&countryCode=DE, pollne
ho do 200 a vypíše měnu + cenu z odpovědi.

Read-only, strop 6 requestů (1 submit + 5 pollů). Async job dozrává ~30–80 s.
Spuštění (v CI): python -m scripts.probe_flightlabs_currency
"""
from __future__ import annotations

import logging
import sys
import time

import requests

from src.config import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs_currency")

URL = "https://www.goflightlabs.com/retrieveFlights"
# Čerstvá kombinace (jiná než dříve testovaný MUC→NRT 11-12/26 nacachovaný USD job).
PARAMS = {
    "originIATACode": "MUC",
    "destinationIATACode": "NRT",
    "date": "2026-11-13",
    "returnDate": "2026-11-27",
    "adults": "1",
    "cabinClass": "economy",
    "mode": "roundtrip",
    "sortBy": "best",
    "group_by_roundtrip": "true",
    # --- testované parametry měny ---
    "currency": "EUR",
    "market": "de-DE",
    "countryCode": "DE",
}
MAX_REQUESTS = 6
POLL_DELAY_S = 12.0
REQUEST_DELAY_S = 2.0


def _currency_of(payload) -> tuple:
    """Vrátí (currency, price) z prvního páru/legu, ať to máme ať je tvar pairs
    nebo flights."""
    container = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        container = payload["data"]
    pairs = container.get("pairs") if isinstance(container, dict) else None
    if isinstance(pairs, list) and pairs:
        leg = pairs[0].get("outbound") or pairs[0]
        return leg.get("currency"), pairs[0].get("price") or leg.get("price")
    flights = container.get("flights") if isinstance(container, dict) else None
    if isinstance(flights, list) and flights:
        return flights[0].get("currency"), flights[0].get("price")
    return None, None


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze testovat.")
        return 1

    session = requests.Session()
    full = {**PARAMS, "access_key": settings.flightlabs_key}

    for attempt in range(1, MAX_REQUESTS + 1):
        resp = session.get(URL, params=full, timeout=60)
        body = (resp.text or "").replace("\n", " ")
        logger.info("req#%d → HTTP %d | %.400s", attempt, resp.status_code, body)
        time.sleep(REQUEST_DELAY_S)
        if resp.status_code == 202:
            if attempt < MAX_REQUESTS:
                time.sleep(POLL_DELAY_S)
                continue
            logger.warning("Job se nedokončil v %d requestech.", MAX_REQUESTS)
            return 0
        if resp.status_code != 200:
            logger.error("HTTP %d → konec.", resp.status_code)
            return 0
        cur, price = _currency_of(resp.json())
        logger.info("VÝSLEDEK: currency=%s, price=%s (params: currency=EUR, "
                    "market=de-DE, countryCode=DE)", cur, price)
        if cur == "EUR":
            logger.info("✅ EUR – kombinace currency+market+countryCode FUNGUJE.")
        else:
            logger.warning("❌ Měna stále %s – tato kombinace nestačí.", cur)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
