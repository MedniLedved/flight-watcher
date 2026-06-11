"""SerpAPI – Google Flights přes SerpAPI (vrstva 1).

Endpointy:
* GET https://serpapi.com/search.json?engine=google_flights

⚠️ FREE TIER: 100 vyhledávání / MĚSÍC. Chování kopíruje Sky Scrapper:
  - kvóta se čte z hlaviček odpovědi (X-RateLimit-*),
  - při vyčerpání se zdroj auto-vypne do konce periody,
  - RATE_LIMIT_COMBINATIONS["serpapi"] je nastaven nízko.

Autentizace: parametr `api_key` (env SERPAPI_KEY).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from . import FlightResult
from .google_flights import google_flights_url

logger = logging.getLogger(__name__)

_BASE_URL = "https://serpapi.com/search.json"
_REQUEST_DELAY = 1.0


class SerpApiSource:
    name = "serpapi"

    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.request_count = 0
        self.quota_remaining: Optional[int] = None
        self.quota_limit: Optional[int] = None
        self.quota_reset_at: Optional[datetime] = None
        self.quota_exhausted = False

    def _note_quota(self, resp: requests.Response) -> None:
        h = resp.headers
        for rem_hdr in ("X-RateLimit-Remaining", "x-ratelimit-requests-remaining"):
            rem = h.get(rem_hdr)
            if rem is not None:
                try:
                    self.quota_remaining = int(rem)
                except ValueError:
                    pass
                break
        for lim_hdr in ("X-RateLimit-Limit", "x-ratelimit-requests-limit"):
            lim = h.get(lim_hdr)
            if lim is not None:
                try:
                    self.quota_limit = int(lim)
                except ValueError:
                    pass
                break
        for rst_hdr in ("X-RateLimit-Reset", "x-ratelimit-requests-reset"):
            rst = h.get(rst_hdr)
            if rst is not None:
                try:
                    # může být epoch timestamp nebo sekundy do resetu
                    val = float(rst)
                    if val > 1e9:
                        self.quota_reset_at = datetime.fromtimestamp(val)
                    else:
                        self.quota_reset_at = datetime.now() + timedelta(seconds=val)
                except ValueError:
                    pass
                break

    def _get(self, params: dict) -> requests.Response:
        resp = self.session.get(_BASE_URL, params=params, timeout=40)
        self.request_count += 1
        self._note_quota(resp)
        if resp.status_code == 429 or (
            self.quota_remaining is not None and self.quota_remaining <= 0
        ):
            self.quota_exhausted = True
        time.sleep(_REQUEST_DELAY)
        resp.raise_for_status()
        return resp

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
        """Vyhledá lety přes SerpAPI Google Flights engine.

        Open-jaw (různý return_origin) se neposílá jako multi-city – vrací
        se jen outbound leg a return_origin/destination se doplní ze vstupů,
        stejně jako u Sky Scrapper. Pro open-jaw pokrytí zůstává primárně
        Duffel/GoogleFlights.
        """
        flight_type = "1" if return_date else "2"  # 1=roundtrip, 2=oneway
        cabin_map = {
            "economy": "1", "premium_economy": "2",
            "business": "3", "first": "4",
        }
        params: dict = {
            "engine": "google_flights",
            "departure_id": origin.upper(),
            "arrival_id": destination.upper(),
            "outbound_date": departure_date.isoformat(),
            "currency": "EUR",
            "hl": "en",
            "type": flight_type,
            "adults": adults,
            "travel_class": cabin_map.get(cabin_class, "1"),
            "api_key": self.api_key,
        }
        if return_date:
            params["return_date"] = return_date.isoformat()

        try:
            resp = self._get(params)
        except requests.RequestException as exc:
            logger.error("SerpAPI %s→%s chyba: %s", origin, destination, exc)
            raise

        payload = resp.json()

        # Zkontroluj chybu vracenou v těle (SerpAPI vrací HTTP 200 i pro chyby).
        if "error" in payload:
            logger.error("SerpAPI %s→%s chyba v odpovědi: %s",
                         origin, destination, payload["error"])
            return []

        results: list[FlightResult] = []
        for section in ("best_flights", "other_flights"):
            for itinerary in payload.get(section, []):
                r = self._parse_itinerary(
                    itinerary, origin, destination,
                    return_origin or destination,
                    return_destination or origin,
                    return_date, route_name,
                )
                if r is not None:
                    results.append(r)

        results.sort(key=lambda r: r.price)
        return results[:max_results]

    def _parse_itinerary(
        self,
        it: dict,
        origin: str,
        destination: str,
        ret_origin: str,
        ret_destination: str,
        return_date: Optional[date],
        route_name: str,
    ) -> Optional[FlightResult]:
        price = it.get("price")
        if price is None:
            return None
        try:
            price = float(price)
        except (ValueError, TypeError):
            return None

        flights = it.get("flights", [])
        if not flights:
            return None

        # Odlet = první flight segment, přílet = poslední
        first_seg = flights[0]
        last_seg = flights[-1]

        dep_airport = first_seg.get("departure_airport", {})
        arr_airport = last_seg.get("arrival_airport", {})

        o_code = dep_airport.get("id") or origin
        d_code = arr_airport.get("id") or destination

        depart_dt = self._parse_dt(dep_airport.get("time"))
        return_dt: Optional[date] = return_date  # SerpAPI roundtrip vrací jen outbound

        airlines: list[str] = []
        seen: set[str] = set()
        for seg in flights:
            # Preferuj IATA kód z pole "airline" pokud je to 2 znaky.
            carrier = seg.get("airline", "")
            if carrier and len(carrier) <= 3:
                if carrier not in seen:
                    seen.add(carrier)
                    airlines.append(carrier)
            elif carrier:
                # Zkus vytáhnout kód z čísla letu (např. "LH 714" → "LH")
                parts = carrier.split()
                if parts and len(parts[0]) <= 3 and parts[0].isalpha():
                    key = parts[0]
                    if key not in seen:
                        seen.add(key)
                        airlines.append(key)

        # Fallback na flight_number prefix ("LH714" → "LH")
        if not airlines:
            for seg in flights:
                fn = seg.get("flight_number", "")
                if fn:
                    prefix = "".join(c for c in fn if c.isalpha())[:2]
                    if prefix and prefix not in seen:
                        seen.add(prefix)
                        airlines.append(prefix)

        return FlightResult(
            price=price,
            currency="EUR",
            origin=o_code,
            destination=d_code,
            return_origin=ret_origin if return_date else "",
            return_destination=ret_destination if return_date else "",
            depart_date=depart_dt,
            return_date=return_dt,
            airlines=airlines,
            source=self.name,
            deep_link=google_flights_url(
                o_code, d_code, depart_dt, return_dt,
                ret_origin if return_date else "",
                ret_destination if return_date else "",
            ),
            route_name=route_name,
        )

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        # Formát: "2026-09-05 10:30" nebo "2026-09-05"
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:16], fmt).date()
            except ValueError:
                continue
        return None
