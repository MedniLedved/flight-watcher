"""Sky Scrapper API přes RapidAPI (Skyscanner data, vrstva 1).

RapidAPI host: sky-scrapper.p.rapidapi.com
Autentizace: hlavičky `x-rapidapi-key: RAPIDAPI_KEY`, `x-rapidapi-host`.

Endpointy:
* GET /api/v1/flights/searchAirport?query=FRA  → resolve skyId + entityId
* GET /api/v1/flights/searchFlights            → vyhledání letů

⚠️ FREE TIER: 100 requestů / MĚSÍC. To je velmi málo – proto:
  - skyId/entityId letišť se cachují na disk (data/skyscrapper_airports.json),
    aby se searchAirport nevolal opakovaně,
  - RATE_LIMIT_COMBINATIONS["skyscrapper"] je nastaven nízko (viz config.py),
  - každé volání se počítá; při vyčerpání kvóty zdroj jen zaloguje chybu a
    scan pokračuje dál.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from . import FlightResult
from .http_utils import make_api_session, random_sleep
from .skyscanner_common import itineraries_from_payload, parse_itinerary

logger = logging.getLogger(__name__)


RAPIDAPI_HOST = "sky-scrapper.p.rapidapi.com"
SEARCH_AIRPORT_URL = f"https://{RAPIDAPI_HOST}/api/v1/flights/searchAirport"
SEARCH_FLIGHTS_URL = f"https://{RAPIDAPI_HOST}/api/v1/flights/searchFlights"
_REQUEST_DELAY = 1.0
_AIRPORT_CACHE_PATH = Path("data/skyscrapper_airports.json")


class SkyScrapperSource:
    name = "skyscrapper"

    def __init__(self, rapidapi_key: str, session: Optional[requests.Session] = None,
                 cache_path: Path | str = _AIRPORT_CACHE_PATH):
        self.rapidapi_key = rapidapi_key
        self.session = session or make_api_session()
        self.cache_path = Path(cache_path)
        self.request_count = 0
        self._airports = self._load_airport_cache()
        # Stav kvóty zjištěný z RapidAPI hlaviček (viz _note_quota).
        self.quota_remaining: Optional[int] = None
        self.quota_limit: Optional[int] = None
        self.quota_reset_at: Optional[datetime] = None
        self.quota_exhausted = False

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "x-rapidapi-key": self.rapidapi_key,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }

    def _note_quota(self, resp: requests.Response) -> None:
        """Přečte RapidAPI rate-limit hlavičky (kolik requestů zbývá a za jak
        dlouho se kvóta resetuje) – slouží k auto-vypnutí i rozpočítání."""
        h = resp.headers
        rem = h.get("x-ratelimit-requests-remaining")
        lim = h.get("x-ratelimit-requests-limit")
        rst = h.get("x-ratelimit-requests-reset")
        if rem is not None:
            try:
                self.quota_remaining = int(rem)
            except ValueError:
                pass
        if lim is not None:
            try:
                self.quota_limit = int(lim)
            except ValueError:
                pass
        if rst is not None:
            try:
                self.quota_reset_at = datetime.now() + timedelta(seconds=float(rst))
            except ValueError:
                pass

    def _get(self, url: str, params: dict) -> requests.Response:
        """GET s evidencí kvóty z hlaviček a detekcí vyčerpání (HTTP 429)."""
        resp = self.session.get(url, params=params, headers=self._headers, timeout=40)
        self.request_count += 1
        self._note_quota(resp)
        if resp.status_code == 429 or self.quota_remaining == 0:
            self.quota_exhausted = True
        random_sleep(_REQUEST_DELAY)
        resp.raise_for_status()
        return resp

    # -- cache letišť -----------------------------------------------------
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
            logger.warning("Nelze uložit cache letišť: %s", exc)

    def resolve_airport(self, iata: str) -> Optional[dict]:
        """Vrátí {'skyId': ..., 'entityId': ...} pro IATA kód. Cachuje na disk,
        aby se nevyčerpávala měsíční kvóta searchAirport voláním."""
        iata = iata.upper()
        if iata in self._airports:
            return self._airports[iata]
        try:
            resp = self._get(SEARCH_AIRPORT_URL, {"query": iata, "locale": "en-US"})
        except requests.RequestException as exc:
            logger.error("Sky Scrapper searchAirport(%s) chyba: %s", iata, exc)
            return None

        data = resp.json().get("data", [])
        for item in data:
            nav = item.get("navigation", {})
            params = nav.get("relevantFlightParams", {})
            sky_id = params.get("skyId") or item.get("skyId")
            entity_id = params.get("entityId") or item.get("entityId")
            # Preferuj přesnou shodu IATA kódu.
            if sky_id and sky_id.upper() == iata and entity_id:
                resolved = {"skyId": sky_id, "entityId": str(entity_id)}
                self._airports[iata] = resolved
                self._save_airport_cache()
                return resolved
        # Fallback: první výsledek typu AIRPORT.
        for item in data:
            nav = item.get("navigation", {})
            params = nav.get("relevantFlightParams", {})
            if params.get("skyId") and params.get("entityId"):
                resolved = {
                    "skyId": params["skyId"],
                    "entityId": str(params["entityId"]),
                }
                self._airports[iata] = resolved
                self._save_airport_cache()
                return resolved
        logger.warning("Sky Scrapper: letiště %s nerozpoznáno", iata)
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
        cabin_class: str = "economy",
        max_results: int = 10,
        route_name: str = "",
    ) -> list[FlightResult]:
        """Vyhledá lety. Sky Scrapper searchFlights podporuje zpáteční let
        (returnDate) se shodným origin/destination. Pro open-jaw API nemá
        nativní podporu – vrací jednosměrný outbound leg, inbound je třeba
        řešit zvlášť (zde se vrací outbound; open-jaw kombinace pokrývá
        primárně Duffel)."""
        org = self.resolve_airport(origin)
        dst = self.resolve_airport(destination)
        if not org or not dst:
            logger.warning("Sky Scrapper: chybí ID letiště pro %s/%s",
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
            "market": "en-US",
            "cabinClass": cabin_class,
            "sortBy": "price_high",
        }
        if return_date:
            params["returnDate"] = return_date.isoformat()

        try:
            resp = self._get(SEARCH_FLIGHTS_URL, params)
        except requests.RequestException as exc:
            logger.error("Sky Scrapper searchFlights %s→%s chyba: %s",
                         origin, destination, exc)
            raise

        itineraries = itineraries_from_payload(resp.json())
        results = [
            self._parse_itinerary(it, origin, destination, route_name)
            for it in itineraries
        ]
        results = [r for r in results if r is not None]
        results.sort(key=lambda r: r.price)
        return results[:max_results]

    def _parse_itinerary(self, it: dict, origin: str, destination: str,
                         route_name: str) -> Optional[FlightResult]:
        """Skyscanner itinerář → FlightResult (sdílené s flightlabs)."""
        return parse_itinerary(it, origin, destination, route_name, self.name)
