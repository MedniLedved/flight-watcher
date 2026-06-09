"""Hlavní orchestrátor scanů – Japan Flight Tracker.

Logika spouštění:
1. Načti konfiguraci z routes.yaml a .env
2. Pro každou trasu: Kiwi → Amadeus → Travelpayouts, agreguj a deduplikuj
3. Parsuj RSS zdroje (Secret Flying, Cestujlevně)
4. Pokus se o Jack's Flight Club scraping
5. Porovnej s historií a prahem
6. Odešli Telegram alerty pro nové dealy
7. Odešli denní souhrn
8. Ulož aktualizovanou historii

Každý zdroj je obalen try/except – jeden chybějící zdroj nezastaví scan.
Spuštění: python -m src.scanner
"""
from __future__ import annotations

import calendar as _calendar
import logging
from datetime import date
from typing import Any, Optional

from .config import (
    RATE_LIMIT_COMBINATIONS,
    Settings,
    airport_name,
    trim_airports,
)
from .history import PriceHistory
from .notifier import TelegramNotifier
from .sources import DealResult, FlightResult
from .sources.amadeus import AmadeusSource
from .sources.cestujlevne import CestujLevneSource
from .sources.jacks import JacksFlightClubSource
from .sources.kiwi import KiwiSource
from .sources.secret_flying import SecretFlyingSource
from .sources.travelpayouts import TravelpayoutsSource

logger = logging.getLogger(__name__)

AMADEUS_MONTHLY_LIMIT = 2000


def _window_bounds(year: int, months: list[int]) -> tuple[date, date]:
    """Vrátí (první den prvního měsíce, poslední den posledního měsíce)."""
    first = date(year, min(months), 1)
    last_month = max(months)
    last_day = _calendar.monthrange(year, last_month)[1]
    last = date(year, last_month, last_day)
    return first, last


class Scanner:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.load()
        logging.basicConfig(
            level=getattr(logging, self.settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.history = PriceHistory()
        self.notifier = TelegramNotifier(
            self.settings.telegram_bot_token, self.settings.telegram_chat_id
        )

        # Inicializace zdrojů (jen pokud jsou credentials).
        self.kiwi = (
            KiwiSource(self.settings.kiwi_api_key)
            if self.settings.kiwi_api_key else None
        )
        self.amadeus = (
            AmadeusSource(
                self.settings.amadeus_client_id,
                self.settings.amadeus_client_secret,
                env=self.settings.amadeus_env,
            )
            if (self.settings.amadeus_client_id and self.settings.amadeus_client_secret)
            else None
        )
        self.travelpayouts = (
            TravelpayoutsSource(self.settings.travelpayouts_token)
            if self.settings.travelpayouts_token else None
        )

        self.request_count = 0
        self.api_count = 0  # počet zapojených API zdrojů

    # -- Trasy ------------------------------------------------------------
    def _legs_for_route(self, route: dict[str, Any]) -> dict[str, list[str]]:
        """Rozloží konfiguraci trasy na outbound/inbound origins/destinations."""
        if route.get("type") == "openjaw":
            ob = route.get("outbound", {})
            ib = route.get("inbound", {})
            return {
                "out_origins": self.settings.resolve_airport_list(ob.get("origins")),
                "out_dests": self.settings.resolve_airport_list(ob.get("destinations")),
                "in_origins": self.settings.resolve_airport_list(ib.get("origins")),
                "in_dests": self.settings.resolve_airport_list(ib.get("destinations")),
            }
        # roundtrip
        origins = self.settings.resolve_airport_list(route.get("origins"))
        dests = self.settings.resolve_airport_list(route.get("destinations"))
        return {
            "out_origins": origins, "out_dests": dests,
            "in_origins": dests, "in_dests": origins,
        }

    def scan_route(self, route: dict[str, Any]) -> list[FlightResult]:
        name = route.get("name", "?")
        is_openjaw = route.get("type") == "openjaw"
        legs = self._legs_for_route(route)
        window = self.settings.search_windows[0] if self.settings.search_windows else {
            "year": date.today().year, "months": [date.today().month]
        }
        date_from, date_to = _window_bounds(window["year"], window["months"])
        stay = self.settings.stay_length
        results: list[FlightResult] = []

        # --- Kiwi ---
        if self.kiwi:
            try:
                ko, kd = trim_airports(
                    legs["out_origins"], legs["out_dests"],
                    RATE_LIMIT_COMBINATIONS["kiwi"],
                )
                fly_to = kd if not is_openjaw else kd  # Kiwi zvládne open-jaw přes city aliasy
                results += self.kiwi.search(
                    fly_from=ko, fly_to=fly_to,
                    date_from=date_from, date_to=date_to,
                    nights_from=stay["min_nights"], nights_to=stay["max_nights"],
                    flight_type="round", route_name=name,
                )
                logger.info("Kiwi %s: %d nabídek", name, len(results))
            except Exception as exc:  # noqa: BLE001
                logger.error("Kiwi scan selhal pro %s: %s", name, exc)

        # --- Amadeus ---
        if self.amadeus and self._amadeus_has_budget():
            try:
                ao, ad = trim_airports(
                    legs["out_origins"], legs["out_dests"],
                    RATE_LIMIT_COMBINATIONS["amadeus"],
                )
                in_o = legs["in_origins"]
                in_d = legs["in_dests"]
                depart = date_from
                ret = self._add_nights(depart, stay["min_nights"])
                for o in ao:
                    for d in ad:
                        if not self._amadeus_has_budget():
                            break
                        r_origin = in_o[0] if is_openjaw and in_o else d
                        r_dest = in_d[0] if is_openjaw and in_d else o
                        try:
                            results += self.amadeus.search(
                                origin=o, destination=d,
                                departure_date=depart, return_date=ret,
                                return_origin=r_origin, return_destination=r_dest,
                                route_name=name,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.error("Amadeus %s %s→%s: %s", name, o, d, exc)
                logger.info("Amadeus %s hotovo", name)
            except Exception as exc:  # noqa: BLE001
                logger.error("Amadeus scan selhal pro %s: %s", name, exc)

        # --- Travelpayouts (záloha/trend) ---
        if self.travelpayouts:
            try:
                to, td = trim_airports(
                    legs["out_origins"], legs["out_dests"],
                    RATE_LIMIT_COMBINATIONS["travelpayouts"],
                )
                dep_month = f"{window['year']:04d}-{min(window['months']):02d}"
                for o in to[:5]:  # rozumné omezení
                    for d in td[:3]:
                        try:
                            results += self.travelpayouts.search(
                                origin=o, destination=d,
                                departure_at=dep_month, return_at=None,
                                route_name=name,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.error("Travelpayouts %s→%s: %s", o, d, exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("Travelpayouts scan selhal pro %s: %s", name, exc)

        return self._deduplicate(results)

    def _amadeus_has_budget(self) -> bool:
        if not self.amadeus:
            return False
        used = self.history.amadeus_usage() + self.amadeus.request_count
        return used < AMADEUS_MONTHLY_LIMIT

    @staticmethod
    def _add_nights(d: date, nights: int) -> date:
        from datetime import timedelta
        return d + timedelta(days=nights)

    @staticmethod
    def _deduplicate(results: list[FlightResult]) -> list[FlightResult]:
        """Pro stejnou trasu+data ponech jen nejnižší cenu."""
        best: dict[tuple, FlightResult] = {}
        for r in results:
            key = (r.origin, r.destination, r.return_origin,
                   r.depart_date, r.return_date)
            if key not in best or r.price < best[key].price:
                best[key] = r
        return sorted(best.values(), key=lambda r: r.price)

    # -- RSS / scraping ---------------------------------------------------
    def scan_deals(self) -> tuple[list[DealResult], dict[str, bool]]:
        deals: list[DealResult] = []
        status: dict[str, bool] = {}

        try:
            sf = SecretFlyingSource().fetch(max_age_days=2)
            deals += sf
            status["secret_flying"] = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Secret Flying selhal: %s", exc)
            status["secret_flying"] = False

        try:
            cl = CestujLevneSource(czk_eur_rate=self.settings.czk_eur_rate).fetch(
                max_age_days=2
            )
            deals += cl
            status["cestujlevne"] = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Cestujlevně selhal: %s", exc)
            status["cestujlevne"] = False

        try:
            jk = JacksFlightClubSource().fetch()
            deals += jk
            status["jacks"] = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Jack's selhal: %s", exc)
            status["jacks"] = False

        return deals, status

    # -- Hlavní běh -------------------------------------------------------
    def run(self) -> None:
        logger.info("=== Japan Flight Tracker – start scanu ===")
        self.api_count = sum(
            1 for s in (self.kiwi, self.amadeus, self.travelpayouts) if s
        )

        all_flights: list[FlightResult] = []
        routes = self.settings.routes
        for route in routes:
            try:
                flights = self.scan_route(route)
                all_flights += flights
            except Exception as exc:  # noqa: BLE001
                logger.error("Trasa %s selhala: %s", route.get("name"), exc)

        # Vyhodnocení alertů vůči historii a prahu.
        self._process_flights(all_flights)

        # RSS / scraping dealy.
        deals, source_status = self.scan_deals()
        self._process_deals(deals)

        # Aktualizuj Amadeus usage.
        if self.amadeus:
            self.history.add_amadeus_usage(self.amadeus.request_count)

        # Denní souhrn.
        self._send_summary(all_flights, source_status, len(routes))

        self.history.save()
        logger.info("=== Scan dokončen ===")

    def _process_flights(self, flights: list[FlightResult]) -> None:
        threshold = self.settings.price_threshold_eur
        for f in flights:
            key = f.route_key()
            below_threshold = f.price < threshold
            is_low = self.history.is_new_low(key, f.price)
            delta = self.history.price_delta(key, f.price)

            # Zaznamenej do historie vždy.
            should_send = (below_threshold or is_low) and self.history.should_alert(
                key, f.price
            )
            self.history.record(key, f.price, f.source, f.depart_date)

            if should_send:
                if self.notifier.send_price_alert(f, delta=delta):
                    self.history.mark_alerted(key, f.price)
                    logger.info("Alert odeslán: %s %.0f EUR", key, f.price)

    def _process_deals(self, deals: list[DealResult]) -> None:
        for deal in deals:
            self.notifier.send_deal_alert(deal)

    def _send_summary(self, flights: list[FlightResult],
                      source_status: dict[str, bool], route_count: int) -> None:
        # Sestav nejlepší ceny na route_key.
        best: dict[str, FlightResult] = {}
        for f in flights:
            key = f.route_key()
            if key not in best or f.price < best[key].price:
                best[key] = f

        summary_lines: list[str] = []
        for key, f in sorted(best.items(), key=lambda kv: kv[1].price)[:10]:
            nights = f"{f.nights} dní" if f.nights is not None else "?"
            label = {
                "kiwi": "Kiwi", "amadeus": "Amadeus",
                "travelpayouts": "Travelpayouts",
            }.get(f.source, f.source)
            delta = self.history.price_delta(key, f.price)
            trend = ""
            if delta is not None and delta < 0:
                trend = f" ⬇️ {delta:.0f} EUR"
            elif delta is not None and delta > 0:
                trend = f" ⬆️ +{delta:.0f} EUR"
            route_disp = self._route_display(f)
            summary_lines.append(
                f"{route_disp}: {f.price:.0f} EUR, {nights} ({label}){trend}"
            )

        amadeus_used = self.history.amadeus_usage()
        total_requests = route_count * max(self.api_count, 1)
        stats = {
            "scans": (
                f"Celkem scanů dnes: {route_count} tras × "
                f"{self.api_count} API = ~{total_requests} requestů"
            ),
            "amadeus": (
                f"Amadeus využití: {amadeus_used}/{AMADEUS_MONTHLY_LIMIT} "
                f"requestů tento měsíc"
            ),
        }
        self.notifier.send_daily_summary(summary_lines, source_status, stats)

    @staticmethod
    def _route_display(f: FlightResult) -> str:
        if f.return_origin and f.return_origin != f.destination:
            return f"{f.origin}→{f.destination}/{f.return_origin}→{f.return_destination}"
        return f"{f.origin}→{f.destination} zpáteční"


def main() -> None:
    Scanner().run()


if __name__ == "__main__":
    main()
