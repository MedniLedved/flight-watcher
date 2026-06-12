"""Shared HTTP utilities — UA rotation, session factory, random delays.

Every scan run picks a fresh randomised User-Agent so that consecutive GitHub
Actions runs don't present a perfectly stable browser fingerprint.  Scraping
sessions also clear their cookie jar after each response to prevent any
tracking cookies from persisting across requests.

Rules enforced here:
- Accept-Language is always "en-US,en;q=0.9" — never the system locale or
  "cs-CZ", which would hint at geolocation and could trigger price localisation.
- No cookie persistence between runs (each Source creates a fresh session).
- Random jitter on every sleep so request timing is not perfectly predictable.
"""
from __future__ import annotations

import random
import time

import requests

# Desktop browser UAs — Chrome/Firefox on Windows/macOS/Linux.
# Rotated once per scanner run (a new session is created per Source.__init__).
USER_AGENTS: list[str] = [
    # Chrome 124 – Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 – macOS 14
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox 125 – Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox 124 – macOS 14
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Chrome 123 – Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Edge 124 – Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_SCRAPER_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_API_HEADERS: dict[str, str] = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def random_ua() -> str:
    """Return a random realistic desktop browser User-Agent string."""
    return random.choice(USER_AGENTS)


def make_scraper_session() -> requests.Session:
    """Create a stateless scraping session with a randomised browser UA.

    A response hook clears the cookie jar after every response so that no
    tracking cookies accumulate across requests within a single scan run.
    """
    session = requests.Session()
    session.headers.update({**_SCRAPER_HEADERS, "User-Agent": random_ua()})

    def _clear_cookies(response, *args, **kwargs):  # noqa: ARG001
        session.cookies.clear()

    session.hooks["response"].append(_clear_cookies)
    return session


def make_api_session() -> requests.Session:
    """Create a session for authenticated API calls with a realistic UA.

    API calls don't need full browser headers, but a neutral Accept-Language
    and a real-looking UA are still good hygiene.
    """
    session = requests.Session()
    session.headers.update({**_API_HEADERS, "User-Agent": random_ua()})
    return session


def random_sleep(base: float, jitter_ratio: float = 0.5) -> None:
    """Sleep for *base* seconds plus a random fraction of *base*.

    Jitter prevents perfectly predictable inter-request intervals that could
    be a bot-detection signal (e.g. exactly 1.000 s every time).
    """
    time.sleep(base + random.uniform(0.0, base * jitter_ratio))
