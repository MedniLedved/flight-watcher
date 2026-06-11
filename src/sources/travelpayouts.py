"""Travelpayouts Data API (vrstva 1 – cache, záloha a trendy).

Dokumentace: https://support.travelpayouts.com/hc/en-us/categories/200358578
Endpoint: GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates
Autentizace: header `X-Access-Token: TRAVELPAYOUTS_TOKEN`.

Data jsou z cache (až 7 dní stará) – používej pro detekci trendů a jako
zálohu, NE jako primární zdroj aktuální ceny. Bez limitu na počet requestů.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult

logger = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class TravelpayoutsSource:
    name = "travelpayouts"

    def __init__(self, token: str, session: Optional[requests.Session] = None):
        self.token = token
        self.session = session or requests.Session()
        self.request_count = 0

    def search(
        self,
        origin: str,
        destination: str,
        departure_at: str,        # "YYYY-MM" nebo "YYYY-MM-DD"
        return_at: Optional[str] = None,
        limit: int = 10,
        route_name: str = "",
    ) -> list[FlightResult]:
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_at,
            "currency": "eur",
            "sorting": "price",
            "limit": limit,
            "unique": "false",
            "one_way": "false" if return_at else "true",
        }
        if return_at:
            params["return_at"] = return_at
        headers = {"X-Access-Token": self.token, "accept": "application/json"}

        try:
            resp = self.session.get(
                BASE_URL, params=params, headers=headers, timeout=30
            )
            resp.raise_for_status()
            self.request_count += 1
        except requests.RequestException as exc:
            logger.error(
                "Travelpayouts chyba %s→%s: %s", origin, destination, exc
            )
            raise

        payload = resp.json()
        results: list[FlightResult] = []
        for item in payload.get("data", []):
            results.append(self._parse_item(item, origin, destination, route_name))
        results.sort(key=lambda r: r.price)
        return results

    def _parse_item(self, item: dict, origin: str, destination: str,
                    route_name: str) -> FlightResult:
        link = item.get("link", "")
        if link and link.startswith("/"):
            link = f"https://www.aviasales.com{link}"
        return FlightResult(
            price=float(item.get("price", 0)),
            currency="EUR",
            origin=item.get("origin", origin),
            destination=item.get("destination", destination),
            return_origin=item.get("destination", destination),
            return_destination=item.get("origin", origin),
            depart_date=self._parse_date(item.get("departure_at")),
            return_date=self._parse_date(item.get("return_at")),
            airlines=[item["airline"]] if item.get("airline") else [],
            source=self.name,
            deep_link=link,
            route_name=route_name,
        )

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
