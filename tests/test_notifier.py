"""Testy pro notifier, kalendář a historii cen."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from src.calendar_renderer import render_calendar
from src.history import PriceHistory
from src.notifier import TelegramNotifier, _fmt_date
from src.sources import DealResult, FlightResult


# -- Kalendář --------------------------------------------------------------
def test_render_calendar_single_month():
    cal = render_calendar(date(2026, 9, 5), date(2026, 9, 20))
    assert "Září 2026" in cal
    assert "Po Út St Čt Pá So Ne" in cal
    assert "✈" in cal
    assert "🛬" in cal


def test_render_calendar_multi_month():
    cal = render_calendar(date(2026, 9, 12), date(2026, 10, 7))
    assert "Září 2026" in cal
    assert "Říjen 2026" in cal


def test_render_calendar_swaps_reversed_dates():
    cal = render_calendar(date(2026, 10, 7), date(2026, 9, 12))
    # Nespadne a vyrenderuje oba měsíce.
    assert "Září 2026" in cal and "Říjen 2026" in cal


# -- Datum formát ----------------------------------------------------------
def test_fmt_date_czech():
    assert _fmt_date(date(2026, 9, 12)) == "12. září 2026 (so)"


def test_fmt_date_none():
    assert _fmt_date(None) == "?"


# -- Notifier (bez sítě) ---------------------------------------------------
def test_notifier_disabled_without_credentials():
    n = TelegramNotifier(None, None)
    assert not n.enabled
    # _send vrací False, nepadá.
    assert n.send_deal_alert(
        DealResult(title="Test", link="http://x", source="secretflying.com")
    ) is False


def test_notifier_price_alert_no_credentials():
    n = TelegramNotifier(None, None)
    f = FlightResult(
        price=489, origin="MUC", destination="KIX",
        return_origin="NRT", return_destination="PRG",
        depart_date=date(2026, 9, 12), return_date=date(2026, 10, 7),
        source="kiwi", deep_link="https://kiwi.com/x",
    )
    assert n.send_price_alert(f, delta=-61) is False


# -- Historie cen ----------------------------------------------------------
def test_history_record_and_min(tmp_path):
    h = PriceHistory(path=tmp_path / "hist.json")
    h.record("FRA-NRT-roundtrip", 567, "kiwi")
    assert h.last_price("FRA-NRT-roundtrip") == 567
    assert h.all_time_min("FRA-NRT-roundtrip") == 567
    h.record("FRA-NRT-roundtrip", 489, "amadeus")
    assert h.all_time_min("FRA-NRT-roundtrip") == 489
    assert h.is_new_low("FRA-NRT-roundtrip", 450)
    assert not h.is_new_low("FRA-NRT-roundtrip", 500)


def test_history_delta(tmp_path):
    h = PriceHistory(path=tmp_path / "hist.json")
    h.record("X-Y-roundtrip", 550, "kiwi")
    assert h.price_delta("X-Y-roundtrip", 489) == -61


def test_history_alert_dedup(tmp_path):
    h = PriceHistory(path=tmp_path / "hist.json")
    key = "FRA-NRT-roundtrip"
    assert h.should_alert(key, 489)
    h.mark_alerted(key, 489)
    assert not h.should_alert(key, 489)
    # Jiná cena projde.
    assert h.should_alert(key, 450)


def test_history_persistence(tmp_path):
    p = tmp_path / "hist.json"
    h = PriceHistory(path=p)
    h.record("A-B-roundtrip", 400, "kiwi")
    h.save()
    h2 = PriceHistory(path=p)
    assert h2.all_time_min("A-B-roundtrip") == 400


def test_history_prune(tmp_path):
    h = PriceHistory(path=tmp_path / "hist.json")
    old = date.today() - timedelta(days=200)
    h.record("A-B-roundtrip", 400, "kiwi", on_date=old)
    h.record("A-B-roundtrip", 410, "kiwi")  # dnešní – spustí prune
    hist = h.get_route("A-B-roundtrip")["history"]
    # Starý záznam (200 dní) byl odstraněn, zůstává jen dnešní.
    assert all(d["date"] != old.isoformat() for d in hist)


def test_amadeus_usage_counter(tmp_path):
    h = PriceHistory(path=tmp_path / "hist.json")
    month = datetime.now().strftime("%Y-%m")
    assert h.amadeus_usage(month) == 0
    h.add_amadeus_usage(10, month)
    h.add_amadeus_usage(5, month)
    assert h.amadeus_usage(month) == 15
