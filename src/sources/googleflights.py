"""Google Flights (vrstva 1 – real-time, scraping přes knihovnu fast-flights).

Primární bezplatný zdroj po vyřazení Duffelu (live účet není zdarma)
a Amadeu (sunset 17. 7. 2026, test režim syntetický):

- žádný API klíč, žádná kvóta ani pravidelné náklady,
- ceny přímo z Google Flights → alert, deep link i ruční ověření ukazují
  TOTÉŽ (přesně ta konzistence, kvůli které se zdroj zavedl),
- `currency="EUR"` si vynutíme v requestu; cizí měna (kdyby Google parametr
  ignoroval) se převádí kurzem ECB, bez kurzu se nabídka zahodí.

Je to scraping: křehčí než API (Google může změnit HTML – pak spadne parsing
v knihovně fast-flights, ne celý scan) a je třeba být šetrný – sekvenční
volání s prodlevou, malý limit kombinací (viz RATE_LIMIT_COMBINATIONS).

Režim stahování (env GOOGLEFLIGHTS_FETCH_MODE):
- "common" (výchozí) – přímý GET s browser impersonation (primp),
- "fallback" – jako common, při neúspěchu zkusí externí playwright službu
  (try.playwright.tech) – posílají se jí jen letiště a termíny, žádné secrets.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from typing import Callable, Optional

from . import FlightResult
from .fx import FxRates
from .google_flights import google_flights_url

logger = logging.getLogger(__name__)

_REQUEST_DELAY = 2.0  # scraping → šetrně, sekvenčně (viz scanner)

# Detekce měny z textové ceny ("€533", "$1234", "CHF 920"…). Delší tokeny
# musí být před kratšími ("US$" před "$").
_CURRENCY_TOKENS: list[tuple[str, str]] = [
    ("US$", "USD"), ("CA$", "CAD"), ("A$", "AUD"), ("NZ$", "NZD"),
    ("HK$", "HKD"), ("CN¥", "CNY"), ("€", "EUR"), ("£", "GBP"),
    ("CHF", "CHF"), ("Kč", "CZK"), ("zł", "PLN"), ("¥", "JPY"),
    ("$", "USD"),
]
_CODE_RE = re.compile(r"\b([A-Z]{3})\b")


class GoogleFlightsSource:
    name = "googleflights"

    def __init__(self, fetch_mode: Optional[str] = None,
                 fx: Optional[FxRates] = None,
                 fetcher: Optional[Callable] = None):
        # "or" řetěz: nenastavená Actions variable přijde jako PRÁZDNÝ string,
        # ne None – nesmí protéct jako neplatný mode do fast-flights.
        self.fetch_mode = (fetch_mode
                           or os.getenv("GOOGLEFLIGHTS_FETCH_MODE") or "common")
        self.fx = fx or FxRates()
        # Testovací šev: fetcher(legs, trip, adults) → list objektů s atributy
        # price/name (viz fast_flights.schema.Flight). None = reálný scraping.
        self._fetcher = fetcher

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
        """Vyhledá nabídky na Google Flights pro daný termín.

        Open-jaw (odlišný návratový pár) jde přes multi-city vyhledávání –
        zobrazená cena je za celý itinerář, stejně jako u roundtripu.
        """
        openjaw = bool(return_date and return_origin and return_destination
                       and (return_origin != destination
                            or return_destination != origin))
        legs = [(origin, destination, departure_date)]
        if return_date and openjaw:
            trip = "multi-city"
            legs.append((return_origin, return_destination, return_date))
        elif return_date:
            trip = "round-trip"
            legs.append((destination, origin, return_date))
        else:
            trip = "one-way"

        try:
            flights = self._fetch(legs, trip, adults)
        finally:
            time.sleep(_REQUEST_DELAY)

        r_o = (return_origin or destination) if return_date else ""
        r_d = (return_destination or origin) if return_date else ""
        results = []
        unknown_currencies: set[str] = set()
        for fl in flights:
            result = self._to_result(
                fl, origin, destination, r_o, r_d,
                departure_date, return_date, route_name, unknown_currencies,
            )
            if result is not None:
                results.append(result)
        if unknown_currencies:
            logger.warning(
                "Google Flights %s→%s: nabídky s neznámou/nepřevoditelnou "
                "měnou (%s) přeskočeny.",
                origin, destination, ", ".join(sorted(unknown_currencies)),
            )
        results.sort(key=lambda r: r.price)
        return results[:max_results]

    # -- stahování ---------------------------------------------------------
    def _fetch(self, legs: list[tuple[str, str, date]], trip: str,
               adults: int) -> list:
        if self._fetcher is not None:
            return self._fetcher(legs, trip, adults)
        # Líný import – chybějící závislost nesmí shodit zbytek scanneru
        # (stejný vzor jako feedparser u RSS zdrojů).
        from fast_flights import (FlightData, Passengers, create_filter,
                                  get_flights_from_filter)
        filter_ = create_filter(
            flight_data=[
                FlightData(date=d.isoformat(), from_airport=o, to_airport=dst)
                for o, dst, d in legs
            ],
            trip=trip,
            passengers=Passengers(adults=adults),
            seat="economy",
        )
        result = get_flights_from_filter(
            filter_, currency="EUR", mode=self.fetch_mode
        )
        return list(result.flights)

    # -- mapování na FlightResult -------------------------------------------
    def _to_result(self, fl, origin: str, destination: str,
                   return_origin: str, return_destination: str,
                   depart_date: date, return_date: Optional[date],
                   route_name: str,
                   unknown_currencies: set[str]) -> Optional[FlightResult]:
        price, currency = self._parse_price(getattr(fl, "price", "") or "")
        if price is None or price <= 0:
            return None
        if not currency:
            unknown_currencies.add(repr(getattr(fl, "price", "")))
            return None
        if currency != "EUR":
            eur = self.fx.to_eur(price, currency)
            if eur is None:
                unknown_currencies.add(currency)
                return None
            price = eur
        airline = " ".join((getattr(fl, "name", "") or "").split())
        # Letiště i termíny bereme z dotazu – výsledková stránka platí přesně
        # pro ně (Google v listingu konkrétní letiště/termíny neopakuje).
        return FlightResult(
            price=price,
            currency="EUR",
            origin=origin,
            destination=destination,
            return_origin=return_origin,
            return_destination=return_destination,
            depart_date=depart_date,
            return_date=return_date,
            airlines=[airline] if airline else [],
            source=self.name,
            deep_link=google_flights_url(
                origin, destination, depart_date, return_date,
                return_origin, return_destination,
            ),
            route_name=route_name,
        )

    @staticmethod
    def _parse_price(raw: str) -> tuple[Optional[float], str]:
        """Z textové ceny ("€533", "$1234", "CHF 920") vrátí (hodnota, měna).

        Měna "" = nerozpoznaná → volající nabídku přeskočí (nikdy nehádat).
        fast-flights odstraňuje čárky (oddělovače tisíců) už při parsování.
        """
        text = raw.strip()
        if not text:
            return None, ""
        currency = ""
        for token, code in _CURRENCY_TOKENS:
            if token in text:
                currency = code
                break
        else:
            m = _CODE_RE.search(text)
            if m:
                currency = m.group(1)
        digits = re.sub(r"[^\d.]", "", text)
        try:
            value = float(digits)
        except ValueError:
            return None, ""
        return value, currency
