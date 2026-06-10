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

## Implementace fází — checklist pro bezchybný kód

Po implementaci každé fáze projdi **vždy všechny** body v tomto pořadí. Vynechání jednoho se
vracího jako bug během testování.

1. **TypeScript:** `npm run build` v `web/` bez chyb → pokud chyba, oprav ji, ne workaround
2. **Ověř, že všechny komponenty jsou importovány** kde je třeba — mrtvý kód se nezobrazí
3. **Zkontroluj data:** pokud fáze pracuje s JSON daty, ověř že všechny soubory existují:
   - `web/public/data/*.json` (latest, stats, routes, meta, insights)
   - `web/public/data/calendar/{route_key}.json` pro každou trasu, která je v `latest.json`
   - `web/public/data/history/{route_key}.json` (pokud je třeba)
4. **Mock data jsou konzistentní:** `latest.json` obsahuje routeKey, který existuje v `calendar/` i `history/`
5. **Spusť dev server:** `npm run dev` v `web/`, ručně otestuj zlatý path (klik na trasu, zobraz detail, filtruj)
6. **Build bez warningů:** `npm run build` → zkontroluj výstup na "error" nebo "warning"
7. **Spusť validační skript:** `bash scripts/validate.sh` — ověří bundle, data, JSON syntax
8. **Deployuj:** `bash scripts/deploy.sh` — skript sám ověří všechno a pushe na GitHub Pages
9. **Otestuj na GitHub Pages:** refreshni https://medniledved.github.io/flight-watcher/
   (Ctrl+Shift+R), zkus přesně totéž co v bodu 5 — mělo by to fungovat stejně

Pokud v kterémkoliv bodě selže → **zastav, oprav příčinu, ne symptom**, a vrať se na bod 1.
Např.: pokud dev server padne s "Module not found", neobcházej `npm install --force`; najdi
proč se import přerušil (chyby v souboru? špatná cesta?).

## Git
Vyvíjej na designované feature branchi, commituj s popisnými zprávami, push až je hotovo.
Pull requesty nevytvářej bez explicitního pokynu.

## Deploy na GitHub Pages
Po projití všech bodů checklistu spusť (z kořene repozitáře, na dev větvi):
```
bash scripts/deploy.sh
```
Skript: (1) vybuiluje `web/dist`, (2) spustí `validate.sh`, (3) zkopíruje vše do gh-pages
přes `git worktree`, (4) commitne a pushne. Neselže-li žádný bod checklistu, deploy uspěje.
**Nikdy nedeployuj ručně.**

---
Plné zadání viz @docs/zadani-flight-dashboard.md
