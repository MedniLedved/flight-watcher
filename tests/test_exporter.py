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
    assert len(latest) == 2  # dedup na nejlevnější per route_key
    prg = next(x for x in latest if x["routeKey"] == "PRG-TYO-roundtrip")
    assert prg["airlines"] == ["AY", "JL"]
    assert prg["dealUrl"] == "https://example.com/deal"
    assert prg["price"] == 487 and prg["nights"] == 14
    assert prg["flags"]["isNewLow"] is True
    assert prg["flags"]["priceDeltaEur"] == -25
    assert prg["flags"]["pctChange7d"] is not None  # data z 1.6. (≥7 dní)
    muc = next(x for x in latest if x["routeKey"] == "MUC-KIX-OSA-openjaw")
    assert muc["returnOrigin"] == "OSA"
    assert muc["flags"]["priceDeltaEur"] is None


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
