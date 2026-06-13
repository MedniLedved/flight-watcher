"""Post-processing enrichment: pro nabídky pod cenovým prahem s Aviasales URL
parsuje booking token a bere data z URL jako autoritativní zdroj.

Token formát (Aviasales `t` parametr):
  {carrier_2}{depart_unix_10}{arrive_unix_10}{duration_8}{airports_3n}
  příklad: CA17899197001790090700002430MXPBCNPVGKIX
    → carrier=CA, total_duration=2430 min, airports=[MXP, BCN, PVG, KIX]

Co dostaneme z URL (bez HTTP requestu):
  ✓ expected_price + currency  → přepíše nascanovanou cenu
  ✓ hlavní carrier             → doplní/přepíše airlines
  ✓ sekvence letišť            → segmenty (origin/destination per úsek)
  ✓ celková doba cesty (min)   → duration_out_min
  ✗ délky jednotlivých úseků  → Segment.duration_min zůstane None
  ✗ časy odletu/příletu        → Segment.depart_at/arrive_at zůstanou None
  ✗ čekání na přestupech       → Segment.layover_min zůstane None
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from .sources import FlightResult, Segment

logger = logging.getLogger(__name__)

_AVIASALES_RE = re.compile(r"aviasales\.com", re.IGNORECASE)
# carrier(2) + depart_unix(10) + arrive_unix(10) + duration_min(6) + airports(3n)
_TOKEN_PART_RE = re.compile(r"^([A-Z]{2})(\d{10})(\d{10})(\d{6})([A-Z]{6,})$")


def parse_aviasales_url(url: str) -> dict | None:
    """Vrátí slovník s daty z Aviasales booking URL, nebo None.

    Vrací:
      flight_parts: list[dict]  – každá část tokenu (může být víc pro multi-leg)
        carrier: str
        total_duration_min: int
        airports: list[str]     – sekvence letišť [origin, stop1…, dest]
      expected_price: float | None
      expected_price_currency: str | None  – "EUR" apod.
    """
    if not _AVIASALES_RE.search(url):
        return None
    try:
        qs = parse_qs(urlparse(url).query)
        token_raw = qs.get("t", [""])[0]
        if not token_raw:
            return None

        flight_parts = []
        for part in token_raw.split("_"):
            m = _TOKEN_PART_RE.match(part)
            if not m:
                continue
            airports_str = m.group(5)
            if len(airports_str) % 3 != 0:
                continue
            flight_parts.append({
                "carrier": m.group(1),
                "total_duration_min": int(m.group(4)),
                "airports": [airports_str[i:i + 3] for i in range(0, len(airports_str), 3)],
            })

        if not flight_parts:
            return None

        expected_price = None
        expected_currency = None
        price_str = qs.get("expected_price", [None])[0]
        currency_str = qs.get("expected_price_currency", [None])[0]
        if price_str:
            try:
                expected_price = float(price_str)
                expected_currency = (currency_str or "eur").upper()
            except (ValueError, TypeError):
                pass

        return {
            "flight_parts": flight_parts,
            "expected_price": expected_price,
            "expected_price_currency": expected_currency,
        }
    except Exception:
        return None


def _segments_from_part(fp: dict) -> list[Segment]:
    """Sestaví Segment objekty z jedné části tokenu."""
    airports = fp["airports"]
    if len(airports) < 2:
        return []
    carrier = fp["carrier"]
    return [
        Segment(origin=airports[i], destination=airports[i + 1], airline=carrier)
        for i in range(len(airports) - 1)
    ]


def enrich_results(
    results: list[FlightResult],
    deal_max_eur: float,
    per_origin_thresholds: dict[str, float] | None = None,
) -> list[FlightResult]:
    """Pro výsledky s cenou ≤ deal_max_eur a Aviasales URL v deep_link:
    parsuje token a aplikuje data jako autoritativní (přepíše cenu, airlines,
    doplní segmenty a celkovou dobu cesty).
    ``per_origin_thresholds``: per-EU-letiště efektivní prahy; přepíše deal_max_eur
    pro letiště, která mají transport config (dealMaxEur − doprava).
    """
    for f in results:
        eff_max = (per_origin_thresholds or {}).get(f.origin or "", deal_max_eur)
        if f.price > eff_max:
            continue
        if not f.deep_link or not _AVIASALES_RE.search(f.deep_link):
            continue

        data = parse_aviasales_url(f.deep_link)
        if not data:
            continue

        parts = data["flight_parts"]
        expected_price = data["expected_price"]

        # -- Korekce ceny -------------------------------------------------
        if expected_price is not None and expected_price != f.price:
            logger.info(
                "URL price correction %s: %.0f → %.0f EUR (source: %s, url)",
                f.route_key(), f.price, expected_price, f.source,
            )
            f.scanned_price = f.price
            f.price = expected_price

        # -- Segmenty (outbound = první token part) -----------------------
        if not f.segments_out and parts:
            f.segments_out = _segments_from_part(parts[0])
            f.duration_out_min = parts[0]["total_duration_min"] or None

        # -- Return leg (druhý token part, pokud existuje) ----------------
        if not f.segments_in and len(parts) > 1:
            f.segments_in = _segments_from_part(parts[1])
            f.duration_in_min = parts[1]["total_duration_min"] or None

        # -- Airlines: sloučit z tokenu + původní list --------------------
        token_carriers = list({p["carrier"] for p in parts})
        merged = sorted(set(f.airlines) | set(token_carriers))
        if merged != f.airlines:
            f.airlines = merged

    return results
