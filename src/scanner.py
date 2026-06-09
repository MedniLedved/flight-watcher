"""Hlavní orchestrátor scanů – Japan Flight Tracker.

Logika spouštění:
1. Načti konfiguraci z routes.yaml a .env
2. Pro každou trasu: Duffel → Sky Scrapper → Amadeus → Travelpayouts, agreguj a deduplikuj
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
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Optional

from .airport_stats import (
    format_airport_stats,
    format_weekday_stats,
    priority_order,
    rank_airports,
)
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
from .sources.duffel import DuffelSource
from .sources.jacks import JacksFlightClubSource
from .sources.miles_and_more import MilesAndMoreSource
from .sources.miles_and_more import should_run_today as mm_should_run_today
from .sources.secret_flying import SecretFlyingSource
from .sources.skyscrapper import SkyScrapperSource
from .sources.travelpayouts import TravelpayoutsSource

logger = logging.getLogger(__name__)

AMADEUS_MONTHLY_LIMIT = 2000
SKYSCRAPPER_MONTHLY_LIMIT = 100  # RapidAPI free tier

# Počet souběžných vláken pro per-combo volání zdrojů bez kvótového limitu
# (Duffel, Travelpayouts). ~100 volání tak netrvá 15+ min sekvenčně.
# Lze přepsat přes env SCAN_MAX_WORKERS; rozumné rozmezí 4–8 (víc = riziko 429).
SCAN_MAX_WORKERS = max(1, int(os.getenv("SCAN_MAX_WORKERS", "6")))

# Kolik kombinací (odlet, návrat) prohledat za jeden běh. Termíny denně
# rotují napříč celým oknem, takže se postupně pokryje celé období i různé
# délky pobytu, aniž by jeden běh dělal stovky requestů navíc.
SCAN_DATE_SAMPLES = max(1, int(os.getenv("SCAN_DATE_SAMPLES", "2")))

# Plánování vzorkování (coverage-driven greedy + recency decay).
# Studený start: dokud nemá každý den v týdnu / letiště aspoň tolik vážených
# pozorování, jede se čistě podle deficitu (rovnoměrné pokrytí). Pak se
# rozpočet dělí EXPLORE_FRACTION na průzkum (čerstvost) a zbytek na exploit
# (převzorkování nejakčnějších dnů/letišť).
COLD_START_TARGET = max(0.0, float(os.getenv("SCAN_COLD_START_TARGET", "3")))
EXPLORE_FRACTION = min(1.0, max(0.0, float(os.getenv("SCAN_EXPLORE_FRACTION", "0.3"))))


def _window_bounds(year: int, months: list[int]) -> tuple[date, date]:
    """Vrátí (první den prvního měsíce, poslední den posledního měsíce)."""
    first = date(year, min(months), 1)
    last_month = max(months)
    last_day = _calendar.monthrange(year, last_month)[1]
    last = date(year, last_month, last_day)
    return first, last


def _best_weekday(wd_data: dict[int, dict]) -> Optional[int]:
    """Den v týdnu s nejvyšší deal frequency (tiebreaker levnější medián).
    Vrací None, pokud nejsou data."""
    if not wd_data:
        return None
    def _key(item):
        wd, s = item
        dm = s.get("deal_median")
        if dm is None:
            dm = s.get("all_median", float("inf"))
        return (-s.get("deal_rate", 0.0), dm)
    return min(wd_data.items(), key=_key)[0]


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
        if self.notifier.enabled:
            logger.info("Telegram: nakonfigurován (souhrn se odešle)")
        else:
            logger.warning(
                "Telegram: NENÍ nakonfigurován – chybí TELEGRAM_BOT_TOKEN nebo "
                "TELEGRAM_CHAT_ID. Žádné zprávy se neodešlou!"
            )

        # Inicializace zdrojů (jen pokud jsou credentials).
        self.duffel = (
            DuffelSource(self.settings.duffel_token)
            if self.settings.duffel_token else None
        )
        self.skyscrapper = (
            SkyScrapperSource(self.settings.rapidapi_key)
            if self.settings.rapidapi_key else None
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
        self.scanned_date_pairs: list[tuple[date, date]] = []
        # Plánovací stav (naplní se v run() před scanem tras).
        self.coverage: dict[str, dict] = {}
        self.best_depart_wd: Optional[int] = None
        self.best_return_wd: Optional[int] = None

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
        date_pairs = self._plan_scan_dates(
            date_from, date_to, stay,
            coverage=self.coverage,
            best_depart_wd=self.best_depart_wd,
            best_return_wd=self.best_return_wd,
        )
        self.scanned_date_pairs = date_pairs
        results: list[FlightResult] = []

        # --- Duffel (primární náhrada za Kiwi) – všechny vzorky termínů ---
        if self.duffel:
            for depart, ret in date_pairs:
                results += self._scan_per_combo(
                    self.duffel, "duffel", legs, is_openjaw,
                    depart, ret, name,
                    limit=RATE_LIMIT_COMBINATIONS["duffel"],
                )

        # Kvótované zdroje šetří requesty → jen první (hlavní) termín.
        depart, ret = date_pairs[0]

        # --- Sky Scrapper / RapidAPI (pozor na 100 req/měsíc) ---
        if self.skyscrapper and self._skyscrapper_has_budget():
            results += self._scan_per_combo(
                self.skyscrapper, "skyscrapper", legs, is_openjaw,
                depart, ret, name,
                limit=RATE_LIMIT_COMBINATIONS["skyscrapper"],
                budget_check=self._skyscrapper_has_budget,
            )

        # --- Amadeus ---
        if self.amadeus and self._amadeus_has_budget():
            results += self._scan_per_combo(
                self.amadeus, "amadeus", legs, is_openjaw,
                depart, ret, name,
                limit=RATE_LIMIT_COMBINATIONS["amadeus"],
                budget_check=self._amadeus_has_budget,
            )

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

    def _scan_per_combo(self, source, source_name, legs, is_openjaw,
                        depart, ret, name, limit, budget_check=None):
        """Spustí per-combo (origin×destination) vyhledávání nad zdrojem,
        který má jednotné rozhraní search(origin, destination, departure_date,
        return_date, return_origin, return_destination, route_name).

        budget_check (callable→bool) volitelně zastaví smyčku při vyčerpání
        kvóty zdroje. Zdroje bez kvóty (budget_check=None) běží paralelně,
        aby ~100 volání netrvalo 15+ minut sekvenčně.
        """
        results: list[FlightResult] = []
        try:
            origins, dests = trim_airports(
                legs["out_origins"], legs["out_dests"], limit
            )
            in_o, in_d = legs["in_origins"], legs["in_dests"]

            # Sestav seznam kombinací (origin, destination, return_o, return_d).
            combos = []
            for o in origins:
                for d in dests:
                    r_origin = in_o[0] if is_openjaw and in_o else d
                    r_dest = in_d[0] if is_openjaw and in_d else o
                    combos.append((o, d, r_origin, r_dest))

            def _one(combo):
                o, d, r_origin, r_dest = combo
                try:
                    return source.search(
                        origin=o, destination=d,
                        departure_date=depart, return_date=ret,
                        return_origin=r_origin, return_destination=r_dest,
                        route_name=name,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("%s %s %s→%s: %s", source_name, name, o, d, exc)
                    return []

            if budget_check is None:
                # Bez kvótového limitu → paralelně (výrazně rychlejší).
                with ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as pool:
                    for res in pool.map(_one, combos):
                        results += res
            else:
                # S kvótou → sekvenčně, ať lze průběžně kontrolovat budget.
                for combo in combos:
                    if not budget_check():
                        logger.warning("%s: vyčerpána kvóta, scan trasy zkrácen",
                                       source_name)
                        break
                    results += _one(combo)
            logger.info("%s %s: %d nabídek", source_name, name, len(results))
        except Exception as exc:  # noqa: BLE001
            logger.error("%s scan selhal pro %s: %s", source_name, name, exc)
        return results

    def _amadeus_has_budget(self) -> bool:
        if not self.amadeus:
            return False
        used = self.history.amadeus_usage() + self.amadeus.request_count
        return used < AMADEUS_MONTHLY_LIMIT

    def _skyscrapper_has_budget(self) -> bool:
        if not self.skyscrapper:
            return False
        used = self.history.skyscrapper_usage() + self.skyscrapper.request_count
        return used < SKYSCRAPPER_MONTHLY_LIMIT

    @staticmethod
    def _add_nights(d: date, nights: int) -> date:
        return d + timedelta(days=nights)

    @staticmethod
    def _plan_scan_dates(date_from: date, date_to: date, stay: dict,
                         coverage: Optional[dict] = None,
                         best_depart_wd: Optional[int] = None,
                         best_return_wd: Optional[int] = None,
                         samples: int = SCAN_DATE_SAMPLES,
                         today: Optional[date] = None) -> list[tuple[date, date]]:
        """Coverage-driven výběr kombinací (odlet, návrat) pro dnešní běh.

        Greedy zaplňuje nejřidší buňky pokrytí (den odletu × den návratu).
        ``coverage`` jsou vážená (recency-decayed) počítadla z historie
        (``PriceHistory.coverage_weights``). Den návratu je řiditelný přes
        počet nocí: pro odlet ve dni ``a`` a ``n`` nocí padne návrat na
        ``(a+n) % 7``; rozsah nocí > 7 umožní trefit libovolný den návratu.

        Fáze:
        - **studený start** (některý den má vážené pokrytí < COLD_START_TARGET)
          → všechny vzorky podle deficitu (rovnoměrné pokrytí),
        - **lazení** → EXPLORE_FRACTION vzorků na deficit (čerstvost), zbytek na
          exploit (trefit ``best_depart_wd`` / ``best_return_wd`` =
          historicky nejakčnější dny).
        """
        today = today or date.today()
        coverage = coverage or {}
        dep_cov = dict(coverage.get("depart_wd", {}) or {})
        ret_cov = dict(coverage.get("return_wd", {}) or {})
        min_n = stay.get("min_nights", 12)
        max_n = stay.get("max_nights", min_n)
        start = max(date_from, today + timedelta(days=1))
        last_depart = max(date_to - timedelta(days=min_n), start)
        span = (last_depart - start).days
        if span < 0:  # okno už (skoro) prošlo
            ret = min(start + timedelta(days=min_n), date_to)
            return [(start, ret)]

        def _deficit(cov: dict, wd: int) -> float:
            return max(0.0, COLD_START_TARGET - cov.get(wd, 0.0))

        cold = (
            min((dep_cov.get(i, 0.0) for i in range(7)), default=0.0) < COLD_START_TARGET
            or min((ret_cov.get(i, 0.0) for i in range(7)), default=0.0) < COLD_START_TARGET
        )

        pairs: list[tuple[date, date]] = []
        picked: list[date] = []
        for i in range(samples):
            # Deterministický, ale dlouhodobě EXPLORE_FRACTION průzkumných slotů.
            slot = today.toordinal() * samples + i
            explore = cold or (slot % 10) < round(EXPLORE_FRACTION * 10)
            best: Optional[tuple[float, date, date]] = None
            for off in range(span + 1):
                depart = start + timedelta(days=off)
                dwd = depart.weekday()
                for nights in range(min_n, max_n + 1):
                    ret = depart + timedelta(days=nights)
                    if ret > date_to:
                        break
                    rwd = ret.weekday()
                    if explore:
                        score = _deficit(dep_cov, dwd) + _deficit(ret_cov, rwd)
                    else:
                        score = 0.0
                        if best_depart_wd is not None and dwd == best_depart_wd:
                            score += 2.0
                        if best_return_wd is not None and rwd == best_return_wd:
                            score += 2.0
                        # mezi shodnými dny ber méně pokryté (čerstvost)
                        score -= 0.01 * (dep_cov.get(dwd, 0.0) + ret_cov.get(rwd, 0.0))
                    # rozprostři vzorky po okně – odměň vzdálenost od už vybraných
                    if picked:
                        nearest = min(abs((depart - p).days) for p in picked)
                        score += 0.001 * nearest
                    if (depart, ret) in pairs:
                        continue
                    if best is None or score > best[0]:
                        best = (score, depart, ret)
            if best is None:
                break
            _, depart, ret = best
            pairs.append((depart, ret))
            picked.append(depart)
            # Promítni výběr do pokrytí, ať druhý vzorek necílí stejnou buňku.
            dep_cov[depart.weekday()] = dep_cov.get(depart.weekday(), 0.0) + 1.0
            ret_cov[ret.weekday()] = ret_cov.get(ret.weekday(), 0.0) + 1.0
        return pairs

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

        # Miles & More mileage bargains – jen 1. kalendářní den v měsíci
        # (award nabídky se mění měsíčně).
        if mm_should_run_today():
            try:
                mm = MilesAndMoreSource(
                    api_url=self.settings.milesandmore_api_url,
                    api_key=self.settings.milesandmore_api_key,
                    ignore_robots=self.settings.milesandmore_ignore_robots,
                    extra_headers=self.settings.milesandmore_headers,
                ).fetch()
                deals += mm
                status["miles_and_more"] = True
            except Exception as exc:  # noqa: BLE001
                logger.error("Miles & More selhal: %s", exc)
                status["miles_and_more"] = False
        else:
            logger.info("Miles & More: přeskočeno (kontrola jen 1. v měsíci)")

        return deals, status

    # -- Dynamická priorita letišť ---------------------------------------
    def _apply_dynamic_priority(self) -> dict[str, dict[str, float]]:
        """Přeřadí primární letiště podle historických cen (levná dopředu).
        Vrací spočítanou statistiku (pro zobrazení v souhrnu)."""
        stats = self.history.airport_stats(
            threshold=self.settings.price_threshold_eur
        )
        airport_cov = self.coverage.get("airport", {})
        eu_before = self.settings.european_airports
        jp_before = self.settings.japanese_airports
        # priority_order dává nedostatečně prozkoumaná letiště dopředu
        # (průzkum), jinak řadí dle deal_rate (exploit).
        eu_after = priority_order(eu_before, stats, airport_cov, COLD_START_TARGET)
        jp_after = priority_order(jp_before, stats, airport_cov, COLD_START_TARGET)

        # Přepiš pořadí v konfiguraci → promítne se do resolve_airport_list
        # (all_european / all_japanese) a tím i do trim_airports.
        self.settings.routes_config["european_airports"] = eu_after
        self.settings.routes_config["japanese_airports"] = jp_after

        if eu_after != eu_before:
            logger.info("Priorita EU letišť přeřazena dle cen: %s → %s",
                        eu_before, eu_after)
        if jp_after != jp_before:
            logger.info("Priorita JP letišť přeřazena dle cen: %s → %s",
                        jp_before, jp_after)
        return stats

    # -- Hlavní běh -------------------------------------------------------
    def run(self) -> None:
        logger.info("=== Japan Flight Tracker – start scanu ===")
        # Spočítej pokrytí (recency-decayed) a nejakčnější dny PŘED scanem –
        # řídí jak prioritu letišť, tak greedy výběr termínů.
        self.coverage = self.history.coverage_weights()
        wd_stats = self.history.weekday_stats(
            threshold=self.settings.price_threshold_eur
        )
        self.best_depart_wd = _best_weekday(wd_stats.get("depart", {}))
        self.best_return_wd = _best_weekday(wd_stats.get("return", {}))
        # Dynamicky přeřaď letiště podle historických cen PŘED scanem,
        # aby levnější letiště přežila ořezání dle rate limitů.
        airport_stats = self._apply_dynamic_priority()
        self.api_count = sum(
            1 for s in (self.duffel, self.skyscrapper, self.amadeus,
                        self.travelpayouts) if s
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

        # Aktualizuj počítadla spotřeby kvót.
        if self.amadeus:
            self.history.add_amadeus_usage(self.amadeus.request_count)
        if self.skyscrapper:
            self.history.add_skyscrapper_usage(self.skyscrapper.request_count)

        # Denní souhrn.
        self._send_summary(all_flights, source_status, len(routes))

        self.history.save()
        logger.info("=== Scan dokončen ===")

    def _process_flights(self, flights: list[FlightResult]) -> None:
        threshold = self.settings.price_threshold_eur
        for f in flights:
            key = f.route_key()
            # Alert jen pod prahem – dražší výsledky se pouze zaznamenají
            # do historie (pro statistiky letišť a trendy).
            below_threshold = f.price < threshold
            delta = self.history.price_delta(key, f.price)

            should_send = below_threshold and self.history.should_alert(
                key, f.price
            )
            # on_date NEvyplňujeme → výchozí dnešek (datum pozorování). Datum
            # letu se ukládá zvlášť přes depart_date/return_date. Díky tomu
            # funguje recency decay v coverage_weights i 90denní prořezávání.
            self.history.record(key, f.price, f.source,
                                depart_date=f.depart_date,
                                return_date=f.return_date)

            if should_send:
                if self.notifier.send_price_alert(f, delta=delta):
                    self.history.mark_alerted(key, f.price)
                    logger.info("Alert odeslán: %s %.0f EUR", key, f.price)

    def _process_deals(self, deals: list[DealResult]) -> None:
        for deal in deals:
            self.notifier.send_deal_alert(deal)

    def _send_summary(self, flights: list[FlightResult],
                      source_status: dict[str, bool], route_count: int) -> None:
        threshold = self.settings.price_threshold_eur
        # Sestav nejlepší ceny na route_key – jen pod prahem, dražší nezajímají.
        best: dict[str, FlightResult] = {}
        cheapest_over: Optional[FlightResult] = None
        for f in flights:
            if f.price >= threshold:
                if cheapest_over is None or f.price < cheapest_over.price:
                    cheapest_over = f
                continue
            key = f.route_key()
            if key not in best or f.price < best[key].price:
                best[key] = f

        summary_lines: list[str] = []
        for key, f in sorted(best.items(), key=lambda kv: kv[1].price)[:10]:
            nights_part = f", {f.nights} nocí" if f.nights is not None else ""
            label = {
                "duffel": "Duffel", "skyscrapper": "Sky Scrapper",
                "amadeus": "Amadeus", "travelpayouts": "Travelpayouts",
            }.get(f.source, f.source)
            delta = self.history.price_delta(key, f.price)
            trend = ""
            if delta is not None and delta < 0:
                trend = f" ⬇️ {delta:.0f} EUR"
            elif delta is not None and delta > 0:
                trend = f" ⬆️ +{delta:.0f} EUR"
            route_disp = self._route_display(f)
            summary_lines.append(
                f"{route_disp}: {f.price:.0f} EUR{nights_part} ({label}){trend}"
            )
        if not summary_lines and cheapest_over is not None:
            summary_lines.append(
                f"Žádná cena pod prahem {threshold:.0f} EUR (nejlevnější "
                f"nalezená: {self._route_display(cheapest_over)} za "
                f"{cheapest_over.price:.0f} EUR)"
            )

        total_requests = route_count * max(self.api_count, 1)
        stats = {
            "scans": (
                f"Celkem scanů dnes: {route_count} tras × "
                f"{self.api_count} API = ~{total_requests} requestů"
            ),
        }
        if self.scanned_date_pairs:
            terms = ", ".join(
                f"{d.strftime('%d.%m.')}–{r.strftime('%d.%m.')}"
                for d, r in self.scanned_date_pairs
            )
            stats["dates"] = f"🔎 Dnes prověřené termíny: {terms}"
        # Počítadla kvót jen u skutečně zapojených zdrojů.
        if self.amadeus:
            stats["amadeus"] = (
                f"Amadeus využití: {self.history.amadeus_usage()}/"
                f"{AMADEUS_MONTHLY_LIMIT} requestů tento měsíc"
            )
        if self.skyscrapper:
            stats["skyscrapper"] = (
                f"Sky Scrapper využití: {self.history.skyscrapper_usage()}/"
                f"{SKYSCRAPPER_MONTHLY_LIMIT} requestů tento měsíc"
            )
        # Statistika letišť dle podílu dealů (vč. dnešních záznamů) – seřazeno
        # od nejakčnějšího. Reflektuje dynamicky upravenou prioritu.
        airport_stats = self.history.airport_stats(threshold=threshold)
        eu_lines = format_airport_stats(
            self.settings.european_airports, airport_stats
        )
        jp_lines = format_airport_stats(
            self.settings.japanese_airports, airport_stats
        )
        weekday_stats = self.history.weekday_stats(threshold=threshold)
        wd_lines = format_weekday_stats(weekday_stats)

        sent = self.notifier.send_daily_summary(
            summary_lines, source_status, stats,
            eu_airport_stats=eu_lines, jp_airport_stats=jp_lines,
            weekday_stats_lines=wd_lines,
        )
        if self.notifier.enabled and not sent:
            logger.warning(
                "Denní souhrn se NEPODAŘILO odeslat na Telegram – viz chyba výše "
                "(typicky špatný TELEGRAM_CHAT_ID nebo bot bez /start)."
            )
        elif sent:
            logger.info("Denní souhrn odeslán na Telegram.")

    @staticmethod
    def _route_display(f: FlightResult) -> str:
        if f.return_origin and f.return_origin != f.destination:
            return f"{f.origin}→{f.destination}/{f.return_origin}→{f.return_destination}"
        return f"{f.origin}→{f.destination} zpáteční"


def main() -> None:
    Scanner().run()


if __name__ == "__main__":
    main()
