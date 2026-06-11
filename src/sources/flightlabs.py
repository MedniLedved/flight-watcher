"""FlightLabs (goflightlabs.com) – Flight Prices API (vrstva 1).

Endpoint: GET https://app.goflightlabs.com/retrieve-cheapest-flights
Docs:     https://www.goflightlabs.com/flight-prices
Auth:     query param `access_key=FLIGHTLABS_KEY`

Trial:    50 requestů celkem. Zdroj slouží k bootstrap statistik (dny v
          týdnu, letiště) a po vyčerpání trialu se vypne v agent.json.
          Kvóta se trackuje v price_history._meta["flightlabs_requests"].

Pozn.: FlightLabs je re-seller Kiwi/Skyscanner dat a neprovozuje vlastní
scraping. Response je rychlá (~1–2 s). Ceny jsou indikativní (cache), ne
live booking — vhodné pro statistiky, ne jako primární alert zdroj.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests

from . import FlightResult

logger = logging.getLogger(__name__)

BASE_URL = "https://app.goflightlabs.com/retrieve-cheapest-flights"


class FlightLabsSource:
    name = "flightlabs"

    def __init__(self, access_key: str, session: Optional[requests.Session] = None):
        self.access_key = access_key
        self.session = session or requests.Session()
        self.request_count = 0

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        return_origin: Optional[str] = None,
        return_destination: Optional[str] = None,
        adults: int = 1,
        max_results: int = 5,
        cabin_class: str = "economy",
        route_name: str = "",
    ) -> list[FlightResult]:
        params: dict = {
            "access_key": self.access_key,
            "origin": origin,
            "destination": destination,
            "departureDate": str(departure_date),
            "adults": adults,
            "currency": "EUR",
        }
        if return_date:
            params["returnDate"] = str(return_date)

        try:
            resp = self.session.get(BASE_URL, params=params, timeout=30)
            self.request_count += 1
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("FlightLabs %s→%s %s: %s", origin, destination, departure_date, exc)
            raise

        payload = resp.json()

        # Různé FlightLabs response shapes – normalizujeme.
        if payload.get("success") is False:
            logger.error("FlightLabs API: success=false %s→%s", origin, destination)
            return []
        if "error" in payload:
            logger.error("FlightLabs API chyba %s→%s: %s", origin, destination, payload["error"])
            return []

        items = payload.get("data") or payload.get("flights") or []
        if isinstance(items, dict):
            # Někdy data = {"cheapest": [...], ...}
            items = items.get("cheapest") or items.get("results") or []

        results: list[FlightResult] = []
        for item in items[:max_results]:
            try:
                fr = self._parse_item(
                    item, origin, destination,
                    departure_date, return_date,
                    return_origin or destination,
                    return_destination or origin,
                    route_name,
                )
                if fr is not None:
                    results.append(fr)
            except Exception as exc:
                logger.debug("FlightLabs parse %s→%s: %s", origin, destination, exc)

        results.sort(key=lambda r: r.price)
        return results

    # ------------------------------------------------------------------
    def _parse_item(
        self, item: dict,
        origin: str, destination: str,
        departure_date: date, return_date: Optional[date],
        return_origin: str, return_destination: str,
        route_name: str,
    ) -> Optional[FlightResult]:
        price = self._extract_price(item)
        if not price:
            return None

        airlines = self._extract_airlines(item)
        link = self._extract_link(item)
        dep_date = self._extract_date(item, "departureDate", "departure") or departure_date
        ret_date = self._extract_date(item, "returnDate", "return") or return_date

        return FlightResult(
            price=price,
            currency="EUR",
            origin=origin,
            destination=destination,
            return_origin=return_origin,
            return_destination=return_destination,
            depart_date=dep_date,
            return_date=ret_date,
            airlines=airlines,
            source=self.name,
            deep_link=link,
            route_name=route_name,
        )

    @staticmethod
    def _extract_price(item: dict) -> Optional[float]:
        # Různé FlightLabs / partner API shapes
        for key in ("price", "total", "amount", "fare", "totalPrice", "total_price"):
            val = item.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        # price jako nested dict {"amount": ..., "currency": ...}
        price_obj = item.get("price") or item.get("priceBreakdown") or {}
        if isinstance(price_obj, dict):
            for key in ("amount", "total", "value", "grandTotal"):
                val = price_obj.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass
        return None

    @staticmethod
    def _extract_airlines(item: dict) -> list[str]:
        # airline jako string IATA nebo nested objekt
        for key in ("airlines", "carriers"):
            val = item.get(key)
            if isinstance(val, list):
                return [str(a) if not isinstance(a, dict) else (a.get("iataCode") or a.get("iata") or str(a)) for a in val if a]
        for key in ("airline", "carrier"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return [val]
            if isinstance(val, dict):
                code = val.get("iataCode") or val.get("iata") or val.get("code")
                if code:
                    return [code]
        # Z legs
        legs = item.get("legs") or item.get("segments") or []
        codes = []
        for leg in legs:
            if isinstance(leg, dict):
                al = leg.get("airline") or leg.get("carrier") or {}
                code = (al.get("iataCode") if isinstance(al, dict) else al) or leg.get("airlineCode") or leg.get("carrierCode")
                if code and code not in codes:
                    codes.append(str(code))
        return codes

    @staticmethod
    def _extract_link(item: dict) -> str:
        for key in ("deepLink", "deep_link", "link", "bookingUrl", "booking_url", "url"):
            val = item.get(key)
            if val and isinstance(val, str) and val.startswith("http"):
                return val
        return ""

    @staticmethod
    def _extract_date(item: dict, *keys: str) -> Optional[date]:
        from datetime import datetime
        for key in keys:
            val = item.get(key)
            if not val:
                continue
            if isinstance(val, dict):
                val = val.get("at") or val.get("time") or val.get("date") or ""
            if isinstance(val, str) and val:
                # Parse ISO date or datetime — always extract the date portion (first 10 chars).
                try:
                    return datetime.strptime(val[:10], "%Y-%m-%d").date()
                except ValueError:
                    pass
        return None
