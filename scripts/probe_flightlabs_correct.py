"""Read-only probe goflightlabs podle SPRÁVNÉHO Skyscanner-style kontraktu.

Smysl: na malém vzorku (cap ~6 requestů, JEDNA trasa) ověřit, jestli při
správném volání dostáváme reálné spoje – na rozdíl od produkčního zdroje
(`src/sources/flightlabs.py`), který posílá `originIATACode`/`destinationIATACode`
a poll dělá re-submitem celého hledání (špalil kvótu, 429, 0 výsledků).

Správný flow (goflightlabs = Skyscanner klon):
  1. GET /searchAirport?query=MUC   → skyId + entityId   (1 req / letiště)
  2. GET /retrieveFlights?originSkyId&originEntityId&destinationSkyId&
         destinationEntityId&date[&returnDate&adults&currency&cabinClass]
     → 200 {context:{status, sessionId, totalResults}, itineraries:[{price:{raw},legs}]}
  3. když context.status == "incomplete": GET /retrieveFlightsIncomplete?sessionId=…
     (poll STEJNÝM sessionId, NE re-submit celého hledání) až do "complete".

NEdělá nic destruktivního: nezapisuje historii, neposílá Telegram, necommituje.
Vypisuje surové (zkrácené) odpovědi, ať vidíme skutečný tvar a field names.

Spuštění (v CI, kde je FLIGHTLABS_KEY): python -m scripts.probe_flightlabs_correct
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Optional

import requests

from src.config import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("probe_flightlabs_correct")

BASE = "https://www.goflightlabs.com"
SEARCH_AIRPORT_URL = f"{BASE}/searchAirport"
RETRIEVE_FLIGHTS_URL = f"{BASE}/retrieveFlights"
RETRIEVE_INCOMPLETE_URL = f"{BASE}/retrieveFlightsIncomplete"

# Jedna trasa, termíny v cestovním okně (září–prosinec 2026).
ORIGIN_IATA = "MUC"
DEST_IATA = "NRT"
DEPART = "2026-11-12"
RETURN = "2026-11-26"

MAX_REQUESTS = 6          # tvrdý strop – ať se nespálí víc, než uživatel schválil
INCOMPLETE_POLLS = 3      # kolik dotažení sessionId po prvním "incomplete"
POLL_DELAY_S = 6.0
REQUEST_DELAY_S = 2.0


class Budget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def spend(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _dump(label: str, resp: requests.Response) -> None:
    body = resp.text or ""
    logger.info("%s → HTTP %d | %.700s", label, resp.status_code,
                body.replace("\n", " "))


def _find_airport(payload: object, iata: str) -> Optional[dict]:
    """Najde v odpovědi searchAirport položku se skyId+entityId; preferuje
    shodu skyId == IATA, jinak první nalezenou (tvar odpovědi neznáme jistě,
    proto rekurzivně)."""
    candidates: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            sky = node.get("skyId") or node.get("skyid")
            ent = node.get("entityId") or node.get("entityid")
            if sky and ent:
                candidates.append({"skyId": str(sky), "entityId": str(ent),
                                   "raw": node})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    if not candidates:
        return None
    for c in candidates:
        if c["skyId"].upper() == iata.upper():
            return c
    return candidates[0]


def resolve_airport(session: requests.Session, key: str, iata: str,
                    budget: Budget) -> Optional[dict]:
    if not budget.spend():
        logger.warning("Rozpočet vyčerpán před resolcí %s", iata)
        return None
    params = {"query": iata, "access_key": key}
    resp = session.get(SEARCH_AIRPORT_URL, params=params, timeout=40)
    _dump(f"searchAirport {iata}", resp)
    time.sleep(REQUEST_DELAY_S)
    if resp.status_code != 200:
        return None
    try:
        found = _find_airport(resp.json(), iata)
    except ValueError:
        logger.error("searchAirport %s: tělo není JSON", iata)
        return None
    if found:
        logger.info("  %s → skyId=%s entityId=%s", iata, found["skyId"],
                    found["entityId"])
    else:
        logger.warning("  %s: v odpovědi nenalezen skyId/entityId", iata)
    return found


def parse_itineraries(payload: dict) -> tuple[Optional[str], Optional[str], list]:
    """Vrátí (status, sessionId, itineraries) z context obálky."""
    ctx = payload.get("context", {}) if isinstance(payload, dict) else {}
    status = ctx.get("status")
    session_id = ctx.get("sessionId") or ctx.get("sessionid")
    its = payload.get("itineraries") if isinstance(payload, dict) else None
    if its is None and isinstance(payload.get("data"), dict):
        inner = payload["data"]
        its = inner.get("itineraries")
        ctx2 = inner.get("context", {})
        status = status or ctx2.get("status")
        session_id = session_id or ctx2.get("sessionId")
    return status, session_id, its or []


def cheapest(itineraries: list, n: int = 5) -> list[tuple]:
    rows = []
    for it in itineraries:
        if not isinstance(it, dict):
            continue
        raw = (it.get("price") or {}).get("raw")
        legs = it.get("legs") or []
        carriers = []
        for leg in legs:
            for c in (leg.get("carriers", {}) or {}).get("marketing", []) or []:
                code = c.get("alternateId") or c.get("name")
                if code:
                    carriers.append(str(code))
        rows.append((raw, len(legs), ",".join(sorted(set(carriers))) or "-"))
    rows = [r for r in rows if r[0] is not None]
    rows.sort(key=lambda r: r[0])
    return rows[:n]


def main() -> int:
    settings = Settings.load()
    if not settings.flightlabs_key:
        logger.error("FLIGHTLABS_KEY není nastaven – nelze probovat.")
        return 1
    key = settings.flightlabs_key

    session = requests.Session()
    budget = Budget(MAX_REQUESTS)

    origin = resolve_airport(session, key, ORIGIN_IATA, budget)
    dest = resolve_airport(session, key, DEST_IATA, budget)
    if not origin or not dest:
        logger.error("Resolce letiště selhala → nelze volat retrieveFlights "
                     "(použito %d req).", budget.used)
        return 0

    if not budget.spend():
        logger.warning("Rozpočet vyčerpán před retrieveFlights.")
        return 0
    params = {
        "originSkyId": origin["skyId"],
        "originEntityId": origin["entityId"],
        "destinationSkyId": dest["skyId"],
        "destinationEntityId": dest["entityId"],
        "date": DEPART,
        "returnDate": RETURN,
        "adults": 1,
        "currency": "EUR",
        "cabinClass": "economy",
        "access_key": key,
    }
    resp = session.get(RETRIEVE_FLIGHTS_URL, params=params, timeout=60)
    _dump("retrieveFlights", resp)
    time.sleep(REQUEST_DELAY_S)
    if resp.status_code != 200:
        logger.error("retrieveFlights vrátil HTTP %d (použito %d req).",
                     resp.status_code, budget.used)
        return 0

    payload = resp.json()
    status, session_id, its = parse_itineraries(payload)
    logger.info("retrieveFlights: status=%s sessionId=%s itineraries=%d",
                status, (session_id or "")[:12], len(its))

    # Poll incomplete přes sessionId (NE re-submit), dokud nemáme complete
    # nebo nedojde rozpočet.
    polls = 0
    while (status == "incomplete" and session_id and polls < INCOMPLETE_POLLS
           and budget.used < budget.limit):
        time.sleep(POLL_DELAY_S)
        if not budget.spend():
            break
        polls += 1
        ip = {"sessionId": session_id, "access_key": key}
        r = session.get(RETRIEVE_INCOMPLETE_URL, params=ip, timeout=60)
        _dump(f"retrieveFlightsIncomplete #{polls}", r)
        time.sleep(REQUEST_DELAY_S)
        if r.status_code != 200:
            logger.warning("incomplete poll #%d: HTTP %d", polls, r.status_code)
            break
        payload = r.json()
        status, sid2, its = parse_itineraries(payload)
        session_id = sid2 or session_id
        logger.info("  poll #%d: status=%s itineraries=%d", polls, status,
                    len(its))

    logger.info("VÝSLEDEK: status=%s, %d itinerářů, %d requestů celkem.",
                status, len(its), budget.used)
    for raw, nlegs, carriers in cheapest(its):
        logger.info("  %.0f EUR | %d legů | %s", raw, nlegs, carriers)
    if not its:
        logger.warning("Žádné itineráře – viz surová těla výše pro skutečný tvar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
