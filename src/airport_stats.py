"""Dynamická priorita letišť podle historie – metrika "deal frequency".

Z historie cen (`PriceHistory`) se pro každé letiště spočítá, jak často mívá
ceny **pod prahem** (podíl akčních letenek = ``deal_rate``). Letiště, která
nejčastěji generují dealy, dostanou vyšší prioritu – díky tomu přežijí
adaptivní ořezávání podle rate limitů (`trim_airports` ořezává od konce).

Proč ne průměr? Letiště může mít vysoký průměr (drahé základní ceny), ale
zároveň hodně výprodejů pod prahem – právě o ty nám jde. Průměr by je
nespravedlivě potopil. ``deal_rate`` je odolný vůči drahým outlierům a přímo
modeluje cíl aplikace (najít dealy). Tiebreaker je medián cen dealů
(levnější dealy = lepší). Letiště bez dealů se řadí dle celkového mediánu.

Modul je čistě výpočetní (žádné I/O, žádná síť) a snadno testovatelný.
"""
from __future__ import annotations

from .config import airport_name

# Minimální počet pozorování, aby se letiště bralo jako statisticky podložené.
# Letiště s méně daty si drží původní (konfigurační) prioritu.
MIN_SAMPLES = 3


def _sort_key(stats: dict, airport: str):
    """Řadicí klíč: nejdřív vyšší podíl dealů, pak levnější medián dealu.

    Letiště bez dealů (deal_rate 0) se mezi sebou seřadí dle celkového
    mediánu (blíž k prahu = perspektivnější).
    """
    s = stats.get(airport, {})
    deal_rate = s.get("deal_rate", 0.0)
    tie = s.get("deal_median")
    if tie is None:  # žádné dealy → fallback na celkový medián
        tie = s.get("median", float("inf"))
    return (-deal_rate, tie)


def rank_airports(airports: list[str], stats: dict[str, dict],
                  min_samples: int = MIN_SAMPLES) -> list[str]:
    """Přeřadí letiště tak, že ta s nejvyšším podílem dealů jdou dopředu.

    Letiště s dostatkem dat (>= min_samples) se seřadí dle ``deal_rate``
    (sestupně), tiebreaker medián dealu (vzestupně). Letiště bez dostatku dat
    si zachovají původní pořadí a zařadí se AŽ ZA seřazená (nepředbíhají na
    základě náhody z malého vzorku).
    """
    rated = [
        a for a in airports
        if stats.get(a, {}).get("count", 0) >= min_samples
    ]
    rated.sort(key=lambda a: _sort_key(stats, a))
    rated_set = set(rated)
    rest = [a for a in airports if a not in rated_set]  # zachová původní pořadí
    return rated + rest


def priority_order(airports: list[str], stats: dict[str, dict],
                   airport_coverage: dict[str, float],
                   cold_target: float, min_samples: int = MIN_SAMPLES) -> list[str]:
    """Pořadí letišť pro scan: nejdřív průzkum, pak exploitace.

    Letiště s nedostatečným *čerstvým* pokrytím (vážený počet < ``cold_target``)
    se dají DOPŘEDU (vzestupně dle pokrytí = nejméně prozkoumaná první), aby
    rychle nasbírala data a přežila ořezání dle rate limitů. Zbytek se seřadí
    klasicky podle ``deal_rate`` (rank_airports). Jakmile mají všechna letiště
    dost dat, ``under`` je prázdný a vrací se čistě exploit pořadí.
    """
    under = sorted(
        (a for a in airports if airport_coverage.get(a, 0.0) < cold_target),
        key=lambda a: airport_coverage.get(a, 0.0),
    )
    under_set = set(under)
    ranked = [a for a in rank_airports(airports, stats, min_samples)
              if a not in under_set]
    return under + ranked


def format_airport_stats(airports: list[str], stats: dict[str, dict],
                         min_samples: int = MIN_SAMPLES) -> list[str]:
    """Vytvoří řádky pro zobrazení uživateli – letiště od nejakčnějšího po
    nejméně akční. Letiště bez dat se vypíšou na konci jako "bez dat".

    Příklad řádku:
      "💚 Osaka (KIX): 30 % pod prahem (12/40), medián dealu 470 EUR"
      "Frankfurt (FRA): 5 % pod prahem (2/40), ⌀ 690 EUR"
    """
    rated = [a for a in airports if stats.get(a, {}).get("count", 0) >= min_samples]
    rated.sort(key=lambda a: _sort_key(stats, a))
    lines: list[str] = []
    for idx, a in enumerate(rated):
        s = stats[a]
        marker = ""
        if len(rated) >= 2 and idx == 0:
            marker = "💚 "          # nejvíc dealů
        elif len(rated) >= 2 and idx == len(rated) - 1:
            marker = "💸 "          # nejmíň dealů
        lines.append(f"{marker}{airport_name(a)} ({a}): {_describe(s)}")
    no_data = [a for a in airports if a not in set(rated)]
    if no_data:
        codes = ", ".join(no_data)
        lines.append(f"❔ Bez dostatku dat: {codes}")
    return lines


_WEEKDAY_CZ = ["po", "út", "st", "čt", "pá", "so", "ne"]


def format_weekday_stats(weekday_stats: dict[str, dict[int, dict]]) -> list[str]:
    """Formátuje statistiku dní v týdnu pro Telegram.

    Pro každý směr (odlet / přílet) vypíše nejlepší den a pro ostatní dny
    rozdíl mediánu dealu oproti nejlepšímu dni (v EUR).

    Vrací prázdný seznam, pokud nejsou dostatečná data.
    """
    lines: list[str] = []
    labels = [("depart", "Odlet"), ("return", "Přílet")]
    for field, title in labels:
        wd_data = weekday_stats.get(field, {})
        # Potřebujeme alespoň 2 dny s daty a celkový počet záznamů >= 5.
        if len(wd_data) < 2 or sum(s["count"] for s in wd_data.values()) < 5:
            continue
        # Seřaď dny dle deal_rate sestupně, tiebreaker deal_median vzestupně.
        def _key(item):
            wd, s = item
            dm = s["deal_median"] if s["deal_median"] is not None else s["all_median"]
            return (-s["deal_rate"], dm)
        sorted_days = sorted(wd_data.items(), key=_key)
        best_wd, best_s = sorted_days[0]
        best_median = (best_s["deal_median"] if best_s["deal_median"] is not None
                       else best_s["all_median"])
        lines.append(f"📅 <b>Nejlevnější den – {title}:</b>")
        best_pct = best_s["deal_rate"] * 100
        lines.append(
            f"  🏆 {_WEEKDAY_CZ[best_wd].upper()}: "
            f"{best_pct:.0f} % dealů, medián {best_median:.0f} EUR"
        )
        for wd, s in sorted_days[1:]:
            if s["count"] < 2:
                continue
            dm = s["deal_median"] if s["deal_median"] is not None else s["all_median"]
            diff = dm - best_median
            diff_str = f"+{diff:.0f}" if diff >= 0 else f"{diff:.0f}"
            pct = s["deal_rate"] * 100
            lines.append(
                f"  {_WEEKDAY_CZ[wd]}: {pct:.0f} % dealů, "
                f"medián {dm:.0f} EUR ({diff_str} EUR vs. {_WEEKDAY_CZ[best_wd]})"
            )
    return lines


def _describe(s: dict) -> str:
    """Popíše letiště dle deal frequency; fallback na průměr, když práh chybí."""
    if "deal_rate" not in s:
        # Práh nebyl zadán → zpětně kompatibilní popis průměrem.
        return (f"⌀ {s['avg']:.0f} EUR (min {s['min']:.0f}, n={s['count']})")
    pct = s["deal_rate"] * 100
    base = f"{pct:.0f} % pod prahem ({s['deals']}/{s['count']})"
    if s.get("deal_median") is not None:
        return f"{base}, medián dealu {s['deal_median']:.0f} EUR"
    return f"{base}, ⌀ {s['avg']:.0f} EUR"
