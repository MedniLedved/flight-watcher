"""Jack's Flight Club (vrstva 2 – scraping veřejné stránky dealů).

Jack's Flight Club NEMÁ veřejné RSS. Tento modul scrapuje veřejně dostupné
(ne-premium) dealy z https://jacksflightclub.com/eu/flights pomocí requests +
BeautifulSoup.

UPOZORNĚNÍ: Scraping je křehký – struktura stránky se může změnit, případně
ji blokuje robots.txt nebo anti-bot ochrana. Pokud scraping selže, vrací se
prázdný seznam a chyba se zaloguje; CELÝ scan se NEZASTAVÍ. Viz README,
sekce Troubleshooting.
"""
from __future__ import annotations

import logging
import re
import urllib.robotparser
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import requests

from . import DealResult
from .http_utils import make_scraper_session
from .secret_flying import JAPAN_KEYWORDS

logger = logging.getLogger(__name__)

DEALS_URL = "https://jacksflightclub.com/eu/flights"


def _robots_allows(url: str, user_agent: str) -> bool:
    """Ověří robots.txt. Při chybě (nedostupné) konzervativně povolí."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001 – robots nedostupný, nezastavujeme
        logger.warning("Nelze přečíst robots.txt (%s): %s", robots_url, exc)
        return True
    return rp.can_fetch(user_agent, url)


def _matches(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in JAPAN_KEYWORDS)


class JacksFlightClubSource:
    name = "jacks"

    def __init__(self, deals_url: str = DEALS_URL,
                 session: Optional[requests.Session] = None):
        self.deals_url = deals_url
        self.session = session or make_scraper_session()
        # UA used for both robots.txt check and the actual request.
        self._ua: str = self.session.headers.get("User-Agent", "")

    def fetch(self) -> list[DealResult]:
        if not _robots_allows(self.deals_url, self._ua):
            logger.warning("robots.txt zakazuje scraping %s – přeskakuji",
                           self.deals_url)
            return []

        try:
            resp = self.session.get(self.deals_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Jack's Flight Club scraping selhal: %s", exc)
            raise RuntimeError(f"Jack's scraping selhal: {exc}") from exc

        from bs4 import BeautifulSoup  # lazy import – volitelná závislost
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:  # noqa: BLE001 – fallback parser
            soup = BeautifulSoup(resp.text, "html.parser")

        deals: list[DealResult] = []
        # Heuristika: hledáme nadpisy/odkazy zmiňující dealy. Struktura se
        # může změnit – proto je to best-effort placeholder.
        candidates = soup.find_all(["article", "h2", "h3", "a"])
        seen_links: set[str] = set()
        for node in candidates:
            text = node.get_text(" ", strip=True)
            if not text or not _matches(text):
                continue
            link = node.get("href") if node.name == "a" else None
            if not link:
                anchor = node.find("a", href=True)
                link = anchor["href"] if anchor else self.deals_url
            if link and link.startswith("/"):
                parsed = urlparse(self.deals_url)
                link = f"{parsed.scheme}://{parsed.netloc}{link}"
            if link in seen_links:
                continue
            seen_links.add(link)
            deals.append(DealResult(
                title=text[:200],
                link=link or self.deals_url,
                source="jacksflightclub.com",
                price_eur=_extract_eur(text),
                published=date.today(),
                summary="",
            ))
        if not deals:
            logger.info("Jack's: žádné odpovídající veřejné dealy nenalezeny "
                        "(může jít o změnu struktury stránky).")
        return deals


_EUR_RE = re.compile(r"€\s?(\d+)|from\s+\$\s?(\d+)", re.IGNORECASE)


def _extract_eur(text: str) -> Optional[float]:
    m = _EUR_RE.search(text)
    if not m:
        return None
    for g in m.groups():
        if g:
            return float(g)
    return None
