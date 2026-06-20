"""Sdílené parsování Skyscanner-formátu itinerářů.

Skyscanner data (price.raw + legs[] s carriers/segments) vrací VÍCE zdrojů:
* Sky Scrapper přes RapidAPI (`sky-scrapper.p.rapidapi.com`)
* FlightLabs / goflightlabs (`retrieveFlights`)

Tvar odpovědi je u obou identický (oba proxují Skyscanner). Aby se parsovací
logika neduplikovala (a oprava jednoho tvaru se nemusela dělat dvakrát),
žije zde jako sdílené funkce. Při změně tvaru Skyscanner odpovědi uprav JEN
tento soubor – oba zdroje se aktualizují naráz.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from . import FlightResult, Segment
from .google_flights import google_flights_url

logger = logging.getLogger(__name__)


def itineraries_from_payload(payload: dict) -> list[dict]:
    """Vytáhne seznam itinerářů z payloadu nezávisle na obalu.

    RapidAPI sky-scrapper vrací `{"data": {"itineraries": [...]}}`, goflightlabs
    `retrieveFlights` vrací `{"itineraries": [...]}` (bez `data` obalu, někdy
    s `context`). Zvládni oba tvary."""
    if not isinstance(payload, dict):
        return []
    inner = payload.get("data")
    if isinstance(inner, dict) and "itineraries" in inner:
        node = inner
    else:
        node = payload
    items = node.get("itineraries", [])
    return items if isinstance(items, list) else []


def format_skyscanner_dt(dt: dict | str) -> Optional[str]:
    """Převede Skyscanner datetime strukturu nebo ISO string na 'HH:MM'."""
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt).strftime("%H:%M")
        except ValueError:
            return None
    if isinstance(dt, dict):
        h = dt.get("hour") or dt.get("hours")
        m = dt.get("minute") or dt.get("minutes")
        if h is not None and m is not None:
            return f"{int(h):02d}:{int(m):02d}"
    return None


def extract_segments(leg: dict) -> list[Segment]:
    """Extrahuje Segment objekty z leg.segments[] (Skyscanner). Prázdný seznam
    pokud leg nemá sub-segmenty."""
    raw_segs = leg.get("segments", []) if leg else []
    if not raw_segs:
        return []
    result = []
    for s in raw_segs:
        orig = (s.get("origin") or {}).get("displayCode", "")
        dest = (s.get("destination") or {}).get("displayCode", "")
        carrier = (
            (s.get("marketingCarrier") or {}).get("alternateId", "")
            or (s.get("operatingCarrier") or {}).get("alternateId", "")
        )
        duration = s.get("durationInMinutes")
        depart_at = format_skyscanner_dt(s.get("departureDateTime") or {})
        arrive_at = format_skyscanner_dt(s.get("arrivalDateTime") or {})
        result.append(Segment(
            origin=orig,
            destination=dest,
            airline=carrier,
            duration_min=duration,
            depart_at=depart_at,
            arrive_at=arrive_at,
        ))
    return result


def leg_iata(leg: dict, key: str, fallback: str) -> str:
    node = leg.get(key, {}) if leg else {}
    if isinstance(node, dict):
        return node.get("displayCode") or node.get("id") or fallback
    return fallback


def leg_date(leg: dict) -> Optional[date]:
    value = leg.get("departure", "") if leg else ""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def parse_itinerary(it: dict, origin: str, destination: str, route_name: str,
                    source_name: str) -> Optional[FlightResult]:
    """Skyscanner itinerář → FlightResult. Společné pro skyscrapper i flightlabs.

    Deep link Skyscanner přímo nevrací → fallback na ověřovací Google Flights
    odkaz (stejně jako u skyscrapperu)."""
    price_obj = it.get("price", {}) if isinstance(it, dict) else {}
    raw = price_obj.get("raw")
    if raw is None:
        return None
    try:
        price = float(raw)
    except (ValueError, TypeError):
        return None

    legs = it.get("legs", [])
    out_leg = legs[0] if legs else {}
    in_leg = legs[1] if len(legs) > 1 else {}

    airlines: set[str] = set()
    for leg in (out_leg, in_leg):
        carriers = leg.get("carriers", {}).get("marketing", []) if leg else []
        for c in carriers:
            code = c.get("alternateId") or c.get("name")
            if code:
                airlines.add(code)

    o_code = leg_iata(out_leg, "origin", origin)
    d_code = leg_iata(out_leg, "destination", destination)
    r_o = leg_iata(in_leg, "origin", "") if in_leg else ""
    r_d = leg_iata(in_leg, "destination", "") if in_leg else ""
    depart_dt = leg_date(out_leg)
    return_dt = leg_date(in_leg) if in_leg else None

    return FlightResult(
        price=price,
        currency="EUR",
        origin=o_code,
        destination=d_code,
        return_origin=r_o,
        return_destination=r_d,
        depart_date=depart_dt,
        return_date=return_dt,
        airlines=sorted(airlines),
        source=source_name,
        deep_link=google_flights_url(o_code, d_code, depart_dt, return_dt, r_o, r_d),
        route_name=route_name,
        segments_out=extract_segments(out_leg),
        segments_in=extract_segments(in_leg) if in_leg else [],
        duration_out_min=out_leg.get("durationInMinutes") if out_leg else None,
        duration_in_min=in_leg.get("durationInMinutes") if in_leg else None,
    )
