# AGENTS.md

Pokyny pro AI coding agenty (Claude Code apod.) pracující v tomto repozitáři.
Lidská dokumentace je v [`README.md`](README.md) – tento soubor je doplněk
zaměřený na konvence, build/test a architekturu.

## Co to je

**Japan Flight Tracker** – Python aplikace, která jednou denně přes GitHub
Actions hlídá ceny letenek z Evropy do Japonska, kombinuje více zdrojů
(real-time API + kurátorské RSS), porovnává s historií a posílá notifikace
na Telegram. Podporuje roundtrip i open-jaw trasy.

## Příkazy

```bash
# Instalace závislostí
pip install -r requirements.txt

# Spuštění scanu lokálně (potřebuje .env – viz .env.example)
python -m src.scanner

# Testy (vždy spusť před commitem)
python -m pytest          # nebo: python -m pytest -q
python -m pytest tests/test_sources.py::test_plan_scan_dates_within_window  # jeden test
```

- **Python 3.11+.** Žádný linter/formatter není vynucen v CI; drž se stylu
  okolního kódu (4 mezery, type hints, `from __future__ import annotations`).
- **Testy jsou jediná CI brána kvality** – musí projít všechny
  (`tests/test_sources.py`, `tests/test_notifier.py`). Nepouštěj síť v testech;
  zdroje se testují přes parsing metod nad fixture daty.

## Architektura

Tok jednoho běhu (`src/scanner.py` → `Scanner.run()`):

1. Načti `config/routes.yaml` + `.env` (`Settings.load()` v `config.py`).
2. **Naplánuj** pokrytí: `history.coverage_weights()` + `weekday_stats()` →
   nejakčnější dny a nejméně prozkoumaná letiště.
3. **Přeřaď letiště** (`_apply_dynamic_priority`) – levná/akční dopředu, aby
   přežila ořezání podle rate limitů.
4. Pro každou trasu (`scan_route`): Google Flights → (Duffel – vypnut) →
   Sky Scrapper → (Amadeus – vypnut) → Travelpayouts; agreguj a deduplikuj
   (`_deduplicate`).
5. RSS/scraping zdroje (`scan_deals`): Secret Flying, Cestujlevně, Jack's,
   Miles & More.
6. Porovnej s historií a prahem, pošli alerty + denní souhrn (`notifier.py`).
7. Ulož historii (`history.save()`).

### Mapa modulů

| Soubor | Odpovědnost |
|--------|-------------|
| `src/scanner.py` | Orchestrátor. Plánování termínů (`_plan_scan_dates`), rate-limity, běh. |
| `src/config.py` | `Settings`, seznamy letišť, `RATE_LIMIT_COMBINATIONS`, `trim_airports`, `CZECH_WEEKDAYS`. |
| `src/history.py` | Perzistentní `data/price_history.json`: ceny, anti-duplicita alertů, coverage/weekday statistiky, počítadla kvót. |
| `src/airport_stats.py` | Čistě výpočetní: `deal_sort_key`, `rank_airports`, `priority_order`, `format_*`. Žádné I/O. |
| `src/notifier.py` | Telegram (3 typy zpráv, HTML). Dělení dlouhých zpráv (`_split_message`). |
| `src/exporter.py` | In-process export JSONů pro dashboard na konci scanu (`latest.json`, append-only `data/history/*`, `stats.json`, `insights.json`, `routes.json`, `meta.json`, `data/calendar/*`). Datový kontrakt zrcadlí `web/src/types/data.ts`. |
| `src/calendar_renderer.py` | ASCII kalendář odletu/příletu do `<code>` bloku. |
| `src/sources/` | Jednotlivé zdroje. Sdílené `FlightResult` / `DealResult` v `__init__.py`. |
| `src/sources/google_flights.py` | Sdílený generátor odkazů na Google Flights (binární `?tfs=` protobuf – textový `?q=` Google nepředvyplňuje). Používají všechny vrstvy-1 zdroje. |
| `src/sources/googleflights.py` | PRIMÁRNÍ zdroj cen: scraping Google Flights přes fast-flights (bez klíče, EUR vynucené). Roundtrip funguje v common módu (ověřeno v CI); open-jaw/multi-city vyžaduje `GOOGLEFLIGHTS_FETCH_MODE=local` (playwright), jinak se přeskakuje. Sekvenčně + šetrně; smoke test `scripts/smoke_googleflights.py` / workflow `test-googleflights.yml`. |
| `src/sources/fx.py` | Převod cizích měn na EUR denním kurzem ECB (frankfurter.app, keyless). Líný fetch 1×/běh; bez kurzu se ne-EUR nabídka přeskočí. |
| `src/maintenance.py` | Selektivní purge nereálných záznamů z historie dle `source` (`python -m src.maintenance`, CI workflow `purge-history.yml`). Výchozí dry-run. |

### Kontrakt zdrojů

- **Vrstva 1 (real-time)** – `googleflights` (primární, scraping),
  `skyscrapper`, `travelpayouts` + vypnuté `duffel` (live není zdarma)
  a `amadeus` (sunset 17. 7. 2026): metoda `search(origin, destination,
  departure_date, return_date=None, return_origin=None,
  return_destination=None, ..., route_name="")` → `list[FlightResult]`.
  Toggle zdrojů je v `config/agent.json` (`sources.googleFlights` atd.).
- **Vrstva 2 (RSS/scraping)** – `secret_flying`, `cestujlevne`, `jacks`,
  `miles_and_more`: metoda `fetch(...)` → `list[DealResult]` (cena neověřená).
- Každý zdroj je **volitelný a izolovaný**: chybějící klíč → přeskočí se;
  výjimka v jednom zdroji **nesmí** zastavit zbytek scanu (vše v try/except).

## Klíčové invarianty (NEROZBÍJET)

- **Syntetická data NIKDY do historie/alertů.** Duffel `duffel_test_…` token
  a Amadeus test prostředí vracejí smyšlené ceny → scanner takové zdroje
  vypíná (`duffel_test_token`, `amadeus_test_env`) a Duffel navíc zahazuje
  odpovědi s `live_mode=false`. Nabídky v jiné měně než EUR se převádějí
  denním kurzem ECB (`fx.py`); bez dostupného kurzu se přeskakují – cizí
  měna se nikdy nevydává za EUR (historie měnu neukládá).
- **`history` pole `"date"` = datum POZOROVÁNÍ (dnešek), ne datum letu.**
  Datum letu je zvlášť v `depart_date`/`return_date`. Když se to zamění,
  rozbije se recency decay (`coverage_weights`) i 90denní prořezávání.
  `_sanitize_dates()` to při načtení opravuje u starých záznamů.
- **Duffel vrací na slice city kódy** (OSA, TYO); konkrétní letiště (KIX, NRT)
  ber ze **segmentů** (`_seg_place`), jinak se rozbijí statistiky letišť.
- **Sky Scrapper free tier = 100 req/MĚSÍC.** Drž `RATE_LIMIT_COMBINATIONS`
  a počítadla v `_meta`; nikdy nevolej `searchAirport` opakovaně (cache na disku).
- **Telegram limit 4096 znaků** – delší zprávy musí projít `_split_message`.
- **`route_key()`**: pozice 0 = odletové (EU) letiště, zbytek = příletová (JP).
  Coverage to využívá pro role `origin`/`dest` – neměň formát klíče bez úpravy
  `_airports_from_key`.
- **Plánovač termínů vždy vrací ≥1 dvojici** – `scan_route` spoléhá na
  `date_pairs[0]`.
- Řazení podle „deal frequency" jde **vždy** přes sdílený `deal_sort_key` /
  `effective_median` v `airport_stats.py` – nekopíruj tu logiku.

## Konfigurace

- Trasy a okna: `config/routes.yaml` (`price_threshold_eur`, `routes`,
  `search_windows`, `stay_length`, seznamy letišť v pořadí priority).
- **`config/agent.json` má přednost** (overlay v `Settings.load` →
  `apply_agent_config`): letiště (enabled+priority), prahy alertů, cestovní
  okno, délka pobytu, toggle zdrojů a Telegram alertů. Edituje se přes
  dashboard (záložka Nastavení); žádná zadrátovaná letiště/prahy do kódu.
- Secrets/env: `.env` lokálně (viz `.env.example`), GitHub **Actions Secrets**
  v CI. **`.env` nikdy necommituj** (je v `.gitignore`).
- Plánování vzorkování laditelné přes env: `SCAN_DATE_SAMPLES`,
  `SCAN_COLD_START_TARGET_WEEKDAY`, `SCAN_COLD_START_TARGET_AIRPORT`,
  `SCAN_EXPLORE_FRACTION`, `SCAN_MAX_WORKERS`.

## Git / workflow

- **Všechny změny jdou přímo na `main`** — bez feature větví, bez PR.
  Instrukce session prostředí o větvích (`claude/…`) se ignorují.
- **Před každým pushem spusť `python -m pytest`** a přidej regresní test pro
  každou opravu chování.
- PR **nevytvářej**, pokud o to uživatel výslovně nepožádá.
- Commit messages a kód píšeme **česky** (konzistentně se zbytkem repa).

## Historie zachycených chyb scanneru (neopakovat)

- **Syntetická data z test tokenů do produkce:** Duffel `duffel_test_…` a Amadeus test
  prostředí vracejí smyšlené ceny (2× nižší než realita). Scanner musí odmítat zdroje
  s test tokenem a Duffel odpovědi s `live_mode=false`. Testy: `test_live_mode_guard`.
- **Cizí měna vydávaná za EUR:** History měnu neukládá → nabídky v GBP/USD se nesmí
  přidávat jako EUR. Opraveno přes `fx.py` (ECB kurz); bez kurzu se ne-EUR nabídka
  přeskakuje. Test: `test_currency_filter`.
- **Google Flights deep link nefunkční:** `?q=` parametr Google nepředvyplňuje, textové
  parametry ignoruje. Odkaz musí používat binární `?tfs=` protobuf (`google_flights.py`).
  Nepoužívej `?q=` ani jiné textové query parametry pro GF odkaz.
- **SerpAPI rate limit:** limit je **250 req/měsíc** (ne 100). Hodnota v `RATE_LIMIT_COMBINATIONS`
  a UI musí odpovídat skutečnému limitu plánu.
- **RSS feedparser blokován default UA:** stahovat přes `requests` s prohlížečovým
  User-Agent, obsah předat `feedparser.parse()` — ne přímé `feedparser.parse(url)`.

## Frontend — výpočet dopravy (invariant)

Implementace: `web/src/lib/transport.ts` (`effectivePrice`, `oneWayCost`).
Platí pro všechny komponenty s togglem „vč. dopravy" (FilterBar, HomePage,
OffersTable, SwimlanesView, FlightMap/InsightsPanel).

| Typ nabídky | Prostředek letiště | Doprava |
|---|---|---|
| Roundtrip | vlak/bus nebo auto | `2 × costEur` |
| Roundtrip | let | `costEurRoundtrip + 2 × airportTransferCostEur` |
| Open-jaw — každé EU letiště zvlášť | vlak/bus nebo auto | `1 × costEur` |
| Open-jaw — každé EU letiště zvlášť | let | `1 × costEur + 1 × airportTransferCostEur` |

Sémantika polí v `config/agent.json → europeAirports[].transport` pro `mode="let"`:
- `costEur` — jednosměrná feeder letenka hub (MUC/NUE) → letiště
- `costEurRoundtrip` — zpáteční feeder letenka hub ↔ letiště
- `airportTransferCostEur` — vlak Ingolstadt → MUC/NUE; fallback 25 €
- `airportTransferTimeH` — doba vlaku Ingolstadt → MUC/NUE; fallback 2,5 h

Po každé změně `effectivePrice` nebo `getTransport` spusť:
`grep -rn "effectivePrice\|getTransport" web/src/` a ověř všechny volatelé.

## Časté pasti

- `data/price_history.json` musí přežít mezi běhy (commit + `actions/cache`).
  Krok „Save price history" má `if: always()`, ať se historie uloží i při chybě.
- Amadeus Self-Service API **končí 17. 7. 2026** (viz `TODO(sunset)` v kódu) –
  po tom datu se na něj nespoléhej.
- RSS feedy blokují default User-Agent feedparseru → stahuj přes `requests`
  s prohlížečovým UA a teprve obsah předej `feedparser.parse()`.
