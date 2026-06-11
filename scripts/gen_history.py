#!/usr/bin/env python3
"""Generates data/history/ and data/calendar/ from data/price_history.json.

Run once to bootstrap real data without a full scanner run. Safe to re-run —
appends deduplicated records (same logic as Exporter.append_history_series).
"""
import json
from pathlib import Path

REPO = Path(__file__).parent.parent
data_dir = REPO / "data"

ph: dict = json.loads((data_dir / "price_history.json").read_text(encoding="utf-8"))


def record_key(rec: dict) -> tuple:
    return (rec.get("date"), rec.get("source"), rec.get("departDate"),
            rec.get("returnDate"), rec.get("price"))


hist_dir = data_dir / "history"
cal_dir = data_dir / "calendar"
hist_dir.mkdir(exist_ok=True)
cal_dir.mkdir(exist_ok=True)

n_routes = 0
for route_key, entry in ph.items():
    if route_key.startswith("_"):
        continue

    # Build deduplicated history records
    hist_path = hist_dir / f"{route_key}.json"
    existing: list[dict] = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    seen = {record_key(r) for r in existing}
    added = False
    for h in entry.get("history", []):
        rec: dict = {"date": h.get("date"), "price": h.get("price"), "source": h.get("source")}
        if h.get("depart_date"):
            rec["departDate"] = h["depart_date"]
        if h.get("return_date"):
            rec["returnDate"] = h["return_date"]
        rk = record_key(rec)
        if rk not in seen:
            seen.add(rk)
            existing.append(rec)
            added = True
    existing.sort(key=lambda r: (r.get("date") or "", r.get("price") or 0))
    if added or not hist_path.exists():
        hist_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Build calendar: best price per depart_date (from most recent observation)
    by_depart: dict[str, dict] = {}
    for r in existing:
        dep = r.get("departDate")
        if not dep or r.get("price") is None:
            continue
        cur = by_depart.get(dep)
        obs = r.get("date") or ""
        if (cur is None or obs > cur["observedDate"]
                or (obs == cur["observedDate"] and r["price"] < cur["price"])):
            by_depart[dep] = {
                "departDate": dep,
                "returnDate": r.get("returnDate"),
                "price": r["price"],
                "source": r.get("source"),
                "observedDate": obs,
            }
    days = sorted(by_depart.values(), key=lambda d: d["departDate"])
    (cal_dir / f"{route_key}.json").write_text(
        json.dumps(days, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    n_routes += 1

print(f"Done: generated history/ and calendar/ for {n_routes} routes.")
