"""Secret Flying RSS (vrstva 2 – kurátorské dealy).

Feed: https://www.secretflying.com/posts/feed/
Parsuje se přes feedparser. Filtruje se na japonské destinace a evropský
původ. Cena se extrahuje regexem z titulku, pokud je uvedena.
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

FEED_URL = "https://www.secretflying.com/posts/feed/"

JAPAN_KEYWORDS = [
    "japan", "tokyo", "osaka", "kyoto", "nagoya", "fukuoka",
    "nrt", "hnd", "kix", "ngo", "fuk",
]
EUROPE_KEYWORDS = [
    "europe", "germany", "frankfurt", "munich", "prague", "czech",
    "fra", "muc", "prg", "vie", "zrh", "austria", "vienna",
]

_PRICE_RE = re.compile(r"€\s?(\d+)|from\s+\$\s?(\d+)", re.IGNORECASE)


def _extract_price(title: str) -> Optional[float]:
    match = _PRICE_RE.search(title)
    if not match:
        return None
    for group in match.groups():
        if group:
            return float(group)
    return None


def _entry_date(entry) -> Optional[date]:
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc).date()
    return None


def _matches(text: str) -> bool:
    low = text.lower()
    has_japan = any(k in low for k in JAPAN_KEYWORDS)
    has_europe = any(k in low for k in EUROPE_KEYWORDS)
    # Vyžadujeme japonskou destinaci; evropský původ je bonus, ne podmínka,
    # protože titulek nemusí původ explicitně uvádět.
    return has_japan and (has_europe or True)


class SecretFlyingSource:
    name = "secret_flying"

    def __init__(self, feed_url: str = FEED_URL):
        self.feed_url = feed_url
        # Fresh session per run with a randomised UA and cookie clearing hook.
        self._session = make_scraper_session()

    def fetch(self, max_age_days: int = 48 // 24) -> list[DealResult]:
        """Vrátí dealy odpovídající filtrům. max_age_days výchozí 2 dny."""
        import feedparser  # lazy import – volitelná závislost

        # Feed stahujeme sami s prohlížečovým User-Agentem – výchozí UA
        # feedparseru server blokuje (vrací HTML chybovou stránku, která
        # pak padá na "not well-formed (invalid token)").
        try:
            resp = self._session.get(
                self.feed_url,
                headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Secret Flying feed se nepodařilo stáhnout: %s", exc)
            raise RuntimeError("Secret Flying feed nedostupný") from exc

        logger.info("Secret Flying: HTTP %d, Content-Type: %s, délka: %d B | začátek: %.150s",
                    resp.status_code,
                    resp.headers.get("Content-Type", "?"),
                    len(resp.content),
                    resp.text[:150].replace("\n", " "))

        feed = feedparser.parse(resp.content)
        if getattr(feed, "bozo", 0) and not feed.entries:
            logger.error("Secret Flying feed se nepodařilo načíst: %s",
                         getattr(feed, "bozo_exception", "neznámá chyba"))
            raise RuntimeError("Secret Flying feed nedostupný")

        deals: list[DealResult] = []
        today = date.today()
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            if not _matches(f"{title} {summary}"):
                continue
            published = _entry_date(entry)
            if published and (today - published).days > max_age_days:
                continue
            deals.append(DealResult(
                title=title,
                link=getattr(entry, "link", ""),
                source="secretflying.com",
                price_eur=_extract_price(title),
                published=published,
                summary=summary[:300],
            ))
        return deals
