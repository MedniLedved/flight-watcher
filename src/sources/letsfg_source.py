"""LetsFG zdroj (vrstva 1 – reálné ceny, 400+ aerolinky).

Dokumentace: https://letsfg.co / https://github.com/LetsFG/LetsFG
Instalace:   pip install letsfg
Autentizace: volitelný API klíč; bez klíče = free local search (výchozí).

Poznámky k integraci:
- LetsFG API vrací objekt s .cheapest a volitelně iterovatelné výsledky.
  Wrapper zkouší iterovat všechny výsledky, fallbackuje na .cheapest.
- Cena je dostupná ze search() bez unlock() – unlock() je pro rezervaci.
- Roundtrip: LetsFG search je one-way; při return_date se vyhledá outbound
  a výsledek se doplní return info (jako TracKer u jiných zdrojů, kde server
  vrací jen jednosměrné nabídky).
- Pokud vrátí ceny v jiné měně než EUR, je potřeba dopsat FX konverzi
  (zatím se předpokládá EUR ze search result – viz _extract_price).
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from . import FlightResult

logger = logging.getLogger(__name__)

_MAX_PER_COMBO = 5  # kolik výsledků vzít z jednoho search() volání


class LetsFGSource:
    name = "letsfg"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        return_origin: Optional[str] = None,
        return_destination: Optional[str] = None,
        adults: int = 1,
        max_results: int = _MAX_PER_COMBO,
        cabin_class: str = "economy",
        route_name: str = "",
    ) -> list[FlightResult]:
        try:
            from letsfg import LetsFG  # type: ignore[import]
        except ImportError:
            logger.error("LetsFG: balík není nainstalován – spusť: pip install letsfg")
            return []

        dep_str = str(departure_date)
        try:
            bt = LetsFG(api_key=self.api_key) if self.api_key else LetsFG()
            results_obj = bt.search(origin, destination, dep_str)
        except Exception as exc:
            logger.error("LetsFG search %s→%s %s: %s", origin, destination, dep_str, exc)
            return []

        ret_o = return_origin or destination
        ret_d = return_destination or origin

        raw_flights = self._unwrap_results(results_obj, max_results)
        results: list[FlightResult] = []
        for flight in raw_flights:
            try:
                fr = self._to_flight_result(
                    flight, origin, destination,
                    departure_date, return_date, ret_o, ret_d, route_name,
                )
                if fr is not None:
                    results.append(fr)
            except Exception as exc:
                logger.debug("LetsFG parse chyba (%s→%s): %s", origin, destination, exc)

        results.sort(key=lambda r: r.price)
        return results

    # ------------------------------------------------------------------
    def _unwrap_results(self, results_obj, max_results: int) -> list:
        """Vrátí list letů z výsledkového objektu LetsFG.

        LetsFG může vrátit iterovatelný objekt nebo objekt s .cheapest.
        """
        # 1) Pokus o iteraci (všechny výsledky)
        try:
            flights = list(results_obj)[:max_results]
            if flights:
                return flights
        except TypeError:
            pass

        # 2) Fallback: atribut .cheapest
        cheapest = getattr(results_obj, "cheapest", None)
        if cheapest is not None:
            return [cheapest]

        # 3) Fallback: objekt je sám let
        price = self._extract_price(results_obj)
        if price is not None:
            return [results_obj]

        logger.warning("LetsFG: nelze extrahovat lety z výsledku typu %s", type(results_obj))
        return []

    def _to_flight_result(
        self, flight, origin, destination,
        departure_date, return_date, return_origin, return_destination, route_name,
    ) -> Optional[FlightResult]:
        price = self._extract_price(flight)
        if price is None:
            return None
        return FlightResult(
            price=price,
            currency="EUR",
            origin=origin,
            destination=destination,
            return_origin=return_origin,
            return_destination=return_destination,
            depart_date=departure_date,
            return_date=return_date,
            airlines=self._extract_airlines(flight),
            source=self.name,
            deep_link=self._extract_link(flight),
            route_name=route_name,
        )

    @staticmethod
    def _extract_price(flight) -> Optional[float]:
        for attr in ("price", "total_price", "amount", "fare", "cost", "total"):
            val = getattr(flight, attr, None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass

        # Pokus parsovat z .summary() nebo str()
        try:
            text = (
                flight.summary()
                if callable(getattr(flight, "summary", None))
                else str(flight)
            )
            m = re.search(r"(\d[\d\s,]*\.?\d*)\s*(?:EUR|€)", text, re.IGNORECASE)
            if m:
                return float(m.group(1).replace(",", "").replace(" ", ""))
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_airlines(flight) -> list[str]:
        for attr in ("airlines", "carriers", "airline", "carrier", "operated_by"):
            val = getattr(flight, attr, None)
            if val:
                if isinstance(val, (list, tuple)):
                    return [str(a) for a in val if a]
                if isinstance(val, str):
                    return [val]
        return []

    @staticmethod
    def _extract_link(flight) -> str:
        for attr in ("link", "url", "booking_url", "deep_link", "book_url"):
            val = getattr(flight, attr, None)
            if val and isinstance(val, str) and val.startswith("http"):
                return val
        return ""
