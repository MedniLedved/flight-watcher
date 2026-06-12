"""Cestujlevně.cz RSS (vrstva 2 – kurátorské dealy v češtině).

Feed: https://www.cestujlevne.com/feed
Parsuje se přes feedparser. Filtruje na japonské destinace (české i anglické
varianty). Ceny v Kč se převedou na EUR fixním kurzem (výchozí 25 CZK/EUR).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Optional

import requests

from . import DealResult
from .http_utils import make_scraper_session

logger = logging.getLogger(__name__)

FEED_URL = "https://www.cestujlevne.com/feed"

JAPAN_KEYWORDS = [
    "japonsk", "japon", "tokio", "tokyo", "osaka", "kjóto", "kjoto", "kyoto",
    "nagoja", "nagoya", "fukuoka", "japan",
]

_CZK_RE = re.compile(r"(\d[\d\s]*)\s*Kč", re.IGNORECASE)
_EUR_RE = re.compile(r"€\s?(\d+)|(\d+)\s?€")


def _entry_date(entry) -> Optional[date]:
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc).date()
    return None


def _matches(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in JAPAN_KEYWORDS)


class CestujLevneSource:
    name = "cestujlevne"

    def __init__(self, feed_url: str = FEED_URL, czk_eur_rate: float = 25.0,
                 session: Optional[requests.Session] = None):
        self.feed_url = feed_url
        self.czk_eur_rate = czk_eur_rate
        self.session = session or make_scraper_session()

    def _extract_price_eur(self, text: str) -> Optional[float]:
        eur_match = _EUR_RE.search(text)
        if eur_match:
            for g in eur_match.groups():
                if g:
                    return float(g)
        czk_match = _CZK_RE.search(text)
        if czk_match:
            digits = czk_match.group(1).replace(" ", "")
            try:
                return round(float(digits) / self.czk_eur_rate, 0)
            except (ValueError, ZeroDivisionError):
                return None
        return None

    def fetch(self, max_age_days: int = 2) -> list[DealResult]:
        import feedparser  # lazy import – volitelná závislost

        # Fetch manually so feedparser uses our browser UA instead of its own
        # default (which servers often block with a 403 or redirect to a CAPTCHA).
        try:
            resp = self.session.get(
                self.feed_url,
                headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Cestujlevně feed se nepodařilo stáhnout: %s", exc)
            raise RuntimeError("Cestujlevně feed nedostupný") from exc

        feed = feedparser.parse(resp.content)
        if getattr(feed, "bozo", 0) and not feed.entries:
            logger.error("Cestujlevně feed se nepodařilo načíst: %s",
                         getattr(feed, "bozo_exception", "neznámá chyba"))
            raise RuntimeError("Cestujlevně feed nedostupný")

        deals: list[DealResult] = []
        today = date.today()
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            blob = f"{title} {summary}"
            if not _matches(blob):
                continue
            published = _entry_date(entry)
            if published and (today - published).days > max_age_days:
                continue
            deals.append(DealResult(
                title=title,
                link=getattr(entry, "link", ""),
                source="cestujlevne.com",
                price_eur=self._extract_price_eur(blob),
                published=published,
                summary=summary[:300],
            ))
        return deals
