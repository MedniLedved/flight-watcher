"""Údržba dat: selektivní odstranění nereálných záznamů z historie cen.

Typický případ: scanner běžel s Duffel TEST tokenem nebo Amadeus test
prostředím → historie obsahuje syntetické (smyšlené) ceny smíchané s reálnými
záznamy ostatních zdrojů. Každý záznam nese pole ``source``, takže jde cíleně
odstranit jen zasažené zdroje a zbytek zachovat.

Co se čistí:
- ``data/price_history.json`` – interní stav scanneru (90denní okno),
  vč. přepočtu all_time_min / last_price / last_seen a smazání alert razítek,
- ``data/history/{route_key}.json`` – dlouhodobé append-only řady dashboardu.

all_time_min se po purge přepočítává z PRŮNIKU obou úložišť (dlouhodobá řada
je kanonická plná historie). Limitace: syntetický záznam starší 90 dní, který
vznikl ještě PŘED zavedením exportu (není v žádném úložišti, jen kdysi nastavil
all_time_min), odhalit nelze – u trasy bez jediného odstraněného záznamu se
all_time_min nemění.

Použití (výchozí je DRY-RUN, nic nezapisuje):

    python -m src.maintenance --sources duffel amadeus
    python -m src.maintenance --sources duffel --before 2026-06-10 --apply

V CI: workflow „Purge price history" (workflow_dispatch) – obnoví cache
s historií, spustí tento modul a při --apply uloží cache + commitne
dlouhodobé řady.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .history import PriceHistory, _parse_iso

logger = logging.getLogger(__name__)

PRICE_HISTORY_PATH = Path("data/price_history.json")
LONGTERM_DIR = Path("data/history")


def purge_longterm_records(records: list[dict], sources: set[str],
                           before: Optional[date] = None
                           ) -> tuple[list[dict], int]:
    """Filtr dlouhodobé řady: vrátí (ponechané záznamy, počet odstraněných).

    Odstraňuje záznamy s ``source`` v ``sources``; s ``before`` jen ty
    s datem pozorování před daným dnem.
    """
    kept: list[dict] = []
    for rec in records:
        hit = rec.get("source") in sources
        if hit and before is not None:
            rd = _parse_iso(rec.get("date"))
            if rd is not None and rd >= before:
                hit = False
        if not hit:
            kept.append(rec)
    return kept, len(records) - len(kept)


def purge_one_way_longterm_records(records: list[dict], route_key: str
                                   ) -> tuple[list[dict], int]:
    """Filtr dlouhodobé řady: u roundtrip/openjaw trasy odstraní záznamy bez
    ``returnDate`` (one-way pollution). Pole je camelCase – formát dlouhodobých
    řad (na rozdíl od snake_case ``return_date`` v price_history.json). Řady,
    které nejsou roundtrip/openjaw, nechá beze změny.

    Vrací (ponechané záznamy, počet odstraněných).
    """
    kind = route_key.split("-")[-1]
    if kind not in ("roundtrip", "openjaw"):
        return records, 0
    kept = [r for r in records if r.get("returnDate")]
    return kept, len(records) - len(kept)


def _min_price(records: list[dict]) -> Optional[float]:
    prices = [float(r["price"]) for r in records if r.get("price") is not None]
    return min(prices) if prices else None


def purge_history(sources: list[str] | set[str],
                  before: Optional[date] = None,
                  apply: bool = False,
                  price_history_path: Path | str = PRICE_HISTORY_PATH,
                  longterm_dir: Path | str = LONGTERM_DIR) -> dict[str, Any]:
    """Orchestrace purge nad oběma úložišti. Bez ``apply`` jen dry-run.

    Vrací souhrn::

        {
          "applied": bool,
          "price_history": {route_key: počet odstraněných},
          "longterm": {route_key: počet odstraněných},
        }
    """
    sources = set(sources)
    return _purge(
        apply,
        ph_remove=lambda h: h.purge_sources(sources, before=before),
        longterm_filter=lambda recs, key: purge_longterm_records(
            recs, sources, before),
        price_history_path=price_history_path,
        longterm_dir=longterm_dir,
    )


def purge_one_way(apply: bool = False,
                  price_history_path: Path | str = PRICE_HISTORY_PATH,
                  longterm_dir: Path | str = LONGTERM_DIR) -> dict[str, Any]:
    """Odstraní one-way pollution (roundtrip/openjaw záznamy bez návratového
    data) z OBOU úložišť. Bez ``apply`` jen dry-run. Stejný tvar souhrnu jako
    ``purge_history``. Remediace pro guard v ``scripts/validate-data.sh``."""
    return _purge(
        apply,
        ph_remove=lambda h: h.purge_one_way(),
        longterm_filter=lambda recs, key: purge_one_way_longterm_records(
            recs, key),
        price_history_path=price_history_path,
        longterm_dir=longterm_dir,
    )


def _purge(apply: bool, ph_remove, longterm_filter,
           price_history_path: Path | str = PRICE_HISTORY_PATH,
           longterm_dir: Path | str = LONGTERM_DIR) -> dict[str, Any]:
    """Sdílené jádro purge nad oběma úložišti.

    ``ph_remove(history) -> {route_key: počet}`` odstraní záznamy z
    price_history.json; ``longterm_filter(records, route_key) -> (kept, n)``
    filtruje dlouhodobou řadu. all_time_min se přepočítá z průniku obou úložišť.
    """
    history = PriceHistory(price_history_path)
    summary: dict[str, Any] = {
        "applied": apply,
        "price_history": ph_remove(history),
        "longterm": {},
    }

    # Dlouhodobé řady: filtruj a posbírej minima ponechaných záznamů
    # (kanonická plná historie → správný základ pro all_time_min).
    longterm_minima: dict[str, Optional[float]] = {}
    longterm_dir = Path(longterm_dir)
    if longterm_dir.is_dir():
        for path in sorted(longterm_dir.glob("*.json")):
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Přeskakuji nečitelný soubor %s: %s", path, exc)
                continue
            if not isinstance(records, list):
                continue
            kept, n_removed = longterm_filter(records, path.stem)
            if n_removed:
                summary["longterm"][path.stem] = n_removed
                if apply:
                    path.write_text(
                        json.dumps(kept, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            longterm_minima[path.stem] = _min_price(kept)

    # Přepočet all_time_min z průniku obou úložišť – jen u tras zasažených
    # purgem (u netknutých nechceme riskovat ztrátu reálného minima, které
    # může být starší než obě úložiště).
    affected = set(summary["price_history"]) | set(summary["longterm"])
    for key in affected:
        entry = history.data.get(key)
        if not isinstance(entry, dict):
            continue
        candidates = []
        window_min = _min_price(entry.get("history", []))
        if window_min is not None:
            candidates.append(window_min)
        longterm_min = longterm_minima.get(key)
        if longterm_min is not None:
            candidates.append(longterm_min)
        if candidates:
            entry["all_time_min"] = min(candidates)
        else:
            entry.pop("all_time_min", None)

    if apply:
        history.save()
    return summary


def _format_summary(summary: dict[str, Any]) -> str:
    lines = []
    mode = "APLIKOVÁNO" if summary["applied"] else "DRY-RUN (nic nezapsáno)"
    lines.append(f"=== Purge historie cen – {mode} ===")
    for label, counts in (("price_history.json", summary["price_history"]),
                          ("data/history/*.json", summary["longterm"])):
        total = sum(counts.values())
        lines.append(f"{label}: odstraněno {total} záznamů "
                     f"na {len(counts)} trasách")
        for key in sorted(counts):
            lines.append(f"  - {key}: {counts[key]}")
    if not summary["applied"]:
        lines.append("Pro skutečné zapsání spusť znovu s --apply.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Selektivně odstraní záznamy daných zdrojů z historie cen "
                    "(syntetická data z test režimů). Výchozí je dry-run.",
    )
    parser.add_argument("--sources", nargs="+", default=None,
                        metavar="ZDROJ",
                        help="zdroje k odstranění (např. duffel amadeus)")
    parser.add_argument("--one-way", action="store_true", dest="one_way",
                        help="odstraní one-way pollution: roundtrip/openjaw "
                             "záznamy bez návratového data (viz validate-data.sh)")
    parser.add_argument("--before", type=date.fromisoformat, default=None,
                        metavar="YYYY-MM-DD",
                        help="jen záznamy s datem pozorování PŘED tímto dnem "
                             "(platí jen pro --sources)")
    parser.add_argument("--apply", action="store_true",
                        help="skutečně zapsat změny (jinak jen dry-run výpis)")
    args = parser.parse_args(argv)
    if args.one_way:
        summary = purge_one_way(apply=args.apply)
    elif args.sources:
        summary = purge_history(args.sources, before=args.before,
                                apply=args.apply)
    else:
        parser.error("zadej --sources ZDROJ… nebo --one-way")
    print(_format_summary(summary))


if __name__ == "__main__":
    main()
