"""FlightLabs (goflightlabs.com) – retrieveFlights (Flight Prices API).

Kontrakt dle OFICIÁLNÍ dokumentace (goflightlabs dashboard, Flight Prices API):
* GET https://www.goflightlabs.com/retrieveFlights
  params: access_key, originIATACode, destinationIATACode, date (=odlet),
          volitelně returnDate, adults, currency, cabinClass,
          mode=roundtrip, sortBy=best, group_by_roundtrip=true
  → IATA kódy přímo (NE skyId/entityId; goflightlabs retrieveFlights bere IATA).
* ASYNC job-queue (ověřeno živě 2026-06-22): první volání vrátí HTTP 202
  {"status":"processing","jobId":...,"message":"...check again later with the
  same parameters..."}; výsledky se získají OPAKOVANÝM voláním STEJNÝCH parametrů
  (poll), dokud nevrátí HTTP 200. Joby dozrávají ~30–80 s → scanner submitne
  (uloží pending) a sebere je collectem v dalším běhu. (Dokumentační příklad
  vypadá synchronně, ale produkční API je job-queue.)
* Tělo 200 (group_by_roundtrip=true):
    {"pairs":[{"outbound":{...leg...},"inbound":{...leg...}, "price":...}],
     "unpaired":[...legů, které nešly spárovat...]}
  Bez group_by_roundtrip vrací ploché {"flights":[...]} (jednotlivé možnosti).
  Každý leg/flight má: price, currency, origin{code,city}, destination{code,city},
  departure, arrival, durationInMinutes, stopCount, flightNumber,
  marketingCarrier, operatingCarrier.
  Z páru outbound+inbound stavíme jeden roundtrip FlightResult. Nespárované
  (`unpaired`) se zahazují (ochrana proti one-way pollution).

Kvóta: 4000 req/měsíc (po upgradu), fakturační období kotví na 19. Rate limit
~10 req/10 s. POZOR: async poll znamená VÍC requestů na kombinaci (submit + N
collect pollů v dalších bězích).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult
from .fx import FxRates
from .google_flights import google_flights_url
from .http_utils import make_api_session

logger = logging.getLogger(__name__)

RETRIEVE_FLIGHTS_URL = "https://www.goflightlabs.com/retrieveFlights"
# Delay zvýšen z 1.1 s: i tak API vracelo 429 na KAŽDÝ request dva dny po sobě
# (118/118 i 210/210 submitů). Pomalejší tempo dává plánu šanci, pokud je limit
# per-minute. Pokud 429 přetrvá i tak, je to tvrdý blok (viz circuit breaker níže).
_REQUEST_DELAY = 3.0
# Circuit breaker: po tolika 429 ZA SEBOU přestaň v daném běhu submitovat. Když
# je klíč tvrdě throttlovaný, nemá smysl spálit celý per-run rozpočet na samé
# 429 (každý 429 navíc utrácí kvótu i čas). Reset při prvním ne-429.
_RATE_LIMIT_CIRCUIT = 4
_POLL_DELAY = 2.5      # pauza mezi polly (jen když submit dostane max_polls>0)
# Submit defaultně NEPOLLUJE (0): async job není v rámci submitu nikdy hotový
# (ověřeno – dozrává 30–80 s), takže každý poll = zbytečný request + 2,5 s
# čekání. Nedokončené joby sebere collect v dalším běhu. Polling lze zapnout
# (max_polls>0) pro test/diagnostiku, kde chceme chytit už nacachovaný job.
_MAX_POLLS = 0

# Klíče, které tvoří dotaz na API (zbytek pending dictu je metadata).
QUERY_KEYS = ("originIATACode", "destinationIATACode", "date", "returnDate",
              "adults", "currency", "cabinClass", "mode", "sortBy",
              "group_by_roundtrip")

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
                 max_polls: int = _MAX_POLLS, poll_delay: float = _POLL_DELAY,
                 fx: Optional[FxRates] = None):
        self.access_key = access_key
        self.session = session or make_api_session()
        self.max_polls = max_polls
        self.poll_delay = poll_delay
        self.request_count = 0
        # retrieveFlights vrací ceny v USD (ověřeno živě – param currency/market/
        # countryCode se nectí). Historie ukládá vždy EUR → převádíme denním
        # kurzem ECB; při výpadku ECB fallback na poslední známý kurz, pak 0,88.
        self.fx = fx or FxRates()
        # Circuit breaker stav (per instance = per scan běh).
        self._consecutive_429 = 0
        self.rate_limited = False

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
            "mode": "roundtrip",
            "sortBy": "best",
            # group_by_roundtrip=true → odpověď má pairs[].outbound/.inbound;
            # string "true" (ne bool – requests by serializoval "True").
            "group_by_roundtrip": "true",
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
        # Circuit breaker: počítej 429 za sebou; po _RATE_LIMIT_CIRCUIT shoď flag,
        # ať scanner přestane v tomto běhu submitovat (nepálí kvótu na samé 429).
        if resp.status_code == 429:
            self._consecutive_429 += 1
            if self._consecutive_429 >= _RATE_LIMIT_CIRCUIT and not self.rate_limited:
                self.rate_limited = True
                logger.warning(
                    "FlightLabs: %d× 429 za sebou → zastavuji submit pro tento "
                    "běh (klíč je throttlovaný, další requesty by jen pálily kvótu)",
                    self._consecutive_429,
                )
        else:
            self._consecutive_429 = 0
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
        # Někdy je obsah zabalený v "data"; jinak je to rovnou objekt s pairs/flights.
        container = payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
            container = payload["data"]

        # Primární tvar (group_by_roundtrip=true): {"pairs":[{outbound,inbound}], ...}
        if isinstance(container, dict) and isinstance(container.get("pairs"), list):
            results = self._parse_pairs(container["pairs"], origin, destination,
                                        route_name)
            results.sort(key=lambda r: r.price)
            return results

        # Fallback: ploché {"flights":[...]} nebo přímo pole legů → adjacency párování.
        legs = None
        if isinstance(container, dict):
            legs = container.get("flights")
        elif isinstance(container, list):
            legs = container
        if not isinstance(legs, list):
            logger.warning("FlightLabs %s→%s: neočekávaný tvar odpovědi (%s)",
                           origin, destination, type(payload).__name__)
            return []
        results = self._parse_legs(legs, origin, destination, route_name)
        results.sort(key=lambda r: r.price)
        return results

    # -- parsování pairs[] (group_by_roundtrip=true) ----------------------
    def _parse_pairs(self, pairs: list, origin: str, destination: str,
                     route_name: str) -> list[FlightResult]:
        """Z každého páru {outbound, inbound} postaví roundtrip FlightResult.
        Pár bez obou nohou (odpovídá `unpaired`) se zahodí – ochrana proti
        one-way pollution."""
        results: list[FlightResult] = []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            out_leg = pair.get("outbound")
            in_leg = pair.get("inbound")
            if not isinstance(out_leg, dict) or not isinstance(in_leg, dict):
                continue
            fr = self._build_roundtrip(out_leg, in_leg, origin, destination,
                                       route_name, pair_price=pair.get("price"))
            if fr is not None:
                results.append(fr)
        return results

    # -- parsování plochých leg párů (fallback bez group_by_roundtrip) ----
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
                         route_name: str,
                         pair_price=None) -> Optional[FlightResult]:
        # Cena: přednostně z páru (celková zpáteční), jinak z outbound legu.
        raw_price = self._parse_price(pair_price if pair_price is not None
                                      else out_leg.get("price"))
        if raw_price is None:
            return None
        # Měna z odpovědi (typicky USD) → převod na EUR denním kurzem ECB.
        # API vždy vrací USD (ignoruje currency/market/countryCode params).
        # Při nedostupném ECB kurzu: zkus poslední známý, pak hardcoded záloha.
        # Kurz v ECB konvenci: 1 EUR = X USD → dělíme (shodně s _last_known).
        currency = str(out_leg.get("currency") or in_leg.get("currency") or "EUR").upper()
        price = self.fx.to_eur_with_fallback(raw_price, currency,
                                             hardcoded={"USD": 1.09})
        if price is None:
            logger.warning("FlightLabs %s→%s: kurz %s→EUR nedostupný ani "
                           "jako fallback – nabídku přeskakuji",
                           origin, destination, currency)
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
            stops_out=self._parse_stops(out_leg.get("stopCount")),
            stops_in=self._parse_stops(in_leg.get("stopCount")),
            duration_out_min=self._parse_stops(out_leg.get("durationInMinutes")),
            duration_in_min=self._parse_stops(in_leg.get("durationInMinutes")),
        )

    @staticmethod
    def _parse_stops(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
