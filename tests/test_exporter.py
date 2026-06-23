"""Testy exportu pro dashboard (Fáze 0) – bez sítě, nad fixture daty."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.config import Settings, apply_agent_config
from src.exporter import Exporter, parse_route_key
from src.history import PriceHistory
from src.sources import FlightResult

TODAY = date(2026, 6, 10)
NOW = datetime(2026, 6, 10, 6, 42, tzinfo=timezone.utc)

AGENT = {
    "europeAirports": [
        {"code": "PRG", "name": "Praha", "lat": 50.1, "lon": 14.26,
         "priority": 2, "enabled": True},
        {"code": "MUC", "name": "Mnichov", "lat": 48.35, "lon": 11.79,
         "priority": 1, "enabled": True},
        {"code": "FMM", "name": "Memmingen", "lat": 47.99, "lon": 10.24,
         "priority": 3, "enabled": False},
    ],
    "japanAirports": [
        {"code": "NRT", "name": "Tokio Narita", "lat": 35.76, "lon": 140.39,
         "priority": 1, "enabled": True},
    ],
    "cityAliases": {"TYO": {"name": "Tokio (město)", "lat": 35.68, "lon": 139.77}},
    "alertThresholds": {"dealMaxEur": 600, "bigDropPct": 15,
                        "newLowSensitivityPct": 2},
}


def _history(tmp_path: Path) -> PriceHistory:
    h = PriceHistory(tmp_path / "price_history.json")
    h.record("PRG-TYO-roundtrip", 567, "duffel", on_date=date(2026, 6, 1),
             depart_date=date(2026, 9, 5), return_date=date(2026, 9, 19))
    h.record("PRG-TYO-roundtrip", 512, "duffel", on_date=date(2026, 6, 3),
             depart_date=date(2026, 9, 12), return_date=date(2026, 9, 26))
    h.record("PRG-TYO-roundtrip", 487, "duffel", on_date=TODAY,
             depart_date=date(2026, 9, 5), return_date=date(2026, 9, 19))
    h.record("MUC-KIX-OSA-openjaw", 640, "amadeus", on_date=TODAY,
             depart_date=date(2026, 10, 2), return_date=date(2026, 10, 16))
    return h


def _settings() -> Settings:
    s = Settings(price_threshold_eur=600.0)
    s.agent_config = AGENT
    return s


def _flights() -> list[FlightResult]:
    return [
        FlightResult(price=487, origin="PRG", destination="TYO",
                     return_origin="TYO", return_destination="PRG",
                     depart_date=date(2026, 9, 5),
                     return_date=date(2026, 9, 19),
                     airlines=["AY", "JL"], source="duffel",
                     deep_link="https://example.com/deal"),
        FlightResult(price=530, origin="PRG", destination="TYO",
                     return_origin="TYO", return_destination="PRG",
                     source="skyscrapper"),
        FlightResult(price=640, origin="MUC", destination="KIX",
                     return_origin="OSA", return_destination="MUC",
                     depart_date=date(2026, 10, 2),
                     return_date=date(2026, 10, 16),
                     source="amadeus"),
    ]


@pytest.fixture
def exported(tmp_path):
    history = _history(tmp_path)
    out = tmp_path / "data"
    exporter = Exporter(history, _settings(), out_dir=out)
    prev = {"PRG-TYO-roundtrip": {"all_time_min": 512, "last_price": 512},
            "MUC-KIX-OSA-openjaw": {"all_time_min": None, "last_price": None}}
    exporter.run(_flights(), prev_state=prev, now=NOW)
    return out, history, prev


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_parse_route_key_roundtrip_and_openjaw():
    rt = parse_route_key("PRG-TYO-roundtrip")
    assert rt["type"] == "roundtrip" and rt["origin"] == "PRG"
    assert rt["destination"] == "TYO" and rt["returnOrigin"] is None
    oj = parse_route_key("MUC-KIX-OSA-openjaw")
    assert oj["type"] == "openjaw" and oj["returnOrigin"] == "OSA"
    assert oj["returnDestination"] == "MUC"


def test_latest_contains_ephemeral_fields_and_flags(exported):
    out, _, _ = exported
    latest = _load(out / "latest.json")
    # dedup per (route_key, depart_date): PRG-TYO má 2 záznamy (487 s datem, 530 bez)
    assert len(latest) == 3
    # cheapest PRG-TYO entry (sorted by price ascending)
    prg = next(x for x in latest if x["routeKey"] == "PRG-TYO-roundtrip" and x["price"] == 487)
    assert prg["airlines"] == ["AY", "JL"]
    assert prg["dealUrl"] == "https://example.com/deal"
    assert prg["price"] == 487 and prg["nights"] == 14
    assert prg["flags"]["isNewLow"] is True
    assert prg["flags"]["priceDeltaEur"] == -25
    assert prg["flags"]["pctChange7d"] is not None  # data z 1.6. (≥7 dní)
    muc = next(x for x in latest if x["routeKey"] == "MUC-KIX-OSA-openjaw")
    assert muc["returnOrigin"] == "OSA"
    assert muc["flags"]["priceDeltaEur"] is None


def test_latest_alternatives_keeps_pricier_premium_options(tmp_path):
    """3 nabídky PRG→NRT na stejný termín: nejlevnější (přestupní, podřadná
    aerolinka) je hlavní řádek, dvě dražší přímé/prémiové se zachovají jako
    alternativy v detailu (i s počtem přestupů)."""
    history = _history(tmp_path)
    out = tmp_path / "data"
    trip = dict(origin="PRG", destination="NRT", return_origin="NRT",
                return_destination="PRG", depart_date=date(2026, 9, 5),
                return_date=date(2026, 9, 19))
    cheap = FlightResult(price=1011, airlines=["PC"], source="flightlabs",
                         stops_out=1, stops_in=1, **trip)          # podřadná, přestup
    direct_ek = FlightResult(price=1025, airlines=["EK"], source="flightlabs",
                             stops_out=0, stops_in=0, **trip)       # přímý, prémiový
    direct_qr = FlightResult(price=1029, airlines=["QR"], source="flightlabs",
                             stops_out=0, stops_in=0, **trip)
    raw = [cheap, direct_ek, direct_qr]
    Exporter(history, _settings(), out_dir=out).run(
        [cheap], prev_state={}, now=NOW, raw_offers=raw)

    latest = _load(out / "latest.json")
    prg = next(x for x in latest if x["routeKey"] == "PRG-NRT-roundtrip")
    assert prg["price"] == 1011 and prg["stopsOut"] == 1   # hlavní = nejlevnější
    alts = prg["alternatives"]
    assert len(alts) == 2
    assert {a["airlines"][0] for a in alts} == {"EK", "QR"}
    assert all(a["stopsOut"] == 0 for a in alts)           # přímé lety zachovány
    assert [a["price"] for a in alts] == [1025, 1029]      # seřazené dle ceny


def test_alternatives_exclude_duplicate_of_cheapest(tmp_path):
    """Když je nejlevnější nabídka ve dvou zdrojích (stejná cena+aerolinka),
    ta druhá kopie se NESMÍ zapsat jako 'alternativa' – alternativa je jen
    skutečně dražší/jiná varianta."""
    history = _history(tmp_path)
    out = tmp_path / "data"
    trip = dict(origin="PRG", destination="NRT", return_origin="NRT",
                return_destination="PRG", depart_date=date(2026, 9, 5),
                return_date=date(2026, 9, 19))
    pc1 = FlightResult(price=1011, airlines=["PC"], source="flightlabs",
                       stops_out=1, stops_in=1, **trip)
    pc2 = FlightResult(price=1011, airlines=["PC"], source="googleflights",
                       stops_out=1, stops_in=1, **trip)  # duplikát nejlevnější
    ek = FlightResult(price=1025, airlines=["EK"], source="flightlabs",
                      stops_out=0, stops_in=0, **trip)
    Exporter(history, _settings(), out_dir=out).run(
        [pc1], prev_state={}, now=NOW, raw_offers=[pc1, pc2, ek])

    alt = _load(out / "alternatives" / "PRG-NRT-roundtrip.json")
    assert [r["airlines"] for r in alt] == [["EK"]]  # PC duplikát vyřazen
    latest = _load(out / "latest.json")
    prg = next(x for x in latest if x["routeKey"] == "PRG-NRT-roundtrip")
    assert [a["airlines"] for a in prg["alternatives"]] == [["EK"]]


def test_alternatives_history_append_only_and_excludes_cheapest(tmp_path):
    """Dražší varianty se ukládají do data/alternatives/{route}.json (append-only),
    nejlevnější tam NENÍ (ta jde do běžné history/stats)."""
    history = _history(tmp_path)
    out = tmp_path / "data"
    trip = dict(origin="PRG", destination="NRT", return_origin="NRT",
                return_destination="PRG", depart_date=date(2026, 9, 5),
                return_date=date(2026, 9, 19))
    cheap = FlightResult(price=1011, airlines=["PC"], source="flightlabs",
                         stops_out=1, stops_in=1, **trip)
    ek = FlightResult(price=1025, airlines=["EK"], source="flightlabs",
                      stops_out=0, stops_in=0, **trip)
    exp = Exporter(history, _settings(), out_dir=out)
    exp.run([cheap], prev_state={}, now=NOW, raw_offers=[cheap, ek])

    alt = _load(out / "alternatives" / "PRG-NRT-roundtrip.json")
    assert len(alt) == 1
    assert alt[0]["airlines"] == ["EK"] and alt[0]["stopsOut"] == 0
    assert alt[0]["price"] == 1025 and alt[0]["date"] == "2026-06-10"
    # nejlevnější (PC) se do alternatives NEpíše
    assert all(r["airlines"] != ["PC"] for r in alt)

    # Druhý běh (jiný den, dražší EK) → append, ne přepis; starý záznam zůstává.
    later = datetime(2026, 6, 12, 6, 0, tzinfo=timezone.utc)
    ek2 = FlightResult(price=999, airlines=["EK"], source="flightlabs",
                       stops_out=0, stops_in=0, **trip)
    Exporter(history, _settings(), out_dir=out).run(
        [cheap], prev_state={}, now=later, raw_offers=[cheap, ek2])
    alt2 = _load(out / "alternatives" / "PRG-NRT-roundtrip.json")
    assert len(alt2) == 2  # append-only: oba dny
    assert {r["date"] for r in alt2} == {"2026-06-10", "2026-06-12"}


def test_latest_alternatives_empty_without_raw_offers(exported):
    """Bez raw_offers (zpětná kompatibilita) má každá nabídka prázdné alternatives."""
    out, _, _ = exported
    latest = _load(out / "latest.json")
    assert all(x["alternatives"] == [] for x in latest)


def test_history_append_only_dedup(exported):
    out, history, prev = exported
    series_path = out / "history" / "PRG-TYO-roundtrip.json"
    first = _load(series_path)
    assert len(first) == 3
    # Druhý běh se stejnými daty nesmí nic zduplikovat, jen přidat nové.
    history.record("PRG-TYO-roundtrip", 499, "duffel", on_date=TODAY,
                   depart_date=date(2026, 9, 6), return_date=date(2026, 9, 20))
    Exporter(history, _settings(), out_dir=out).run([], prev_state=prev, now=NOW)
    second = _load(series_path)
    assert len(second) == 4
    keys = [(r["date"], r["source"], r.get("departDate"),
             r.get("returnDate"), r["price"]) for r in second]
    assert len(keys) == len(set(keys))


def test_stats_and_calendar(exported):
    out, _, _ = exported
    stats = _load(out / "stats.json")
    prg = stats["PRG-TYO-roundtrip"]
    assert prg["allTimeMin"] == 487
    assert prg["min90d"] == 487 and prg["max90d"] == 567
    assert prg["lastPrice"] == 487
    assert prg["biggestDrop"]["from"] == 567
    assert prg["trend30d"] is not None
    cal = _load(out / "calendar" / "PRG-TYO-roundtrip.json")
    by_dep = {c["departDate"]: c for c in cal}
    # Pro 2026-09-05 vyhrává novější pozorování (487 z 10.6.), ne starší 567.
    assert by_dep["2026-09-05"]["price"] == 487
    assert by_dep["2026-09-12"]["price"] == 512


def test_insights_meta_routes(exported):
    out, _, _ = exported
    ins = _load(out / "insights.json")
    eu_codes = [r["code"] for r in ins["airportPriority"]["europe"]]
    jp_codes = [r["code"] for r in ins["airportPriority"]["japan"]]
    assert "PRG" in eu_codes and "MUC" in eu_codes
    assert "TYO" in jp_codes  # city alias patří do japonské skupiny
    assert ins["cheapestDepartureDow"], "weekday insights nesmí být prázdné"
    meta = _load(out / "meta.json")
    assert meta["lastScan"].startswith("2026-06-10T06:42")
    assert meta["schemaVersion"] == 1
    assert "skyscrapper" in meta["apiQuota"]
    routes = _load(out / "routes.json")
    keys = {r["routeKey"] for r in routes}
    assert {"PRG-TYO-roundtrip", "MUC-KIX-OSA-openjaw"} <= keys
    prg = next(r for r in routes if r["routeKey"] == "PRG-TYO-roundtrip")
    assert prg["coords"]["origin"] == {"lat": 50.1, "lon": 14.26}
    assert prg["coords"]["destination"] == {"lat": 35.68, "lon": 139.77}


def test_apply_agent_config_overrides():
    cfg = apply_agent_config({"price_threshold_eur": 550}, {
        **AGENT,
        "travelWindow": {"from": "2026-09-01", "to": "2026-12-31"},
        "stayLength": {"minNights": 7, "maxNights": 21},
    })
    # priorita: MUC (1) před PRG (2); FMM disabled vypadne
    assert cfg["european_airports"] == ["MUC", "PRG"]
    assert cfg["japanese_airports"] == ["NRT"]
    assert cfg["price_threshold_eur"] == 600
    assert cfg["stay_length"] == {"min_nights": 7, "max_nights": 21}
    assert cfg["search_windows"] == [{"year": 2026, "months": [9, 10, 11, 12]}]


def test_apply_agent_config_rejects_narrow_travel_window():
    """Degenerované okno (užší než min_nights + 2) se přeskočí
    (ochrána proti fallbacku v _plan_scan_dates)."""
    cfg = apply_agent_config({"price_threshold_eur": 550}, {
        **AGENT,
        "travelWindow": {"from": "2026-09-01", "to": "2026-09-05"},  # jen 4 dny
        "stayLength": {"minNights": 7, "maxNights": 21},
    })
    # search_windows se neuloží, protože travelWindow je příliš úzké
    assert "search_windows" not in cfg or not cfg.get("search_windows")


def test_apply_agent_config_rejects_inverted_travel_window():
    """Okno s to < from se přeskočí."""
    cfg = apply_agent_config({"price_threshold_eur": 550}, {
        **AGENT,
        "travelWindow": {"from": "2026-12-31", "to": "2026-09-01"},  # inverzní
        "stayLength": {"minNights": 7, "maxNights": 21},
    })
    assert "search_windows" not in cfg or not cfg.get("search_windows")


def test_settings_toggles_default_true():
    s = Settings()
    assert s.source_enabled("duffel") and s.rss_enabled("jacks")
    assert s.telegram_alert_enabled("dailySummary")
    s.agent_config = {"sources": {"duffel": False, "rss": {"jacks": False}},
                      "telegramAlerts": {"dailySummary": False}}
    assert not s.source_enabled("duffel")
    assert not s.rss_enabled("jacks")
    assert not s.telegram_alert_enabled("dailySummary")
    assert s.source_enabled("amadeus")  # neuvedený zdroj zůstává zapnutý


# -- Stale merge (latest.json doplňuje historické zálohy) --------------------

def test_latest_stale_fill_for_missing_routes(tmp_path):
    """Sparsy scan (jen jedna trasa nalezena) doplní ostatní z historie."""
    history = _history(tmp_path)
    out = tmp_path / "data"
    prev = {"PRG-TYO-roundtrip": {"all_time_min": 512, "last_price": 512},
            "MUC-KIX-OSA-openjaw": {"all_time_min": None, "last_price": None}}
    # Scan vrátil jen MUC (PRG chybí).
    live = [FlightResult(price=640, origin="MUC", destination="KIX",
                         return_origin="OSA", return_destination="MUC",
                         depart_date=date(2026, 10, 2),
                         return_date=date(2026, 10, 16),
                         source="amadeus")]
    Exporter(history, _settings(), out_dir=out).run(live, prev_state=prev, now=NOW)
    latest = _load(out / "latest.json")
    routes = {x["routeKey"] for x in latest}
    # Obě trasy musí být přítomny – MUC živě, PRG jako záloha z historie.
    assert "MUC-KIX-OSA-openjaw" in routes
    assert "PRG-TYO-roundtrip" in routes
    # Živá nabídka má staleDays=None, záloha má staleDays≥0.
    muc = next(x for x in latest if x["routeKey"] == "MUC-KIX-OSA-openjaw")
    prg = next(x for x in latest if x["routeKey"] == "PRG-TYO-roundtrip")
    assert muc["flags"]["staleDays"] is None
    assert prg["flags"]["staleDays"] is not None
    # Záloha nemá efemérní pole.
    assert prg["airlines"] == []
    assert prg["dealUrl"] is None


def test_stale_fill_skips_one_way_pollution(tmp_path):
    """One-way historický záznam (roundtrip/openjaw bez returnDate, např. starší
    travelpayouts data) NESMÍ prosáknout do latest.json jako záloha — i když je
    nejlevnější. Reálný zpáteční záznam má přednost."""
    h = PriceHistory(tmp_path / "price_history.json")
    # Levný one-way (bez return_date) + dražší reálný zpáteční, stejný den.
    h.record("PRG-TYO-roundtrip", 414, "travelpayouts", on_date=TODAY,
             depart_date=date(2026, 9, 5))  # bez return_date = one-way pollution
    h.record("PRG-TYO-roundtrip", 567, "duffel", on_date=TODAY,
             depart_date=date(2026, 9, 5), return_date=date(2026, 9, 19))
    out = tmp_path / "data"
    prev = {"PRG-TYO-roundtrip": {"all_time_min": 567, "last_price": 567}}
    # Žádný živý výsledek pro PRG → musí se doplnit z historie.
    Exporter(h, _settings(), out_dir=out).run([], prev_state=prev, now=NOW)
    latest = _load(out / "latest.json")
    prg = [x for x in latest if x["routeKey"] == "PRG-TYO-roundtrip"]
    assert len(prg) == 1
    # Vybrat se MUSÍ reálná zpáteční nabídka (567 s returnDate), ne one-way 414.
    assert prg[0]["price"] == 567
    assert prg[0]["returnDate"] == "2026-09-19"
    assert prg[0]["source"] == "duffel"
    # Žádná roundtrip/openjaw nabídka v latest.json bez returnDate.
    for x in latest:
        if x["type"] in ("roundtrip", "openjaw"):
            assert x["returnDate"] is not None, f"{x['routeKey']}: one-way pollution"


def test_stale_fill_all_one_way_dropped(tmp_path):
    """Pokud má trasa POUZE one-way záznamy, nezobjeví se v latest.json vůbec
    (radši žádná nabídka než podhodnocená one-way)."""
    h = PriceHistory(tmp_path / "price_history.json")
    h.record("PRG-TYO-roundtrip", 414, "travelpayouts", on_date=TODAY,
             depart_date=date(2026, 9, 5))  # jediný záznam, bez return_date
    out = tmp_path / "data"
    Exporter(h, _settings(), out_dir=out).run([], prev_state={}, now=NOW)
    latest = _load(out / "latest.json")
    assert not any(x["routeKey"] == "PRG-TYO-roundtrip" for x in latest)


def test_latest_live_routes_not_in_stale(tmp_path):
    """Trasy s živým výsledkem se nezdvojí jako záloha."""
    history = _history(tmp_path)
    out = tmp_path / "data"
    prev: dict = {}
    Exporter(history, _settings(), out_dir=out).run(_flights(), prev_state=prev, now=NOW)
    latest = _load(out / "latest.json")
    # Každý routeKey může mít více záznamů (různé depart daty), ale nesmí být
    # zároveň živý I stale.
    for rk in {x["routeKey"] for x in latest}:
        entries = [x for x in latest if x["routeKey"] == rk]
        live_e = [e for e in entries if e["flags"]["staleDays"] is None]
        stale_e = [e for e in entries if e["flags"]["staleDays"] is not None]
        assert not (live_e and stale_e), f"{rk}: má živou i stale nabídku zároveň"
