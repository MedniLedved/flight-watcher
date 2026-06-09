"""Miles & More – Mileage Bargains (vrstva 2, měsíční kontrola award nabídek).

Stránka: https://www.miles-and-more.com/de/en/spend/flights.html#mileagebargains

Tzv. „Meilenschnäppchen" (mileage bargains) jsou award nabídky placené
MÍLEMI (ne EUR), které se mění JEDNOU MĚSÍČNĚ. Proto se tento zdroj kontroluje
jen první kalendářní den v měsíci (gating řeší scanner přes should_run_today()).

⚠️ Stránka je JavaScriptová SPA s anti-bot ochranou a NEMÁ veřejné API.
Scraping je proto best-effort:
  - zkouší najít nabídky v embedded JSON (<script>) i ve viditelném HTML,
  - respektuje robots.txt a posílá prohlížečovou hlavičku User-Agent,
  - když selže (403 / změna struktury / anti-bot), jen zaloguje chybu a
    NEZASTAVÍ celý scan (stejně jako Jack's Flight Club).

Volitelně lze přes MILESANDMORE_API_URL nasměrovat na skutečný datový
endpoint (pokud ho objevíš), který vrací JSON – pak se použije místo HTML.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

import requests

from . import DealResult
from .jacks import _robots_allows
from .secret_flying import JAPAN_KEYWORDS

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.miles-and-more.com/de/en/spend/flights.html"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Mílová cena, např. "35,000 miles" / "35.000 Meilen".
_MILES_RE = re.compile(r"([\d][\d.,\s]*\d)\s*(?:miles|meilen)", re.IGNORECASE)


def should_run_today(today: Optional[date] = None) -> bool:
    """True jen první kalendářní den v měsíci."""
    today = today or date.today()
    return today.day == 1


def _matches_japan(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in JAPAN_KEYWORDS)


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
                 ignore_robots: bool = False,
                 session: Optional[requests.Session] = None):
        self.page_url = page_url
        self.api_url = api_url
        # ignore_robots: vědomý opt-in uživatele pro monitorování této konkrétní
        # veřejné stránky (osobní, 1×měsíčně). Výchozí False = robots.txt se ctí.
        self.ignore_robots = ignore_robots
        self.session = session or requests.Session()

    def fetch(self) -> list[DealResult]:
        """Vrátí nalezené Europe→Japonsko mileage bargains. Prázdný seznam,
        pokud žádná nabídka neodpovídá; vyhazuje výjimku při chybě sítě/struktury
        (scanner ji zachytí a označí zdroj jako nefunkční)."""
        if self.api_url:
            return self._fetch_from_api()
        return self._fetch_from_html()

    # -- varianta přes JSON API (pokud je nakonfigurován) -----------------
    def _fetch_from_api(self) -> list[DealResult]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            resp = self.session.get(self.api_url, headers=headers, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("Miles & More API selhalo: %s", exc)
            raise RuntimeError(f"Miles & More API selhalo: {exc}") from exc
        return self._deals_from_offers(payload)

    def _deals_from_offers(self, payload) -> list[DealResult]:
        deals: list[DealResult] = []
        offers = payload if isinstance(payload, list) else payload.get("offers", payload)
        if isinstance(offers, dict):
            offers = offers.get("items", [])
        for offer in offers or []:
            text = " ".join(_walk_json(offer))
            if not _matches_japan(text):
                continue
            deals.append(self._build_deal(text, self.page_url))
        return deals

    # -- varianta přes scraping HTML --------------------------------------
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

        # 1) Embedded JSON ve <script> tazích (SPA stav / JSON-LD).
        for script in soup.find_all("script"):
            raw = script.string or script.get_text() or ""
            raw = raw.strip()
            if not raw or "{" not in raw:
                continue
            for blob in self._json_candidates(raw):
                try:
                    data = json.loads(blob)
                except (json.JSONDecodeError, ValueError):
                    continue
                for text in _walk_json(data):
                    if _matches_japan(text) and text not in seen:
                        seen.add(text)
                        deals.append(self._build_deal(text, self.page_url))

        # 2) Viditelné HTML karty (fallback, pokud SPA vyrenderovala obsah).
        for node in soup.find_all(["article", "li", "div", "h2", "h3"]):
            text = node.get_text(" ", strip=True)
            if not text or len(text) > 400 or not _matches_japan(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            deals.append(self._build_deal(text, self.page_url))

        if not deals:
            logger.info("Miles & More: žádná Europe→Japonsko nabídka nenalezena "
                        "(SPA/anti-bot mohly zabránit načtení obsahu).")
        return deals

    @staticmethod
    def _json_candidates(raw: str) -> list[str]:
        """Z textu skriptu vytáhne kandidáty na JSON objekt/pole."""
        candidates: list[str] = []
        # Celý skript jako JSON (typicky JSON-LD).
        if raw[0] in "{[":
            candidates.append(raw)
        # window.__STATE__ = {...}; apod.
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", raw, re.DOTALL)
        if m:
            candidates.append(m.group(1))
        return candidates

    def _build_deal(self, text: str, link: str) -> DealResult:
        miles = _extract_miles(text)
        title = text[:200]
        if miles:
            summary = f"Award nabídka cca {miles:,} mil".replace(",", " ")
        else:
            summary = "Award nabídka (mileage bargain)"
        return DealResult(
            title=title,
            link=link,
            source="miles-and-more.com",
            price_eur=None,           # platí se MÍLEMI, ne EUR
            published=date.today(),
            summary=summary,
        )
