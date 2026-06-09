"""Dynamická priorita letišť podle historických cen.

Z historie cen (`PriceHistory`) se spočítá průměrná/mediánová cena pozorovaná
pro každé letiště. Letiště, která statisticky vycházejí levněji, dostanou
vyšší prioritu – díky tomu přežijí adaptivní ořezávání podle rate limitů
(`trim_airports` ořezává od konce seznamu).

Modul je čistě výpočetní (žádné I/O, žádná síť) a snadno testovatelný.
"""
from __future__ import annotations

from typing import Optional

from .config import airport_name

# Minimální počet pozorování, aby se letiště bralo jako statisticky podložené.
# Letiště s méně daty si drží původní (konfigurační) prioritu.
MIN_SAMPLES = 3


def rank_airports(airports: list[str], stats: dict[str, dict],
                  min_samples: int = MIN_SAMPLES) -> list[str]:
    """Přeřadí letiště tak, že staticky levnější jdou dopředu.

    Letiště s dostatkem dat (>= min_samples) se seřadí vzestupně dle průměrné
    ceny. Letiště bez dostatku dat si zachovají původní pořadí a zařadí se
    AŽ ZA seřazená (nepředbíhají na základě náhody z malého vzorku).
    """
    rated = [
        a for a in airports
        if stats.get(a, {}).get("count", 0) >= min_samples
    ]
    rated.sort(key=lambda a: stats[a]["avg"])
    rated_set = set(rated)
    rest = [a for a in airports if a not in rated_set]  # zachová původní pořadí
    return rated + rest


def format_airport_stats(airports: list[str], stats: dict[str, dict],
                         min_samples: int = MIN_SAMPLES) -> list[str]:
    """Vytvoří řádky pro zobrazení uživateli – letiště od nejlevnějšího po
    nejdražší. Letiště bez dat se vypíšou na konci jako "bez dat".

    Příklad řádku: "💚 Mnichov (MUC): ⌀ 489 EUR (min 450, n=12)"
    """
    rated = [a for a in airports if stats.get(a, {}).get("count", 0) >= min_samples]
    rated.sort(key=lambda a: stats[a]["avg"])
    lines: list[str] = []
    for idx, a in enumerate(rated):
        s = stats[a]
        marker = ""
        if len(rated) >= 2 and idx == 0:
            marker = "💚 "          # nejlevnější
        elif len(rated) >= 2 and idx == len(rated) - 1:
            marker = "💸 "          # nejdražší
        lines.append(
            f"{marker}{airport_name(a)} ({a}): ⌀ {s['avg']:.0f} EUR "
            f"(min {s['min']:.0f}, n={s['count']})"
        )
    no_data = [a for a in airports if a not in set(rated)]
    if no_data:
        codes = ", ".join(no_data)
        lines.append(f"❔ Bez dostatku dat: {codes}")
    return lines
