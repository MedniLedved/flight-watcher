"""Amadeus Self-Service API (vrstva 1 – real-time).

Dokumentace: https://developers.amadeus.com
OAuth2 token: POST /v1/security/oauth2/token (client_credentials).
Flight Offers Search: GET /v2/shopping/flight-offers.

TODO(sunset): Amadeus Self-Service API bude ukončeno 17. července 2026.
Po tomto datu je nutné migrovat na jiný zdroj (Kiwi / Travelpayouts) nebo
na placený Amadeus Enterprise. Viz README, sekce Troubleshooting.

Open-jaw je nativně podporován – pro každý leg lze zadat vlastní
originLocationCode / destinationLocationCode.

Free tier: 2 000 requestů/měsíc → cachujeme výsledky (stejný dotaz se
neopakuje do 6 hodin) a počítáme spotřebu.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult

logger = logging.getLogger(__name__)

TEST_HOST = "https://test.api.amadeus.com"
PROD_HOST = "https://api.amadeus.com"

_CACHE_TTL_SECONDS = 6 * 3600  # 6 hodin
_REQUEST_DELAY = 0.5


class AmadeusSource:
    name = "amadeus"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        env: str = "test",
        session: Optional[requests.Session] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.host = PROD_HOST if env == "production" else TEST_HOST
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        # Jednoduchá in-memory cache: klíč -> (timestamp, results)
        self._cache: dict[str, tuple[float, list[FlightResult]]] = {}
        self.request_count = 0  # spotřeba v rámci tohoto běhu

    # -- OAuth2 -----------------------------------------------------------
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        url = f"{self.host}/v1/security/oauth2/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = self.session.post(url, data=data, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        # Bezpečnostní rezerva 60 s.
        self._token_expiry = time.time() + payload.get("expires_in", 1799) - 60
        return self._token

    # -- Search -----------------------------------------------------------
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
        route_name: str = "",
    ) -> list[FlightResult]:
        """Vyhledá flight offers. Pro open-jaw zadej return_origin/return_destination.

        Pozn.: Flight Offers Search v jednoduché GET podobě podporuje
        roundtrip se shodným origin/destination. Pro skutečný open-jaw
        (odlišný návratový pár) je nutné použít POST variantu s
        originDestinations – zde implementováno přes POST když se páry liší.
        """
        cache_key = (
            f"{origin}-{destination}-{return_origin}-{return_destination}-"
            f"{departure_date}-{return_date}"
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            logger.debug("Amadeus cache hit: %s", cache_key)
            return cached[1]

        is_openjaw = bool(
            return_date
            and (
                (return_origin and return_origin != destination)
                or (return_destination and return_destination != origin)
            )
        )

        try:
            if is_openjaw:
                results = self._search_post(
                    origin, destination, departure_date, return_date,
                    return_origin or destination, return_destination or origin,
                    adults, max_results, route_name,
                )
            else:
                results = self._search_get(
                    origin, destination, departure_date, return_date,
                    adults, max_results, route_name,
                )
        finally:
            time.sleep(_REQUEST_DELAY)

        self._cache[cache_key] = (time.time(), results)
        return results

    def _search_get(self, origin, destination, departure_date, return_date,
                    adults, max_results, route_name) -> list[FlightResult]:
        url = f"{self.host}/v2/shopping/flight-offers"
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure_date.isoformat(),
            "adults": adults,
            "currencyCode": "EUR",
            "max": max_results,
        }
        if return_date:
            params["returnDate"] = return_date.isoformat()
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        resp = self.session.get(url, params=params, headers=headers, timeout=30)
        self.request_count += 1
        resp.raise_for_status()
        return self._parse_response(resp.json(), origin, destination, route_name)

    def _search_post(self, origin, destination, departure_date, return_date,
                     return_origin, return_destination, adults, max_results,
                     route_name) -> list[FlightResult]:
        url = f"{self.host}/v2/shopping/flight-offers"
        body = {
            "currencyCode": "EUR",
            "originDestinations": [
                {
                    "id": "1",
                    "originLocationCode": origin,
                    "destinationLocationCode": destination,
                    "departureDateTimeRange": {"date": departure_date.isoformat()},
                },
                {
                    "id": "2",
                    "originLocationCode": return_origin,
                    "destinationLocationCode": return_destination,
                    "departureDateTimeRange": {"date": return_date.isoformat()},
                },
            ],
            "travelers": [{"id": "1", "travelerType": "ADULT"}],
            "sources": ["GDS"],
            "searchCriteria": {"maxFlightOffers": max_results},
        }
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }
        resp = self.session.post(url, json=body, headers=headers, timeout=30)
        self.request_count += 1
        resp.raise_for_status()
        return self._parse_response(resp.json(), origin, destination, route_name)

    def _parse_response(self, payload: dict, origin: str, destination: str,
                        route_name: str) -> list[FlightResult]:
        results: list[FlightResult] = []
        for offer in payload.get("data", []):
            try:
                price = float(offer["price"]["total"])
            except (KeyError, ValueError, TypeError):
                continue
            itineraries = offer.get("itineraries", [])
            airlines: set[str] = set()
            for it in itineraries:
                for seg in it.get("segments", []):
                    if seg.get("carrierCode"):
                        airlines.add(seg["carrierCode"])

            out_segs = itineraries[0].get("segments", []) if itineraries else []
            in_segs = itineraries[1].get("segments", []) if len(itineraries) > 1 else []

            depart_dt = self._seg_date(out_segs[0]) if out_segs else None
            return_dt = self._seg_date(in_segs[0]) if in_segs else None

            results.append(FlightResult(
                price=price,
                currency=offer.get("price", {}).get("currency", "EUR"),
                origin=out_segs[0]["departure"]["iataCode"] if out_segs else origin,
                destination=out_segs[-1]["arrival"]["iataCode"] if out_segs else destination,
                return_origin=in_segs[0]["departure"]["iataCode"] if in_segs else "",
                return_destination=in_segs[-1]["arrival"]["iataCode"] if in_segs else "",
                depart_date=depart_dt,
                return_date=return_dt,
                airlines=sorted(airlines),
                source=self.name,
                deep_link="",  # Amadeus deep link vyžaduje další volání; ponecháno prázdné
                route_name=route_name,
            ))
        results.sort(key=lambda r: r.price)
        return results

    @staticmethod
    def _seg_date(seg: dict) -> Optional[date]:
        try:
            return datetime.fromisoformat(seg["departure"]["at"]).date()
        except (KeyError, ValueError, TypeError):
            return None
