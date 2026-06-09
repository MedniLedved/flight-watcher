"""Kiwi Tequila API (vrstva 1 – real-time).

Dokumentace: https://tequila.kiwi.com/portal/docs/tequila_api
Endpoint: https://api.tequila.kiwi.com/v2/search
Autentizace: header `apikey: KIWI_API_KEY`.

Podporuje open-jaw přes `fly_from` / `fly_to` jako čárkou oddělené IATA kódy
nebo `city:XXX` prefix pro celé město.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tequila.kiwi.com/v2/search"
_REQUEST_DELAY = 0.7  # throttling – Kiwi má ~100 req/min


def _parse_kiwi_date(value: str) -> Optional[date]:
    """Kiwi vrací lokal departure jako ISO string nebo 'dd/mm/yyyy'."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            continue
    # Fallback – jen datová část ISO
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None


class KiwiSource:
    name = "kiwi"

    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.session = session or requests.Session()

    def search(
        self,
        fly_from: list[str],
        fly_to: list[str],
        date_from: date,
        date_to: date,
        return_from: Optional[date] = None,
        return_to: Optional[date] = None,
        nights_from: int = 12,
        nights_to: int = 25,
        flight_type: str = "round",
        limit: int = 10,
        route_name: str = "",
    ) -> list[FlightResult]:
        """Vyhledá lety. fly_from/fly_to jsou seznamy IATA kódů (open-jaw OK).

        Vrací seznam FlightResult, seřazený dle ceny (vzestupně).
        """
        params = {
            "fly_from": ",".join(fly_from),
            "fly_to": ",".join(fly_to),
            "date_from": date_from.strftime("%d/%m/%Y"),
            "date_to": date_to.strftime("%d/%m/%Y"),
            "flight_type": flight_type,
            "curr": "EUR",
            "limit": limit,
            "sort": "price",
            "adults": 1,
        }
        if flight_type == "round":
            params["nights_in_dst_from"] = nights_from
            params["nights_in_dst_to"] = nights_to
            if return_from:
                params["return_from"] = return_from.strftime("%d/%m/%Y")
            if return_to:
                params["return_to"] = return_to.strftime("%d/%m/%Y")

        headers = {"apikey": self.api_key, "accept": "application/json"}

        try:
            resp = self.session.get(
                BASE_URL, params=params, headers=headers, timeout=30
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Kiwi API chyba pro %s→%s: %s", fly_from, fly_to, exc)
            raise
        finally:
            time.sleep(_REQUEST_DELAY)  # throttling

        data = resp.json()
        results: list[FlightResult] = []
        for item in data.get("data", []):
            results.append(self._parse_item(item, route_name))
        return results

    def _parse_item(self, item: dict, route_name: str) -> FlightResult:
        route = item.get("route", [])
        airlines = sorted({seg.get("airline", "") for seg in route if seg.get("airline")})

        # U round/open-jaw určíme outbound a inbound dle 'return' příznaku.
        outbound = [s for s in route if s.get("return", 0) == 0]
        inbound = [s for s in route if s.get("return", 0) == 1]

        origin = item.get("flyFrom", "")
        destination = outbound[-1].get("flyTo", item.get("flyTo", "")) if outbound else item.get("flyTo", "")
        return_origin = inbound[0].get("flyFrom", "") if inbound else ""
        return_destination = inbound[-1].get("flyTo", "") if inbound else ""

        return FlightResult(
            price=float(item.get("price", 0)),
            currency="EUR",
            origin=origin,
            destination=destination,
            return_origin=return_origin,
            return_destination=return_destination,
            depart_date=_parse_kiwi_date(item.get("local_departure", "")),
            return_date=(
                _parse_kiwi_date(inbound[0].get("local_departure", "")) if inbound else None
            ),
            airlines=airlines,
            source=self.name,
            deep_link=item.get("deep_link", ""),
            route_name=route_name,
        )
