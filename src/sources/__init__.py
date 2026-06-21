"""Datové zdroje pro vyhledávání letenek (vrstva 1 API + vrstva 2 RSS/scraping).

Definuje sdílené datové struktury používané všemi zdroji.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Segment:
    """Jeden letový úsek v itineráři (např. MXP→BCN nebo BCN→PVG)."""

    origin: str
    destination: str
    airline: str = ""               # IATA kód aerolinky
    duration_min: Optional[int] = None  # doba letu v minutách (bez čekání)
    depart_at: Optional[str] = None     # čas odletu ("HH:MM" nebo ISO datetime)
    arrive_at: Optional[str] = None     # čas příletu
    layover_min: Optional[int] = None   # čekání na TOMTO letišti před tímto úsekem


@dataclass
class FlightResult:
    """Jeden konkrétní nalezený let (vrstva 1 – real-time API)."""

    price: float
    currency: str = "EUR"
    origin: str = ""              # IATA odletového letiště (outbound origin)
    destination: str = ""         # IATA cílového letiště (outbound destination)
    return_origin: str = ""       # IATA odletu zpět (pro open-jaw)
    return_destination: str = ""  # IATA příletu zpět
    depart_date: Optional[date] = None
    return_date: Optional[date] = None
    airlines: list[str] = field(default_factory=list)
    source: str = ""              # duffel / skyscrapper / amadeus / travelpayouts
    deep_link: str = ""           # přímý odkaz na koupi
    route_name: str = ""          # jméno trasy z routes.yaml
    segments_out: list[Segment] = field(default_factory=list)  # úseky tam
    segments_in: list[Segment] = field(default_factory=list)   # úseky zpět
    duration_out_min: Optional[int] = None  # celková doba cesty tam (let + čekání)
    duration_in_min: Optional[int] = None   # celková doba cesty zpět
    scanned_price: Optional[float] = None   # původní cena před korekcí z URL
    stops_out: Optional[int] = None  # počet přestupů tam (0 = přímý)
    stops_in: Optional[int] = None   # počet přestupů zpět (0 = přímý)

    @property
    def nights(self) -> Optional[int]:
        if self.depart_date and self.return_date:
            return (self.return_date - self.depart_date).days
        return None

    def route_key(self) -> str:
        """Klíč pro historii cen, např. 'MUC-KIX-roundtrip'."""
        if self.return_origin and self.return_origin != self.destination:
            return f"{self.origin}-{self.destination}-{self.return_origin}-openjaw"
        return f"{self.origin}-{self.destination}-roundtrip"


@dataclass
class DealResult:
    """Kurátorský deal z RSS / scrapingu (vrstva 2 – cena neověřená)."""

    title: str
    link: str
    source: str = ""
    price_eur: Optional[float] = None
    published: Optional[date] = None
    summary: str = ""
