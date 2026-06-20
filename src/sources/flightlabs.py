"""FlightLabs (goflightlabs.com) – Skyscanner Flight Prices API (vrstva 1).

Endpointy (auth query param `access_key=FLIGHTLABS_KEY`):
* GET https://www.goflightlabs.com/retrieveAirport?query=MUC → skyId + entityId
* GET https://www.goflightlabs.com/retrieveFlights            → vyhledání letů

Pozn.: starší endpoint `/retrieve-cheapest-flights` byl odstaven (vracel 404 na
každý dotaz – viz git historie). FlightLabs migroval na Skyscanner-based API se
stejným tvarem odpovědi jako Sky Scrapper, proto se parsování sdílí přes
`skyscanner_common`. retrieveFlights vyžaduje skyId+entityId (ne IATA), které se
dohledají přes retrieveAirport a cachují na disk (kvóta se tím šetří).

Kvóta: 4000 req/měsíc, rate limit 10 req/10 s → sekvenčně s ~1 s prodlevou.
Kvóta se trackuje v price_history._meta["flightlabs_requests"].
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from . import FlightResult
from .http_utils import make_api_session
from .skyscanner_common import itineraries_from_payload, parse_itinerary

logger = logging.getLogger(__name__)

BASE_URL = "https://www.goflightlabs.com"
RETRIEVE_AIRPORT_URL = f"{BASE_URL}/retrieveAirport"
RETRIEVE_FLIGHTS_URL = f"{BASE_URL}/retrieveFlights"
_REQUEST_DELAY = 1.1  # 10 req/10 s → drž ~1 req/s
_AIRPORT_CACHE_PATH = Path("data/flightlabs_airports.json")


class FlightLabsSource:
    name = "flightlabs"

    def __init__(self, access_key: str, session: Optional[requests.Session] = None,
                 cache_path: Path | str = _AIRPORT_CACHE_PATH):
        self.access_key = access_key
        self.session = session or make_api_session()
        self.cache_path = Path(cache_path)
        self.request_count = 0
        self._airports = self._load_airport_cache()

    # -- HTTP -------------------------------------------------------------
    def _get(self, url: str, params: dict) -> requests.Response:
        """GET s access_key, počítadlem requestů a rate-limit prodlevou."""
        params = {**params, "access_key": self.access_key}
        resp = self.session.get(url, params=params, timeout=40)
        self.request_count += 1
        time.sleep(_REQUEST_DELAY)
        return resp

    # -- cache letišť (skyId/entityId) ------------------------------------
    def _load_airport_cache(self) -> dict[str, dict]:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_airport_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump(self._airports, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("FlightLabs: nelze uložit cache letišť: %s", exc)

    def resolve_airport(self, iata: str) -> Optional[dict]:
        """Vrátí {'skyId': ..., 'entityId': ...} pro IATA kód. Cachuje na disk,
        ať se měsíční kvóta nepálí opakovaným retrieveAirport voláním."""
        iata = iata.upper()
        if iata in self._airports:
            return self._airports[iata]
        try:
            resp = self._get(RETRIEVE_AIRPORT_URL, {"query": iata})
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("FlightLabs retrieveAirport(%s) chyba: %s", iata, exc)
            return None

        data = self._airport_items(resp.json())
        resolved = self._pick_airport(data, iata)
        if resolved:
            self._airports[iata] = resolved
            self._save_airport_cache()
            return resolved
        logger.warning("FlightLabs: letiště %s nerozpoznáno (položek=%d)",
                       iata, len(data))
        return None

    @staticmethod
    def _airport_items(payload) -> list[dict]:
        """retrieveAirport vrací buď {"data": [...]} nebo přímo [...]."""
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _airport_ids(item: dict) -> tuple[Optional[str], Optional[str]]:
        """Vytáhne (skyId, entityId) z položky bez ohledu na tvar (přímo na
        položce nebo v navigation.relevantFlightParams jako u sky-scrapperu)."""
        params = (item.get("navigation") or {}).get("relevantFlightParams") or {}
        sky_id = params.get("skyId") or item.get("skyId")
        entity_id = params.get("entityId") or item.get("entityId")
        return sky_id, (str(entity_id) if entity_id is not None else None)

    @classmethod
    def _pick_airport(cls, items: list[dict], iata: str) -> Optional[dict]:
        # Preferuj přesnou shodu IATA (skyId) – jinak první platná položka.
        for item in items:
            sky_id, entity_id = cls._airport_ids(item)
            if sky_id and entity_id and sky_id.upper() == iata:
                return {"skyId": sky_id, "entityId": entity_id}
        for item in items:
            sky_id, entity_id = cls._airport_ids(item)
            if sky_id and entity_id:
                return {"skyId": sky_id, "entityId": entity_id}
        return None

    # -- vyhledání --------------------------------------------------------
    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        return_origin: Optional[str] = None,
        return_destination: Optional[str] = None,
        adults: int = 1,
        max_results: int = 10,
        cabin_class: str = "economy",
        route_name: str = "",
    ) -> list[FlightResult]:
        """Vyhledá ZPÁTEČNÍ lety (origin↔destination). retrieveFlights je vždy
        roundtrip se shodným origin/destination – open-jaw API nepodporuje."""
        org = self.resolve_airport(origin)
        dst = self.resolve_airport(destination)
        if not org or not dst:
            logger.warning("FlightLabs: chybí ID letiště pro %s/%s",
                           origin, destination)
            return []

        params = {
            "originSkyId": org["skyId"],
            "destinationSkyId": dst["skyId"],
            "originEntityId": org["entityId"],
            "destinationEntityId": dst["entityId"],
            "date": departure_date.isoformat(),
            "adults": adults,
            "currency": "EUR",
            "cabinClass": cabin_class,
            "sortBy": "price_high",
        }
        if return_date:
            params["returnDate"] = return_date.isoformat()

        try:
            resp = self._get(RETRIEVE_FLIGHTS_URL, params)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("FlightLabs retrieveFlights %s→%s %s: %s",
                         origin, destination, departure_date, exc)
            raise

        # Diagnostika prvních 3 volání v scanu: plný request + raw response,
        # ať je při výpadku zdroje hned vidět příčina (jiný tvar / prázdno).
        if self.request_count <= 3:
            logger.info(
                "FlightLabs DIAG req#%d %s→%s %s: params=%s | HTTP %d | body=%.1500s",
                self.request_count, origin, destination, departure_date,
                {k: v for k, v in params.items()}, resp.status_code, resp.text,
            )

        payload = resp.json()
        if isinstance(payload, dict):
            if payload.get("success") is False or "error" in payload:
                logger.error("FlightLabs API chyba %s→%s: %.400s",
                             origin, destination, payload)
                return []

        itineraries = itineraries_from_payload(payload)
        if not itineraries:
            logger.warning(
                "FlightLabs %s→%s: 0 itinerářů. payload klíče=%s",
                origin, destination,
                list(payload.keys()) if isinstance(payload, dict) else type(payload),
            )
            return []

        results = [
            parse_itinerary(it, origin, destination, route_name, self.name)
            for it in itineraries
        ]
        results = [r for r in results if r is not None]
        results.sort(key=lambda r: r.price)
        return results[:max_results]
