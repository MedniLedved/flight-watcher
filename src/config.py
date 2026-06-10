"""Načítání konfigurace z .env a config/routes.yaml.

Definuje seznamy letišť, rate-limity a pomocnou funkci pro adaptivní
ořezávání seznamů letišť podle limitů jednotlivých zdrojů.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Letiště – seřazena od prioritních. Méně prioritní se ořežou první.
# ---------------------------------------------------------------------------
EUROPEAN_AIRPORTS = [
    "MUC",  # Mnichov
    "PRG",  # Praha
    "VIE",  # Vídeň
    "FRA",  # Frankfurt
    "NUE",  # Norimberk
    "MXP",  # Milán Malpensa
    "FCO",  # Řím Fiumicino
    "BUD",  # Budapešť
    "AMS",  # Amsterdam
    "FMM",  # Memmingen
]

JAPANESE_AIRPORTS = [
    "HND",  # Tokio Haneda
    "NRT",  # Tokio Narita
    "KIX",  # Osaka Kansai
    "ITM",  # Osaka Itami
    "NGO",  # Nagoja Chubu
    # Aliasy pro city-level vyhledávání:
    # TYO = město Tokio (HND + NRT), OSA = město Osaka (KIX + ITM)
]

# Lidská jména letišť pro notifikace.
AIRPORT_NAMES = {
    "MUC": "Mnichov",
    "PRG": "Praha",
    "VIE": "Vídeň",
    "FRA": "Frankfurt",
    "NUE": "Norimberk",
    "MXP": "Milán",
    "FCO": "Řím",
    "BUD": "Budapešť",
    "AMS": "Amsterdam",
    "FMM": "Memmingen",
    "HND": "Tokio",
    "NRT": "Tokio",
    "KIX": "Osaka",
    "ITM": "Osaka",
    "NGO": "Nagoja",
    "FUK": "Fukuoka",
    # City kódy (Duffel je někdy vrací na úrovni slice) – jen pro zobrazení.
    "OSA": "Osaka (město)",
    "TYO": "Tokio (město)",
}

# Maximální počty kombinací origin×destination na jeden denní běh
# České zkratky dnů v týdnu (0=po … 6=ne) – sdíleno notifierem i statistikami,
# ať se zobrazení v kalendáři a v reportech nerozejde.
CZECH_WEEKDAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]

RATE_LIMIT_COMBINATIONS = {
    "googleflights": 12,   # scraping Google Flights → šetrně (×2 termíny/běh)
    "duffel":        50,   # Duffel – štědrý test režim, šetříme kvótu
    "amadeus":       20,   # 2 000 req/měsíc → ~66/den, bereme méně pro jistotu
    "skyscrapper":   3,    # RapidAPI free tier 100 req/MĚSÍC → ~3/den!
    "travelpayouts": 100,  # neomezeno, ale rozumná hranice
    "secret_flying": None, # RSS – bez limitu kombinací
    "jacks":         None, # scraping – bez limitu kombinací
    "cestujlevne":   None, # RSS – bez limitu kombinací
}


def trim_airports(origins, destinations, max_combinations):
    """Ořeže seznam letišť od konce tak, aby počet kombinací nepřekročil
    max_combinations. Zachovává pořadí priorit (méně prioritní = na konci).
    """
    if max_combinations is None:
        return list(origins), list(destinations)
    origins = list(origins)
    destinations = list(destinations)
    while (
        len(origins) * len(destinations) > max_combinations
        and (len(origins) > 1 or len(destinations) > 1)
    ):
        # Odstraňuj střídavě z většího seznamu
        if len(origins) >= len(destinations) and len(origins) > 1:
            origins = origins[:-1]
        elif len(destinations) > 1:
            destinations = destinations[:-1]
        else:
            break
    return origins, destinations


def airport_name(code: str) -> str:
    """Vrátí lidské jméno letiště, fallback na samotný IATA kód."""
    return AIRPORT_NAMES.get(code.upper(), code.upper())


def _enabled_airport_codes(airports: list[dict]) -> list[str]:
    """Z agent.json seznamu letišť vrátí kódy zapnutých, seřazené dle priority
    (nižší číslo = přednost; stabilně dle pořadí v souboru)."""
    enabled = [a for a in airports if a.get("enabled", True) and a.get("code")]
    enabled.sort(key=lambda a: a.get("priority", 999))
    return [a["code"] for a in enabled]


def _travel_window_to_search_windows(window: dict) -> list[dict]:
    """Převod travelWindow {from, to} (ISO data) na search_windows
    [{year, months}]. Okno přes přelom roku se ořízne na první rok (scanner
    zatím podporuje jen jedno okno v jednom roce)."""
    try:
        from datetime import date as _date
        d_from = _date.fromisoformat(window["from"])
        d_to = _date.fromisoformat(window["to"])
    except (KeyError, ValueError, TypeError):
        return []
    if d_to < d_from:
        return []
    if d_to.year != d_from.year:
        logger.warning(
            "travelWindow přes přelom roku – ořezávám na rok %s", d_from.year
        )
        d_to = _date(d_from.year, 12, 31)
    months = list(range(d_from.month, d_to.month + 1))
    return [{"year": d_from.year, "months": months}]


def apply_agent_config(routes_config: dict, agent: dict) -> dict:
    """Promítne config/agent.json do routes_config (agent.json má přednost).
    Externalizuje letiště, prahy, okno a délku pobytu z kódu/yaml do configu
    editovatelného přes dashboard."""
    if agent.get("europeAirports"):
        codes = _enabled_airport_codes(agent["europeAirports"])
        if codes:
            routes_config["european_airports"] = codes
    if agent.get("japanAirports"):
        codes = _enabled_airport_codes(agent["japanAirports"])
        if codes:
            routes_config["japanese_airports"] = codes
    thresholds = agent.get("alertThresholds", {})
    if thresholds.get("dealMaxEur") is not None:
        routes_config["price_threshold_eur"] = thresholds["dealMaxEur"]
    stay = agent.get("stayLength", {})
    if stay.get("minNights") is not None and stay.get("maxNights") is not None:
        routes_config["stay_length"] = {
            "min_nights": stay["minNights"], "max_nights": stay["maxNights"],
        }
    windows = _travel_window_to_search_windows(agent.get("travelWindow", {}))
    if windows:
        routes_config["search_windows"] = windows
    # Doplň lidská jména letišť pro notifikace/exporty.
    for a in agent.get("europeAirports", []) + agent.get("japanAirports", []):
        if a.get("code") and a.get("name"):
            AIRPORT_NAMES.setdefault(a["code"], a["name"])
    for code, info in agent.get("cityAliases", {}).items():
        if info.get("name"):
            AIRPORT_NAMES.setdefault(code, info["name"])
    return routes_config


def _parse_json_env(name: str) -> dict:
    """Načte env proměnnou jako JSON objekt; při chybě/prázdnu vrátí {}."""
    import json
    raw = os.getenv(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning("%s není platný JSON – ignoruji", name)
        return {}


@dataclass
class Settings:
    """Konfigurace načtená z prostředí (.env) a routes.yaml."""

    # Sekrety / credentials
    duffel_token: str | None = None
    rapidapi_key: str | None = None
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_env: str = "test"
    travelpayouts_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Chování
    price_threshold_eur: float = 550.0
    log_level: str = "INFO"
    czk_eur_rate: float = 25.0
    # Volitelný JSON endpoint Miles & More mileage bargains (pokud se objeví);
    # bez něj se scrapuje HTML stránka.
    milesandmore_api_url: str | None = None
    # Veřejný x-api-key webového frontendu M&M (lze přepsat); None = vestavěný.
    milesandmore_api_key: str | None = None
    # Opt-in: ignorovat robots.txt u Miles & More (vědomé rozhodnutí uživatele
    # pro osobní měsíční monitoring). Výchozí False = robots.txt se ctí.
    milesandmore_ignore_robots: bool = False
    # Volitelné HTTP hlavičky pro Miles & More jako JSON řetězec, typicky
    # {"Cookie": "..."} z přihlášené prohlížečové relace pro průchod anti-botem.
    milesandmore_headers: dict = field(default_factory=dict)

    # routes.yaml
    routes_config: dict[str, Any] = field(default_factory=dict)
    # config/agent.json (editovatelný přes dashboard, čte se při každém běhu)
    agent_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, routes_path: str | Path = "config/routes.yaml",
             agent_path: str | Path = "config/agent.json") -> "Settings":
        routes_config: dict[str, Any] = {}
        path = Path(routes_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                routes_config = yaml.safe_load(fh) or {}
        else:
            logger.warning("routes.yaml nenalezen na %s", path)

        agent_config: dict[str, Any] = {}
        apath = Path(agent_path)
        if apath.exists():
            import json
            try:
                with open(apath, "r", encoding="utf-8") as fh:
                    agent_config = json.load(fh) or {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Nelze načíst %s: %s – pokračuji bez něj", apath, exc)
        if agent_config:
            routes_config = apply_agent_config(routes_config, agent_config)

        threshold = float(
            os.getenv("PRICE_THRESHOLD_EUR")
            or routes_config.get("price_threshold_eur", 550)
        )

        return cls(
            duffel_token=os.getenv("DUFFEL_TOKEN"),
            rapidapi_key=os.getenv("RAPIDAPI_KEY"),
            amadeus_client_id=os.getenv("AMADEUS_CLIENT_ID"),
            amadeus_client_secret=os.getenv("AMADEUS_CLIENT_SECRET"),
            amadeus_env=os.getenv("AMADEUS_ENV", "test"),
            travelpayouts_token=os.getenv("TRAVELPAYOUTS_TOKEN"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            price_threshold_eur=threshold,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            czk_eur_rate=float(os.getenv("CZK_EUR_RATE", "25")),
            milesandmore_api_url=os.getenv("MILESANDMORE_API_URL"),
            milesandmore_api_key=os.getenv("MILESANDMORE_API_KEY"),
            milesandmore_ignore_robots=(
                os.getenv("MILESANDMORE_IGNORE_ROBOTS", "false").lower()
                in ("1", "true", "yes")
            ),
            milesandmore_headers=_parse_json_env("MILESANDMORE_HEADERS"),
            routes_config=routes_config,
            agent_config=agent_config,
        )

    # -- toggle zdrojů / alertů z config/agent.json ------------------------
    def source_enabled(self, name: str) -> bool:
        """Zapnutí API zdroje dle agent.json (duffel/skyScrapper/amadeus/
        travelpayouts). Chybějící klíč = zapnuto (zpětná kompatibilita)."""
        sources = self.agent_config.get("sources", {})
        value = sources.get(name)
        return True if value is None else bool(value)

    def rss_enabled(self, name: str) -> bool:
        """Zapnutí RSS/scraping zdroje (secretFlying/cestujlevne/jacks/
        milesAndMore) dle agent.json."""
        rss = self.agent_config.get("sources", {}).get("rss", {})
        value = rss.get(name)
        return True if value is None else bool(value)

    def telegram_alert_enabled(self, kind: str) -> bool:
        """Zapnutí typu Telegram zprávy (priceAlert/dealAlert/dailySummary)."""
        alerts = self.agent_config.get("telegramAlerts", {})
        value = alerts.get(kind)
        return True if value is None else bool(value)

    # -- pomocné gettery z routes.yaml ------------------------------------
    @property
    def european_airports(self) -> list[str]:
        return self.routes_config.get("european_airports", EUROPEAN_AIRPORTS)

    @property
    def japanese_airports(self) -> list[str]:
        return self.routes_config.get("japanese_airports", JAPANESE_AIRPORTS)

    @property
    def routes(self) -> list[dict[str, Any]]:
        return self.routes_config.get("routes", [])

    @property
    def search_windows(self) -> list[dict[str, Any]]:
        return self.routes_config.get("search_windows", [])

    @property
    def stay_length(self) -> dict[str, int]:
        return self.routes_config.get(
            "stay_length", {"min_nights": 12, "max_nights": 25}
        )

    def resolve_airport_list(self, value) -> list[str]:
        """Přeloží 'all_european' / 'all_japanese' nebo vrátí explicitní seznam."""
        if value == "all_european":
            return list(self.european_airports)
        if value == "all_japanese":
            return list(self.japanese_airports)
        if isinstance(value, list):
            return list(value)
        return []
