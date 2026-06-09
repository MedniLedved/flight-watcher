"""Telegram notifikace.

Posílá tři typy zpráv:
1. Alert na nízkou cenu (real-time API) – s ASCII kalendářem v <code> bloku
2. Deal alert (RSS zdroje) – cena neověřená
3. Denní souhrn – posílá se vždy

Zprávy se posílají jako HTML (parse_mode="HTML"), aby byl kalendář čitelný
jako monospace blok.
"""
from __future__ import annotations

import html
import logging
from datetime import date, datetime
from typing import Optional

import requests

from .calendar_renderer import render_calendar
from .config import airport_name
from .sources import DealResult, FlightResult

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

CZECH_MONTHS_GEN = {
    1: "ledna", 2: "února", 3: "března", 4: "dubna", 5: "května", 6: "června",
    7: "července", 8: "srpna", 9: "září", 10: "října", 11: "listopadu", 12: "prosince",
}
CZECH_WEEKDAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]


def _fmt_date(d: Optional[date]) -> str:
    if not d:
        return "?"
    return f"{d.day}. {CZECH_MONTHS_GEN[d.month]} {d.year} ({CZECH_WEEKDAYS[d.weekday()]})"


class TelegramNotifier:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str],
                 session: Optional[requests.Session] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _send(self, text: str) -> bool:
        if not self.enabled:
            logger.warning("Telegram není nakonfigurován – zpráva se neodešle:\n%s", text)
            return False
        url = TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Telegram odeslání selhalo: %s", exc)
            return False
        return True

    # -- 1. Alert na nízkou cenu -----------------------------------------
    def send_price_alert(self, flight: FlightResult,
                         delta: Optional[float] = None) -> bool:
        e = html.escape
        out_from = f"{airport_name(flight.origin)} ({flight.origin})"
        out_to = f"{airport_name(flight.destination)} ({flight.destination})"
        lines = [
            "✈️ <b>NOVÁ NÍZKÁ CENA – Japonsko</b>",
            "",
            f"🛫 {e(out_from)} → {e(out_to)}",
        ]
        if flight.return_origin and flight.return_destination:
            in_from = f"{airport_name(flight.return_origin)} ({flight.return_origin})"
            in_to = f"{airport_name(flight.return_destination)} ({flight.return_destination})"
            lines.append(f"🛬 {e(in_from)} → {e(in_to)}")

        lines.append(f"🗓 Odlet: {_fmt_date(flight.depart_date)}")
        if flight.return_date:
            lines.append(f"🗓 Návrat: {_fmt_date(flight.return_date)}")
        if flight.nights is not None:
            lines.append(f"⏳ Délka pobytu: {flight.nights} dní")

        price_line = f"💶 Cena: {flight.price:.0f} {flight.currency}"
        if delta is not None and delta != 0:
            arrow = "↓" if delta < 0 else "↑"
            price_line += f" ({arrow} o {abs(delta):.0f} od posledního scanu)"
        lines.append(price_line)

        source_label = {
            "duffel": "Duffel", "skyscrapper": "Sky Scrapper",
            "amadeus": "Amadeus", "travelpayouts": "Travelpayouts",
        }.get(flight.source, flight.source)
        lines.append(f"🏢 Zdroj: {source_label}")
        if flight.airlines:
            lines.append(f"🛩 Aerolinky: {e(', '.join(flight.airlines))}")
        if flight.deep_link:
            lines.append(f'🔗 <a href="{e(flight.deep_link)}">Koupit letenku</a>')

        if flight.depart_date and flight.return_date:
            cal = render_calendar(flight.depart_date, flight.return_date)
            lines.append("")
            lines.append(f"<code>{e(cal)}</code>")

        lines.append("")
        lines.append(f"⏱ Nalezeno: {datetime.now().strftime('%d.%m. %H:%M')}")
        return self._send("\n".join(lines))

    # -- 2. Deal alert (RSS) ---------------------------------------------
    def send_deal_alert(self, deal: DealResult) -> bool:
        e = html.escape
        source_title = {
            "secretflying.com": "Secret Flying",
            "cestujlevne.com": "Cestujlevně",
            "jacksflightclub.com": "Jack's Flight Club",
        }.get(deal.source, deal.source)
        lines = [
            f"🔥 <b>DEAL – {e(source_title)}</b>",
            "",
            f"📌 {e(deal.title)}",
        ]
        if deal.price_eur:
            lines.append(f"💶 Cca {deal.price_eur:.0f} EUR")
        if deal.published:
            lines.append(f"📅 Publikováno: {deal.published.isoformat()}")
        lines.append(f"🌐 Zdroj: {e(deal.source)}")
        if deal.link:
            lines.append(f'🔗 <a href="{e(deal.link)}">Zobrazit deal</a>')
        lines.append("")
        lines.append("⚠️ Cena neověřena – zkontroluj aktuálnost")
        return self._send("\n".join(lines))

    # -- 3. Denní souhrn -------------------------------------------------
    def send_daily_summary(self, summary_lines: list[str],
                           source_status: dict[str, bool],
                           stats: dict[str, str]) -> bool:
        e = html.escape
        now = datetime.now()
        month_gen = CZECH_MONTHS_GEN[now.month]
        lines = [
            "📊 <b>Denní souhrn – Japan Flight Tracker</b>",
            f"📅 {now.day}. {month_gen} {now.year}, {now.strftime('%H:%M')}",
            "",
            "<b>Nejlepší aktuální ceny (září–prosinec 2026):</b>",
        ]
        if summary_lines:
            lines.extend(f"• {e(line)}" for line in summary_lines)
        else:
            lines.append("• (žádná data – zkontroluj konfiguraci API klíčů)")

        lines.append("")
        status_parts = []
        labels = {
            "secret_flying": "Secret Flying",
            "cestujlevne": "Cestujlevně",
            "jacks": "Jack's",
        }
        for key, label in labels.items():
            if key in source_status:
                mark = "✓" if source_status[key] else "✗ (chyba)"
                status_parts.append(f"{label} {mark}")
        if status_parts:
            lines.append("RSS zdroje: " + " | ".join(status_parts))

        for stat in stats.values():
            lines.append(e(stat))

        return self._send("\n".join(lines))
