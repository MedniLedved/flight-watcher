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

    @classmethod
    def load(cls, routes_path: str | Path = "config/routes.yaml") -> "Settings":
        routes_config: dict[str, Any] = {}
        path = Path(routes_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                routes_config = yaml.safe_load(fh) or {}
        else:
            logger.warning("routes.yaml nenalezen na %s", path)

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
        )

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
