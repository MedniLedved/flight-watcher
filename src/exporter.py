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
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import CZECH_WEEKDAYS, airport_name
from .history import META_KEY, PriceHistory
from .sources import FlightResult, Segment

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


def _offer_stops(f: "FlightResult", direction: str) -> Optional[int]:
    """Počet přestupů v daném směru ('out'/'in'). Bere explicitní stops_* (má
    FlightLabs), jinak odvodí ze segmentů (Skyscanner: počet úseků − 1)."""
    explicit = f.stops_out if direction == "out" else f.stops_in
    if explicit is not None:
        return explicit
    segs = f.segments_out if direction == "out" else f.segments_in
    return (len(segs) - 1) if segs else None


def _seg_to_dict(s: Segment) -> dict:
    return {
        "from": s.origin,
        "to": s.destination,
        "airline": s.airline or None,
        "durationMin": s.duration_min,
        "departAt": s.depart_at,
        "arriveAt": s.arrive_at,
        "layoverMin": s.layover_min,
    }


def _record_key(rec: dict) -> tuple:
    """Deduplikační n-tice append-only řady."""
    return (rec.get("date"), rec.get("source"), rec.get("departDate"),
            rec.get("returnDate"), rec.get("price"))


def _alt_record_key(rec: dict) -> tuple:
    """Dedup n-tice řady alternativ (rozlišuje i aerolinku – víc variant na
    stejnou cenu)."""
    return (rec.get("date"), rec.get("source"), rec.get("departDate"),
            rec.get("returnDate"), rec.get("price"),
            tuple(rec.get("airlines") or []))


def _best_historical_offer(records: list[dict],
                           require_return: bool = False) -> Optional[dict]:
    """Nejlepší (nejlevnější) záznam z nejnovějšího dne pozorování v řadě.
    Vrátí None, pokud řada neobsahuje žádný použitelný záznam.

    Používá se jako záloha pro trasy, které v aktuálním scanu neposkytly
    žádný živý výsledek, aby nezmizely z latest.json po sparsy scanu.

    ``require_return``: pro zpáteční/open-jaw trasy vynech záznamy bez
    ``returnDate`` (one-way pollution, např. starší travelpayouts data) — ty
    nesmí prosáknout do latest.json jako podhodnocená „zpáteční" nabídka.
    """
    if not records:
        return None
    usable = [r for r in records if r.get("returnDate")] if require_return else records
    if not usable:
        return None
    last_date = max((r.get("date") or "") for r in usable)
    if not last_date:
        return None
    day_records = [r for r in usable if r.get("date") == last_date
                   and r.get("price") is not None]
    if not day_records:
        return None
    return min(day_records, key=lambda r: r["price"])

def _geocode_airport(code: str, name: str, cache: dict) -> Optional[dict]:
    """Nominatim geocoding; vrací {lat, lon} nebo None. Cachuje do předaného dict."""
    if code in cache:
        return cache[code]
    query = f"{code} airport"
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({"q": query, "format": "json", "limit": "1"})
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "flight-watcher/1.0 (github.com/medniledved/flight-watcher)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(1.1)  # Nominatim rate limit: 1 req/s
        if data:
            result = {
                "lat": round(float(data[0]["lat"]), 5),
                "lon": round(float(data[0]["lon"]), 5),
            }
            cache[code] = result
            return result
    except Exception as exc:
        logger.warning("Geocoding selhalo pro %s (%s): %s", code, name, exc)
    return None


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
            now: Optional[datetime] = None,
            raw_offers: Optional[list[FlightResult]] = None) -> None:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        prev_state = prev_state or {}

        series = self.append_history_series()
        self.write_calendar(series)
        self.write_latest(flights, prev_state, series, today,
                          raw_offers=raw_offers)
        self.write_alternatives_history(raw_offers, today)
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

    def write_alternatives_history(self, raw_offers: Optional[list[FlightResult]],
                                   today: date) -> None:
        """Append-only řada DRAŽŠÍCH variant (nad nejlevnější) per trasa do
        ``data/alternatives/{route_key}.json``. ODDĚLENÉ od history/stats –
        cenové statistiky (min/avg/medián/trend) se počítají DÁL jen z nejlevnější
        (history), tahle řada slouží k sledování lepších variant (přímý let,
        prémiová aerolinka) v čase. Nikdy se neprořezává; dedup na
        (date, source, departDate, returnDate, price, airlines). Běží in-process
        (efemérní pole airlines/stops existují jen v živém scanu)."""
        if not raw_offers:
            return
        trips: dict[tuple, list[FlightResult]] = {}
        for o in raw_offers:
            if o.depart_date is None or o.price is None:
                continue
            tk = (o.route_key(), o.depart_date.isoformat(),
                  o.return_date.isoformat() if o.return_date else None)
            trips.setdefault(tk, []).append(o)

        today_iso = today.isoformat()
        new_by_route: dict[str, list[dict]] = {}
        for (route_key, dep, ret), offers in trips.items():
            offers_sorted = sorted(offers, key=lambda o: o.price)
            seen_opt: set = set()
            for o in offers_sorted[1:]:  # přeskoč nejlevnější = hlavní (v history)
                sig = (tuple(sorted(o.airlines)), round(float(o.price), 2))
                if sig in seen_opt:
                    continue
                seen_opt.add(sig)
                new_by_route.setdefault(route_key, []).append({
                    "date": today_iso, "departDate": dep, "returnDate": ret,
                    "price": _round(o.price), "source": o.source,
                    "airlines": list(o.airlines),
                    "stopsOut": _offer_stops(o, "out"),
                    "stopsIn": _offer_stops(o, "in"),
                })

        alt_dir = self.out_dir / "alternatives"
        for route_key, recs in new_by_route.items():
            path = alt_dir / f"{route_key}.json"
            existing: list[dict] = _read_json(path, [])
            seen = {_alt_record_key(r) for r in existing}
            added = False
            for rec in recs:
                k = _alt_record_key(rec)
                if k in seen:
                    continue
                seen.add(k)
                existing.append(rec)
                added = True
            existing.sort(key=lambda r: (r.get("date") or "", r.get("price") or 0))
            if added or not path.exists():
                _write_json(path, existing)

    # -- latest.json ---------------------------------------------------------
    @staticmethod
    def _alternatives_by_trip(
        raw_offers: Optional[list[FlightResult]],
    ) -> dict[tuple, list[dict]]:
        """Z (nededuplikovaného) seznamu nabídek sestaví alternativy per
        ‚zájezd' = (route_key, odlet, návrat). Pro každý termín vrátí dražší
        varianty (jiné aerolinky/zdroje) než nejlevnější – ta je hlavní nabídka
        v latest.json, alternativy se zobrazí v detailu trasy. Dedup na
        (aerolinky, cena); max 6 alternativ na termín."""
        if not raw_offers:
            return {}
        trips: dict[tuple, list[FlightResult]] = {}
        for o in raw_offers:
            if o.depart_date is None or o.price is None:
                continue
            tk = (o.route_key(), o.depart_date.isoformat(),
                  o.return_date.isoformat() if o.return_date else None)
            trips.setdefault(tk, []).append(o)
        result: dict[tuple, list[dict]] = {}
        for tk, offers in trips.items():
            offers_sorted = sorted(offers, key=lambda o: o.price)
            seen: set = set()
            alts: list[dict] = []
            for o in offers_sorted[1:]:  # přeskoč nejlevnější = hlavní nabídka
                sig = (tuple(sorted(o.airlines)), round(float(o.price), 2))
                if sig in seen:
                    continue
                seen.add(sig)
                alts.append({
                    "price": _round(o.price),
                    "airlines": list(o.airlines),
                    "source": o.source,
                    "dealUrl": o.deep_link or None,
                    "stopsOut": _offer_stops(o, "out"),
                    "stopsIn": _offer_stops(o, "in"),
                })
                if len(alts) >= 6:
                    break
            if alts:
                result[tk] = alts
        return result

    def write_latest(self, flights: list[FlightResult],
                     prev_state: dict[str, dict],
                     series: dict[str, list[dict]], today: date,
                     raw_offers: Optional[list[FlightResult]] = None) -> None:
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

        # Doplň historické zálohy pro trasy bez živého výsledku, aby sparsy scan
        # nezpůsobil zmizení tras z dashboardu. Záloha nemá efemérní pole
        # (airlines, dealUrl) a nese flag staleDays = počet dní od posledního
        # pozorování. Frontend může tato data zobrazit odlišně (šedě, s popiskem).
        live_routes = {rk for (rk, _) in best}
        stale_items: list[dict] = []
        for route_key, records in series.items():
            if route_key in live_routes:
                continue
            parsed = parse_route_key(route_key)
            require_return = parsed["type"] in ("roundtrip", "openjaw")
            hist_rec = _best_historical_offer(records, require_return=require_return)
            if hist_rec is None:
                continue
            obs_date = hist_rec.get("date") or ""
            stale_days = (today - date.fromisoformat(obs_date)).days if obs_date else None
            prev = prev_state.get(route_key, {})
            prev_min = prev.get("all_time_min")
            stale_items.append({
                "routeKey": route_key,
                "type": parsed["type"],
                "origin": parsed["origin"],
                "destination": parsed["destination"],
                "returnOrigin": parsed.get("returnOrigin"),
                "returnDestination": parsed.get("returnDestination"),
                "price": hist_rec["price"],
                "source": hist_rec.get("source", ""),
                "departDate": hist_rec.get("departDate"),
                "returnDate": hist_rec.get("returnDate"),
                "nights": (
                    (date.fromisoformat(hist_rec["returnDate"])
                     - date.fromisoformat(hist_rec["departDate"])).days
                    if hist_rec.get("departDate") and hist_rec.get("returnDate")
                    else None
                ),
                "airlines": [],
                "dealUrl": None,
                "observedDate": obs_date,
                "segments": {"out": [], "in": []},
                "durationOutMin": None,
                "durationInMin": None,
                "stopsOut": None,
                "stopsIn": None,
                "scannedPrice": None,
                "alternatives": [],
                "flags": {
                    "isNewLow": prev_min is not None and hist_rec["price"] < prev_min,
                    "priceDeltaEur": None,
                    "pctChange7d": self._pct_change_7d(records, hist_rec["price"], today),
                    "isBigDrop": False,
                    "staleDays": stale_days,
                },
            })

        alternatives_map = self._alternatives_by_trip(raw_offers)

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
                "segments": {
                    "out": [_seg_to_dict(s) for s in f.segments_out],
                    "in": [_seg_to_dict(s) for s in f.segments_in],
                },
                "durationOutMin": f.duration_out_min,
                "durationInMin": f.duration_in_min,
                "stopsOut": _offer_stops(f, "out"),
                "stopsIn": _offer_stops(f, "in"),
                "scannedPrice": _round(f.scanned_price),
                # Alternativní aerolinky/ceny na stejný termín (jen pro detail
                # trasy); hlavní nabídka výše je nejlevnější. Efemérní – live scan.
                "alternatives": alternatives_map.get(
                    (key, f.depart_date.isoformat() if f.depart_date else None,
                     f.return_date.isoformat() if f.return_date else None), []
                ),
                "flags": {
                    "isNewLow": prev_min is None or f.price < prev_min,
                    "priceDeltaEur": _round(delta),
                    "pctChange7d": self._pct_change_7d(
                        series.get(key, []), f.price, today
                    ),
                    "isBigDrop": is_big_drop,
                    "staleDays": None,
                },
            })
        # Přidej historické zálohy seřazené podle ceny, za živými nabídkami.
        stale_items.sort(key=lambda x: x["price"])
        items.extend(stale_items)
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
        per_origin = self.settings.deal_thresholds_by_origin()
        a_stats = self.history.airport_stats(
            threshold=threshold, per_origin_thresholds=per_origin
        )
        wd_stats = self.history.weekday_stats(
            threshold=threshold, per_origin_thresholds=per_origin
        )

        eu_codes = {a["code"] for a in self.agent.get("europeAirports", [])}
        jp_codes = {a["code"] for a in self.agent.get("japanAirports", [])}
        jp_codes |= set(self.agent.get("cityAliases", {}))

        # Zahrnout i letiště odstraněná z configu, ale s historickými daty
        for key, _ in self.history.routes():
            parts = key.split("-")
            if len(parts) >= 3:
                eu_codes.add(parts[0])  # origin = vždy evropské
                jp_codes.add(parts[1])  # destination = vždy japonské
                if parts[-1] == "openjaw" and len(parts) == 4:
                    jp_codes.add(parts[2])  # return_origin u openjaw = japonské

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
        cache_path = self.out_dir / "airport_coords_cache.json"
        cache: dict[str, dict] = _read_json(cache_path, {})
        coords: dict[str, dict] = {}
        cache_dirty = False

        all_airports = (
            self.agent.get("europeAirports", [])
            + self.agent.get("japanAirports", [])
        )
        for a in all_airports:
            code = a.get("code")
            if not code:
                continue
            lat = a.get("lat") or 0
            lon = a.get("lon") or 0
            if lat and lon:
                coords[code] = {"lat": lat, "lon": lon}
            else:
                result = _geocode_airport(code, a.get("name", ""), cache)
                if result:
                    coords[code] = result
                    cache_dirty = True

        if cache_dirty:
            _write_json(cache_path, cache)

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
