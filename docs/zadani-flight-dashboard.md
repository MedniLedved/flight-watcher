# Zadání pro Claude Code: Analytický dashboard pro sledování cen letenek

---
## 1. Kontext a cíl
Existuje funkční backend (`scanner.py`), který sleduje ceny letenek na trasách
**Evropa → Japonsko** pro cestovní termíny **září–prosinec 2026** a posílá výsledky
na Telegram.
**Problém:** Telegram přestal být vhodným *hlavním* rozhraním — desítky zpráv denně,
nelze v nich vyhledávat, filtrovat ani srovnávat, chybí vizuální přehled.
**Cíl:** Vytvořit moderní webový analytický dashboard (HMI), který převezme hlavní
informační roli. Telegram zůstane **pouze jako alertovací kanál** pro významné události
(nové historické minimum, výrazný pokles ceny, mimořádná nabídka).
**Cílové zařízení:** desktop s velkým monitorem. Mobil **není** priorita.
**Inspirace vizuálem:** úroveň moderních datových aplikací (např. rentorbuy.cz).
---
## 2. Architektura
```
GitHub Actions (cron)
      ↓
scanner.py  ──►  čte config/agent.json; zde probíhá VEŠKERÁ agregace a výpočty
      ↓
export krok (in-process na konci scanu)  ──►  partícované JSON soubory do /data
      ↓
React + TypeScript (statický build)  ──  jen načítá a vykresluje, lehké klientské filtrování
      ↓
GitHub Pages / Cloudflare Pages  (hosting zdarma)
```
### Klíčová architektonická pravidla
- **Žádný backend server, žádná databáze v prohlížeči (žádný SQLite/DuckDB-WASM, žádné FastAPI).**
- Všechny statistiky a agregace počítá `scanner.py` v CI a zapisuje je jako hotové JSONy.
- Frontend je „hloupý": načte JSON a vykreslí. Filtrování a řazení probíhá klientsky v paměti.
- Konfiguraci agenta čte scanner z commitnutého `config/agent.json` (viz sekce 3 a 5.8).
---
## 3. Datová vrstva (datový kontrakt)
Toto je **jediný zdroj pravdy** mezi `scanner.py` (producent) a frontendem (konzument).
Definuj odpovídající **TypeScript typy** v `src/types/data.ts`, které přesně kopírují
strukturu JSONů níže.
> **Zdroj dat ve scanneru:** Interní stav je `data/price_history.json` (POZOR: **ne**
> `history.json`). Struktura: mapa `route_key → { all_time_min, last_seen, last_price,
> alerts, history[] }` plus speciální klíč `_meta`. Není to plochý seznam.
> - `history[]` je časová řada per trasa; každý záznam má `date` (YYYY-MM-DD — **jen datum
>   pozorování, ne čas dne**), `price` (EUR), `source` a *volitelně* `depart_date`/`return_date`.
> - **Měna se neukládá — vždy EUR.**
> - V jednom dni může být na trasu víc záznamů (různé zdroje/termíny), **bez deduplikace**.
> - **Retence 90 dní:** `price_history.json` se prořezává (HISTORY_RETENTION_DAYS), starší
>   záznamy mizí. `all_time_min` se ukládá zvlášť a prořez přežívá.
> - **Bohatá pole se neperzistují** (aerolinky, deep_link, open-jaw návratová letiště,
>   route_name, nights) — žijí jen během scanu a v Telegramu.
>
> Scanner zatím nemá machine-readable výstup pro frontend; konzumní JSONy níže jsou nová,
> **additivní** funkcionalita. Denní Telegram souhrn už počítá většinu analytiky — refaktoruj
> ji do sdílených funkcí pohánějících *zároveň* Telegram i export.
### Dvě klíčová pravidla exportu (vyplývají ze struktury scanneru)
**1) Dlouhodobá akumulace kvůli 90denní retenci.**
`price_history.json` drží jen 90 dní, ale kritérium úspěchu vyžaduje *dlouhodobé trendy*.
Proto jsou commitnuté soubory `data/history/{route_key}.json` **kanonické dlouhodobé řady** —
export do nich při každém běhu **jen přidává** (append-only) nové záznamy z `price_history.json`,
s deduplikací na celou n-tici `(date, source, depart_date, return_date, price)`. Tyto soubory
se **nikdy neprořezávají**. Tím vznikne plná historie nezávislá na 90denním okně scanneru.
**2) Efemérní pole jen v `latest.json`, a to in-process.**
Aerolinky, deep_link a open-jaw návratová letiště nejsou na disku. Aby je `latest.json` mohl
obsahovat, **export krok musí běžet ve stejném procesu na konci scanu**, kde má živé objekty
`FlightResult`. Historické řady tato pole mít nemohou — nejsou uložená.
### Parsování `route_key`
Klíč má 3 nebo 4 segmenty oddělené `-`:
- `{origin}-{destination}-roundtrip` (běžný zpáteční), např. `PRG-TYO-roundtrip`
- `{origin}-{destination}-{return_origin}-openjaw` (open-jaw), např. `MUC-KIX-OSA-openjaw`
Parser musí zvládnout 3 i 4 segmenty. `origin`/`destination` můžou být **city kódy**
(TYO, OSA), ne nutně konkrétní letiště. Název souborů řad/kalendáře = celý `route_key`
(je file-safe).
### Rozložení souborů (`/data` a `/config`)
| Soubor | Obsah | Načítá se |
|---|---|---|
| `data/meta.json` | čas běhu exportu, počet scanů, API kvóty (z `_meta`), verze schématu | při startu |
| `data/routes.json` | seznam tras: route_key, kódy/názvy, souřadnice (pro mapu) | při startu |
| `data/latest.json` | aktuální nejlepší nabídky + efemérní pole + příznaky | při startu |
| `data/stats.json` | předpočítané agregáty per trasa | při startu |
| `data/insights.json` | cross-cutting analytika (deal rate per letiště, nejlevnější dny) | při startu |
| `data/calendar/{route_key}.json` | aktuální nejlepší cena per odletový den (heatmapa) | lazy, na detailu |
| `data/history/{route_key}.json` | dlouhodobá akumulovaná řada (append-only, neprořezává se) | lazy, na detailu |
| `config/agent.json` | konfigurace agenta editovatelná přes Nastavení | při startu |
### Příklady schématu
```jsonc
// latest.json — běží in-process na konci scanu, takže má i efemérní pole
[
  {
    "routeKey": "PRG-TYO-roundtrip",
    "type": "roundtrip",            // nebo "openjaw"
    "origin": "PRG",
    "destination": "TYO",           // může být city kód
    "returnOrigin": null,           // vyplněné jen u openjaw
    "returnDestination": null,
    "price": 487,                   // EUR (měna vždy EUR)
    "source": "duffel",
    "departDate": "2026-09-05",     // volitelné (chybí, když zdroj nevrátil)
    "returnDate": "2026-09-19",     // volitelné
    "nights": 14,
    "airlines": ["AY", "JL"],       // EFEMÉRNÍ – jen z živého scanu
    "dealUrl": "https://...",       // EFEMÉRNÍ – jen z živého scanu
    "observedDate": "2026-06-10",   // = date posledního pozorování
    "flags": {
      "isNewLow": true,
      "priceDeltaEur": -23,         // price - last_price (baseline ze scanneru)
      "pctChange7d": -18.4,         // z akumulované řady; null dokud není 7 dní dat
      "isBigDrop": true
    }
  }
]
```
```jsonc
// data/history/PRG-TYO-roundtrip.json — append-only, kanonická dlouhodobá řada
[
  { "date": "2026-06-10", "price": 567, "source": "duffel",
    "departDate": "2026-09-05", "returnDate": "2026-09-19" },
  { "date": "2026-06-11", "price": 512, "source": "skyscrapper",
    "departDate": "2026-09-12", "returnDate": "2026-09-26" }
]
```
```jsonc
// stats.json — agregáty per trasa
{
  "PRG-TYO-roundtrip": {
    "allTimeMin": 389,              // ze scanneru (přežívá 90denní prořez)
    "min90d": 487, "max90d": 690, "avg90d": 560,
    "trend30d": -6.2,               // % trend, z akumulované řady
    "biggestDrop": { "from": 720, "to": 590, "date": "2026-05-28" },
    "lastPrice": 567,
    "currentVsAvgPct": -12.1
  }
}
```
```jsonc
// insights.json — cross-cutting analytika
{
  "airportPriority": {
    "europe": [ { "code": "PRG", "dealRatePct": 42, "medianEur": 512, "observations": 18 } ],
    "japan":  [ { "code": "TYO", "dealRatePct": 39, "medianEur": 498, "observations": 21 } ]
  },
  "cheapestDepartureDow": [
    { "dow": "PÁ", "dealRatePct": 38, "medianEur": 498 },
    { "dow": "ÚT", "dealRatePct": 22, "medianEur": 521 }
  ],
  "cheapestArrivalDow": [ { "dow": "ÚT", "dealRatePct": 19, "medianEur": 515 } ]
}
```
```jsonc
// meta.json
{
  "lastScan": "2026-06-10T06:42:00Z",   // čas běhu exportu (ve scanneru přesný čas dne není)
  "scanCount": 1234,
  "schemaVersion": 2,
  "apiQuota": {                          // z klíče _meta v price_history.json
    "skyscrapper": { "remaining": 47, "limit": 100, "resetAt": "2026-07-01T00:00:00Z" },
    "requestsThisMonth": { "amadeus": 36, "skyscrapper": 14 },
    "disabledUntil": { "skyscrapper": null }
  }
}
```
```jsonc
// config/agent.json — čte ji scanner při běhu, edituje se přes záložku Nastavení (5.8)
{
  "homeLocation": "Ingolstadt",
  "travelWindow": { "from": "2026-09-01", "to": "2026-12-31" },
  "stayLength": { "minNights": 7, "maxNights": 21 },
  "europeAirports": [
    {
      "code": "MUC", "name": "Mnichov", "lat": 48.35, "lon": 11.79,
      "priority": 1, "enabled": true,
      "transport": { "costEur": 18, "durationMin": 95, "mode": "vlak (DB)" }
    }
  ],
  "japanAirports": [
    { "code": "NRT", "name": "Tokio Narita", "lat": 35.76, "lon": 140.39, "priority": 1, "enabled": true }
  ],
  "alertThresholds": { "dealMaxEur": 600, "bigDropPct": 15, "newLowSensitivityPct": 2 },
  "sources": {
    "duffel": true, "skyScrapper": true,
    "rss": { "secretFlying": true, "cestujlevne": true, "jacks": false }
  },
  "telegramAlerts": { "priceAlert": true, "dealAlert": true, "dailySummary": true }
}
```
> **Úkol pro scanner:** přidat *in-process* export krok na konci scanu, který (a) zapíše
> `latest.json` z živých `FlightResult` (včetně efemérních polí), (b) připojí nové záznamy
> do append-only `data/history/{route_key}.json`, (c) přepočítá `stats.json` / `insights.json`
> (sdílené funkce s Telegram souhrnem; `min` = `all_time_min`), (d) zapíše `meta.json` (kvóty
> z `_meta`). Dále přesunout zadrátovaná letiště/prahy do `config/agent.json`.
---
## 4. Technologický stack
- **Frontend:** React + TypeScript, Vite
- **Styling/komponenty:** Tailwind CSS + shadcn/ui
- **Grafy:** Recharts
- **Mapa:** Leaflet + react-leaflet + OpenStreetMap dlaždice (bez API klíče); heatmapa přes plugin `leaflet.heat`
- **Kalendářová heatmapa:** vlastní grid + barevná škála (d3-scale) nebo lehká knihovna
- **Hosting:** GitHub Pages (preferováno, data i web v jednom repu) nebo Cloudflare Pages
- **Data:** statické JSON soubory (viz sekce 3)
---
## 5. Funkční požadavky
### 5.1 Filtrace dat
Klientské filtrování nad načtenými daty:
- odletové letiště
- cenové limity (rozsah)
- délka pobytu
- cílová destinace
- (případně další parametry dle dat)
### 5.2 Interaktivní kalendář (analytický)
- ceny zobrazené přímo v kalendářních dnech
- barevné zvýraznění podle ceny (heatmapa)
- klik na den → detail
- filtrování podle termínu
### 5.3 Swimlanes kalendář
- dny = sloupce, nabídky = řádky
- nabídky seřazené podle nejbližšího odletu
- bar od odletu po přílet s informací o ceně
- klik na bar → popup se stručnými info (zkratky letišť, aerolinky, odkaz na deal — z `latest.json`)
- *Implementace:* vlastní CSS grid / SVG, případně knihovna typu `vis-timeline`.
  Je to nejvíc custom komponenta — začni jednoduchým gridem a stav ji nakonec.
### 5.4 Toggle „cena včetně dopravy"
- toggle, který k zobrazené ceně nabídky **připočte 2× cenu** veřejné dopravy
  z `homeLocation` → odletové letiště (tam i zpět) podle pole `transport` u letiště
  v `config/agent.json`
- zároveň zobrazí dobu jízdy
### 5.5 Grafy a statistiky
Vše čteno z předpočítaného `stats.json` / `insights.json` / `history/*`:
- vývoj ceny v čase, minima/maxima, průměry, trendy
- procentuální změny, porovnání destinací
- největší poklesy / růsty, nová historická minima
### 5.6 Mapa
- zobrazení destinací (souřadnice z `routes.json`)
- zvýraznění nejzajímavějších nabídek
- volitelně heatmapa cen
- interaktivní výběr lokací
### 5.7 Detail destinace/trasy
Po kliknutí na trasu (lazy-load `history/{route_key}.json` + `calendar/{route_key}.json`):
- historie cen + graf vývoje
- kalendář dostupných termínů
- statistiky
- související nabídky
### 5.8 Záložka Nastavení (editace agenta)
UI pro úpravu chování agenta. Konfigurace žije v repu jako `config/agent.json`; scanner ji
čte při každém běhu.
**Editovatelné položky:**
- evropská odletová letiště: přidat/odebrat (kód, název, souřadnice, priorita, enabled)
- doprava per evropské letiště: cena (EUR), doba (min), prostředek
- japonská cílová letiště: přidat/odebrat
- prioritizace letišť (váhy / pořadí)
- cestovní okno (od–do)
- délka pobytu (min–max nocí)
- cenové prahy alertů: max cena dealu, % pro „velký pokles", citlivost „nového minima"
- zdroje dat / API: zapnout/vypnout (Duffel, Sky Scrapper, jednotlivé RSS zdroje)
- typy Telegram alertů: které posílat
- výchozí lokace pro dopravu (`homeLocation`)
**Perzistence (statický web bez backendu — jeden uživatel):**
- Settings tab čte a zapisuje `config/agent.json` přes **GitHub REST API**.
- Autentizace: **fine-grained Personal Access Token** s přístupem jen k tomuto repu
  (Contents: read/write), uložený v `localStorage` prohlížeče.
- Po uložení změny se commitne `config/agent.json`; změna se projeví **při dalším scanu**
  (cron), případně tlačítkem „Spustit scan teď" přes `workflow_dispatch`.
- Validace ve formuláři proti TS typům před commitem.
- **Bezpečnostní model:** jeden uživatel, jeden privátní repo, token jen v jeho prohlížeči.
  Není to vhodné pro veřejné/multi-user nasazení.
- **Alternativa bez tokenu:** export/import `config/agent.json` ručně (stáhnout → commitnout
  → nahrát).
---
## 6. Mimo rozsah
- mobile-first aplikace, nativní iOS aplikace, distribuce přes App Store
- komerční SaaS řešení
- placené cloudové služby
---
## 7. Náklady (tvrdá podmínka)
**Žádné pravidelné náklady.** Vyhnout se: Vercel Pro, Railway, placeným plánům Render i jiným.
Vše navržené (GitHub Actions, GitHub/Cloudflare Pages, OSM, statické JSONy) je v rámci
free tierů. Commitovat jen agregované JSONy a do časových řad pouze přidávat (append → čisté
diffy); surové logy scanu nechat jako CI artefakty.
---
## 8. Doporučený postup vývoje (fáze)
Stav po fázích, ať lze průběžně korigovat:
1. **Fáze 0 — datový kontrakt, export a externalizace configu:** definovat TS typy; přidat
   do `scanner.py` in-process export krok (`latest.json`, append-only `history/{route_key}.json`,
   `stats.json`, `insights.json`, `meta.json`) podle sekce 3; analytiku z denního Telegram
   souhrnu refaktorovat do sdílených funkcí; přesunout zadrátovaná letiště/prahy do
   `config/agent.json`. Additivní vrstva, ne přepis scanneru.
2. **Fáze 1 — kostra appky:** Vite + React + TS + Tailwind + shadcn, načtení `latest.json`,
   základní tabulka/seznam nabídek + filtrace (5.1).
3. **Fáze 2 — grafy a detail trasy:** Recharts, sekce 5.5 a 5.7.
4. **Fáze 3 — kalendář heatmapa:** sekce 5.2.
5. **Fáze 4 — mapa:** Leaflet, sekce 5.6.
6. **Fáze 5 — toggle dopravy:** sekce 5.4.
7. **Fáze 6 — swimlanes:** sekce 5.3.
8. **Fáze 7 — deploy:** GitHub Actions build + nasazení na Pages.
9. **Fáze 8 — záložka Nastavení:** sekce 5.8 (zápis configu přes GitHub API; nejlépe testovat
   až na nasazeném webu).
---
## 9. Kritérium úspěchu
Přehledný analytický dashboard, který nahradí většinu informační role Telegramu a rychle
odpoví na otázky:
- Kam jsou aktuálně nejvýhodnější nabídky?
- Jak se cena vyvíjela poslední týdny?
- Které termíny jsou nejlevnější?
- Které destinace právě zlevnily?
- Jaké jsou dlouhodobé trendy?
- Jak si vedou jednotlivé trasy ve vzájemném srovnání?
