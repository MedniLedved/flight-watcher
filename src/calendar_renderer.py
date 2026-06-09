"""ASCII kalendář pro Telegram zprávy.

Generuje monospace mřížku měsíců od měsíce odletu po měsíc příletu.
Používá pouze stdlib: calendar, datetime.

Značení:
* datum odletu  -> symbol 🛫 za číslem dne (bez mezery)
* datum příletu -> symbol 🛬 za číslem dne (bez mezery)
* dny strávené v Japonsku (mezi odletem a příletem) -> '·' před číslem dne
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

CZECH_MONTHS = {
    1: "Leden", 2: "Únor", 3: "Březen", 4: "Duben",
    5: "Květen", 6: "Červen", 7: "Červenec", 8: "Srpen",
    9: "Září", 10: "Říjen", 11: "Listopad", 12: "Prosinec",
}

WEEKDAY_HEADER = "Po Út St Čt Pá So Ne"
# Šířka jednoho měsíčního bloku odpovídá hlavičce dní v týdnu.
_COL_WIDTH = len("Po Út St Čt Pá So Ne")
MAX_MONTHS_SIDE_BY_SIDE = 4


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Vrátí seznam (year, month) od start do end včetně."""
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return months


def _japan_days(depart: date, ret: date) -> set[date]:
    """Dny strávené v Japonsku (mezi odletem a příletem, vyjma krajů)."""
    days: set[date] = set()
    d = depart + timedelta(days=1)
    while d < ret:
        days.add(d)
        d += timedelta(days=1)
    return days


def _render_single_month(year: int, month: int, depart: date,
                         ret: date, japan: set[date]) -> list[str]:
    """Vrátí seznam řádků (string) pro jeden měsíc."""
    title = f"{CZECH_MONTHS[month]} {year}"
    lines = [title.center(_COL_WIDTH), WEEKDAY_HEADER]
    cal = calendar.Calendar(firstweekday=0)  # 0 = pondělí
    week_row: list[str] = []
    for day in cal.itermonthdays(year, month):
        if day == 0:
            cell = "  "
        else:
            current = date(year, month, day)
            if current == depart:
                cell = f"{day:>2}🛫"
            elif current == ret:
                cell = f"{day:>2}🛬"
            elif current in japan:
                cell = f"·{day:>2}"[-2:] if day >= 10 else f"·{day}"
            else:
                cell = f"{day:>2}"
        week_row.append(cell)
        if len(week_row) == 7:
            lines.append(_join_week(week_row))
            week_row = []
    if week_row:
        lines.append(_join_week(week_row))
    return lines


def _join_week(cells: list[str]) -> str:
    """Spojí buňky týdne. Markery (🛫/🛬) zabírají navíc, proto fixní padding
    na 2 viditelné znaky čísla + mezera."""
    parts = []
    for c in cells:
        # Normalizuj na šířku 2 pro číslo (markery se přidávají za/před).
        parts.append(c)
    return " ".join(p.rjust(2) if len(p) <= 2 else p for p in parts)


def render_calendar(depart_date: date, return_date: date) -> str:
    """Vyrenderuje ASCII kalendář od měsíce odletu po měsíc příletu.

    Vrací řetězec NEzabalený do <code> tagů – obalení řeší notifier,
    aby šel kalendář použít i mimo Telegram.
    """
    if return_date < depart_date:
        depart_date, return_date = return_date, depart_date

    months = _months_between(depart_date, return_date)
    japan = _japan_days(depart_date, return_date)

    blocks = [
        _render_single_month(y, m, depart_date, return_date, japan)
        for (y, m) in months
    ]

    # Pokud je jen jeden měsíc, vrať ho přímo.
    if len(blocks) == 1:
        return "\n".join(blocks[0])

    # Rozdělíme do skupin max MAX_MONTHS_SIDE_BY_SIDE vedle sebe.
    out_lines: list[str] = []
    for i in range(0, len(blocks), MAX_MONTHS_SIDE_BY_SIDE):
        group = blocks[i:i + MAX_MONTHS_SIDE_BY_SIDE]
        height = max(len(b) for b in group)
        for b in group:
            b += [""] * (height - len(b))  # zarovnej výšku
        for row_idx in range(height):
            row = "   ".join(b[row_idx].ljust(_COL_WIDTH) for b in group)
            out_lines.append(row.rstrip())
        out_lines.append("")  # mezera mezi skupinami

    return "\n".join(out_lines).rstrip()
