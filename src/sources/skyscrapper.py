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
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from . import FlightResult

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
        self.session = session or requests.Session()
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
        time.sleep(_REQUEST_DELAY)
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

        payload = resp.json().get("data", {})
        itineraries = payload.get("itineraries", [])
        results = [
            self._parse_itinerary(it, origin, destination, route_name)
            for it in itineraries
        ]
        results = [r for r in results if r is not None]
        results.sort(key=lambda r: r.price)
        return results[:max_results]

    def _parse_itinerary(self, it: dict, origin: str, destination: str,
                         route_name: str) -> Optional[FlightResult]:
        price_obj = it.get("price", {})
        raw = price_obj.get("raw")
        if raw is None:
            return None
        try:
            price = float(raw)
        except (ValueError, TypeError):
            return None

        legs = it.get("legs", [])
        out_leg = legs[0] if legs else {}
        in_leg = legs[1] if len(legs) > 1 else {}

        airlines: set[str] = set()
        for leg in (out_leg, in_leg):
            carriers = leg.get("carriers", {}).get("marketing", []) if leg else []
            for c in carriers:
                code = c.get("alternateId") or c.get("name")
                if code:
                    airlines.add(code)

        return FlightResult(
            price=price,
            currency="EUR",
            origin=self._leg_iata(out_leg, "origin", origin),
            destination=self._leg_iata(out_leg, "destination", destination),
            return_origin=self._leg_iata(in_leg, "origin", "") if in_leg else "",
            return_destination=self._leg_iata(in_leg, "destination", "") if in_leg else "",
            depart_date=self._leg_date(out_leg),
            return_date=self._leg_date(in_leg) if in_leg else None,
            airlines=sorted(airlines),
            source=self.name,
            deep_link="",  # Sky Scrapper přímý nákupní odkaz nevrací
            route_name=route_name,
        )

    @staticmethod
    def _leg_iata(leg: dict, key: str, fallback: str) -> str:
        node = leg.get(key, {}) if leg else {}
        if isinstance(node, dict):
            return node.get("displayCode") or node.get("id") or fallback
        return fallback

    @staticmethod
    def _leg_date(leg: dict) -> Optional[date]:
        value = leg.get("departure", "") if leg else ""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
