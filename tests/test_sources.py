"""Testy pro datové zdroje a pomocné funkce konfigurace."""
from __future__ import annotations

from datetime import date

from src.airport_stats import format_airport_stats, rank_airports
from src.config import RATE_LIMIT_COMBINATIONS, trim_airports
from src.history import PriceHistory
from src.sources import FlightResult
from src.sources.cestujlevne import CestujLevneSource
from src.sources.duffel import DuffelSource
from src.sources.secret_flying import _extract_price, _matches
from src.sources.skyscrapper import SkyScrapperSource
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


def test_skyscrapper_has_tightest_limit():
    # Free tier 100 req/měsíc → nejnižší limit kombinací ze všech zdrojů.
    assert RATE_LIMIT_COMBINATIONS["skyscrapper"] < RATE_LIMIT_COMBINATIONS["amadeus"]
    assert RATE_LIMIT_COMBINATIONS["skyscrapper"] < RATE_LIMIT_COMBINATIONS["duffel"]


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


# -- Duffel parsing --------------------------------------------------------
def test_duffel_parse_offer():
    src = DuffelSource(token="dummy")
    offer = {
        "total_amount": "489.00",
        "total_currency": "EUR",
        "owner": {"iata_code": "LH"},
        "slices": [
            {
                "origin": {"iata_code": "MUC"},
                "destination": {"iata_code": "KIX"},
                "segments": [
                    {"departing_at": "2026-09-12T08:00:00",
                     "marketing_carrier": {"iata_code": "LH"}},
                ],
            },
            {
                "origin": {"iata_code": "NRT"},
                "destination": {"iata_code": "PRG"},
                "segments": [
                    {"departing_at": "2026-10-07T10:00:00",
                     "marketing_carrier": {"iata_code": "NH"}},
                ],
            },
        ],
    }
    r = src._parse_offer(offer, "MUC", "KIX", "Test")
    assert r.price == 489.0
    assert r.origin == "MUC"
    assert r.destination == "KIX"
    assert r.return_origin == "NRT"
    assert r.return_destination == "PRG"
    assert r.depart_date == date(2026, 9, 12)
    assert r.return_date == date(2026, 10, 7)
    assert "LH" in r.airlines and "NH" in r.airlines


def test_duffel_parse_offer_missing_price_returns_none():
    src = DuffelSource(token="dummy")
    assert src._parse_offer({"slices": []}, "MUC", "KIX", "Test") is None


# -- Sky Scrapper parsing --------------------------------------------------
def test_skyscrapper_parse_itinerary(tmp_path):
    src = SkyScrapperSource(rapidapi_key="dummy",
                            cache_path=tmp_path / "airports.json")
    it = {
        "price": {"raw": 512.5, "formatted": "€513"},
        "legs": [
            {
                "origin": {"displayCode": "FRA"},
                "destination": {"displayCode": "NRT"},
                "departure": "2026-09-10T09:00:00",
                "carriers": {"marketing": [{"name": "ANA", "alternateId": "NH"}]},
            },
            {
                "origin": {"displayCode": "NRT"},
                "destination": {"displayCode": "FRA"},
                "departure": "2026-09-24T11:00:00",
                "carriers": {"marketing": [{"name": "ANA", "alternateId": "NH"}]},
            },
        ],
    }
    r = src._parse_itinerary(it, "FRA", "NRT", "Test")
    assert r.price == 512.5
    assert r.origin == "FRA"
    assert r.destination == "NRT"
    assert r.depart_date == date(2026, 9, 10)
    assert r.return_date == date(2026, 9, 24)
    assert "NH" in r.airlines


def test_skyscrapper_airport_cache_roundtrip(tmp_path):
    cache = tmp_path / "airports.json"
    src = SkyScrapperSource(rapidapi_key="dummy", cache_path=cache)
    src._airports["FRA"] = {"skyId": "FRA", "entityId": "95673383"}
    src._save_airport_cache()
    src2 = SkyScrapperSource(rapidapi_key="dummy", cache_path=cache)
    # Z cache → resolve_airport nevolá síť (request_count zůstane 0).
    assert src2.resolve_airport("FRA") == {"skyId": "FRA", "entityId": "95673383"}
    assert src2.request_count == 0


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


# -- Dynamická priorita letišť dle cen -------------------------------------
def test_airport_stats_aggregation(tmp_path):
    h = PriceHistory(path=tmp_path / "h.json")
    # MUC vychází levně, FRA draho.
    h.record("MUC-KIX-roundtrip", 400, "duffel")
    h.record("MUC-KIX-roundtrip", 420, "duffel")
    h.record("MUC-NRT-roundtrip", 440, "duffel")
    h.record("FRA-KIX-roundtrip", 700, "duffel")
    h.record("FRA-NRT-roundtrip", 720, "duffel")
    h.record("FRA-HND-roundtrip", 740, "duffel")
    stats = h.airport_stats()
    assert stats["MUC"]["count"] == 3
    assert stats["MUC"]["avg"] < stats["FRA"]["avg"]
    assert stats["MUC"]["min"] == 400


def test_airport_stats_openjaw_key(tmp_path):
    h = PriceHistory(path=tmp_path / "h.json")
    h.record("MUC-KIX-NRT-openjaw", 500, "duffel")
    stats = h.airport_stats()
    # Všechna tři letiště se započtou.
    assert set(["MUC", "KIX", "NRT"]).issubset(stats.keys())


def test_rank_airports_cheap_first():
    airports = ["FRA", "MUC", "VIE"]
    stats = {
        "FRA": {"count": 5, "avg": 700},
        "MUC": {"count": 5, "avg": 400},
        "VIE": {"count": 5, "avg": 550},
    }
    assert rank_airports(airports, stats) == ["MUC", "VIE", "FRA"]


def test_rank_airports_insufficient_data_keeps_order():
    airports = ["FRA", "MUC", "VIE"]
    # MUC má dost dat (levné), ostatní málo → MUC dopředu, zbytek původní pořadí.
    stats = {
        "MUC": {"count": 5, "avg": 400},
        "FRA": {"count": 1, "avg": 300},  # málo dat, nepředbíhá
        "VIE": {"count": 2, "avg": 350},
    }
    assert rank_airports(airports, stats, min_samples=3) == ["MUC", "FRA", "VIE"]


def test_format_airport_stats_markers():
    airports = ["MUC", "VIE", "FRA"]
    stats = {
        "MUC": {"count": 5, "avg": 400, "min": 380, "median": 400},
        "VIE": {"count": 5, "avg": 550, "min": 500, "median": 550},
        "FRA": {"count": 5, "avg": 700, "min": 650, "median": 700},
    }
    lines = format_airport_stats(airports, stats)
    assert lines[0].startswith("💚")   # nejlevnější = MUC
    assert "MUC" in lines[0]
    assert lines[-1].startswith("💸")  # nejdražší = FRA
    assert "FRA" in lines[-1]


def test_format_airport_stats_no_data_section():
    airports = ["MUC", "XXX"]
    stats = {"MUC": {"count": 5, "avg": 400, "min": 380, "median": 400}}
    lines = format_airport_stats(airports, stats)
    assert any("Bez dostatku dat" in ln and "XXX" in ln for ln in lines)
