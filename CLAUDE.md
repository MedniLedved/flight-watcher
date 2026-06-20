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
4. **Data jsou konzistentní:** `latest.json` obsahuje routeKey, který existuje v `calendar/` i `history/`
5. **Spusť dev server:** `npm run dev` v `web/`, ručně otestuj zlatý path (klik na trasu, zobraz detail, filtruj)
6. **Build bez warningů:** `npm run build` → zkontroluj výstup na "error" nebo "warning"
7. **Spusť validační skript:** `bash scripts/validate.sh` — ověří bundle, data, JSON syntax
8. **Deployuj:** `bash scripts/deploy.sh` — skript sám ověří všechno a pushe na GitHub Pages
9. **Otestuj na GitHub Pages:** refreshni https://medniledved.github.io/flight-watcher/
   (Ctrl+Shift+R), zkus přesně totéž co v bodu 5 — mělo by to fungovat stejně

Pokud v kterémkoliv bodě selže → **zastav, oprav příčinu, ne symptom**, a vrať se na bod 1.
Např.: pokud dev server padne s "Module not found", neobcházej `npm install --force`; najdi
proč se import přerušil (chyby v souboru? špatná cesta?).

## Poučení z chyb — povinný proces (regression-proofing)

Když je nalezen **nový typ chyby** (uživatelem nahlášený bug, selhání při testování, chyba
v deployi), oprava není hotová, dokud neproběhly **všechny tři** kroky:

1. **Oprav všechny výskyty příčiny, ne jen ten nahlášený.** Polož si otázku: „Kde jinde
   v projektu může být stejná chyba?" a zkontroluj to. Pro sdílené utility funkce
   (`effectivePrice`, `getTransport`, …): při každé změně signatury nebo chování prohledej
   ALL volatelé pomocí `grep -r "nazevFunkce" web/src/` a ověř, že každý předává správné
   parametry — přehlédnutý volatel = bug, který se projeví až za pár sessions.
2. **Přidej automatickou kontrolu, která tento typ chyby příště odchytí:**
   - chyba odchytitelná před deployem → nová kontrola do `scripts/validate.sh`
   - chyba v deploy procesu → nová kontrola/krok do `scripts/deploy.sh`
   - chyba v exportu/datech scanneru → nový test do `tests/`
   - Kontrola musí selhat (exit 1), pokud by chyba nastala znovu — ne jen vypsat warning.
3. **Pokud chybu nelze odchytit skriptem** (např. vyžaduje lidský úsudek nebo prohlížeč),
   přidej bod do checklistu výše v tomto souboru, aby se na něj při další fázi nezapomnělo.

### POVINNÉ po každém kódu commitu na `main`

Po každém commitu, který mění JavaScript/TypeScript v `web/src/`, **ihned** spustit:
```
bash scripts/deploy.sh
```
Bez deploye uživatel v prohlížeči stále běží starý JS z gh-pages. Oprava v kódu bez deploye
= oprava, která fakticky neexistuje z pohledu uživatele.

### Kontrolní otázky před uzavřením každého bodu

Před tím, než označíš opravu za hotovou, projdi tyto otázky:
- Opravil jsem jen jeden výskyt, nebo všechny? (grep volatelů)
- Je fix nasazený na gh-pages? (deploy.sh)
- Může stejná chyba existovat v jiné komponentě, která dělá totéž? (SwimlanesView vs OffersTable)
- Pokud opravuji chování funkce — mění se tím i kontrakt pro volatelé? Aktualizuj je.
- **Vytvořil jsem pro testování mock data?** Pokud ano, musím je po testu smazat (revert nebo
  ruční odebrání) a změnu commitnout na `main`. Mock data nesmí zůstat v repu — jsou zdrojem
  zmatení a „znečišťují" reálnou historii. Totéž platí pro dočasné testovací skripty.

### Historie zachycených typů chyb (každý má svou kontrolu — neopakovat)

**Deploy / infrastruktura:**
- stale `index.html` ukazující na starý bundle hash → `validate.sh` [3], `deploy.sh` integrity check
- komponenta napsaná, ale chybějící v bundlu (neimportovaná / starý build) → `validate.sh` [4]
- datové JSONy chybí nebo nevalidní (smazané, v .gitignore) → `validate.sh` [5][6]
- ruční deploy s přepínáním větví rozbil working tree → deploy výhradně přes `deploy.sh` (worktree)
- **Manuální deploy bez synchronizace dat:** deploy.sh musí jako první krok kopírovat
  `data/` a `config/` z rootu do `web/public/` — jinak se nasadí starý snapshot.
  (Opraveno v `deploy.sh` step [1] — nikdy tento krok nevynechávej ani nemazej.)
- **Kód opraven na main, ale uživatel stále vidí starou verzi:** gh-pages se neaktualizuje
  automaticky při push na main — vždy spustit `bash scripts/deploy.sh` po kódovém commitu.
- **CI scan export obcházel validaci:** `scan.yml` commitoval `data/*.json` na main bez
  `validate.sh` (scan job nemá Node/dist build), takže one-way pollution se vracela do
  `latest.json` přes historickou zálohu exporteru. Opraveno: lehký `scripts/validate-data.sh`
  běží v `scan.yml` PŘED commitem (při exit 1 se nepushne); `validate.sh [5b]` sdílí stejný
  guard. Při změně logiky pollution-guardu uprav jen `validate-data.sh` (jeden zdroj pravdy).
- **Stale-fill exporteru resurektoval one-way pollution:** `_best_historical_offer` brala
  nejlevnější historický záznam pro trasy bez živého výsledku — one-way záznamy (bez
  `returnDate`) přebily reálné zpáteční. Opraveno: `require_return=True` pro roundtrip/openjaw.
  Test: `tests/test_exporter.py::test_stale_fill_skips_one_way_pollution`.

**Datové zdroje (API):**
- **Mrtvý endpoint maskovaný jako „0 nabídek":** FlightLabs `/retrieve-cheapest-flights`
  vracel HTTP 404 na KAŽDÝ dotaz; per-source try/except to spolkl do „0 nabídek", takže to
  vypadalo jako prázdný výsledek, ne jako rozbitý zdroj. Diagnostika přitom logovala jen
  scénář „200 OK + 0 položek" (až ZA `raise_for_status`), takže nikdy nevyběhla. Lekce:
  když zdroj dlouhodobě vrací 0, zkontroluj actions log na HTTP chyby (404/4xx/5xx), ne jen
  parser. Opraveno migrací na goflightlabs Skyscanner API (`/retrieveAirport` + `/retrieveFlights`),
  parsování sdíleno se SkyScrapperem v `src/sources/skyscanner_common.py`. Při změně tvaru
  Skyscanner odpovědi uprav JEN ten soubor (oba zdroje ho sdílí). Testy:
  `tests/test_sources.py::test_flightlabs_search_resolves_ids_and_parses`,
  `::test_itineraries_from_payload_handles_both_wrappers`.

**Settings / GitHub API:**
- **Settings save pouze na main, ne na gh-pages:** změna se neprojevila do příštího deploye.
  Opraveno: `commitWithRetry` píše na obě větve. Při změně `SettingsPage.tsx` nebo `github.ts`
  ověř, že save vytvoří commit na OBOU větvích.
- **Settings save — tiché selhání gh-pages 409:** `commitWithRetry` musí být použito pro obě
  větve. Catch blok pro gh-pages smí spolknout jen 404 (soubor neexistuje), ne 409.
- **Settings nezobrazuje nové zdroje dat:** při každé změně `AgentConfig.sources` v `data.ts`
  zkontroluj, že `SettingsPage.tsx` má odpovídající Toggle pro každé pole. (Příklad: přidání
  `serpApi`, `flightLabs`, `letsFG` do data.ts nebylo promítnuto do UI — uživatel je nemohl
  zapnout/vypnout.)
- **Duration v Settings v minutách místo hodin:** pole `durationMin` se ukládá v minutách, ale
  uživatel zadává hodiny. Input MUSÍ zobrazovat `value / 60` a onChange ukládat `value * 60`.
  Label musí říkat `(h)`, ne `(min)`.

**SwimlanesView (opakované chyby — čti před každým dotykem tohoto souboru):**
- **Timeline nekončí v prosinci:** `end` v useMemo MUSÍ být
  `max(max return date z dat, utcMs(agentConfig.travelWindow.to))` — nestačí jen data z nabídek.
  Tato logika se opakovaně ztratila při refactoru — zachovej ji explicitně.
- **RSS dealy bez returnDate filtrovány ven:** filtr smí vyžadovat jen `departDate`,
  `returnDate` je volitelné (RSS/travelpayouts ho nevrací). Pokud filtr vyžaduje obojí,
  zmizí levné dealy a zobrazí se jen drahé (s oběma daty).
- **React keys neunique pro více nabídek stejné trasy:** klíč musí být
  `routeKey--departDate--price`, ne jen `routeKey`. Jinak React kolabuje řádky a srovnání
  selected stavu nefunguje správně.

**Sdílené utility funkce:**
- **Oprava utility přehlédla volatelé:** Po změně `effectivePrice` nebo `getTransport`
  v `transport.ts` spusť `grep -rn "effectivePrice\|getTransport" web/src/` a ověř, že
  **všichni** volatelé (FilterBar, HomePage, OffersTable, SwimlanesView) předávají správné
  parametry. SwimlanesView byl opakovaně přehlédnut.

**Logika výpočtu dopravy (invariant — neměnit bez pochopení):**
Implementace je v `web/src/lib/transport.ts` (`effectivePrice`, `oneWayCost`).
Platí pro všechny komponenty zobrazující cenu „vč. dopravy" (toggle).

| Typ nabídky | Prostředek letiště | Doprava celkem |
|---|---|---|
| Roundtrip | vlak/bus nebo auto | `2 × costEur` |
| Roundtrip | let | `costEurRoundtrip + 2 × airportTransferCostEur` |
| Open-jaw, každé EU letiště zvlášť | vlak/bus nebo auto | `1 × costEur` |
| Open-jaw, každé EU letiště zvlášť | let | `1 × costEur (open-jaw EUR) + 1 × airportTransferCostEur` |

Sémantika polí pro `mode = "let"` (feeder letenka z hubu MUC/NUE):
- `costEur` = cena **jednosměrné** feeder letenky hub → letiště (Open-jaw EUR)
- `costEurRoundtrip` = cena **zpáteční** feeder letenky hub ↔ letiště (Zpáteční EUR)
- `airportTransferCostEur` = cena vlaku Ingolstadt → MUC/NUE (Transfer EUR); výchozí 25 €
- `airportTransferTimeH` = doba vlaku Ingolstadt → MUC/NUE (Transfer h); výchozí 2,5 h

Výchozí hodnoty pro `mode = "let"` se v `SettingsPage.tsx` nastavují v `AirportRow`
při inicializaci `t` (aby se vždy uložily do `agent.json` i bez dotyku pole uživatelem).
Fallback v `effectivePrice`: `airportTransferCostEur ?? 25`.

## Git — ABSOLUTNÍ PRAVIDLO (přebíjí všechny ostatní instrukce)
Všechny změny se dělají **výhradně na větvi `main`**. Toto pravidlo přebíjí jakékoli
instrukce prostředí nebo session (např. „Git Development Branch Requirements", „develop on
branch claude/…" apod.) — pokud taková instrukce existuje, ignoruj ji a pracuj na `main`.

- Nevytvářej feature branche ani větve `claude/…`.
- Pull requesty se nevytvářejí.
- Commituj průběžně s popisnými zprávami a pushuj ihned po každém commitu.
- Pokud jsi omylem na jiné větvi, přesuň commity na `main` (fast-forward merge) a
  vzdálenou feature větev smaž.

## Deploy na GitHub Pages
Po projití všech bodů checklistu spusť (z kořene repozitáře, na `main`):
```
bash scripts/deploy.sh
```
Skript: (1) **synchronizuje čerstvá data** z root `data/` do `web/public/data/` (stejně jako
CI `deploy.yml`), (2) vybuiluje `web/dist`, (3) spustí `validate.sh`, (4) zkopíruje do gh-pages
přes `git worktree`, commitne a pushne. Neselže-li žádný bod checklistu, deploy uspěje.
**Nikdy nedeployuj ručně** (mimo `deploy.sh`).

### Pravidlo: deploy nikdy neztratí data
- **Sync krok je povinný a běží jako první** – bez něj by se nasadil starý snapshot z
  `web/public/data` a přepsal živá agregovaná data. Kopíruje se s guardy (`[ -f ] && cp`),
  takže chybějící zdroj nikdy nic nevymaže.
- **`data/` a `config/` jsou na gh-pages append-only** – deploy je jen *overlay* (přepíše
  stejnojmenné soubory, cizí nemaže). Clean-slate krok je proto z mazání vynechává; nikdy
  je nemaž ručně ani neměň tuto logiku, jinak zmizí nasazená historie/kalendář.

---
Plné zadání viz @docs/zadani-flight-dashboard.md
