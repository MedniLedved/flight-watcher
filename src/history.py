"""Historie cen – perzistentní JSON soubor (data/price_history.json).

Struktura na trasu (route_key):
{
  "FRA-NRT-roundtrip": {
    "all_time_min": 389,
    "last_seen": "2026-01-10",
    "last_price": 567,
    "alerts": {"<price>": "<iso-datetime>"},   # anti-duplicita alertů
    "history": [
      {"date": "2026-01-15", "price": 567, "source": "kiwi"}
    ]
  },
  "_meta": {
    "amadeus_requests": {"2026-01": 36}
  }
}

Uchovává posledních 90 dní historie pro každou trasu.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HISTORY_RETENTION_DAYS = 90
ALERT_DEDUPE_HOURS = 24
META_KEY = "_meta"


class PriceHistory:
    def __init__(self, path: str | Path = "data/price_history.json"):
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Nelze načíst historii (%s): %s", self.path, exc)
                self.data = {}
        else:
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)

    # -- dotazy -----------------------------------------------------------
    def get_route(self, route_key: str) -> Optional[dict[str, Any]]:
        return self.data.get(route_key)

    def all_time_min(self, route_key: str) -> Optional[float]:
        entry = self.data.get(route_key)
        return entry.get("all_time_min") if entry else None

    def last_price(self, route_key: str) -> Optional[float]:
        entry = self.data.get(route_key)
        return entry.get("last_price") if entry else None

    def is_new_low(self, route_key: str, price: float) -> bool:
        """True, pokud cena je nižší než dosavadní historické minimum."""
        atm = self.all_time_min(route_key)
        return atm is None or price < atm

    def price_delta(self, route_key: str, price: float) -> Optional[float]:
        """Rozdíl oproti poslední zaznamenané ceně (záporné = zlevnění)."""
        last = self.last_price(route_key)
        if last is None:
            return None
        return price - last

    # -- zápis ------------------------------------------------------------
    def record(self, route_key: str, price: float, source: str,
               on_date: Optional[date] = None) -> None:
        on_date = on_date or date.today()
        entry = self.data.setdefault(route_key, {
            "all_time_min": price,
            "last_seen": on_date.isoformat(),
            "last_price": price,
            "alerts": {},
            "history": [],
        })
        entry["last_seen"] = on_date.isoformat()
        entry["last_price"] = price
        if price < entry.get("all_time_min", price + 1):
            entry["all_time_min"] = price
        entry.setdefault("history", []).append({
            "date": on_date.isoformat(),
            "price": price,
            "source": source,
        })
        self._prune_history(entry)

    def _prune_history(self, entry: dict[str, Any]) -> None:
        cutoff = date.today() - timedelta(days=HISTORY_RETENTION_DAYS)
        kept = []
        for h in entry.get("history", []):
            try:
                hd = datetime.strptime(h["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            if hd >= cutoff:
                kept.append(h)
        entry["history"] = kept

    # -- anti-duplicita alertů -------------------------------------------
    def should_alert(self, route_key: str, price: float) -> bool:
        """False, pokud stejná cena pro stejnou trasu byla odeslána
        v posledních ALERT_DEDUPE_HOURS hodinách."""
        entry = self.data.get(route_key, {})
        alerts = entry.get("alerts", {})
        ts = alerts.get(str(int(price)))
        if not ts:
            return True
        try:
            sent = datetime.fromisoformat(ts)
        except ValueError:
            return True
        return (datetime.now() - sent) >= timedelta(hours=ALERT_DEDUPE_HOURS)

    def mark_alerted(self, route_key: str, price: float) -> None:
        entry = self.data.setdefault(route_key, {
            "all_time_min": price, "last_seen": date.today().isoformat(),
            "last_price": price, "alerts": {}, "history": [],
        })
        entry.setdefault("alerts", {})[str(int(price))] = datetime.now().isoformat()

    # -- Amadeus počítadlo -----------------------------------------------
    def amadeus_usage(self, month: Optional[str] = None) -> int:
        month = month or datetime.now().strftime("%Y-%m")
        return (
            self.data.get(META_KEY, {})
            .get("amadeus_requests", {})
            .get(month, 0)
        )

    def add_amadeus_usage(self, count: int, month: Optional[str] = None) -> None:
        self._add_usage("amadeus_requests", count, month)

    def skyscrapper_usage(self, month: Optional[str] = None) -> int:
        month = month or datetime.now().strftime("%Y-%m")
        return (
            self.data.get(META_KEY, {})
            .get("skyscrapper_requests", {})
            .get(month, 0)
        )

    def add_skyscrapper_usage(self, count: int, month: Optional[str] = None) -> None:
        self._add_usage("skyscrapper_requests", count, month)

    def _add_usage(self, meta_field: str, count: int,
                   month: Optional[str] = None) -> None:
        month = month or datetime.now().strftime("%Y-%m")
        meta = self.data.setdefault(META_KEY, {})
        reqs = meta.setdefault(meta_field, {})
        reqs[month] = reqs.get(month, 0) + count

    # -- iterace pro souhrn ----------------------------------------------
    def routes(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, v) for k, v in self.data.items() if k != META_KEY]
