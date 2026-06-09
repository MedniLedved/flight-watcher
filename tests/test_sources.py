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


def test_duffel_prefers_segment_airports_over_city_slice():
    # Duffel na slice vrací city kódy (OSA = Osaka). Konkrétní letiště
    # musí přijít ze segmentů, jinak se rozbijí statistiky letišť.
    src = DuffelSource(token="dummy")
    offer = {
        "total_amount": "500.00",
        "total_currency": "EUR",
        "slices": [
            {
                "origin": {"iata_code": "NUE"},
                "destination": {"iata_code": "OSA"},
                "segments": [{
                    "departing_at": "2026-09-01T08:00:00",
                    "origin": {"iata_code": "NUE"},
                    "destination": {"iata_code": "KIX"},
                }],
            },
            {
                "origin": {"iata_code": "OSA"},
                "destination": {"iata_code": "NUE"},
                "segments": [{
                    "departing_at": "2026-09-13T10:00:00",
                    "origin": {"iata_code": "KIX"},
                    "destination": {"iata_code": "NUE"},
                }],
            },
        ],
    }
    r = src._parse_offer(offer, "NUE", "KIX", "Test")
    assert r.destination == "KIX"      # ne city kód OSA
    assert r.return_origin == "KIX"
    assert r.deep_link.startswith("https://www.google.com/travel/flights")
    assert "2026-09-01" in r.deep_link


# -- Rotace termínů hledání --------------------------------------------------
def test_pick_scan_dates_within_window():
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    pairs = Scanner._pick_scan_dates(
        date(2026, 9, 1), date(2026, 12, 31), stay,
        samples=3, today=date(2026, 6, 9),
    )
    assert pairs
    for dep, ret in pairs:
        assert date(2026, 9, 1) <= dep <= date(2026, 12, 31)
        assert dep < ret <= date(2026, 12, 31)
        assert 12 <= (ret - dep).days <= 25


def test_pick_scan_dates_rotates_daily():
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    a = Scanner._pick_scan_dates(date(2026, 9, 1), date(2026, 12, 31), stay,
                                 samples=2, today=date(2026, 6, 9))
    b = Scanner._pick_scan_dates(date(2026, 9, 1), date(2026, 12, 31), stay,
                                 samples=2, today=date(2026, 6, 10))
    assert a != b  # každý den jiné termíny → postupné pokrytí okna


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


# -- Dynamická priorita letišť dle podílu dealů ----------------------------
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


def test_airport_stats_deal_rate(tmp_path):
    h = PriceHistory(path=tmp_path / "h.json")
    # MUC: 2 ze 3 pod prahem 550. FRA: 0 ze 3.
    h.record("MUC-KIX-roundtrip", 400, "duffel")
    h.record("MUC-KIX-roundtrip", 500, "duffel")
    h.record("MUC-NRT-roundtrip", 600, "duffel")
    h.record("FRA-KIX-roundtrip", 700, "duffel")
    h.record("FRA-NRT-roundtrip", 720, "duffel")
    h.record("FRA-HND-roundtrip", 740, "duffel")
    stats = h.airport_stats(threshold=550)
    assert stats["MUC"]["deals"] == 2
    assert stats["MUC"]["deal_rate"] == 2 / 3
    assert stats["MUC"]["deal_median"] == 450  # medián z [400, 500]
    assert stats["FRA"]["deals"] == 0
    assert stats["FRA"]["deal_rate"] == 0
    assert stats["FRA"]["deal_median"] is None


def test_airport_stats_openjaw_key(tmp_path):
    h = PriceHistory(path=tmp_path / "h.json")
    h.record("MUC-KIX-NRT-openjaw", 500, "duffel")
    stats = h.airport_stats()
    # Všechna tři letiště se započtou.
    assert set(["MUC", "KIX", "NRT"]).issubset(stats.keys())


def test_rank_airports_most_deals_first():
    # Klíčový test: VIE má NEJVYŠŠÍ průměr, ale nejvíc dealů → musí být první.
    airports = ["FRA", "MUC", "VIE"]
    stats = {
        "FRA": {"count": 10, "avg": 600, "median": 600,
                "deal_rate": 0.1, "deal_median": 540},
        "MUC": {"count": 10, "avg": 450, "median": 450,
                "deal_rate": 0.3, "deal_median": 480},
        "VIE": {"count": 10, "avg": 700, "median": 700,
                "deal_rate": 0.5, "deal_median": 500},
    }
    assert rank_airports(airports, stats) == ["VIE", "MUC", "FRA"]


def test_rank_airports_tiebreak_by_deal_median():
    # Stejný deal_rate → rozhoduje levnější medián dealu.
    airports = ["A", "B"]
    stats = {
        "A": {"count": 10, "deal_rate": 0.3, "deal_median": 520, "median": 600},
        "B": {"count": 10, "deal_rate": 0.3, "deal_median": 470, "median": 600},
    }
    assert rank_airports(airports, stats) == ["B", "A"]


def test_rank_airports_no_deals_fallback_median():
    # Žádné letiště nemá dealy → řadí dle celkového mediánu (levnější dřív).
    airports = ["FRA", "MUC"]
    stats = {
        "FRA": {"count": 10, "deal_rate": 0.0, "deal_median": None, "median": 700},
        "MUC": {"count": 10, "deal_rate": 0.0, "deal_median": None, "median": 600},
    }
    assert rank_airports(airports, stats) == ["MUC", "FRA"]


def test_rank_airports_insufficient_data_keeps_order():
    airports = ["FRA", "MUC", "VIE"]
    # MUC má dost dat, ostatní málo → MUC dopředu, zbytek původní pořadí.
    stats = {
        "MUC": {"count": 5, "deal_rate": 0.4, "deal_median": 480, "median": 500},
        "FRA": {"count": 1, "deal_rate": 1.0, "deal_median": 300, "median": 300},
        "VIE": {"count": 2, "deal_rate": 0.5, "deal_median": 350, "median": 350},
    }
    assert rank_airports(airports, stats, min_samples=3) == ["MUC", "FRA", "VIE"]


def test_format_airport_stats_markers():
    airports = ["MUC", "VIE", "FRA"]
    stats = {
        "MUC": {"count": 10, "avg": 450, "min": 380, "median": 450,
                "deals": 5, "deal_rate": 0.5, "deal_median": 420},
        "VIE": {"count": 10, "avg": 550, "min": 500, "median": 550,
                "deals": 3, "deal_rate": 0.3, "deal_median": 510},
        "FRA": {"count": 10, "avg": 700, "min": 650, "median": 700,
                "deals": 1, "deal_rate": 0.1, "deal_median": 540},
    }
    lines = format_airport_stats(airports, stats)
    assert lines[0].startswith("💚")   # nejvíc dealů = MUC
    assert "MUC" in lines[0]
    assert "50 %" in lines[0]
    assert lines[-1].startswith("💸")  # nejmíň dealů = FRA
    assert "FRA" in lines[-1]


def test_format_airport_stats_no_data_section():
    airports = ["MUC", "XXX"]
    stats = {"MUC": {"count": 5, "avg": 400, "min": 380, "median": 400,
                     "deals": 2, "deal_rate": 0.4, "deal_median": 390}}
    lines = format_airport_stats(airports, stats)
    assert any("Bez dostatku dat" in ln and "XXX" in ln for ln in lines)


# -- Miles & More mileage bargains -----------------------------------------
def test_mm_should_run_only_first_of_month():
    from datetime import date as _d
    from src.sources.miles_and_more import should_run_today
    assert should_run_today(_d(2026, 9, 1))
    assert not should_run_today(_d(2026, 9, 2))
    assert not should_run_today(_d(2026, 9, 30))


def test_mm_extract_miles():
    from src.sources.miles_and_more import _extract_miles
    assert _extract_miles("Tokyo for 35,000 miles return") == 35000
    assert _extract_miles("Osaka ab 42.000 Meilen") == 42000
    assert _extract_miles("no price here") is None


def test_mm_matches_japan():
    from src.sources.miles_and_more import _matches_japan
    assert _matches_japan("Europe to Tokyo award special")
    assert _matches_japan("Nach Osaka mit 50% Rabatt")
    assert not _matches_japan("Europe to New York deal")


def _air_offer(dest_iata, dest_name, origin_iata, origin_iso, promo, regular):
    return {
        "air": {
            "destinationIata": dest_iata,
            "destinationName": dest_name,
            "originList": [{
                "originIata": origin_iata,
                "originName": origin_iata,
                "originCountryIso": origin_iso,
                "originCountryName": origin_iso,
            }],
            "promoMiles": promo,
            "regularMiles": regular,
            "travelPeriodStart": "2026-09-01",
            "travelPeriodEnd": "2026-12-15",
        },
        "heading": f"{dest_name} special",
        "miles": promo,
        "url": "/de/en/offer/x.html",
    }


def test_mm_air_offers_keeps_japan_from_europe():
    from src.sources.miles_and_more import MilesAndMoreSource
    src = MilesAndMoreSource()
    offers = [
        _air_offer("HND", "Tokyo", "FRA", "DE", 55000, 80000),   # ✓ EU→JP
        _air_offer("JFK", "New York", "MUC", "DE", 30000, 50000),  # ✗ ne Japonsko
        _air_offer("KIX", "Osaka", "JFK", "US", 60000, 90000),     # ✗ původ mimo EU
    ]
    deals = src._deals_from_air_offers(offers)
    assert len(deals) == 1
    d = deals[0]
    assert d.source == "miles-and-more.com"
    assert d.price_eur is None
    assert "Tokyo" in d.title and "HND" in d.title and "FRA" in d.title
    assert "55 000 mil" in d.summary
    assert "01.09.2026" in d.summary
    assert d.link.startswith("https://www.miles-and-more.com/")


def test_mm_air_offers_japan_by_iata_without_name():
    from src.sources.miles_and_more import MilesAndMoreSource
    src = MilesAndMoreSource()
    offers = [_air_offer("NGO", "", "VIE", "AT", 50000, 70000)]
    deals = src._deals_from_air_offers(offers)
    assert len(deals) == 1
    assert "NGO" in deals[0].title


def test_mm_air_offers_empty_origins_allowed():
    from src.sources.miles_and_more import MilesAndMoreSource
    src = MilesAndMoreSource()
    offer = _air_offer("HND", "Tokyo", "FRA", "DE", 55000, 80000)
    offer["air"]["originList"] = []  # neznámý původ → nevylučujeme
    deals = src._deals_from_air_offers([offer])
    assert len(deals) == 1
    assert deals[0].source == "miles-and-more.com"


# -- weekday_stats ---------------------------------------------------------
def test_weekday_stats_best_day_has_highest_deal_rate():
    from src.history import PriceHistory
    from datetime import date
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        h = PriceHistory(path)
        # Monday (2026-06-08 = Monday) – 3 cheap flights
        for price in [300, 310, 290]:
            h.record("FRA-NRT-roundtrip", price, "duffel",
                     depart_date=date(2026, 6, 8), return_date=date(2026, 6, 22))
        # Wednesday (2026-06-10 = Wednesday) – 1 cheap, 2 expensive
        h.record("FRA-NRT-roundtrip", 310, "duffel",
                 depart_date=date(2026, 6, 10), return_date=date(2026, 6, 24))
        h.record("FRA-NRT-roundtrip", 600, "duffel",
                 depart_date=date(2026, 6, 10), return_date=date(2026, 6, 24))
        h.record("FRA-NRT-roundtrip", 700, "duffel",
                 depart_date=date(2026, 6, 10), return_date=date(2026, 6, 24))

        stats = h.weekday_stats(threshold=500)
        dep = stats["depart"]
        # Monday (0) should have deal_rate 1.0, Wednesday (2) should have 1/3
        assert dep[0]["deal_rate"] == 1.0
        assert abs(dep[2]["deal_rate"] - 1/3) < 0.01
    finally:
        os.unlink(path)


def test_format_weekday_stats_returns_lines_with_best_day():
    from src.airport_stats import format_weekday_stats
    stats = {
        "depart": {
            0: {"deal_rate": 1.0, "count": 5, "deals": 5, "deal_median": 300.0, "all_median": 300.0},
            2: {"deal_rate": 0.3, "count": 5, "deals": 2, "deal_median": 350.0, "all_median": 600.0},
            4: {"deal_rate": 0.2, "count": 5, "deals": 1, "deal_median": 380.0, "all_median": 700.0},
        },
        "return": {},
    }
    lines = format_weekday_stats(stats)
    assert any("PO" in l for l in lines)  # best day Monday shown uppercased
    assert any("+50" in l for l in lines)  # Wednesday 350 vs Monday 300 = +50
    assert any("+80" in l for l in lines)  # Friday 380 vs Monday 300 = +80
