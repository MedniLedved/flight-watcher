# CLAUDE.md

Japan Flight Tracker — sleduje ceny letenek Evropa → Japonsko (cestovní termíny
září–prosinec 2026), běží jako cron přes GitHub Actions a posílá alerty na Telegram.
Backend je Python (`scanner.py` + `src/`). Cíl projektu: postavit nad ním statický
React/TS analytický dashboard (viz plné zadání níže).

Vývojářský přehled kódu (moduly, příkazy, source interface, invarianty) je v `AGENTS.md` —
přečti ho.

## Tvrdá architektonická pravidla (neporušovat)

- **Žádný backend server, žádná DB v prohlížeči.** Dashboard je statický build, který jen
  načítá hotové JSONy. Žádné FastAPI/SQLite/DuckDB-WASM.
- **Veškerá agregace a výpočty patří do `scanner.py`** (běží v CI), ne do frontendu.
  Frontend je „hloupý": načte JSON, vykreslí, filtruje klientsky v paměti.
- **Export běží in-process na konci scanu** — jen tam jsou živé `FlightResult` s efemérními
  poli (aerolinky, deep_link, open-jaw návrat). Mimo proces tato pole neexistují.
- **`data/history/{route_key}.json` je append-only** a nikdy se neprořezává (dedup na n-tici
  `date, source, depart_date, return_date, price`). `data/price_history.json` má retenci 90 dní —
  není zdrojem dlouhodobých řad.
- **Konfigurace agenta žije v `config/agent.json`** v repu; scanner ji čte při běhu. Žádná
  zadrátovaná letiště/prahy v kódu.
- **Měna je vždy EUR** (neukládá se). Datum `date` v historii = den pozorování, ne čas dne,
  ne datum letu (to jsou `depart_date`/`return_date`).
- **Telegram zůstává jen alertovací kanál** (nové minimum, velký pokles, mimořádný deal).
  Analytika z denního souhrnu se má refaktorovat do sdílených funkcí pohánějících *zároveň*
  Telegram i export — neduplikovat.
- **Žádné pravidelné náklady.** Jen free tiery (GitHub Actions, Pages, OSM, statické JSONy).

## Pořadí fází
Drž se fází 0–8 ze zadání (sekce 8). Začni vždy Fází 0 (datový kontrakt + export), ostatní
staví na ní. Po každé fázi zastav a nech zkorigovat.

## Git
Vyvíjej na designované feature branchi, commituj s popisnými zprávami, push až je hotovo.
Pull requesty nevytvářej bez explicitního pokynu.

---
Plné zadání viz @docs/zadani-flight-dashboard.md
