"""Testy pro datové zdroje a pomocné funkce konfigurace."""
from __future__ import annotations

from datetime import date

from src.config import RATE_LIMIT_COMBINATIONS, trim_airports
from src.sources import FlightResult
from src.sources.cestujlevne import CestujLevneSource
from src.sources.kiwi import KiwiSource
from src.sources.secret_flying import _extract_price, _matches
from src.sources.travelpayouts import TravelpayoutsSource


# -- trim_airports ---------------------------------------------------------
def test_trim_airports_no_limit_returns_all():
    o = ["A", "B", "C"]
    d = ["X", "Y"]
    assert trim_airports(o, d, None) == (o, d)


def test_trim_airports_respects_limit():
    origins = ["MUC", "PRG", "VIE", "FRA", "NUE"]
    dests = ["HND", "NRT", "KIX", "ITM", "NGO"]
    o, d = trim_airports(origins, dests, 9)
    assert len(o) * len(d) <= 9
    # Zachovává prioritu (od začátku).
    assert o[0] == "MUC"
    assert d[0] == "HND"


def test_trim_airports_never_empties():
    o, d = trim_airports(["A", "B"], ["X", "Y"], 1)
    assert len(o) >= 1 and len(d) >= 1


def test_amadeus_limit_smaller_than_kiwi():
    assert RATE_LIMIT_COMBINATIONS["amadeus"] < RATE_LIMIT_COMBINATIONS["kiwi"]


# -- FlightResult ----------------------------------------------------------
def test_flightresult_nights():
    f = FlightResult(price=500, depart_date=date(2026, 9, 1),
                     return_date=date(2026, 9, 15))
    assert f.nights == 14


def test_flightresult_route_key_roundtrip():
    f = FlightResult(price=500, origin="FRA", destination="NRT",
                     return_origin="NRT")
    assert f.route_key() == "FRA-NRT-roundtrip"


def test_flightresult_route_key_openjaw():
    f = FlightResult(price=500, origin="MUC", destination="KIX",
                     return_origin="NRT", return_destination="PRG")
    assert f.route_key() == "MUC-KIX-NRT-openjaw"


# -- Secret Flying parsing -------------------------------------------------
def test_extract_price_euro():
    assert _extract_price("Flights to Tokyo from €399!") == 399.0


def test_extract_price_dollar():
    assert _extract_price("Japan deal from $450 round trip") == 450.0


def test_extract_price_none():
    assert _extract_price("Amazing Japan flights available now") is None


def test_secret_flying_matches_japan():
    assert _matches("Cheap flights from Frankfurt to Tokyo")
    assert not _matches("Cheap flights to New York")


# -- Cestujlevně CZK→EUR ---------------------------------------------------
def test_cestujlevne_czk_conversion():
    src = CestujLevneSource(czk_eur_rate=25.0)
    assert src._extract_price_eur("Letenky do Japonska za 12500 Kč") == 500.0


def test_cestujlevne_eur_direct():
    src = CestujLevneSource(czk_eur_rate=25.0)
    assert src._extract_price_eur("Tokio za €480") == 480.0


# -- Kiwi parsing ----------------------------------------------------------
def test_kiwi_parse_item():
    src = KiwiSource(api_key="dummy")
    item = {
        "price": 489,
        "flyFrom": "MUC",
        "flyTo": "KIX",
        "deep_link": "https://kiwi.com/deep",
        "local_departure": "2026-09-12T08:00:00.000Z",
        "route": [
            {"airline": "LH", "flyFrom": "MUC", "flyTo": "KIX", "return": 0,
             "local_departure": "2026-09-12T08:00:00.000Z"},
            {"airline": "NH", "flyFrom": "NRT", "flyTo": "PRG", "return": 1,
             "local_departure": "2026-10-07T10:00:00.000Z"},
        ],
    }
    result = src._parse_item(item, "Test")
    assert result.price == 489.0
    assert result.origin == "MUC"
    assert result.destination == "KIX"
    assert result.return_origin == "NRT"
    assert result.return_destination == "PRG"
    assert result.depart_date == date(2026, 9, 12)
    assert "LH" in result.airlines and "NH" in result.airlines


# -- Travelpayouts parsing -------------------------------------------------
def test_travelpayouts_parse_item():
    src = TravelpayoutsSource(token="dummy")
    item = {
        "price": 510, "origin": "FRA", "destination": "NRT",
        "airline": "LH", "departure_at": "2026-09-10T00:00:00Z",
        "return_at": "2026-09-24T00:00:00Z", "link": "/deal/1",
    }
    r = src._parse_item(item, "FRA", "NRT", "Test")
    assert r.price == 510.0
    assert r.depart_date == date(2026, 9, 10)
    assert r.deep_link.startswith("https://")
