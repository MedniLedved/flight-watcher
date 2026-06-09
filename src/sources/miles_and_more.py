"""Miles & More – Mileage Bargains (vrstva 2, měsíční kontrola award nabídek).

Award nabídky („Meilenschnäppchen") placené MÍLEMI (ne EUR) se mění JEDNOU
MĚSÍČNĚ – proto se tento zdroj kontroluje jen první kalendářní den v měsíci
(gating řeší scanner přes should_run_today()).

Zdroj získává data ze STRUKTUROVANÉHO GraphQL endpointu, který používá samotný
web Miles & More:

  POST https://api.miles-and-more.com/content/v3/offers/search
  hlavička x-api-key (veřejný klíč webového frontendu)
  tělo = GraphQL dotaz na nabídky (offers.air.*)

Odpověď obsahuje pro každou leteckou nabídku: destinationIata/Name, originList
(originIata/Name/CountryIso), promoMiles/regularMiles a cestovní období. Z toho
se filtrují nabídky s cílem v JAPONSKU a původem v EVROPĚ.

Endpoint i klíč lze přepsat přes MILESANDMORE_API_URL / MILESANDMORE_API_KEY.
Volitelné hlavičky (Cookie z přihlášené relace) přes MILESANDMORE_HEADERS, pokud
by endpoint vyžadoval session. Když vše selže, zkusí se fallback scraping HTML
stránky. Žádná chyba NEZASTAVÍ celý scan.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

import requests

from . import DealResult
from .jacks import _robots_allows
from .secret_flying import JAPAN_KEYWORDS

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.miles-and-more.com/de/en/spend/flights.html"
DEFAULT_API_URL = "https://api.miles-and-more.com/content/v3/offers/search"
# Veřejný klíč webového frontendu M&M (není to tajemství – posílá ho prohlížeč).
DEFAULT_API_KEY = "agGBZmuTGwFXWzVDg8ckGKGBytemE1nS"
SITE_BASE = "https://www.miles-and-more.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Japonská letiště pro detekci cílové destinace.
JAPAN_IATA = {"HND", "NRT", "KIX", "ITM", "NGO", "FUK", "CTS", "OKA", "KOJ", "HIJ"}
# Evropské země (ISO 3166-1 alpha-2) pro detekci původu.
EUROPEAN_ISO = {
    "DE", "CZ", "AT", "FR", "IT", "NL", "CH", "BE", "PL", "HU", "SK", "ES",
    "PT", "DK", "SE", "NO", "FI", "IE", "GB", "LU", "SI", "HR", "RO", "BG",
    "GR", "EE", "LV", "LT",
}

# GraphQL dotaz zachycený z webu M&M (offers.air.*).
_GRAPHQL_QUERY = """
query Offers($airQuery: AirQuery, $allowSamePartner: Boolean, $channelTag: String!,
  $context: Context, $country: String, $isAirOffer: Boolean!, $language: String,
  $offerType: [String], $orderBy: Order, $placementTags: [String],
  $random: Boolean, $totalAmount: Int) {
  offers(air: $airQuery, allowSamePartner: $allowSamePartner, channelTag: $channelTag,
    context: $context, country: $country, language: $language, orderBy: $orderBy,
    offerTypes: $offerType, placementTags: $placementTags, random: $random,
    totalAmount: $totalAmount) {
    offers {
      aemId
      air @include(if: $isAirOffer) {
        destinationIata
        destinationName
        originList { originIata originName originCountryIso originCountryName }
        promoMiles
        regularMiles
        travelPeriodStart
        travelPeriodEnd
      }
      heading
      miles
      url
      partner { name }
    }
  }
}
"""

_GRAPHQL_VARIABLES = {
    "allowSamePartner": True,
    "offerType": ["airoffer"],
    "orderBy": {"field": "MILES_PRICE", "direction": "ASC"},
    "placementTags": ["Premium"],
    "random": False,
    "totalAmount": 1000,
    "airQuery": {"localPreferredAirline": "", "preferredCountry": "DE"},
    "channelTag": "WEB",
    "context": {"language": "en", "site": "de"},
    "country": "web:system/countries/de",
    "language": "en",
    "isAirOffer": True,
}

_MILES_RE = re.compile(r"([\d][\d.,\s]*\d)\s*(?:miles|meilen)", re.IGNORECASE)


def should_run_today(today: Optional[date] = None) -> bool:
    """True jen první kalendářní den v měsíci."""
    today = today or date.today()
    return today.day == 1


def _matches_japan(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in JAPAN_KEYWORDS)


def _is_japan_destination(iata: str, name: str) -> bool:
    if iata and iata.upper() in JAPAN_IATA:
        return True
    return _matches_japan(name or "")


def _european_origins(origin_list: list[dict]) -> list[dict]:
    """Vrátí jen evropské původy (dle ISO země nebo evropského letiště)."""
    out = []
    for og in origin_list or []:
        iso = (og.get("originCountryIso") or "").upper()
        if iso in EUROPEAN_ISO:
            out.append(og)
    return out


def _extract_miles(text: str) -> Optional[int]:
    m = _MILES_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"[.,\s]", "", m.group(1))
    try:
        return int(digits)
    except ValueError:
        return None


def _walk_json(node) -> list[str]:
    """Rekurzivně posbírá textové řetězce z libovolné JSON struktury."""
    out: list[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out += _walk_json(v)
    elif isinstance(node, list):
        for v in node:
            out += _walk_json(v)
    elif isinstance(node, str):
        out.append(node)
    return out


class MilesAndMoreSource:
    name = "miles_and_more"

    def __init__(self, page_url: str = PAGE_URL,
                 api_url: Optional[str] = None,
                 api_key: Optional[str] = None,
                 ignore_robots: bool = False,
                 extra_headers: Optional[dict[str, str]] = None,
                 session: Optional[requests.Session] = None):
        self.page_url = page_url
        self.api_url = api_url or DEFAULT_API_URL
        self.api_key = api_key or DEFAULT_API_KEY
        self.ignore_robots = ignore_robots
        # Volitelné hlavičky (typicky Cookie z přihlášené relace) pro případ,
        # že by endpoint vyžadoval session / průchod anti-botem.
        self.extra_headers = extra_headers or {}
        self.session = session or requests.Session()

    def fetch(self) -> list[DealResult]:
        """Vrátí Europe→Japonsko mileage bargains. Primárně z GraphQL endpointu,
        s fallbackem na scraping HTML. Prázdný seznam = žádná odpovídající
        nabídka; výjimka = tvrdá chyba (scanner ji zachytí)."""
        try:
            deals = self._fetch_from_graphql()
            logger.info("Miles & More: %d nabídek z GraphQL endpointu", len(deals))
            return deals
        except Exception as exc:  # noqa: BLE001 – zkus fallback na HTML
            logger.warning("Miles & More GraphQL selhal (%s) – zkouším HTML", exc)
            return self._fetch_from_html()

    # -- primární: GraphQL endpoint --------------------------------------
    def _fetch_from_graphql(self) -> list[DealResult]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "Origin": SITE_BASE,
            "Referer": SITE_BASE + "/",
            **self.extra_headers,
        }
        body = {"query": _GRAPHQL_QUERY, "variables": _GRAPHQL_VARIABLES}
        resp = self.session.post(self.api_url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            logger.warning("Miles & More GraphQL errors: %s", payload["errors"])
        offers = (
            payload.get("data", {}).get("offers", {}).get("offers", [])
        )
        return self._deals_from_air_offers(offers)

    def _deals_from_air_offers(self, offers: list[dict]) -> list[DealResult]:
        deals: list[DealResult] = []
        for offer in offers or []:
            air = offer.get("air") or {}
            dest_iata = air.get("destinationIata", "")
            dest_name = air.get("destinationName", "")
            if not _is_japan_destination(dest_iata, dest_name):
                continue
            origins = air.get("originList") or []
            eu_origins = _european_origins(origins)
            # Chceme Europe→Japonsko: pokud jsou původy známé, vyžaduj evropský.
            if origins and not eu_origins:
                continue
            deals.append(self._build_air_deal(offer, air, eu_origins or origins))
        return deals

    def _build_air_deal(self, offer: dict, air: dict,
                        origins: list[dict]) -> DealResult:
        dest = air.get("destinationName") or air.get("destinationIata") or "Japonsko"
        dest_iata = air.get("destinationIata", "")
        miles = air.get("promoMiles") or offer.get("miles") or air.get("regularMiles")
        regular = air.get("regularMiles")

        origin_codes = [
            og.get("originIata") for og in origins if og.get("originIata")
        ]
        origins_str = ", ".join(dict.fromkeys(origin_codes)) or "Evropa"

        title = f"{origins_str} → {dest}"
        if dest_iata:
            title += f" ({dest_iata})"

        summary_parts = []
        if miles:
            sm = f"{int(miles):,} mil".replace(",", " ")
            if regular and regular != miles:
                sm += f" (běžně {int(regular):,} mil)".replace(",", " ")
            summary_parts.append(sm)
        period = self._format_period(
            air.get("travelPeriodStart"), air.get("travelPeriodEnd")
        )
        if period:
            summary_parts.append(f"období {period}")
        summary = "Award nabídka – " + ", ".join(summary_parts) if summary_parts \
            else "Award nabídka (mileage bargain)"

        url = offer.get("url") or ""
        if url.startswith("/"):
            url = SITE_BASE + url
        if not url:
            url = self.page_url

        return DealResult(
            title=title,
            link=url,
            source="miles-and-more.com",
            price_eur=None,            # platí se MÍLEMI, ne EUR
            published=date.today(),
            summary=summary,
        )

    @staticmethod
    def _format_period(start: Optional[str], end: Optional[str]) -> str:
        def fmt(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value[:10]).date().strftime("%d.%m.%Y")
            except (ValueError, TypeError):
                return value
        s, e = fmt(start), fmt(end)
        if s and e:
            return f"{s}–{e}"
        return s or e or ""

    # -- fallback: scraping HTML -----------------------------------------
    def _fetch_from_html(self) -> list[DealResult]:
        if not self.ignore_robots and not _robots_allows(self.page_url, USER_AGENT):
            logger.warning(
                "robots.txt zakazuje scraping %s – přeskakuji "
                "(lze povolit přes MILESANDMORE_IGNORE_ROBOTS=true)",
                self.page_url)
            return []

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            **self.extra_headers,
        }
        try:
            resp = self.session.get(self.page_url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Miles & More scraping selhal: %s", exc)
            raise RuntimeError(f"Miles & More scraping selhal: {exc}") from exc

        from bs4 import BeautifulSoup  # lazy import – volitelná závislost
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:  # noqa: BLE001 – fallback parser
            soup = BeautifulSoup(resp.text, "html.parser")

        deals: list[DealResult] = []
        seen: set[str] = set()
        for node in soup.find_all(["article", "li", "div", "h2", "h3"]):
            text = node.get_text(" ", strip=True)
            if not text or len(text) > 400 or not _matches_japan(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            deals.append(DealResult(
                title=text[:200],
                link=self.page_url,
                source="miles-and-more.com",
                price_eur=None,
                published=date.today(),
                summary="Award nabídka (mileage bargain)",
            ))
        if not deals:
            logger.info("Miles & More: žádná Europe→Japonsko nabídka nenalezena.")
        return deals
