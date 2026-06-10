"""Převod měn na EUR přes denní referenční kurzy ECB (frankfurter.app).

Historie cen měnu neukládá (vše je EUR), takže nabídku v cizí měně je nutné
buď převést, nebo zahodit. Frankfurter je free a keyless (publikuje kurzy
Evropské centrální banky) → žádné pravidelné náklady, žádný API klíč.

Chování při výpadku: kurzy se stahují líně (až při první ne-EUR nabídce)
a JEDNOU za běh. Když fetch selže, ``to_eur`` vrací None a volající nabídku
přeskočí – denní referenční kurz je pro sledování cen letenek dost přesný,
ale smyšlený/zastaralý kurz by zkreslil alerty, takže bez kurzu raději nic.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Bez parametrů vrací kurzy s bází EUR (ECB) – imunní vůči přejmenování
# query parametrů mezi verzemi API.
FRANKFURTER_URL = "https://api.frankfurter.app/latest"
_TIMEOUT = 15


class FxRates:
    """Líné, per-běh cachované kurzy EUR→měna z ECB."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
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

    def _get_rates(self) -> Optional[dict[str, float]]:
        if self._rates is not None or self._fetch_failed:
            return self._rates
        try:
            resp = self.session.get(FRANKFURTER_URL, timeout=_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json().get("rates", {})
            self._rates = {
                code: float(value) for code, value in raw.items()
                if isinstance(value, (int, float)) and value > 0
            }
            logger.info("FX: načteno %d kurzů ECB (frankfurter.app).",
                        len(self._rates))
        except (requests.RequestException, ValueError) as exc:
            # Jen jednou za běh – bez kurzů se ne-EUR nabídky přeskakují.
            self._fetch_failed = True
            logger.warning(
                "FX: kurzy ECB se nepodařilo stáhnout (%s) – nabídky v jiné "
                "měně než EUR budou v tomto běhu přeskočeny.", exc,
            )
        return self._rates
