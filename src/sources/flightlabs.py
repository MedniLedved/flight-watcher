"""FlightLabs (goflightlabs.com) – retrieveFlights (async job-queue API).

Kontrakt zjištěn probem (viz scripts/probe_flightlabs.py, git historie):
* GET https://www.goflightlabs.com/retrieveFlights
  params: access_key, originIATACode, destinationIATACode, date (=odlet),
          volitelně returnDate, adults, currency, cabinClass
* ASYNC: první volání vrátí HTTP 202 {"status":"processing","jobId":...};
  výsledky se získají OPAKOVANÝM voláním STEJNÝCH parametrů (poll), dokud
  nevrátí HTTP 200.
* Tělo 200 je PLOCHÉ pole "legs" – každý prvek je JEDEN směr letu:
    {"price":"1057","currency":"EUR","origin":{"code":"MUC"},
     "destination":{"code":"NRT"},"departure":"2026-09-10T11:25:00",
     "arrival":"...","stopCount":1,"flightNumber":"EY25",
     "marketingCarrier":"Etihad Airways","operatingCarrier":"..."}
  Outbound (origin→dest) a return (dest→origin) jsou SAMOSTATNÉ prvky se
  STEJNOU cenou (cena = celková zpáteční). Páruje se outbound+return →
  jeden roundtrip FlightResult. Nespárovaný leg se zahazuje (nikdy neukládat
  one-way jako zpáteční – ochrana proti pollution).

Starší endpointy (/retrieve-cheapest-flights, /retrieveAirport) byly odstaveny
(404 / 410 Gone). Kvóta: 4000 req/měsíc, rate limit 10 req/10 s. POZOR: async
poll znamená VÍC requestů na kombinaci (submit + N pollů).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult
from .google_flights import google_flights_url
from .http_utils import make_api_session

logger = logging.getLogger(__name__)

RETRIEVE_FLIGHTS_URL = "https://www.goflightlabs.com/retrieveFlights"
_REQUEST_DELAY = 1.1   # rate limit 10 req/10 s → ~1 req/s
_POLL_DELAY = 2.5      # pauza mezi polly (jen když submit dostane max_polls>0)
# Submit defaultně NEPOLLUJE (0): async job není v rámci submitu nikdy hotový
# (ověřeno – dozrává 30–80 s), takže každý poll = zbytečný request + 2,5 s
# čekání. Nedokončené joby sebere collect v dalším běhu. Polling lze zapnout
# (max_polls>0) pro test/diagnostiku, kde chceme chytit už nacachovaný job.
_MAX_POLLS = 0

# Klíče, které tvoří dotaz na API (zbytek pending dictu je metadata).
QUERY_KEYS = ("originIATACode", "destinationIATACode", "date", "returnDate",
              "adults", "currency", "cabinClass")

# IATA kód aerolinky z čísla letu: "EY25"→EY, "LO392"→LO, "U225"→U2, "3U88"→3U.
_FLIGHTNO_RE = re.compile(r"^([A-Z]{2}|[A-Z]\d|\d[A-Z])")


class FlightLabsSource:
    """goflightlabs retrieveFlights – async job-queue API.

    2-fázový provoz (řídí ho scanner):
    * ``submit`` odešle job a krátce pollne; vrátí (results, pending|None).
      Rychlé/nacachované joby se chytí hned, zbytek se vrátí jako *pending*.
    * ``collect`` re-dotáže jeden pending job; vrátí (results, done) – done
      znamená „odeber z pendingu" (přišly výsledky NEBO tvrdá chyba).
    """

    name = "flightlabs"

    def __init__(self, access_key: str, session: Optional[requests.Session] = None,
                 max_polls: int = _MAX_POLLS, poll_delay: float = _POLL_DELAY):
        self.access_key = access_key
        self.session = session or make_api_session()
        self.max_polls = max_polls
        self.poll_delay = poll_delay
        self.request_count = 0

    # -- veřejné rozhraní -------------------------------------------------
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
        """Jednorázové vyhledání (submit + krátký poll), vrací jen výsledky –
        nedokončený job zahodí. Používá diagnostický skript; scanner volá
        ``submit``/``collect`` kvůli 2-fázovému sběru."""
        results, _pending = self.submit(
            origin, destination, departure_date, return_date=return_date,
            adults=adults, cabin_class=cabin_class, route_name=route_name,
        )
        return results[:max_results]

    def submit(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        adults: int = 1,
        cabin_class: str = "economy",
        route_name: str = "",
    ) -> tuple[list[FlightResult], Optional[dict]]:
        """Odešle job a krátce pollne. Vrací (results, pending). Když job
        dokončí v okně → (results, None). Když pořád ‚processing' → ([], pending
        dict) k uložení a sběru v dalším běhu. retrieveFlights je vždy roundtrip
        se shodným origin/destination (open-jaw nepodporuje); bez return_date by
        vrátilo jen nepárovatelné one-way legy, proto ho scanner vždy posílá."""
        params: dict = {
            "originIATACode": origin,
            "destinationIATACode": destination,
            "date": departure_date.isoformat(),
            "adults": adults,
            "currency": "EUR",
            "cabinClass": cabin_class,
        }
        if return_date:
            params["returnDate"] = return_date.isoformat()

        for attempt in range(self.max_polls + 1):
            resp = self._request(params, origin, destination)
            if resp.status_code == 202:
                if attempt < self.max_polls:
                    time.sleep(self.poll_delay)
                    continue
                # Nedokončeno v krátkém okně → ulož jako pending pro collect.
                pending = {**params, "route_name": route_name,
                           "submitted": date.today().isoformat()}
                return [], pending
            results = self._results_from_response(resp, origin, destination,
                                                  route_name)
            return results, None
        return [], None

    def collect(self, pending: dict) -> tuple[list[FlightResult], bool]:
        """Re-dotáže jeden pending job (1 request). Vrací (results, done):
        done=True → odeber z pendingu (200 s výsledky NEBO tvrdá 4xx/5xx chyba);
        done=False → job pořád ‚processing', ponech v pendingu na příště."""
        params = {k: pending[k] for k in QUERY_KEYS if k in pending}
        origin = pending.get("originIATACode", "")
        destination = pending.get("destinationIATACode", "")
        route_name = pending.get("route_name", "")
        try:
            resp = self._request(params, origin, destination)
        except requests.RequestException:
            return [], True  # síťová/tvrdá chyba → zahoď
        if resp.status_code == 202:
            return [], False
        if resp.status_code >= 400:
            logger.warning("FlightLabs collect %s→%s: HTTP %d → zahazuji job",
                           origin, destination, resp.status_code)
            return [], True
        return self._results_from_response(resp, origin, destination, route_name), True

    # -- HTTP --------------------------------------------------------------
    def _request(self, params: dict, origin: str,
                 destination: str) -> requests.Response:
        """Jedno GET volání retrieveFlights (počítá request, drží rate limit)."""
        full = {**params, "access_key": self.access_key}
        try:
            resp = self.session.get(RETRIEVE_FLIGHTS_URL, params=full, timeout=40)
            self.request_count += 1
            time.sleep(_REQUEST_DELAY)
        except requests.RequestException as exc:
            logger.error("FlightLabs %s→%s: %s", origin, destination, exc)
            raise
        if self.request_count <= 3:
            logger.info("FlightLabs DIAG req#%d %s→%s: HTTP %d | %.250s",
                        self.request_count, origin, destination,
                        resp.status_code, resp.text)
        return resp

    def _results_from_response(self, resp: requests.Response, origin: str,
                               destination: str,
                               route_name: str) -> list[FlightResult]:
        """Z 200 odpovědi naparsuje a seřadí výsledky; 4xx/5xx → []."""
        try:
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("FlightLabs %s→%s: %s", origin, destination, exc)
            return []
        payload = resp.json()
        legs = payload if isinstance(payload, list) else (
            payload.get("data") if isinstance(payload, dict) else None
        )
        if not isinstance(legs, list):
            logger.warning("FlightLabs %s→%s: neočekávaný tvar odpovědi (%s)",
                           origin, destination, type(payload).__name__)
            return []
        results = self._parse_legs(legs, origin, destination, route_name)
        results.sort(key=lambda r: r.price)
        return results

    # -- parsování plochých leg párů --------------------------------------
    def _parse_legs(self, legs: list, origin: str, destination: str,
                    route_name: str) -> list[FlightResult]:
        """Spáruje outbound (origin→dest) s následným return (dest→origin) se
        shodnou cenou → roundtrip FlightResult. Nespárovaný leg se zahodí."""
        results: list[FlightResult] = []
        pending_out: Optional[dict] = None
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            o = (leg.get("origin") or {}).get("code")
            d = (leg.get("destination") or {}).get("code")
            if o == origin and d == destination:
                pending_out = leg
            elif o == destination and d == origin and pending_out is not None:
                fr = self._build_roundtrip(pending_out, leg, origin, destination,
                                           route_name)
                if fr is not None:
                    results.append(fr)
                pending_out = None
        return results

    def _build_roundtrip(self, out_leg: dict, in_leg: dict,
                         origin: str, destination: str,
                         route_name: str) -> Optional[FlightResult]:
        price = self._parse_price(out_leg.get("price"))
        if price is None:
            return None
        depart_dt = self._parse_dt(out_leg.get("departure"))
        return_dt = self._parse_dt(in_leg.get("departure"))
        airlines = sorted({
            c for c in (
                self._airline_code(out_leg.get("flightNumber")),
                self._airline_code(in_leg.get("flightNumber")),
            ) if c
        })
        o_code = (out_leg.get("origin") or {}).get("code") or origin
        d_code = (out_leg.get("destination") or {}).get("code") or destination
        return FlightResult(
            price=price,
            currency="EUR",
            origin=o_code,
            destination=d_code,
            return_origin=d_code,
            return_destination=o_code,
            depart_date=depart_dt,
            return_date=return_dt,
            airlines=airlines,
            source=self.name,
            deep_link=google_flights_url(o_code, d_code, depart_dt, return_dt,
                                         d_code, o_code),
            route_name=route_name,
        )

    @staticmethod
    def _parse_price(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

    @staticmethod
    def _airline_code(flight_number: Optional[str]) -> str:
        """IATA kód aerolinky z čísla letu (EY25→EY). Prázdné když nelze."""
        if not flight_number:
            return ""
        m = _FLIGHTNO_RE.match(flight_number.upper())
        return m.group(1) if m else ""
