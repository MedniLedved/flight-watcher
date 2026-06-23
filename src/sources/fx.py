"""Převod měn na EUR přes denní referenční kurzy (ECB a zálohy).

Historie cen měnu neukládá (vše je EUR), takže nabídku v cizí měně je nutné
buď převést, nebo zahodit. Kurzy se tahají z více free/keyless zdrojů v pořadí
priority – pokud jeden selže, zkusí se další:

  1. frankfurter.app  – wrapper ECB kurzů (JSON, bez klíče)
  2. open.er-api.com  – ExchangeRate-API free tier (JSON, bez klíče)
  3. ECB direct XML   – autoritativní zdroj přímo z ECB (XML)

Chování při výpadku všech zdrojů: ``to_eur`` vrací None. Volající, kteří
nechtějí nabídku zahodit, mohou volat ``to_eur_with_fallback`` – ten zkusí
poslední úspěšně stažené kurzy (class-level cache), pak hardcoded aproximaci.
"""
from __future__ import annotations

import logging
from typing import Optional
from xml.etree import ElementTree

import requests

from .http_utils import make_api_session

logger = logging.getLogger(__name__)

_TIMEOUT = 15

# Zdroje kurzů s bází EUR, zkoušeny v pořadí dokud jeden neuspěje.
_RATE_SOURCES = [
    # 1. frankfurter.app – ECB wrapper (JSON)
    ("frankfurter", "https://api.frankfurter.app/latest"),
    # 2. open.er-api.com – free tier bez klíče (JSON, stejný tvar)
    ("open.er-api", "https://open.er-api.com/v6/latest/EUR"),
    # 3. ECB direct XML – autoritativní, ale XML
    ("ecb-xml", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"),
]


def _parse_ecb_xml(text: str) -> dict[str, float]:
    """Parsuje ECB eurofxref XML → {kod: rate_per_EUR}."""
    ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    root = ElementTree.fromstring(text)
    rates: dict[str, float] = {}
    for cube in root.iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
        currency = cube.get("currency")
        rate = cube.get("rate")
        if currency and rate:
            try:
                rates[currency] = float(rate)
            except ValueError:
                pass
    return rates


class FxRates:
    """Líné, per-běh cachované kurzy EUR→měna (ECB a záložní zdroje)."""

    # Poslední úspěšně stažené kurzy (sdílené mezi všemi instancemi v procesu).
    _last_known: Optional[dict[str, float]] = None

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_api_session()
        self._rates: Optional[dict[str, float]] = None
        self._fetch_failed = False

    def to_eur(self, amount: float, currency: str) -> Optional[float]:
        """Převede částku v ``currency`` na EUR; None když kurz není."""
        if currency == "EUR":
            return amount
        rates = self._get_rates()
        rate = rates.get(currency) if rates else None
        if not rate or rate <= 0:
            return None
        return round(amount / rate, 2)

    def to_eur_with_fallback(self, amount: float, currency: str,
                             hardcoded: Optional[dict[str, float]] = None,
                             ) -> Optional[float]:
        """Jako ``to_eur``, ale při nedostupném kurzu zkusí záložní zdroje.

        Pořadí:
        1. Aktuální živý kurz (stažený v tomto běhu z prvního dostupného zdroje).
        2. Poslední známý kurz z předchozího úspěšného fetche (_last_known).
        3. Hardcoded approximace (parametr ``hardcoded``).
        4. None – kurz nelze zjistit ani odhadnout.
        """
        if currency == "EUR":
            return amount
        result = self.to_eur(amount, currency)
        if result is not None:
            return result
        # Fallback 1 – poslední známý kurz (sdílená třídní cache).
        last = FxRates._last_known
        if last:
            rate = last.get(currency)
            if rate and rate > 0:
                logger.warning("FX: kurz %s→EUR nedostupný, použit "
                               "poslední známý (%.4f)", currency, rate)
                return round(amount / rate, 2)
        # Fallback 2 – hardcoded aproximace.
        if hardcoded:
            rate = hardcoded.get(currency)
            if rate and rate > 0:
                logger.warning("FX: kurz %s→EUR nedostupný, použit "
                               "hardcoded fallback (%.4f)", currency, rate)
                return round(amount * rate, 2)
        return None

    def _get_rates(self) -> Optional[dict[str, float]]:
        if self._rates is not None or self._fetch_failed:
            return self._rates
        for name, url in _RATE_SOURCES:
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                resp.raise_for_status()
                if name == "ecb-xml":
                    raw_rates = _parse_ecb_xml(resp.text)
                else:
                    raw_rates = resp.json().get("rates", {})
                rates = {
                    code: float(value) for code, value in raw_rates.items()
                    if isinstance(value, (int, float)) and value > 0
                }
                if rates:
                    self._rates = rates
                    FxRates._last_known = rates
                    logger.info("FX: načteno %d kurzů (%s).", len(rates), name)
                    return self._rates
            except (requests.RequestException, ValueError, ElementTree.ParseError) as exc:
                logger.warning("FX: zdroj %s selhal (%s), zkouším další.", name, exc)
        # Všechny zdroje selhaly.
        self._fetch_failed = True
        logger.warning(
            "FX: všechny zdroje kurzů selhaly – nabídky v jiné měně než EUR "
            "budou v tomto běhu přeskočeny (nebo použit fallback)."
        )
        return self._rates
