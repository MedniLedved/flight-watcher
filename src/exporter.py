"""Export dat pro statický dashboard (Fáze 0 datového kontraktu).

Běží IN-PROCESS na konci scanu (`Scanner.run()`), protože jen tam existují
živé `FlightResult` s efemérními poli (aerolinky, deep_link, open-jaw návrat).
Zapisuje hotové JSONy, které frontend pouze načítá:

- ``data/latest.json``    – nejlepší aktuální nabídky vč. efemérních polí
- ``data/history/{route_key}.json`` – kanonické dlouhodobé řady, APPEND-ONLY
  (dedup na n-tici date/source/departDate/returnDate/price, nikdy se neprořezávají)
- ``data/calendar/{route_key}.json`` – aktuální nejlepší cena per odletový den
- ``data/stats.json``     – předpočítané agregáty per trasa
- ``data/insights.json``  – cross-cutting analytika (sdílené funkce s Telegram
  souhrnem: ``PriceHistory.airport_stats`` / ``weekday_stats``)
- ``data/routes.json``    – seznam tras + souřadnice pro mapu
- ``data/meta.json``      – čas exportu, počet scanů, kvóty z ``_meta``

Veškerá agregace patří sem (CI), frontend nepočítá nic.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import CZECH_WEEKDAYS, airport_name
from .history import META_KEY, PriceHistory
from .sources import FlightResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATS_WINDOW_DAYS = 90
TREND_WINDOW_DAYS = 30
PCT_CHANGE_DAYS = 7
DEFAULT_BIG_DROP_PCT = 15.0


# -- route_key ------------------------------------------------------------
def parse_route_key(route_key: str) -> dict[str, Any]:
    """Rozparsuje route_key na složky. Zvládá 3 segmenty
    (``PRG-TYO-roundtrip``) i 4 (``MUC-KIX-OSA-openjaw``). ``origin`` /
    ``destination`` můžou být city kódy (TYO, OSA)."""
    parts = route_key.split("-")
    kind = parts[-1] if parts else ""
    if kind == "openjaw" and len(parts) == 4:
        return {
            "routeKey": route_key, "type": "openjaw",
            "origin": parts[0], "destination": parts[1],
            "returnOrigin": parts[2], "returnDestination": parts[0],
        }
    if kind == "roundtrip" and len(parts) == 3:
        return {
            "routeKey": route_key, "type": "roundtrip",
            "origin": parts[0], "destination": parts[1],
            "returnOrigin": None, "returnDestination": None,
        }
    # Neznámý tvar – nelámej export, vrať co jde.
    return {
        "routeKey": route_key, "type": kind or "unknown",
        "origin": parts[0] if parts else "",
        "destination": parts[1] if len(parts) > 1 else "",
        "returnOrigin": None, "returnDestination": None,
    }


# -- I/O helpery -----------------------------------------------------------
def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Export: nelze načíst %s: %s", path, exc)
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def _record_key(rec: dict) -> tuple:
    """Deduplikační n-tice append-only řady."""
    return (rec.get("date"), rec.get("source"), rec.get("departDate"),
            rec.get("returnDate"), rec.get("price"))


class Exporter:
    """Zapíše všechny konzumní JSONy. Volat na konci scanu, in-process."""

    def __init__(self, history: PriceHistory, settings,
                 out_dir: str | Path = "data"):
        self.history = history
        self.settings = settings
        self.out_dir = Path(out_dir)
        self.agent = getattr(settings, "agent_config", {}) or {}
        self._coords = self._build_coords()

    # -- hlavní vstup ------------------------------------------------------
    def run(self, flights: list[FlightResult],
            prev_state: Optional[dict[str, dict]] = None,
            now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        prev_state = prev_state or {}

        series = self.append_history_series()
        self.write_calendar(series)
        self.write_latest(flights, prev_state, series, today)
        self.write_stats(series, today)
        self.write_insights()
        self.write_routes(series, flights)
        self.write_meta(now)
        self.write_source_efficiency()
        logger.info("Export pro dashboard zapsán do %s/", self.out_dir)

    # -- append-only dlouhodobé řady ----------------------------------------
    def append_history_series(self) -> dict[str, list[dict]]:
        """Připojí nové záznamy z price_history.json do
        ``data/history/{route_key}.json``. Soubory se NIKDY neprořezávají;
        dedup na (date, source, departDate, returnDate, price). Vrací mapu
        route_key → kompletní akumulovaná řada (pro stats/kalendář)."""
        out: dict[str, list[dict]] = {}
        hist_dir = self.out_dir / "history"
        for key, entry in self.history.routes():
            path = hist_dir / f"{key}.json"
            existing: list[dict] = _read_json(path, [])
            seen = {_record_key(r) for r in existing}
            added = False
            for h in entry.get("history", []):
                rec: dict[str, Any] = {
                    "date": h.get("date"), "price": h.get("price"),
                    "source": h.get("source"),
                }
                if h.get("depart_date"):
                    rec["departDate"] = h["depart_date"]
                if h.get("return_date"):
                    rec["returnDate"] = h["return_date"]
                rk = _record_key(rec)
                if rk in seen:
                    continue
                seen.add(rk)
                existing.append(rec)
                added = True
            existing.sort(key=lambda r: (r.get("date") or "", r.get("price") or 0))
            if added or not path.exists():
                _write_json(path, existing)
            out[key] = existing
        return out

    # -- latest.json ---------------------------------------------------------
    def write_latest(self, flights: list[FlightResult],
                     prev_state: dict[str, dict],
                     series: dict[str, list[dict]], today: date) -> None:
        big_drop_pct = float(
            self.agent.get("alertThresholds", {}).get("bigDropPct",
                                                      DEFAULT_BIG_DROP_PCT)
        )
        # Dedup per (route_key, depart_date) — zachová více nabídek na trasu
        # (různá data odjezdu / různé zdroje), ale vždy nejlevnější za dané combo.
        best: dict[tuple, FlightResult] = {}
        for f in flights:
            depart = f.depart_date.isoformat() if f.depart_date else None
            offer_key = (f.route_key(), depart)
            if offer_key not in best or f.price < best[offer_key].price:
                best[offer_key] = f

        items: list[dict] = []
        for (route_key, _depart), f in sorted(best.items(), key=lambda kv: kv[1].price):
            key = route_key
            parsed = parse_route_key(key)
            prev = prev_state.get(key, {})
            prev_min = prev.get("all_time_min")
            prev_last = prev.get("last_price")
            delta = None if prev_last is None else f.price - prev_last
            is_big_drop = bool(
                delta is not None and prev_last
                and (-delta / prev_last) * 100.0 >= big_drop_pct
            )
            items.append({
                "routeKey": key,
                "type": parsed["type"],
                "origin": f.origin,
                "destination": f.destination,
                "returnOrigin": f.return_origin
                if parsed["type"] == "openjaw" else None,
                "returnDestination": f.return_destination
                if parsed["type"] == "openjaw" else None,
                "price": f.price,
                "source": f.source,
                "departDate": f.depart_date.isoformat() if f.depart_date else None,
                "returnDate": f.return_date.isoformat() if f.return_date else None,
                "nights": f.nights,
                # Efemérní pole – existují jen v živém scanu:
                "airlines": list(f.airlines),
                "dealUrl": f.deep_link or None,
                "observedDate": today.isoformat(),
                "flags": {
                    "isNewLow": prev_min is None or f.price < prev_min,
                    "priceDeltaEur": _round(delta),
                    "pctChange7d": self._pct_change_7d(
                        series.get(key, []), f.price, today
                    ),
                    "isBigDrop": is_big_drop,
                },
            })
        _write_json(self.out_dir / "latest.json", items)

    @staticmethod
    def _pct_change_7d(records: list[dict], price: float,
                       today: date) -> Optional[float]:
        """Změna vs. nejlepší cena pozorovaná před ≥7 dny (nejbližší starší
        den s daty). None, dokud řada nesahá aspoň 7 dní zpět."""
        cutoff = (today - timedelta(days=PCT_CHANGE_DAYS)).isoformat()
        daily = _daily_min(records)
        old_days = [d for d in daily if d <= cutoff]
        if not old_days:
            return None
        ref = daily[max(old_days)]
        if not ref:
            return None
        return round((price - ref) / ref * 100.0, 1)

    # -- calendar/{route_key}.json -------------------------------------------
    def write_calendar(self, series: dict[str, list[dict]]) -> None:
        """Aktuální nejlepší cena per odletový den: pro každé departDate vezmi
        nejnovější den pozorování a v něm minimum."""
        cal_dir = self.out_dir / "calendar"
        for key, records in series.items():
            by_depart: dict[str, dict] = {}
            for r in records:
                dep = r.get("departDate")
                if not dep or r.get("price") is None:
                    continue
                cur = by_depart.get(dep)
                obs = r.get("date") or ""
                if (cur is None or obs > cur["observedDate"]
                        or (obs == cur["observedDate"]
                            and r["price"] < cur["price"])):
                    by_depart[dep] = {
                        "departDate": dep,
                        "returnDate": r.get("returnDate"),
                        "price": r["price"],
                        "source": r.get("source"),
                        "observedDate": obs,
                    }
            days = sorted(by_depart.values(), key=lambda d: d["departDate"])
            _write_json(cal_dir / f"{key}.json", days)

    # -- stats.json ------------------------------------------------------------
    def write_stats(self, series: dict[str, list[dict]], today: date) -> None:
        stats: dict[str, dict] = {}
        for key, entry in self.history.routes():
            records = series.get(key, [])
            cutoff90 = (today - timedelta(days=STATS_WINDOW_DAYS)).isoformat()
            win90 = [r["price"] for r in records
                     if (r.get("date") or "") >= cutoff90
                     and r.get("price") is not None]
            daily = _daily_min(records)
            stats[key] = {
                "allTimeMin": entry.get("all_time_min"),
                "min90d": min(win90) if win90 else None,
                "max90d": max(win90) if win90 else None,
                "avg90d": _round(sum(win90) / len(win90)) if win90 else None,
                "trend30d": _trend_pct(daily, today),
                "biggestDrop": _biggest_drop(daily),
                "lastPrice": entry.get("last_price"),
                "currentVsAvgPct": None,
            }
            last = entry.get("last_price")
            avg = stats[key]["avg90d"]
            if last is not None and avg:
                stats[key]["currentVsAvgPct"] = round(
                    (last - avg) / avg * 100.0, 1
                )
        _write_json(self.out_dir / "stats.json", stats)

    # -- insights.json -----------------------------------------------------------
    def write_insights(self) -> None:
        """Cross-cutting analytika. Záměrně volá STEJNÉ sdílené funkce jako
        denní Telegram souhrn (`airport_stats` / `weekday_stats`), aby se
        čísla v Telegramu a na dashboardu nerozešla."""
        threshold = self.settings.price_threshold_eur
        a_stats = self.history.airport_stats(threshold=threshold)
        wd_stats = self.history.weekday_stats(threshold=threshold)

        eu_codes = {a["code"] for a in self.agent.get("europeAirports", [])}
        jp_codes = {a["code"] for a in self.agent.get("japanAirports", [])}
        jp_codes |= set(self.agent.get("cityAliases", {}))

        def _airport_rows(codes: set[str]) -> list[dict]:
            rows = []
            for code, s in a_stats.items():
                if codes and code not in codes:
                    continue
                rows.append({
                    "code": code,
                    "dealRatePct": _round(s.get("deal_rate", 0.0) * 100.0),
                    "medianEur": _round(s.get("median")),
                    "observations": int(s.get("count", 0)),
                })
            rows.sort(key=lambda r: (-(r["dealRatePct"] or 0),
                                     r["medianEur"] or 0))
            return rows

        def _dow_rows(label: str) -> list[dict]:
            rows = []
            for wd, s in wd_stats.get(label, {}).items():
                rows.append({
                    "dow": CZECH_WEEKDAYS[wd].upper(),
                    "dealRatePct": _round(s.get("deal_rate", 0.0) * 100.0),
                    "medianEur": _round(s.get("all_median")),
                })
            rows.sort(key=lambda r: (-(r["dealRatePct"] or 0),
                                     r["medianEur"] or 0))
            return rows

        _write_json(self.out_dir / "insights.json", {
            "airportPriority": {
                "europe": _airport_rows(eu_codes),
                "japan": _airport_rows(jp_codes),
            },
            "cheapestDepartureDow": _dow_rows("depart"),
            "cheapestArrivalDow": _dow_rows("return"),
        })

    # -- routes.json -----------------------------------------------------------
    def _build_coords(self) -> dict[str, dict]:
        coords: dict[str, dict] = {}
        for a in (self.agent.get("europeAirports", [])
                  + self.agent.get("japanAirports", [])):
            if a.get("code") and a.get("lat") is not None:
                coords[a["code"]] = {"lat": a["lat"], "lon": a["lon"]}
        for code, info in self.agent.get("cityAliases", {}).items():
            if info.get("lat") is not None:
                coords[code] = {"lat": info["lat"], "lon": info["lon"]}
        return coords

    def write_routes(self, series: dict[str, list[dict]],
                     flights: list[FlightResult]) -> None:
        keys = set(series) | {f.route_key() for f in flights}
        routes = []
        for key in sorted(keys):
            parsed = parse_route_key(key)
            routes.append({
                **parsed,
                "originName": airport_name(parsed["origin"]),
                "destinationName": airport_name(parsed["destination"]),
                "returnOriginName": airport_name(parsed["returnOrigin"])
                if parsed["returnOrigin"] else None,
                "coords": {
                    "origin": self._coords.get(parsed["origin"]),
                    "destination": self._coords.get(parsed["destination"]),
                    "returnOrigin": self._coords.get(parsed["returnOrigin"])
                    if parsed["returnOrigin"] else None,
                },
            })
        _write_json(self.out_dir / "routes.json", routes)

    # -- source_efficiency.json ----------------------------------------------
    def write_source_efficiency(self) -> None:
        """Exportuje per-source akumulované metriky efektivity (dealy/request).

        Odvozené hodnoty se počítají z akumulovaných počítadel v _meta.
        """
        eff_raw = self.history.source_efficiency()
        if not eff_raw:
            return
        out = {}
        for src, e in eff_raw.items():
            reqs = e.get("total_requests", 0)
            results = e.get("total_results", 0)
            deals = e.get("total_deals", 0)
            runs = e.get("runs", 0)
            out[src] = {
                "runs": runs,
                "totalResults": results,
                "totalDeals": deals,
                "totalRequests": reqs,
                "avgResultsPerRun": round(results / runs, 2) if runs else None,
                "avgDealsPerRun": round(deals / runs, 2) if runs else None,
                "avgDealsPerRequest": round(deals / reqs, 3) if reqs else None,
                "lastRun": e.get("last_run"),
            }
        _write_json(self.out_dir / "source_efficiency.json", out)

    # -- meta.json -----------------------------------------------------------
    def write_meta(self, now: datetime) -> None:
        meta_src = self.history.data.get(META_KEY, {})
        month = now.strftime("%Y-%m")
        quota = meta_src.get("quota", {})
        sky = quota.get("skyscrapper", {})
        _write_json(self.out_dir / "meta.json", {
            "lastScan": now.isoformat(timespec="seconds"),
            "scanCount": meta_src.get("scan_count", 0),
            "schemaVersion": SCHEMA_VERSION,
            "apiQuota": {
                "skyscrapper": {
                    "remaining": sky.get("remaining"),
                    "limit": sky.get("limit"),
                    "resetAt": sky.get("reset_at"),
                },
                "requestsThisMonth": {
                    "amadeus": meta_src.get("amadeus_requests", {})
                    .get(month, 0),
                    "skyscrapper": meta_src.get("skyscrapper_requests", {})
                    .get(month, 0),
                },
                "disabledUntil": meta_src.get("disabled_until", {}),
            },
        })


# -- čisté pomocné výpočty ---------------------------------------------------
def _daily_min(records: list[dict]) -> dict[str, float]:
    """Denní minimum ceny podle dne POZOROVÁNÍ (pole ``date``)."""
    daily: dict[str, float] = {}
    for r in records:
        d, p = r.get("date"), r.get("price")
        if not d or p is None:
            continue
        if d not in daily or p < daily[d]:
            daily[d] = p
    return daily


def _trend_pct(daily: dict[str, float], today: date,
               window_days: int = TREND_WINDOW_DAYS) -> Optional[float]:
    """% změna denního minima mezi prvním a posledním dnem v okně.
    None, pokud v okně nejsou aspoň 2 dny dat."""
    cutoff = (today - timedelta(days=window_days)).isoformat()
    days = sorted(d for d in daily if d >= cutoff)
    if len(days) < 2:
        return None
    first, last = daily[days[0]], daily[days[-1]]
    if not first:
        return None
    return round((last - first) / first * 100.0, 1)


def _biggest_drop(daily: dict[str, float]) -> Optional[dict]:
    """Největší pokles denního minima mezi dvěma po sobě jdoucími
    pozorovanými dny (celá řada, ne jen okno)."""
    days = sorted(daily)
    best: Optional[dict] = None
    for prev_d, cur_d in zip(days, days[1:]):
        drop = daily[prev_d] - daily[cur_d]
        if drop > 0 and (best is None or drop > best["from"] - best["to"]):
            best = {"from": daily[prev_d], "to": daily[cur_d], "date": cur_d}
    return best
