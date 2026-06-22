"""Testy pro datové zdroje a pomocné funkce konfigurace."""
from __future__ import annotations

import base64
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from src.airport_stats import format_airport_stats, rank_airports
from src.config import RATE_LIMIT_COMBINATIONS, trim_airports
from src.history import PriceHistory
from src.sources import FlightResult
from src.sources.cestujlevne import CestujLevneSource
from src.sources.duffel import DuffelSource
from src.sources.google_flights import google_flights_url
from src.sources.secret_flying import _extract_price, _matches
from src.sources.skyscrapper import SkyScrapperSource
from src.sources.travelpayouts import TravelpayoutsSource


# -- Google Flights deep link (?tfs= protobuf) -------------------------------
def _decode_tfs(url: str) -> bytes:
    """Vytáhne a dekóduje tfs parametr z URL (base64url bez paddingu)."""
    tfs = parse_qs(urlparse(url).query)["tfs"][0]
    return base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))


def _pb_fields(buf: bytes) -> list[tuple[int, object]]:
    """Mini čtečka protobufu: vrátí [(číslo pole, hodnota)]; length-delimited
    pole vrací jako bytes, varint jako int."""
    out: list[tuple[int, object]] = []
    i = 0

    def _varint() -> int:
        nonlocal i
        val = shift = 0
        while True:
            b = buf[i]
            i += 1
            val |= (b & 0x7F) << shift
            if not b & 0x80:
                return val
            shift += 7

    while i < len(buf):
        tag = _varint()
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            out.append((field, _varint()))
        elif wt == 2:
            ln = _varint()
            out.append((field, buf[i:i + ln]))
            i += ln
        else:
            raise AssertionError(f"nečekaný wire type {wt}")
    return out


def _leg_airports(leg: bytes) -> tuple[bytes, bytes, bytes]:
    """Z FlightData vrátí (datum, odletové letiště, příletové letiště)."""
    fields = _pb_fields(leg)
    day = next(v for f, v in fields if f == 2)
    frm = next(v for f, v in fields if f == 13)
    to = next(v for f, v in fields if f == 14)
    frm_code = next(v for f, v in _pb_fields(frm) if f == 2)
    to_code = next(v for f, v in _pb_fields(to) if f == 2)
    return day, frm_code, to_code


def test_google_flights_url_roundtrip_prefills_search():
    url = google_flights_url("MUC", "KIX", date(2026, 9, 12), date(2026, 10, 7))
    assert url.startswith("https://www.google.com/travel/flights/search?tfs=")
    assert "curr=EUR" in url
    top = _pb_fields(_decode_tfs(url))
    legs = [v for f, v in top if f == 3]
    assert len(legs) == 2
    assert [v for f, v in top if f == 19] == [1]   # trip = ROUND_TRIP
    assert [v for f, v in top if f == 8] == [1]    # 1 dospělý
    assert [v for f, v in top if f == 9] == [1]    # economy
    assert _leg_airports(legs[0]) == (b"2026-09-12", b"MUC", b"KIX")
    # Zpáteční leg zrcadlí letiště.
    assert _leg_airports(legs[1]) == (b"2026-10-07", b"KIX", b"MUC")


def test_google_flights_url_openjaw_is_multicity():
    url = google_flights_url("MUC", "KIX", date(2026, 9, 12), date(2026, 10, 7),
                             return_origin="NRT", return_destination="PRG")
    top = _pb_fields(_decode_tfs(url))
    assert [v for f, v in top if f == 19] == [3]   # trip = MULTI_CITY
    legs = [v for f, v in top if f == 3]
    assert len(legs) == 2
    assert _leg_airports(legs[1]) == (b"2026-10-07", b"NRT", b"PRG")


def test_google_flights_url_oneway_and_missing_data():
    url = google_flights_url("MUC", "KIX", date(2026, 9, 12))
    top = _pb_fields(_decode_tfs(url))
    assert [v for f, v in top if f == 19] == [2]   # trip = ONE_WAY
    assert len([v for f, v in top if f == 3]) == 1
    # Bez povinných údajů žádný odkaz – jinak by Google otevřel jiný termín.
    assert google_flights_url("", "KIX", date(2026, 9, 12)) == ""
    assert google_flights_url("MUC", "", date(2026, 9, 12)) == ""
    assert google_flights_url("MUC", "KIX", None) == ""


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
    # Termín i konkrétní letiště musí být v tfs parametru (předvyplnění).
    decoded = _decode_tfs(r.deep_link)
    assert b"2026-09-01" in decoded and b"KIX" in decoded and b"NUE" in decoded


# -- Plánování termínů (coverage-driven) -------------------------------------
def test_plan_scan_dates_within_window():
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    pairs = Scanner._plan_scan_dates(
        date(2026, 9, 1), date(2026, 12, 31), stay,
        samples=3, today=date(2026, 6, 9),
    )
    assert pairs
    for dep, ret in pairs:
        assert date(2026, 9, 1) <= dep <= date(2026, 12, 31)
        assert dep < ret <= date(2026, 12, 31)
        assert 12 <= (ret - dep).days <= 25


def test_plan_scan_dates_cold_start_fills_uncovered_weekdays():
    """Studený start: druhý vzorek necílí stejný den odletu jako první."""
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    # Prázdné pokrytí → vše má deficit → greedy pokrývá různé dny.
    pairs = Scanner._plan_scan_dates(
        date(2026, 9, 1), date(2026, 12, 31), stay,
        coverage={}, samples=2, today=date(2026, 6, 9),
    )
    assert len(pairs) == 2
    dep_weekdays = {dep.weekday() for dep, _ in pairs}
    assert len(dep_weekdays) == 2  # dva různé dny odletu


def test_plan_scan_dates_exploit_targets_best_weekday():
    """Po studeném startu míří exploit vzorek na nejakčnější den."""
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    # Nasyť pokrytí nad COLD_START_TARGET, aby se vyplo cold-start.
    full = {i: 10.0 for i in range(7)}
    coverage = {"depart_wd": dict(full), "return_wd": dict(full), "airport": {}}
    # Najdi den, kdy aspoň jeden slot není explore (deterministicky dle data).
    saw_best = False
    for d in range(20):
        today = date(2026, 6, 9) + timedelta(days=d)
        pairs = Scanner._plan_scan_dates(
            date(2026, 9, 1), date(2026, 12, 31), stay,
            coverage=coverage, best_depart_wd=2, best_return_wd=4,
            samples=2, today=today,
        )
        if any(dep.weekday() == 2 and ret.weekday() == 4 for dep, ret in pairs):
            saw_best = True
            break
    assert saw_best


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
        "transfers": 0, "return_transfers": 1,
    }
    r = src._parse_item(item, "FRA", "NRT", "Test")
    assert r is not None
    assert r.price == 510.0
    assert r.depart_date == date(2026, 9, 10)
    assert r.return_date == date(2026, 9, 24)
    assert r.deep_link.startswith("https://")
    assert r.stops_out == 0 and r.stops_in == 1  # přímý tam, 1 přestup zpět
    # Roundtrip → nesmí být open-jaw a MUSÍ mít návratové datum.
    assert r.route_key().endswith("-roundtrip")


def test_travelpayouts_drops_one_way_item():
    """One-way nabídka (bez return_at) se NESMÍ uložit jako roundtrip."""
    src = TravelpayoutsSource(token="dummy")
    item = {
        "price": 414, "origin": "AMS", "destination": "NRT",
        "departure_at": "2026-09-06T00:00:00Z", "link": "/deal/2",
        # return_at chybí → jednosměrná
    }
    assert src._parse_item(item, "AMS", "NRT", "Test") is None


def test_travelpayouts_filters_nights_out_of_range():
    """Kombinace mimo min/max nocí se zahodí."""
    src = TravelpayoutsSource(token="dummy")
    item = {
        "price": 400, "origin": "FRA", "destination": "NRT",
        "departure_at": "2026-09-10T00:00:00Z",
        "return_at": "2026-09-13T00:00:00Z",  # jen 3 noci
    }
    assert src._parse_item(item, "FRA", "NRT", "Test",
                           min_nights=12, max_nights=25) is None
    # Uvnitř rozsahu projde.
    item["return_at"] = "2026-09-24T00:00:00Z"  # 14 nocí
    assert src._parse_item(item, "FRA", "NRT", "Test",
                           min_nights=12, max_nights=25) is not None


def test_travelpayouts_search_requests_roundtrip(monkeypatch):
    """search() vždy posílá return_at a one_way=false (ne jednosměrný dotaz)."""
    src = TravelpayoutsSource(token="dummy")
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": []}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured.update(params)
        return _Resp()

    monkeypatch.setattr(src.session, "get", _fake_get)
    src.search("FRA", "NRT", departure_at="2026-09", return_at="2026-09",
               min_nights=12, max_nights=25)
    assert captured["one_way"] == "false"
    assert captured["return_at"] == "2026-09"


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


def test_format_weekday_stats_diffs_against_cheapest_not_most_deals():
    """#4: rozdíly se počítají proti NEJLEVNĚJŠÍMU dni, ne proti dni s nejvíc
    dealy → žádné záporné rozdíly, i když nejvíc dealů má dražší den."""
    from src.airport_stats import format_weekday_stats
    stats = {
        "depart": {
            # Pátek má nejvíc dealů (50 %), ale vyšší medián (480).
            4: {"deal_rate": 0.5, "count": 6, "deals": 3, "deal_median": 480.0, "all_median": 480.0},
            # Úterý má míň dealů (30 %), ale je NEJLEVNĚJŠÍ (450).
            1: {"deal_rate": 0.3, "count": 6, "deals": 2, "deal_median": 450.0, "all_median": 450.0},
        },
        "return": {},
    }
    lines = format_weekday_stats(stats)
    text = "\n".join(lines)
    assert "💰" in text and "ÚT" in text          # úterý = nejlevnější
    assert "nejlevnější" in text
    assert "+30 EUR" in text                       # pátek 480 vs úterý 450
    assert "🏆" in text                            # pátek = nejvíc dealů
    assert "-" not in text.replace("–", "")        # žádný záporný rozdíl


# -- coverage_weights (recency decay) --------------------------------------
def test_coverage_weights_decays_old_observations():
    import tempfile, os
    from src.history import PriceHistory
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        h = PriceHistory(path)
        today = date(2026, 6, 9)
        # Čerstvé pozorování (dnes) vs. staré (60 dní = 2 poločasy → ~0.25).
        h.record("FRA-NRT-roundtrip", 400, "duffel", on_date=today,
                 depart_date=date(2026, 9, 7), return_date=date(2026, 9, 21))
        h.record("FRA-NRT-roundtrip", 400, "duffel",
                 on_date=today - timedelta(days=60),
                 depart_date=date(2026, 9, 8), return_date=date(2026, 9, 22))
        cov = h.coverage_weights(halflife_days=30, today=today)
        # Pondělí (depart 2026-09-07) plná váha ~1.0
        assert abs(cov["depart_wd"][0] - 1.0) < 0.01
        # Úterý (depart 2026-09-08, staré 60 dní) ~0.25
        assert abs(cov["depart_wd"][1] - 0.25) < 0.02
        assert cov["airport"]["FRA"] > cov["depart_wd"][1]  # FRA má obě pozorování
    finally:
        os.unlink(path)


def test_priority_order_puts_undersampled_first():
    from src.airport_stats import priority_order
    airports = ["FRA", "MUC", "PRG"]
    stats = {
        "FRA": {"count": 10, "deal_rate": 0.5, "deal_median": 400, "median": 400, "avg": 500, "min": 380, "deals": 5},
        "MUC": {"count": 10, "deal_rate": 0.1, "deal_median": 450, "median": 450, "avg": 600, "min": 420, "deals": 1},
    }
    cov = {"FRA": 10.0, "MUC": 10.0, "PRG": 0.0}  # PRG neprozkoumané
    order = priority_order(airports, stats, cov, cold_target=3.0)
    assert order[0] == "PRG"  # neprozkoumané dopředu


def test_record_uses_today_as_observation_date_not_flight_date():
    """on_date musí být datum pozorování (dnešek), ne datum letu – jinak je
    recency decay rozbitý (věk = záporný → váha vždy 1.0)."""
    import tempfile, os
    from src.history import PriceHistory
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        h = PriceHistory(path)
        h.record("FRA-NRT-roundtrip", 400, "duffel",
                 depart_date=date(2026, 9, 7), return_date=date(2026, 9, 21))
        rec = h.data["FRA-NRT-roundtrip"]["history"][0]
        # "date" = dnešek (pozorování), depart_date = budoucí let
        assert rec["date"] == date.today().isoformat()
        assert rec["depart_date"] == "2026-09-07"
    finally:
        os.unlink(path)


# -- Review fixes: planner, sanitization, message split, role coverage -------
def test_plan_scan_dates_never_empty_on_narrow_window():
    """#1: i pro úzké okno (žádná platná dvojice v rozsahu nocí) vrátí ≥1 pár."""
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    pairs = Scanner._plan_scan_dates(
        date(2026, 9, 1), date(2026, 12, 31), stay,
        samples=2, today=date(2026, 12, 25),  # start=12-26, min 12 nocí přeteče
    )
    assert pairs  # nesmí být prázdné → scan_route by spadl na date_pairs[0]


def test_history_sanitizes_future_observation_date(tmp_path):
    """#2: budoucí datum letu omylem uložené jako 'date' se ořízne na dnešek,
    jinak by záznam nikdy nevyhasl ani se nepromazal."""
    import json
    from src.history import PriceHistory
    p = tmp_path / "h.json"
    future = (date.today() + timedelta(days=60)).isoformat()
    p.write_text(json.dumps({
        "FRA-NRT-roundtrip": {"history": [{"date": future, "price": 400}]}
    }), encoding="utf-8")
    h = PriceHistory(p)
    rec = h.data["FRA-NRT-roundtrip"]["history"][0]
    assert rec["date"] == date.today().isoformat()


def test_split_message_chunks_long_text():
    """#3: dlouhá zpráva se rozdělí na části pod limitem."""
    from src.notifier import _split_message
    text = "\n".join(f"radek {i}" for i in range(1000))
    parts = _split_message(text, 200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)
    # Rekonstrukce obsahu (po řádcích) musí sedět.
    assert "\n".join(parts).replace("\n", "") == text.replace("\n", "")


def test_explore_fraction_exact_longrun_ratio():
    """#5: floor-trik dá dlouhodobě přesně EXPLORE_FRACTION i pro 0.25."""
    import src.scanner as sc
    import math
    f = 0.25
    n = 1000
    explore_slots = sum(
        1 for slot in range(n)
        if math.floor((slot + 1) * f) - math.floor(slot * f) >= 1
    )
    assert abs(explore_slots / n - f) < 0.01  # ~25 %, ne 20 %


def test_coverage_weights_splits_origin_and_dest_roles():
    """#7: kód na pozici 0 = origin (EU), zbytek dest (JP)."""
    import tempfile, os
    from src.history import PriceHistory
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False); f.close(); os.unlink(f.name)
    h = PriceHistory(f.name)
    h.record("MUC-KIX-roundtrip", 500, "duffel",
             depart_date=date(2026, 9, 7), return_date=date(2026, 9, 21))
    cov = h.coverage_weights(today=date(2026, 6, 9))
    assert "MUC" in cov["origin"] and "MUC" not in cov["dest"]
    assert "KIX" in cov["dest"] and "KIX" not in cov["origin"]
    assert cov["airport"]["MUC"] > 0 and cov["airport"]["KIX"] > 0


# -- Měsíční pokrytí a plánování -----------------------------------------------
def test_coverage_weights_tracks_months():
    """coverage_weights vrací depart_month a return_month."""
    import tempfile, os
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False); f.close(); os.unlink(f.name)
    h = PriceHistory(f.name)
    h.record("MUC-NRT-roundtrip", 500, "duffel",
             depart_date=date(2026, 9, 7), return_date=date(2026, 9, 21))
    cov = h.coverage_weights(today=date(2026, 6, 9))
    assert "depart_month" in cov
    assert "return_month" in cov
    assert cov["depart_month"][9] > 0   # září je pokryté
    assert cov["depart_month"][10] == 0.0  # říjen ne
    assert cov["return_month"][9] > 0


def test_plan_scan_dates_prefers_uncovered_month():
    """Po nasycení září musí algoritmus preferovat říjen/listopad, ne září."""
    from src.scanner import Scanner
    stay = {"min_nights": 12, "max_nights": 25}
    # Nasytíme weekday pokrytí (aby cold-start nehrál roli) a měsíce září a
    # prosinci přidáme vysokou váhu; říjen a listopad zůstanou na 0.
    full_wd = {i: 10.0 for i in range(7)}
    month_cov = {m: 0.0 for m in range(1, 13)}
    month_cov[9] = 10.0   # září prozkoumáno
    month_cov[12] = 10.0  # prosinec prozkoumáno
    coverage = {
        "depart_wd": dict(full_wd),
        "return_wd": dict(full_wd),
        "depart_month": dict(month_cov),
        "return_month": dict(month_cov),
    }
    # Opakuj na různé dny → alespoň jednou musí padnout na říjen nebo listopad.
    hit_uncovered = False
    for d in range(14):
        today = date(2026, 6, 9) + timedelta(days=d)
        pairs = Scanner._plan_scan_dates(
            date(2026, 9, 1), date(2026, 12, 31), stay,
            coverage=coverage, samples=2, today=today,
        )
        if any(dep.month in (10, 11) for dep, _ in pairs):
            hit_uncovered = True
            break
    assert hit_uncovered, "Algoritmus nepřešel na neprozkoumané měsíce (říjen/listopad)"


# -- Duffel 429 retry --------------------------------------------------------
def test_duffel_retries_on_429_then_succeeds(monkeypatch):
    """Duffel po HTTP 429 počká a zkusí znovu (bez reálného čekání)."""
    import requests
    from src.sources import duffel as duffel_mod
    from src.sources.duffel import DuffelSource

    monkeypatch.setattr(duffel_mod.time, "sleep", lambda *a, **k: None)

    calls = {"n": 0}

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {}
        def raise_for_status(self):
            if self.status_code >= 400:
                err = requests.HTTPError(f"{self.status_code}")
                err.response = self
                raise err
        def json(self):
            return {"data": {"offers": []}}

    class _Session:
        def post(self, *a, **k):
            calls["n"] += 1
            return _Resp(429 if calls["n"] < 3 else 200)

    src = DuffelSource(token="dummy", session=_Session())
    out = src.search("MUC", "KIX", date(2026, 9, 1), return_date=date(2026, 9, 13))
    assert out == []          # prázdné nabídky, ale bez výjimky
    assert calls["n"] == 3     # 2× 429, pak úspěch


def test_duffel_raises_after_max_429(monkeypatch):
    """Po vyčerpání pokusů 429 propadne výjimka (trasa se zaloguje jako chyba)."""
    import pytest
    import requests
    from src.sources import duffel as duffel_mod
    from src.sources.duffel import DuffelSource

    monkeypatch.setattr(duffel_mod.time, "sleep", lambda *a, **k: None)

    class _Resp:
        status_code = 429
        headers: dict = {}
        def raise_for_status(self):
            err = requests.HTTPError("429")
            err.response = self
            raise err

    class _Session:
        def post(self, *a, **k):
            return _Resp()

    src = DuffelSource(token="dummy", session=_Session())
    with pytest.raises(requests.HTTPError):
        src.search("MUC", "KIX", date(2026, 9, 1))


# -- Duffel: ochrana proti syntetickým datům a cizí měně ---------------------
def _duffel_offer(price="500.00", currency="EUR"):
    return {
        "total_amount": price,
        "total_currency": currency,
        "owner": {"iata_code": "LH"},
        "slices": [
            {
                "origin": {"iata_code": "MUC"},
                "destination": {"iata_code": "KIX"},
                "segments": [{"departing_at": "2026-09-01T08:00:00",
                              "origin": {"iata_code": "MUC"},
                              "destination": {"iata_code": "KIX"}}],
            },
            {
                "origin": {"iata_code": "KIX"},
                "destination": {"iata_code": "MUC"},
                "segments": [{"departing_at": "2026-09-13T10:00:00",
                              "origin": {"iata_code": "KIX"},
                              "destination": {"iata_code": "MUC"}}],
            },
        ],
    }


class _DuffelFakeSession:
    def __init__(self, payload):
        self._payload = payload

    def post(self, *a, **k):
        payload = self._payload

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": payload}

        return _Resp()


def test_duffel_test_mode_offers_dropped(monkeypatch):
    """live_mode=false (duffel_test_… token) = syntetické ceny → vše zahodit,
    jinak smyšlené ceny otráví historii, alerty i dashboard."""
    from src.sources import duffel as duffel_mod
    monkeypatch.setattr(duffel_mod.time, "sleep", lambda *a, **k: None)
    payload = {"live_mode": False, "offers": [_duffel_offer()]}
    src = DuffelSource(token="duffel_test_x",
                       session=_DuffelFakeSession(payload))
    out = src.search("MUC", "KIX", date(2026, 9, 1),
                     return_date=date(2026, 9, 13))
    assert out == []
    assert src.live_mode is False


class _FakeFx:
    """Falešné kurzy pro testy: rates=None simuluje výpadek ECB API."""

    def __init__(self, rates=None):
        self.rates = rates

    def to_eur(self, amount, currency):
        if currency == "EUR":
            return amount
        if not self.rates or currency not in self.rates:
            return None
        return round(amount / self.rates[currency], 2)


def test_duffel_non_eur_offers_converted_via_ecb_rate(monkeypatch):
    """Ne-EUR nabídka se převede denním kurzem ECB a zůstane ve výsledcích."""
    from src.sources import duffel as duffel_mod
    monkeypatch.setattr(duffel_mod.time, "sleep", lambda *a, **k: None)
    payload = {"live_mode": True, "offers": [
        _duffel_offer(price="540.00", currency="USD"),
        _duffel_offer(price="510.00", currency="EUR"),
    ]}
    src = DuffelSource(token="duffel_live_x",
                       session=_DuffelFakeSession(payload),
                       fx=_FakeFx(rates={"USD": 1.08}))
    out = src.search("MUC", "KIX", date(2026, 9, 1),
                     return_date=date(2026, 9, 13))
    # 540 USD / 1.08 = 500 EUR → levnější než EUR nabídka, řadí se první.
    assert [r.price for r in out] == [500.0, 510.0]
    assert all(r.currency == "EUR" for r in out)


def test_duffel_non_eur_offers_skipped_without_rates(monkeypatch):
    """Bez dostupného kurzu (výpadek ECB API / neznámá měna) se ne-EUR
    nabídka přeskočí – nesmí se vydávat za EUR."""
    from src.sources import duffel as duffel_mod
    monkeypatch.setattr(duffel_mod.time, "sleep", lambda *a, **k: None)
    payload = {"live_mode": True, "offers": [
        _duffel_offer(price="250.00", currency="USD"),
        _duffel_offer(price="510.00", currency="EUR"),
    ]}
    src = DuffelSource(token="duffel_live_x",
                       session=_DuffelFakeSession(payload),
                       fx=_FakeFx(rates=None))
    out = src.search("MUC", "KIX", date(2026, 9, 1),
                     return_date=date(2026, 9, 13))
    assert [r.price for r in out] == [510.0]
    assert all(r.currency == "EUR" for r in out)
    assert src.live_mode is True


# -- FxRates (frankfurter.app / ECB) -----------------------------------------
class _FxFakeSession:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        sess = self

        class _Resp:
            def raise_for_status(self):
                if sess.fail:
                    import requests
                    raise requests.HTTPError("503")

            def json(self):
                return sess.payload

        return _Resp()


def test_fx_rates_convert_to_eur():
    from src.sources.fx import FxRates
    session = _FxFakeSession(payload={"base": "EUR",
                                      "rates": {"USD": 1.08, "GBP": 0.85}})
    fx = FxRates(session=session)
    assert fx.to_eur(108.0, "USD") == 100.0
    assert fx.to_eur(85.0, "GBP") == 100.0
    assert fx.to_eur(500.0, "EUR") == 500.0   # EUR bez fetchee
    assert fx.to_eur(100.0, "XXX") is None    # neznámá měna
    assert session.calls == 1                  # kurzy se stahují jen jednou


def test_fx_rates_fetch_failure_only_once():
    from src.sources.fx import FxRates
    session = _FxFakeSession(fail=True)
    fx = FxRates(session=session)
    assert fx.to_eur(100.0, "USD") is None
    assert fx.to_eur(200.0, "USD") is None
    assert session.calls == 1   # po selhání se už znovu nezkouší (per běh)


# -- Kvóty: auto-vypnutí + spread (#1, #2) ----------------------------------
def test_history_disable_source_auto_expires(tmp_path):
    from datetime import datetime, timedelta
    from src.history import PriceHistory
    h = PriceHistory(tmp_path / "h.json")
    future = datetime.now() + timedelta(days=5)
    h.disable_source("skyscrapper", future)
    assert h.is_source_disabled("skyscrapper") is True
    # Po uplynutí lhůty se sám zapne.
    past = datetime.now() - timedelta(minutes=1)
    h.disable_source("skyscrapper", past)
    assert h.is_source_disabled("skyscrapper") is False


def test_skyscrapper_reads_quota_headers_and_flags_429(monkeypatch, tmp_path):
    import requests
    from src.sources import skyscrapper as sk_mod
    from src.sources.skyscrapper import SkyScrapperSource

    import src.sources.http_utils as http_utils_mod
    monkeypatch.setattr(http_utils_mod, "random_sleep", lambda *a, **k: None)

    class _Resp:
        def __init__(self, status, headers):
            self.status_code = status
            self.headers = headers
        def raise_for_status(self):
            if self.status_code >= 400:
                err = requests.HTTPError(str(self.status_code)); err.response = self
                raise err

    class _Session:
        def get(self, *a, **k):
            return _Resp(429, {
                "x-ratelimit-requests-remaining": "0",
                "x-ratelimit-requests-limit": "100",
                "x-ratelimit-requests-reset": "3600",
            })

    src = SkyScrapperSource(rapidapi_key="x", session=_Session(),
                            cache_path=tmp_path / "ap.json")
    # resolve_airport pohltí výjimku a vrátí None, ale kvóta se zaznamená.
    assert src.resolve_airport("FRA") is None
    assert src.quota_exhausted is True
    assert src.quota_remaining == 0
    assert src.quota_limit == 100
    assert src.quota_reset_at is not None


def test_spread_budget_divides_over_remaining_days():
    from datetime import datetime, timedelta
    from src.scanner import _spread_budget
    now = datetime(2026, 6, 9)
    reset = (now + timedelta(days=9)).isoformat()  # ~10 dní vč. dneška
    # 100 zbývá / 10 dní ≈ 10 za běh
    assert _spread_budget(100, reset, now=now) == 10
    assert _spread_budget(0, reset, now=now) == 0
    # Bez reset hlavičky → konzervativně vše až dnes (days_left=1).
    assert _spread_budget(5, None, now=now) == 5


def test_flightlabs_billing_period_anchored_on_19th():
    """FlightLabs kvóta se obnovuje 19., ne 1. Období i příští reset musí jet
    na tomto okně (jinak se 1. dne počítadlo vynuluje, plán ale ne → přečerpání)."""
    from datetime import datetime
    from src.scanner import (
        _flightlabs_period_key, _flightlabs_next_reset,
    )
    # Den ≥ 19 → období začalo 19. tohoto měsíce, reset příští 19.
    after = datetime(2026, 6, 21)
    assert _flightlabs_period_key(after) == "2026-06-19"
    assert _flightlabs_next_reset(after).date().isoformat() == "2026-07-19"
    # Den < 19 → období začalo 19. minulého měsíce.
    before = datetime(2026, 7, 5)
    assert _flightlabs_period_key(before) == "2026-06-19"
    assert _flightlabs_next_reset(before).date().isoformat() == "2026-07-19"
    # Přechod přes leden (období prosinec→leden).
    jan = datetime(2026, 1, 3)
    assert _flightlabs_period_key(jan) == "2025-12-19"
    assert _flightlabs_next_reset(jan).date().isoformat() == "2026-01-19"


def test_flightlabs_migrate_legacy_month_into_period(tmp_path):
    """Migrace přenese spotřebu z legacy kalendářního klíče (YYYY-MM) do období
    (YYYY-MM-19), ať se už spotřebovaná kvóta neztratí a rozpočet nepřečerpá."""
    from src.history import PriceHistory
    h = PriceHistory(tmp_path / "h.json")
    h.add_flightlabs_usage(681, "2026-06")  # legacy kalendářní klíč
    h.migrate_flightlabs_period("2026-06-19")
    assert h.flightlabs_usage("2026-06-19") == 681
    assert h.flightlabs_usage("2026-06") == 0  # legacy klíč odstraněn
    # Idempotence: druhé spuštění nic nepřičte ani nesmaže.
    h.add_flightlabs_usage(10, "2026-06-19")
    h.migrate_flightlabs_period("2026-06-19")
    assert h.flightlabs_usage("2026-06-19") == 691


# -- SkyScrapper / skyscanner_common parser ---------------------------------
def test_skyscanner_common_parse_itinerary():
    """skyscanner_common.parse_itinerary (SkyScrapper) ze Skyscanner itineráře
    vrátí FlightResult s oběma legy."""
    from src.sources.skyscanner_common import parse_itinerary
    it = {
        "price": {"raw": 498.0, "formatted": "€498"},
        "legs": [
            {"origin": {"displayCode": "MUC"}, "destination": {"displayCode": "KIX"},
             "departure": "2026-09-10T09:00:00",
             "carriers": {"marketing": [{"name": "Finnair", "alternateId": "AY"}]}},
            {"origin": {"displayCode": "KIX"}, "destination": {"displayCode": "MUC"},
             "departure": "2026-09-24T11:00:00",
             "carriers": {"marketing": [{"name": "Finnair", "alternateId": "AY"}]}},
        ],
    }
    r = parse_itinerary(it, "MUC", "KIX", "Test", "skyscrapper")
    assert r.price == 498.0
    assert r.source == "skyscrapper"
    assert r.origin == "MUC" and r.destination == "KIX"
    assert r.depart_date == date(2026, 9, 10)
    assert r.return_date == date(2026, 9, 24)
    assert "AY" in r.airlines


def test_format_skyscanner_dt_keeps_midnight_and_whole_hour():
    """hour=0 (půlnoc) ani minute=0 (celá hodina) se nesmí ztratit – dřív je
    `or` spolklo jako chybějící hodnotu."""
    from src.sources.skyscanner_common import format_skyscanner_dt
    assert format_skyscanner_dt({"hour": 0, "minute": 0}) == "00:00"
    assert format_skyscanner_dt({"hour": 14, "minute": 0}) == "14:00"
    assert format_skyscanner_dt({"hour": 0, "minute": 30}) == "00:30"
    assert format_skyscanner_dt("2026-09-06T00:00:00") == "00:00"


def test_itineraries_from_payload_handles_both_wrappers():
    """Sky-scrapper obaluje do data.itineraries, jiné zdroje vrací itineraries
    přímo na top-levelu – obojí musí projít."""
    from src.sources.skyscanner_common import itineraries_from_payload
    wrapped = {"data": {"itineraries": [{"price": {"raw": 1}}]}}
    flat = {"context": {"status": "complete"}, "itineraries": [{"price": {"raw": 2}}]}
    assert len(itineraries_from_payload(wrapped)) == 1
    assert len(itineraries_from_payload(flat)) == 1
    assert itineraries_from_payload({}) == []


# -- FlightLabs (goflightlabs retrieveFlights – async flat-leg API) ----------
# Reálný tvar odpovědi: ploché pole legů (outbound a return zvlášť, stejná cena).
_FLIGHTLABS_LEGS = [
    {"price": "1057", "currency": "EUR",
     "origin": {"code": "MUC"}, "destination": {"code": "NRT"},
     "departure": "2026-09-10T11:25:00", "flightNumber": "EY25",
     "marketingCarrier": "Etihad Airways"},
    {"price": "1057", "currency": "EUR",
     "origin": {"code": "NRT"}, "destination": {"code": "MUC"},
     "departure": "2026-09-24T18:00:00", "flightNumber": "EY13",
     "marketingCarrier": "Etihad Airways"},
    {"price": "1079", "currency": "EUR",
     "origin": {"code": "MUC"}, "destination": {"code": "NRT"},
     "departure": "2026-09-10T16:50:00", "flightNumber": "QR83",
     "marketingCarrier": "Qatar Airways"},
    {"price": "1079", "currency": "EUR",
     "origin": {"code": "NRT"}, "destination": {"code": "MUC"},
     "departure": "2026-09-24T22:25:00", "flightNumber": "QR79",
     "marketingCarrier": "Qatar Airways"},
]


def test_flightlabs_parse_legs_pairs_roundtrips():
    """Ploché legy se párují na roundtrip; cena = celková zpáteční, datumy z
    outbound/return, aerolinka z čísla letu (EY25→EY)."""
    from src.sources.flightlabs import FlightLabsSource
    src = FlightLabsSource(access_key="x")
    out = src._parse_legs(_FLIGHTLABS_LEGS, "MUC", "NRT", "Test")
    assert len(out) == 2
    cheapest = min(out, key=lambda r: r.price)
    assert cheapest.price == 1057.0
    assert cheapest.origin == "MUC" and cheapest.destination == "NRT"
    assert cheapest.return_origin == "NRT" and cheapest.return_destination == "MUC"
    assert cheapest.depart_date == date(2026, 9, 10)
    assert cheapest.return_date == date(2026, 9, 24)
    assert cheapest.airlines == ["EY"]


def test_flightlabs_parse_legs_drops_unpaired_oneway():
    """Nespárovaný outbound (bez return legu) se NIKDY neuloží jako roundtrip
    (ochrana proti one-way pollution)."""
    from src.sources.flightlabs import FlightLabsSource
    src = FlightLabsSource(access_key="x")
    only_out = [_FLIGHTLABS_LEGS[0]]  # jen MUC→NRT, žádný návrat
    assert src._parse_legs(only_out, "MUC", "NRT", "Test") == []


def test_flightlabs_airline_code_from_flight_number():
    from src.sources.flightlabs import FlightLabsSource
    f = FlightLabsSource._airline_code
    assert f("EY25") == "EY"
    assert f("LO392") == "LO"
    assert f("U225") == "U2"
    assert f("") == ""
    assert f(None) == ""


def test_flightlabs_search_polls_then_parses():
    """End-to-end: první volání 202 (processing), druhé 200 s plochými legy.
    Ověří poll smyčku a správné query parametry (originIATACode/date/returnDate)."""
    from src.sources.flightlabs import FlightLabsSource

    class _Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
            self.text = "{}"
        def json(self):
            return self._payload
        def raise_for_status(self):
            pass

    seq = [
        _Resp(202, {"status": "processing", "jobId": "abc"}),
        _Resp(200, _FLIGHTLABS_LEGS),
    ]
    calls = []

    class _Session:
        def get(self, url, params=None, **k):
            calls.append((url, params))
            return seq[min(len(calls) - 1, len(seq) - 1)]

    import src.sources.flightlabs as fl_mod
    _orig = fl_mod.time.sleep
    fl_mod.time.sleep = lambda *a, **k: None
    try:
        # max_polls=1 zapne poll (default je 0 = bez pollu) → 202 pak 200.
        src = FlightLabsSource(access_key="secret", session=_Session(),
                               max_polls=1)
        out = src.search("MUC", "NRT", date(2026, 9, 10),
                         return_date=date(2026, 9, 24))
    finally:
        fl_mod.time.sleep = _orig

    assert len(calls) == 2          # 202 → poll → 200
    assert calls[0][0].endswith("/retrieveFlights")
    assert calls[0][1]["originIATACode"] == "MUC"
    assert calls[0][1]["destinationIATACode"] == "NRT"
    assert calls[0][1]["date"] == "2026-09-10"
    assert calls[0][1]["returnDate"] == "2026-09-24"
    assert "access_key" in calls[0][1]
    assert len(out) == 2
    assert min(r.price for r in out) == 1057.0
    assert all(r.source == "flightlabs" for r in out)


def test_flightlabs_search_gives_up_on_persistent_202():
    """Když job stále jen 'processing', vrátí [] (ne výjimku) a nezacyklí se."""
    from src.sources.flightlabs import FlightLabsSource

    class _Resp:
        status_code = 202
        text = "{}"
        def json(self):
            return {"status": "processing"}
        def raise_for_status(self):
            pass

    class _Session:
        def __init__(self):
            self.n = 0
        def get(self, *a, **k):
            self.n += 1
            return _Resp()

    import src.sources.flightlabs as fl_mod
    _orig = fl_mod.time.sleep
    fl_mod.time.sleep = lambda *a, **k: None
    try:
        sess = _Session()
        src = FlightLabsSource(access_key="x", session=sess, max_polls=3)
        out = src.search("MUC", "NRT", date(2026, 9, 10),
                         return_date=date(2026, 9, 24))
    finally:
        fl_mod.time.sleep = _orig

    assert out == []
    assert sess.n == 4  # submit + 3 polly, pak vzdá


def test_flightlabs_submit_returns_pending_on_202():
    """submit při trvalém 202 vrátí ([], pending) – pending nese query params
    + den submitu pro pozdější collect."""
    from src.sources.flightlabs import FlightLabsSource

    class _Resp:
        status_code = 202
        text = "{}"
        def json(self):
            return {"status": "processing", "jobId": "x"}
        def raise_for_status(self):
            pass

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    import src.sources.flightlabs as fl_mod
    _orig = fl_mod.time.sleep
    fl_mod.time.sleep = lambda *a, **k: None
    try:
        src = FlightLabsSource(access_key="x", session=_Session(), max_polls=1)
        results, pending = src.submit("MUC", "NRT", date(2026, 9, 10),
                                      return_date=date(2026, 9, 24),
                                      route_name="R")
    finally:
        fl_mod.time.sleep = _orig

    assert results == []
    assert pending["originIATACode"] == "MUC"
    assert pending["destinationIATACode"] == "NRT"
    assert pending["date"] == "2026-09-10"
    assert pending["returnDate"] == "2026-09-24"
    assert pending["route_name"] == "R"
    assert pending["submitted"] == date.today().isoformat()


def test_flightlabs_circuit_breaker_trips_on_consecutive_429():
    """Po _RATE_LIMIT_CIRCUIT po sobě jdoucích 429 se shodí rate_limited flag,
    aby scanner přestal submitovat a nepálil kvótu. Ne-429 počítadlo resetuje."""
    from src.sources.flightlabs import FlightLabsSource, _RATE_LIMIT_CIRCUIT

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = "{}"
        def json(self):
            return {}
        def raise_for_status(self):
            if self.status_code >= 400:
                import requests as _rq
                raise _rq.HTTPError(f"{self.status_code}")

    class _Session:
        def __init__(self, statuses):
            self._statuses = statuses
            self.i = 0
        def get(self, *a, **k):
            s = self._statuses[min(self.i, len(self._statuses) - 1)]
            self.i += 1
            return _Resp(s)

    import src.sources.flightlabs as fl_mod
    _orig = fl_mod.time.sleep
    fl_mod.time.sleep = lambda *a, **k: None
    try:
        # _RATE_LIMIT_CIRCUIT × 429 → tripne
        src = FlightLabsSource(access_key="x",
                               session=_Session([429] * _RATE_LIMIT_CIRCUIT))
        for _ in range(_RATE_LIMIT_CIRCUIT):
            src.submit("MUC", "NRT", date(2026, 9, 10),
                       return_date=date(2026, 9, 24))
        assert src.rate_limited is True

        # Ne-429 mezi 429 resetuje počítadlo → netripne.
        src2 = FlightLabsSource(access_key="x",
                                session=_Session([429, 429, 202, 429, 429]))
        for _ in range(5):
            src2.submit("MUC", "NRT", date(2026, 9, 10),
                        return_date=date(2026, 9, 24))
        assert src2.rate_limited is False
    finally:
        fl_mod.time.sleep = _orig


def test_flightlabs_collect_completes_and_keeps():
    """collect: 200 → (results, done=True); 202 → ([], done=False, ponech)."""
    from src.sources.flightlabs import FlightLabsSource

    class _Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
            self.text = "{}"
        def json(self):
            return self._payload
        def raise_for_status(self):
            pass

    job = {"originIATACode": "MUC", "destinationIATACode": "NRT",
           "date": "2026-09-10", "returnDate": "2026-09-24",
           "adults": 1, "currency": "EUR", "cabinClass": "economy",
           "route_name": "R", "submitted": "2026-06-20"}

    import src.sources.flightlabs as fl_mod
    _orig = fl_mod.time.sleep
    fl_mod.time.sleep = lambda *a, **k: None
    try:
        # 200 s výsledky → done
        src = FlightLabsSource(access_key="x",
                               session=type("S", (), {"get": lambda self, *a, **k:
                                   _Resp(200, _FLIGHTLABS_LEGS)})())
        res, done = src.collect(job)
        assert done is True
        assert min(r.price for r in res) == 1057.0
        # query bez metadat (route_name/submitted se neposílá do API)
        # 202 → ponech
        src2 = FlightLabsSource(access_key="x",
                                session=type("S", (), {"get": lambda self, *a, **k:
                                    _Resp(202, {"status": "processing"})})())
        res2, done2 = src2.collect(job)
        assert res2 == [] and done2 is False
    finally:
        fl_mod.time.sleep = _orig


def test_history_flightlabs_pending_roundtrip(tmp_path):
    from src.history import PriceHistory
    h = PriceHistory(tmp_path / "h.json")
    assert h.flightlabs_pending() == []
    jobs = [{"originIATACode": "MUC", "destinationIATACode": "NRT",
             "submitted": "2026-06-20"}]
    h.set_flightlabs_pending(jobs)
    h.save()
    h2 = PriceHistory(tmp_path / "h.json")
    assert h2.flightlabs_pending() == jobs


def test_scanner_flightlabs_collect_drops_expired_and_collects(tmp_path):
    """run-fáze collect: hotový job se sebere, příliš starý se zahodí, ‚processing'
    zůstane v pending pro příští běh."""
    from datetime import timedelta
    from src.history import PriceHistory
    from src.scanner import Scanner, FLIGHTLABS_PENDING_EXPIRY_DAYS

    s = _scanner_settings()
    sc = Scanner(settings=s)
    sc.history = PriceHistory(tmp_path / "h.json")

    today = date.today()
    fresh = today.isoformat()
    expired = (today - timedelta(days=FLIGHTLABS_PENDING_EXPIRY_DAYS + 1)).isoformat()
    done_job = {"originIATACode": "MUC", "destinationIATACode": "NRT",
                "route_name": "R", "submitted": fresh, "tag": "done"}
    proc_job = {"originIATACode": "PRG", "destinationIATACode": "NRT",
                "route_name": "R", "submitted": fresh, "tag": "proc"}
    old_job = {"originIATACode": "VIE", "destinationIATACode": "HND",
               "route_name": "R", "submitted": expired, "tag": "old"}
    sc.history.set_flightlabs_pending([done_job, proc_job, old_job])

    fr = FlightResult(price=599, origin="MUC", destination="NRT",
                      depart_date=date(2026, 9, 10), return_date=date(2026, 9, 24),
                      source="flightlabs")

    class _FakeFL:
        request_count = 0
        def collect(self, job):
            if job.get("tag") == "done":
                return [fr], True
            return [], False  # proc → ponech

    sc.flightlabs = _FakeFL()
    collected = sc._flightlabs_collect_pending()

    assert collected == [fr]                       # hotový job se sebral
    survivors = sc._flightlabs_pending_survivors
    tags = {j["tag"] for j in survivors}
    assert tags == {"proc"}                         # expired zahozen, done sebrán


# -- Scanner: syntetické režimy zdrojů se nesmí pustit do scanu --------------
def _scanner_settings(**overrides):
    """Settings pro testy Scanneru – bez Telegramu a bez API klíčů,
    jednotlivé testy si zapnou jen to, co testují."""
    from src.config import Settings
    s = Settings.load()
    # V repo config/agent.json jsou duffel a amadeus vypnuté (nejsou zdarma /
    # sunset) – testy jejich blokační logiky je ale potřebují zapnuté, jinak
    # se vůbec nespustí.
    s.agent_config.setdefault("sources", {})["amadeus"] = True
    s.agent_config["sources"]["duffel"] = True
    s.telegram_bot_token = None
    s.telegram_chat_id = None
    s.duffel_token = None
    s.rapidapi_key = None
    s.amadeus_client_id = None
    s.amadeus_client_secret = None
    s.travelpayouts_token = None
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_scanner_blocks_duffel_test_token_and_amadeus_test_env():
    """duffel_test_… token a Amadeus test prostředí vracejí syntetické ceny –
    scanner je nesmí použít (jinak celá aplikace ukazuje nesmysly)."""
    from src.scanner import Scanner
    s = _scanner_settings(
        duffel_token="duffel_test_abc",
        amadeus_client_id="id", amadeus_client_secret="secret",
        amadeus_env="test",
    )
    sc = Scanner(settings=s)
    assert sc.duffel is None
    assert sc.duffel_test_token is True
    assert sc.amadeus is None
    assert sc.amadeus_test_env is True


def test_scanner_allows_live_duffel_and_production_amadeus():
    from src.scanner import Scanner
    s = _scanner_settings(
        duffel_token="duffel_live_abc",
        amadeus_client_id="id", amadeus_client_secret="secret",
        amadeus_env="production",
    )
    sc = Scanner(settings=s)
    assert sc.duffel is not None
    assert sc.duffel_test_token is False
    assert sc.amadeus is not None
    assert sc.amadeus_test_env is False


def test_scanner_summary_short_sends_scan_count(tmp_path):
    """Zkrácený denní souhrn posílá počet scanů a prověřené termíny."""
    from src.history import PriceHistory
    from src.scanner import Scanner
    s = _scanner_settings()
    sc = Scanner(settings=s)
    sc.history = PriceHistory(tmp_path / "h.json")
    captured: dict = {}

    def fake_short(lines):
        captured["lines"] = lines
        return True

    sc.notifier.send_daily_summary_short = fake_short
    sc._send_summary(route_count=3)
    joined = " ".join(captured.get("lines", []))
    assert "scanů" in joined and "3" in joined


# -- Google Flights zdroj (scraping přes fast-flights) ------------------------
class _GfFlight:
    """Duck-typed fast_flights.schema.Flight pro testy."""

    def __init__(self, price, name="Lufthansa"):
        self.price = price
        self.name = name


def _gf_source(flights, captured=None, fx=None, fetch_mode="common"):
    from src.sources.googleflights import GoogleFlightsSource

    def fetcher(legs, trip, adults):
        if captured is not None:
            captured.update({"legs": legs, "trip": trip, "adults": adults})
        return flights

    return GoogleFlightsSource(fetch_mode=fetch_mode, fetcher=fetcher,
                               fx=fx or _FakeFx())


def test_googleflights_roundtrip_maps_results(monkeypatch):
    from src.sources import googleflights as gf_mod
    import src.sources.http_utils as _hu; monkeypatch.setattr(_hu, "random_sleep", lambda *a, **k: None)
    captured: dict = {}
    src = _gf_source([_GfFlight("€533"), _GfFlight("€489", name="ANA")],
                     captured=captured)
    out = src.search("MUC", "NRT", date(2026, 9, 5),
                     return_date=date(2026, 9, 19), route_name="Test")
    assert captured["trip"] == "round-trip"
    assert captured["legs"] == [("MUC", "NRT", date(2026, 9, 5)),
                                ("NRT", "MUC", date(2026, 9, 19))]
    assert [r.price for r in out] == [489.0, 533.0]   # řazeno dle ceny
    best = out[0]
    assert best.currency == "EUR"
    assert best.source == "googleflights"
    assert best.origin == "MUC" and best.destination == "NRT"
    assert best.return_origin == "NRT" and best.return_destination == "MUC"
    assert best.depart_date == date(2026, 9, 5)
    assert best.return_date == date(2026, 9, 19)
    assert best.airlines == ["ANA"]
    assert best.deep_link.startswith(
        "https://www.google.com/travel/flights/search?tfs=")
    assert best.nights == 14


def test_googleflights_openjaw_uses_multicity(monkeypatch):
    from src.sources import googleflights as gf_mod
    import src.sources.http_utils as _hu; monkeypatch.setattr(_hu, "random_sleep", lambda *a, **k: None)
    captured: dict = {}
    # Open-jaw vyžaduje JS render → mód local (v common se přeskakuje).
    src = _gf_source([_GfFlight("€612")], captured=captured,
                     fetch_mode="local")
    out = src.search("MUC", "KIX", date(2026, 9, 5),
                     return_date=date(2026, 9, 19),
                     return_origin="NRT", return_destination="PRG")
    assert captured["trip"] == "multi-city"
    assert captured["legs"] == [("MUC", "KIX", date(2026, 9, 5)),
                                ("NRT", "PRG", date(2026, 9, 19))]
    assert out[0].route_key() == "MUC-KIX-NRT-openjaw"


def test_googleflights_converts_foreign_currency_and_skips_unknown(monkeypatch):
    """Kdyby Google ignoroval curr=EUR: USD se převede kurzem ECB, cena
    s nerozpoznanou měnou se zahodí (nikdy nehádat)."""
    from src.sources import googleflights as gf_mod
    import src.sources.http_utils as _hu; monkeypatch.setattr(_hu, "random_sleep", lambda *a, **k: None)
    src = _gf_source([_GfFlight("$540"), _GfFlight("1 234"),
                      _GfFlight("€510")],
                     fx=_FakeFx(rates={"USD": 1.08}))
    out = src.search("MUC", "NRT", date(2026, 9, 5),
                     return_date=date(2026, 9, 19))
    assert [r.price for r in out] == [500.0, 510.0]   # 540/1.08 a EUR přímo
    assert all(r.currency == "EUR" for r in out)


def test_googleflights_parse_price_variants():
    from src.sources.googleflights import GoogleFlightsSource
    p = GoogleFlightsSource._parse_price
    assert p("€533") == (533.0, "EUR")
    assert p("$1234") == (1234.0, "USD")
    assert p("CA$999") == (999.0, "CAD")
    assert p("CHF 920") == (920.0, "CHF")
    assert p("CZK 12500") == (12500.0, "CZK")
    assert p("") == (None, "")
    assert p("1 234") == (1234.0, "")   # bez měny → volající přeskočí


def test_scanner_initializes_googleflights_by_default():
    """Google Flights je primární zdroj – jede bez klíče, dokud ho agent.json
    nevypne."""
    from src.scanner import Scanner
    s = _scanner_settings()
    sc = Scanner(settings=s)
    assert sc.googleflights is not None
    s2 = _scanner_settings()
    s2.agent_config["sources"]["googleFlights"] = False
    sc2 = Scanner(settings=s2)
    assert sc2.googleflights is None


def test_googleflights_openjaw_skipped_in_common_mode(monkeypatch):
    """Multi-city stránky Google neservíruje server-side a veřejný fallback
    je mrtvý (401) → v common módu se open-jaw přeskakuje BEZ dotazu na
    Google; s local/fallback módem se vyhledává jako multi-city."""
    from src.sources import googleflights as gf_mod
    import src.sources.http_utils as _hu; monkeypatch.setattr(_hu, "random_sleep", lambda *a, **k: None)
    calls: list = []

    def fetcher(legs, trip, adults):
        calls.append(trip)
        return []

    src = gf_mod.GoogleFlightsSource(fetch_mode="common", fetcher=fetcher)
    out = src.search("MUC", "KIX", date(2026, 9, 5),
                     return_date=date(2026, 9, 19),
                     return_origin="NRT", return_destination="PRG")
    assert out == [] and calls == []   # žádný dotaz neproběhl
    # Roundtrip v common módu normálně jede.
    src.search("MUC", "NRT", date(2026, 9, 5), return_date=date(2026, 9, 19))
    assert calls == ["round-trip"]
    # local mód open-jaw vyhledává přes multi-city.
    src_local = gf_mod.GoogleFlightsSource(fetch_mode="local", fetcher=fetcher)
    src_local.search("MUC", "KIX", date(2026, 9, 5),
                     return_date=date(2026, 9, 19),
                     return_origin="NRT", return_destination="PRG")
    assert calls[-1] == "multi-city"


def test_googleflights_truncates_parse_error(monkeypatch):
    """RuntimeError z fast-flights obsahuje celý markdown stránky – do logu
    (a tedy výjimky) smí jít jen krátká diagnóza."""
    import pytest
    fast_flights = pytest.importorskip("fast_flights")
    from src.sources import googleflights as gf_mod
    import src.sources.http_utils as _hu; monkeypatch.setattr(_hu, "random_sleep", lambda *a, **k: None)

    def fake_gfff(*a, **k):
        raise RuntimeError("No flights found:\n" + "x" * 50000)

    monkeypatch.setattr(fast_flights, "get_flights_from_filter", fake_gfff)
    src = gf_mod.GoogleFlightsSource()
    with pytest.raises(RuntimeError) as ei:
        src.search("MUC", "NRT", date(2026, 9, 5))
    assert len(str(ei.value)) < 300
