"""Testy selektivního purge nereálných záznamů (src/maintenance.py).

Scénář: historie obsahuje syntetické ceny z test režimů (duffel/amadeus)
smíchané s reálnými záznamy (skyscrapper/travelpayouts). Purge musí odstranit
jen zasažené zdroje a přepočítat odvozené hodnoty.
"""
from __future__ import annotations

import json
from datetime import date

from src.history import PriceHistory
from src.maintenance import purge_history, purge_longterm_records


def _seed_history(path):
    h = PriceHistory(path)
    # Syntetické duffel ceny (podezřele nízké) + reálné skyscrapper.
    h.record("MUC-KIX-roundtrip", 250, "duffel", on_date=date(2026, 6, 1))
    h.record("MUC-KIX-roundtrip", 240, "duffel", on_date=date(2026, 6, 5))
    h.record("MUC-KIX-roundtrip", 560, "skyscrapper", on_date=date(2026, 6, 3))
    # Trasa jen ze syntetického zdroje → po purge zmizí celá.
    h.record("FRA-NRT-roundtrip", 230, "duffel", on_date=date(2026, 6, 2))
    # Nezasažená trasa – nesmí se jí nic stát.
    h.record("VIE-NRT-roundtrip", 580, "travelpayouts", on_date=date(2026, 6, 4))
    h.mark_alerted("MUC-KIX-roundtrip", 240)
    h.save()
    return h


def test_purge_sources_removes_only_listed_and_recomputes(tmp_path):
    h = _seed_history(tmp_path / "h.json")
    removed = h.purge_sources({"duffel"})

    assert removed == {"MUC-KIX-roundtrip": 2, "FRA-NRT-roundtrip": 1}
    # Trasa jen se syntetickými záznamy zmizela celá.
    assert "FRA-NRT-roundtrip" not in h.data
    # Smíšená trasa: zůstal jen reálný záznam a odvozené hodnoty sedí.
    entry = h.data["MUC-KIX-roundtrip"]
    assert [r["source"] for r in entry["history"]] == ["skyscrapper"]
    assert entry["all_time_min"] == 560
    assert entry["last_price"] == 560
    assert entry["last_seen"] == "2026-06-03"
    assert entry["alerts"] == {}   # razítka patřila smazaným cenám
    # Nezasažená trasa beze změny.
    assert h.data["VIE-NRT-roundtrip"]["all_time_min"] == 580


def test_purge_sources_respects_before_cutoff(tmp_path):
    h = PriceHistory(tmp_path / "h.json")
    h.record("MUC-KIX-roundtrip", 250, "duffel", on_date=date(2026, 6, 1))
    h.record("MUC-KIX-roundtrip", 480, "duffel", on_date=date(2026, 6, 9))
    removed = h.purge_sources({"duffel"}, before=date(2026, 6, 9))
    # Odstraněn jen záznam PŘED cutoffem; novější (už s live tokenem) zůstal.
    assert removed == {"MUC-KIX-roundtrip": 1}
    entry = h.data["MUC-KIX-roundtrip"]
    assert [r["price"] for r in entry["history"]] == [480]
    assert entry["all_time_min"] == 480


def test_purge_longterm_records_filters_by_source():
    records = [
        {"date": "2026-06-01", "price": 250, "source": "duffel"},
        {"date": "2026-06-02", "price": 540, "source": "skyscrapper"},
        {"date": "2026-06-03", "price": 260, "source": "duffel"},
    ]
    kept, n_removed = purge_longterm_records(records, {"duffel"})
    assert n_removed == 2
    assert [r["source"] for r in kept] == ["skyscrapper"]


def test_purge_history_dry_run_does_not_write(tmp_path):
    ph_path = tmp_path / "price_history.json"
    _seed_history(ph_path)
    lt_dir = tmp_path / "history"
    lt_dir.mkdir()
    lt_file = lt_dir / "MUC-KIX-roundtrip.json"
    lt_file.write_text(json.dumps([
        {"date": "2026-05-01", "price": 245, "source": "duffel"},
        {"date": "2026-05-02", "price": 520, "source": "skyscrapper"},
    ]), encoding="utf-8")
    before_ph = ph_path.read_text(encoding="utf-8")
    before_lt = lt_file.read_text(encoding="utf-8")

    summary = purge_history({"duffel"}, apply=False,
                            price_history_path=ph_path, longterm_dir=lt_dir)

    assert summary["applied"] is False
    assert summary["price_history"]["MUC-KIX-roundtrip"] == 2
    assert summary["longterm"]["MUC-KIX-roundtrip"] == 1
    # Dry-run: na disku se nesmí nic změnit.
    assert ph_path.read_text(encoding="utf-8") == before_ph
    assert lt_file.read_text(encoding="utf-8") == before_lt


def test_purge_history_apply_writes_and_uses_longterm_min(tmp_path):
    ph_path = tmp_path / "price_history.json"
    _seed_history(ph_path)
    lt_dir = tmp_path / "history"
    lt_dir.mkdir()
    # Dlouhodobá řada má STARŠÍ reálné minimum (520 < 560 z 90denního okna) –
    # purge ho musí použít pro all_time_min.
    (lt_dir / "MUC-KIX-roundtrip.json").write_text(json.dumps([
        {"date": "2026-05-01", "price": 245, "source": "duffel"},
        {"date": "2026-05-02", "price": 520, "source": "skyscrapper"},
    ]), encoding="utf-8")
    (lt_dir / "FRA-NRT-roundtrip.json").write_text(json.dumps([
        {"date": "2026-06-02", "price": 230, "source": "duffel"},
    ]), encoding="utf-8")

    summary = purge_history({"duffel"}, apply=True,
                            price_history_path=ph_path, longterm_dir=lt_dir)
    assert summary["applied"] is True

    # price_history.json zapsaný a přepočtený.
    data = json.loads(ph_path.read_text(encoding="utf-8"))
    assert "FRA-NRT-roundtrip" not in data
    assert data["MUC-KIX-roundtrip"]["all_time_min"] == 520  # z dlouhodobé řady
    assert [r["source"] for r in data["MUC-KIX-roundtrip"]["history"]] \
        == ["skyscrapper"]

    # Dlouhodobé řady vyčištěné (čistě syntetická → prázdný seznam).
    lt = json.loads((lt_dir / "MUC-KIX-roundtrip.json").read_text("utf-8"))
    assert [r["source"] for r in lt] == ["skyscrapper"]
    assert json.loads(
        (lt_dir / "FRA-NRT-roundtrip.json").read_text("utf-8")) == []
