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
# Poločas rozpadu váhy pozorování pro výpočet pokrytí (dny). Pozorování
# starší ~poločasu má poloviční váhu → staré ceny postupně „vyhasínají"
# a plánovač je znovu navštíví, aby data zůstala čerstvá.
COVERAGE_HALFLIFE_DAYS = 30.0


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
               on_date: Optional[date] = None,
               depart_date: Optional[date] = None,
               return_date: Optional[date] = None) -> None:
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
        rec: dict = {
            "date": on_date.isoformat(),
            "price": price,
            "source": source,
        }
        if depart_date:
            rec["depart_date"] = depart_date.isoformat()
        if return_date:
            rec["return_date"] = return_date.isoformat()
        entry.setdefault("history", []).append(rec)
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

    # -- statistika dne v týdnu ------------------------------------------
    def weekday_stats(
        self, threshold: Optional[float] = None
    ) -> dict[str, dict[int, dict]]:
        """Statistika deal-frequency per den v týdnu (0=po … 6=ne).

        Vrací dva slovníky (depart / return), každý mapuje weekday → stats dict:
          {
            "count": int,           # celkem pozorování
            "deals": int,           # počet pod prahem
            "deal_rate": float,     # podíl pod prahem
            "deal_median": float|None,  # medián cen pod prahem
            "all_median": float,    # medián všech cen (pro EUR-rozdíl)
          }

        Jen záznamy, kde je uloženo depart_date resp. return_date.
        """
        dep_acc: dict[int, list[float]] = {i: [] for i in range(7)}
        ret_acc: dict[int, list[float]] = {i: [] for i in range(7)}
        for _, entry in self.routes():
            for h in entry.get("history", []):
                price = h.get("price")
                if price is None:
                    continue
                price = float(price)
                for field, acc in (("depart_date", dep_acc), ("return_date", ret_acc)):
                    raw = h.get(field)
                    if not raw:
                        continue
                    try:
                        wd = datetime.strptime(raw, "%Y-%m-%d").weekday()
                    except ValueError:
                        continue
                    acc[wd].append(price)

        result: dict[str, dict[int, dict]] = {"depart": {}, "return": {}}
        for label, acc in (("depart", dep_acc), ("return", ret_acc)):
            for wd, prices in acc.items():
                if not prices:
                    continue
                ordered = sorted(prices)
                n = len(ordered)
                deals = [p for p in ordered if threshold is None or p < threshold]
                result[label][wd] = {
                    "count": n,
                    "deals": len(deals),
                    "deal_rate": len(deals) / n if n else 0.0,
                    "deal_median": _median(sorted(deals)) if deals else None,
                    "all_median": _median(ordered),
                }
        return result

    # -- pokrytí vzorkování (recency-decayed) ----------------------------
    def coverage_weights(
        self, halflife_days: float = COVERAGE_HALFLIFE_DAYS,
        today: Optional[date] = None,
    ) -> dict[str, dict]:
        """Vážené pokrytí jednotlivých faktorů, počítané přímo z historie.

        Každé pozorování přispívá vahou ``0.5 ** (věk_dní / halflife_days)`` –
        staré ceny „vyhasínají", takže buňka, která nebyla dlouho vzorkována,
        klesne a algoritmus plánování ji znovu navštíví (drží data čerstvá).

        Vrací::

            {
              "depart_wd": {0..6: vážený počet},
              "return_wd": {0..6: vážený počet},
              "airport":   {kód: vážený počet},
            }
        """
        today = today or date.today()
        cov: dict[str, dict] = {
            "depart_wd": {i: 0.0 for i in range(7)},
            "return_wd": {i: 0.0 for i in range(7)},
            "airport": {},
        }
        for key, entry in self.routes():
            airports = self._airports_from_key(key)
            for h in entry.get("history", []):
                w = _decay_weight(h.get("date"), today, halflife_days)
                if w <= 0:
                    continue
                for field, covkey in (("depart_date", "depart_wd"),
                                      ("return_date", "return_wd")):
                    raw = h.get(field)
                    if not raw:
                        continue
                    try:
                        wd = datetime.strptime(raw, "%Y-%m-%d").weekday()
                    except ValueError:
                        continue
                    cov[covkey][wd] += w
                for a in airports:
                    cov["airport"][a] = cov["airport"].get(a, 0.0) + w
        return cov

    # -- iterace pro souhrn ----------------------------------------------
    def routes(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, v) for k, v in self.data.items() if k != META_KEY]

    # -- statistika cen per letiště --------------------------------------
    @staticmethod
    def _airports_from_key(route_key: str) -> list[str]:
        """Z route_key (např. 'MUC-KIX-roundtrip' nebo 'MUC-KIX-NRT-openjaw')
        vytáhne zúčastněná letiště (bez koncového typu)."""
        parts = route_key.split("-")
        if parts and parts[-1] in ("roundtrip", "openjaw"):
            parts = parts[:-1]
        return [p for p in parts if p]

    def airport_stats(
        self, threshold: Optional[float] = None
    ) -> dict[str, dict[str, float]]:
        """Spočítá statistiku pozorovaných cen pro každé letiště napříč všemi
        trasami v historii. Vrací {kód: {count, avg, min, median, ...}}.

        Cena trasy se přičítá každému letišti, které se na trase podílí –
        slouží jako proxy pro to, jak "akční" letiště bývá.

        Je-li zadán ``threshold`` (práh pro deal), přidá navíc:
        - ``deals``       – počet pozorování pod prahem,
        - ``deal_rate``   – podíl pozorování pod prahem (0–1),
        - ``deal_median`` – medián cen pod prahem (None, pokud žádné nejsou).
        Tato metrika lépe modeluje cíl aplikace (najít dealy) než průměr –
        letiště s mnoha akčními letenkami se prosadí i přes vysoký průměr.
        """
        acc: dict[str, list[float]] = {}
        for key, entry in self.routes():
            airports = self._airports_from_key(key)
            for h in entry.get("history", []):
                price = h.get("price")
                if price is None:
                    continue
                for a in airports:
                    acc.setdefault(a, []).append(float(price))

        stats: dict[str, dict[str, float]] = {}
        for a, prices in acc.items():
            ordered = sorted(prices)
            n = len(ordered)
            stats[a] = {
                "count": n,
                "avg": sum(prices) / n,
                "min": min(prices),
                "median": _median(ordered),
            }
            if threshold is not None:
                deals = [p for p in ordered if p < threshold]
                stats[a]["deals"] = len(deals)
                stats[a]["deal_rate"] = len(deals) / n
                stats[a]["deal_median"] = _median(deals) if deals else None
        return stats


def _decay_weight(raw_date: Optional[str], today: date,
                  halflife_days: float) -> float:
    """Váha pozorování podle stáří (0.5 na poločas). Záznam bez data nebo
    s nečitelným datem se počítá plnou vahou (1.0)."""
    if not raw_date:
        return 1.0
    try:
        d = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        return 1.0
    age = (today - d).days
    if age <= 0:
        return 1.0
    return 0.5 ** (age / halflife_days)


def _median(ordered: list[float]) -> float:
    """Medián z předem seřazeného seznamu (prázdný → 0.0)."""
    n = len(ordered)
    if n == 0:
        return 0.0
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2
