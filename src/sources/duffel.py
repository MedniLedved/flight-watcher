"""Duffel API (vrstva 1 – real-time, náhrada za uzavřené Kiwi Tequila).

Dokumentace: https://duffel.com/docs/api
Endpoint: POST https://api.duffel.com/air/offer_requests
Autentizace: header `Authorization: Bearer DUFFEL_TOKEN`
Povinné hlavičky: `Duffel-Version: v2`, `Content-Type: application/json`.

Duffel nativně podporuje open-jaw / multi-city přes pole `slices` – každý
slice má vlastní origin/destination/departure_date. Pro zpáteční let stačí
dva slice (tam + zpět), pro open-jaw mají slice odlišné letiště.

Pozn.: Duffel má test režim (token `duffel_test_...`) se syntetickými daty
i produkční režim (`duffel_live_...`) s reálnými cenami. Token sám určuje
režim – není potřeba zvláštní přepínač.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

from . import FlightResult
from .fx import FxRates
from .google_flights import google_flights_url

logger = logging.getLogger(__name__)

BASE_URL = "https://api.duffel.com/air/offer_requests"
DUFFEL_VERSION = "v2"
_REQUEST_DELAY = 0.5
# Retry na rate-limit (HTTP 429) a dočasné výpadky (5xx) s exponenciálním
# backoffem. Duffel při paralelních voláních snadno vrátí 429 – místo ztráty
# trasy počkáme a zkusíme znovu (respektujeme i hlavičku Retry-After).
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0  # s: 2, 4, 8, 16


class DuffelSource:
    name = "duffel"

    def __init__(self, token: str, session: Optional[requests.Session] = None,
                 fx: Optional[FxRates] = None):
        self.token = token
        self.session = session or requests.Session()
        # Duffel (na rozdíl od ostatních zdrojů) neumí vynutit měnu odpovědi –
        # ne-EUR nabídky se převádějí denním kurzem ECB (viz fx.py).
        self.fx = fx or FxRates()
        # None = zatím neznámé; False = API potvrdilo TEST režim (syntetická
        # data se smyšlenými cenami) → nabídky se zahazují, ať neotráví
        # historii, alerty ani dashboard. Scanner z příznaku staví varování
        # do denního souhrnu.
        self.live_mode: Optional[bool] = None

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
        """Vytvoří offer request a vrátí nabídky jako FlightResult.

        Pro open-jaw zadej return_origin/return_destination odlišné od
        destination/origin – promítne se do druhého slice.
        """
        slices = [{
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date.isoformat(),
        }]
        if return_date:
            slices.append({
                "origin": return_origin or destination,
                "destination": return_destination or origin,
                "departure_date": return_date.isoformat(),
            })

        body = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(adults)],
                "cabin_class": cabin_class,
            }
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Duffel-Version": DUFFEL_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # return_offers=true (výchozí) → nabídky přijdou rovnou v odpovědi.
        params = {"return_offers": "true", "supplier_timeout": "15000"}

        resp = self._post_with_retry(body, headers, params, origin, destination)

        payload = resp.json().get("data", {})
        live_mode = payload.get("live_mode")
        if live_mode is not None:
            self.live_mode = bool(live_mode)
        offers = payload.get("offers", [])
        if live_mode is False:
            # TEST režim (duffel_test_… token): syntetické nabídky se
            # smyšlenými cenami, které neodpovídají žádné reálné letence.
            logger.error(
                "Duffel %s→%s: API běží v TEST režimu (live_mode=false) – "
                "zahazuji %d syntetických nabídek. Nastav produkční "
                "duffel_live_… token.",
                origin, destination, len(offers),
            )
            return []

        # Historie ukládá ceny bez měny (vždy EUR). Ne-EUR nabídky převeď
        # denním kurzem ECB; bez dostupného kurzu nabídku přeskoč – vydávat
        # cizí měnu za EUR by zkreslilo alerty i trendy.
        offers, skipped_currencies = self._offers_in_eur(offers)
        if skipped_currencies:
            logger.warning(
                "Duffel %s→%s: nabídky v měně %s přeskočeny (kurz na EUR "
                "není k dispozici).",
                origin, destination, ", ".join(skipped_currencies),
            )

        results = [
            self._parse_offer(o, origin, destination, route_name)
            for o in offers
        ]
        results = [r for r in results if r is not None]
        results.sort(key=lambda r: r.price)
        return results[:max_results]

    def _offers_in_eur(self, offers: list[dict]) -> tuple[list[dict], list[str]]:
        """Vrátí (nabídky s cenou v EUR, kódy měn bez dostupného kurzu).

        EUR nabídky projdou beze změny; ostatní se převedou denním kurzem
        ECB (total_amount → EUR, kopie – původní payload se nemutuje).
        """
        kept: list[dict] = []
        skipped: set[str] = set()
        for o in offers:
            currency = o.get("total_currency") or "EUR"
            if currency == "EUR":
                kept.append(o)
                continue
            try:
                amount = float(o["total_amount"])
            except (KeyError, ValueError, TypeError):
                continue
            eur = self.fx.to_eur(amount, currency)
            if eur is None:
                skipped.add(str(currency))
                continue
            converted = dict(o)
            converted["total_amount"] = eur
            converted["total_currency"] = "EUR"
            kept.append(converted)
        return kept, sorted(skipped)

    def _post_with_retry(self, body, headers, params, origin, destination):
        """POST s retry na 429/5xx (exponenciální backoff, respektuje
        Retry-After). Po vyčerpání pokusů vyhodí poslední výjimku."""
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self.session.post(
                    BASE_URL, json=body, headers=headers, params=params, timeout=40
                )
                resp.raise_for_status()
                return resp
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                last_exc = exc
                if status in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                    wait = self._retry_after(exc.response) or _BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "Duffel %s→%s: HTTP %s, pokus %d/%d, čekám %.0f s",
                        origin, destination, status, attempt + 1, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.error("Duffel chyba %s→%s: %s", origin, destination, exc)
                raise
            except requests.RequestException as exc:
                last_exc = exc
                logger.error("Duffel chyba %s→%s: %s", origin, destination, exc)
                raise
            finally:
                time.sleep(_REQUEST_DELAY)
        # Sem se nedostaneme (poslední pokus buď vrátí, nebo raise), ale pro
        # jistotu:
        raise last_exc if last_exc else RuntimeError("Duffel: neznámá chyba")

    @staticmethod
    def _retry_after(resp) -> Optional[float]:
        """Sekundy z hlavičky Retry-After (číslo), jinak None."""
        if resp is None:
            return None
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _parse_offer(self, offer: dict, origin: str, destination: str,
                     route_name: str) -> Optional[FlightResult]:
        try:
            price = float(offer["total_amount"])
        except (KeyError, ValueError, TypeError):
            return None
        currency = offer.get("total_currency", "EUR")
        slices = offer.get("slices", [])
        out_slice = slices[0] if slices else {}
        in_slice = slices[1] if len(slices) > 1 else {}

        out_segs = out_slice.get("segments", [])
        in_segs = in_slice.get("segments", [])

        airlines: set[str] = set()
        owner = offer.get("owner", {})
        if owner.get("iata_code"):
            airlines.add(owner["iata_code"])
        for seg in out_segs + in_segs:
            carrier = seg.get("marketing_carrier") or seg.get("operating_carrier") or {}
            if carrier.get("iata_code"):
                airlines.add(carrier["iata_code"])

        # Letiště ber ze segmentů (konkrétní letiště), ne ze slice – tam Duffel
        # vrací city kódy (OSA = Osaka město, TYO = Tokio), které by rozbily
        # statistiky letišť i zobrazení tras.
        o_code = (self._seg_place(out_segs[0], "origin") if out_segs else None) \
            or self._slice_origin(out_slice, origin)
        d_code = (self._seg_place(out_segs[-1], "destination") if out_segs else None) \
            or self._slice_destination(out_slice, destination)
        r_o = (self._seg_place(in_segs[0], "origin") if in_segs else None) \
            or (self._slice_origin(in_slice, "") if in_slice else "")
        r_d = (self._seg_place(in_segs[-1], "destination") if in_segs else None) \
            or (self._slice_destination(in_slice, "") if in_slice else "")
        depart_date = self._seg_date(out_segs[0]) if out_segs else None
        return_date = self._seg_date(in_segs[0]) if in_segs else None

        return FlightResult(
            price=price,
            currency=currency,
            origin=o_code,
            destination=d_code,
            return_origin=r_o,
            return_destination=r_d,
            depart_date=depart_date,
            return_date=return_date,
            airlines=sorted(airlines),
            source=self.name,
            # Duffel je booking API bez veřejného nákupního odkazu – sestavíme
            # vyhledávací odkaz na Google Flights pro stejnou trasu a termín
            # (binární ?tfs= parametr; textový ?q= Google nepředvyplňuje).
            deep_link=google_flights_url(
                o_code, d_code, depart_date, return_date, r_o, r_d
            ),
            route_name=route_name,
        )

    @staticmethod
    def _seg_place(seg: dict, key: str) -> Optional[str]:
        place = seg.get(key) or {}
        return place.get("iata_code") if isinstance(place, dict) else None

    @staticmethod
    def _slice_origin(slc: dict, fallback: str) -> str:
        org = slc.get("origin") or {}
        return org.get("iata_code", fallback) if isinstance(org, dict) else fallback

    @staticmethod
    def _slice_destination(slc: dict, fallback: str) -> str:
        dst = slc.get("destination") or {}
        return dst.get("iata_code", fallback) if isinstance(dst, dict) else fallback

    @staticmethod
    def _seg_date(seg: dict) -> Optional[date]:
        value = seg.get("departing_at", "")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
