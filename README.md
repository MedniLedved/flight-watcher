# Japan Flight Tracker ✈️🇯🇵

Python aplikace pro automatické hlídání cen letenek z Evropy (primárně
Německo/ČR) do Japonska. Kombinuje více datových zdrojů, podporuje
**open-jaw** (otevřené čelisti) i multi-city itineráře a posílá notifikace
přes **Telegram**. Běží zdarma jednou denně přes **GitHub Actions**.

## Jak to funguje

Aplikace má dvě vrstvy zdrojů:

**Vrstva 1 – real-time API** (ověřené aktuální ceny):
- **Duffel API** – primární zdroj, nativní open-jaw / multi-city přes pole
  `slices` *(náhrada za Kiwi Tequila – viz [poznámka](#proč-ne-kiwi))*
- **Sky Scrapper (RapidAPI)** – Skyscanner data; ⚠️ free tier jen
  **100 requestů/měsíc**, proto se používá velmi úsporně
- **Travelpayouts Data API** – cache (až 7 dní stará), slouží jako záloha a
  pro detekci cenových trendů
- **Amadeus Self-Service API** *(volitelný)* – nativní open-jaw přes POST
  `originDestinations`. ⚠️ **Bez nastavených klíčů se přeskakuje** a
  **API končí 17. 7. 2026** (viz [sunset](#amadeus-volitelný--brzy-končí)).
  Aplikace funguje plně i bez něj.

> **Minimálně stačí Duffel** (vrstva 1) + Telegram. Každý další zdroj je
> volitelný – chybí-li klíč, zdroj se přeskočí a scan pokračuje dál.

### Proč ne Kiwi?

Kiwi.com **v květnu 2024 uzavřel veřejný přístup k Tequila API**, takže ho
už nelze použít. Jako náhrada slouží **Duffel** (primárně) a **Sky Scrapper
přes RapidAPI**.

**Vrstva 2 – kurátorské zdroje** (cena neověřená, dobré tipy):
- **Secret Flying** (RSS)
- **Cestujlevně.cz** (RSS, ceny v Kč → EUR)
- **Jack's Flight Club** (scraping veřejných dealů – nejkřehčí, viz
  [Troubleshooting](#troubleshooting))
- **Miles & More – mileage bargains** (award nabídky placené *mílemi*; kontrola
  **jen 1. kalendářní den v měsíci**, protože se mění měsíčně)

Výsledky se porovnají s historií cen (`data/price_history.json`) a prahem.
Při nové nízké ceně přijde **alert s ASCII kalendářem** odletu/příletu;
jednou denně přijde **souhrn** stavu všech tras a zdrojů.

## Lokální spuštění

```bash
cp .env.example .env      # vyplň API klíče a Telegram údaje
pip install -r requirements.txt
python -m src.scanner
```

Spuštění testů:

```bash
pip install pytest
python -m pytest
```

## Získání API klíčů

| Zdroj | Povinný? | Kde získat | Proměnná(é) |
|-------|----------|-----------|-------------|
| Duffel | **doporučeno** (hlavní zdroj) | https://duffel.com → registrace → Dashboard → Developers → Access tokens (token `duffel_test_...` pro test, `duffel_live_...` pro produkci) | `DUFFEL_TOKEN` |
| Sky Scrapper | volitelný | https://rapidapi.com/apiheya/api/sky-scrapper → Subscribe (Basic/Free) → zkopíruj `X-RapidAPI-Key` | `RAPIDAPI_KEY` |
| Travelpayouts | volitelný | https://www.travelpayouts.com → Dashboard → API tokens | `TRAVELPAYOUTS_TOKEN` |
| Amadeus | volitelný, **končí 17. 7. 2026** | https://developers.amadeus.com → My Self-Service Workspace → New App | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` |
| Telegram bot | **povinný** (notifikace) | viz [Nastavení Telegram bota](#nastavení-telegram-bota) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

### Duffel: test vs. produkce

Token sám určuje režim – `duffel_test_...` vrací **syntetická testovací data
se smyšlenými cenami** (neodpovídají žádné reálné letence!), `duffel_live_...`
reálné ceny (vyžaduje aktivovaný účet). Není potřeba zvláštní přepínač.

> ⚠️ **Scanner testovací token odmítne**: zdroj se vypne a denní souhrn
> zobrazí varování. Stejně tak se zahodí odpovědi, u kterých API samo ohlásí
> `live_mode=false`. Smyšlené ceny by jinak otrávily alerty, historii i
> dashboard – přesně tak vznikají „nabídky", které na Google Flights stojí
> dvojnásobek.

Nabídky v jiné měně než EUR (Duffel měnu odpovědi neumí vynutit) se
**převádějí denním referenčním kurzem ECB** (frankfurter.app – zdarma, bez
klíče); když kurz není k dispozici (výpadek API, exotická měna), nabídka se
přeskočí – cizí měna se nikdy nevydává za EUR.

### Vyčištění historie po běhu na testovacích datech

Pokud scanner nějakou dobu běžel s testovacím tokenem, historie obsahuje
syntetické ceny smíchané s reálnými záznamy ostatních zdrojů. Každý záznam
nese pole `source`, takže jde cíleně odstranit jen zasažené zdroje:

- **V CI (doporučeno – reálná historie žije v Actions cache):** spusť workflow
  *Actions → Purge price history → Run workflow*. Výchozí běh je **dry-run**
  (jen vypíše do logu, co by se smazalo); teprve se zaškrtnutým `apply` se
  změny zapíší do cache a commitnou dlouhodobé řady (`data/history/*`).
- **Lokálně:** `python -m src.maintenance --sources duffel amadeus
  [--before YYYY-MM-DD] [--apply]`.

Purge přepočítá `all_time_min`/`last_price`/`last_seen` ze zbývajících
záznamů (minimum bere z dlouhodobé řady, která přežívá 90denní retenci)
a smaže anti-duplicitní razítka alertů zasažených tras.

### Sky Scrapper: pozor na kvótu

Free tier má jen **100 requestů/měsíc** (~3/den). Aplikace proto:
- cachuje skyId/entityId letišť na disk (`data/skyscrapper_airports.json`),
  aby se `searchAirport` nevolal opakovaně,
- má nejnižší limit kombinací (`RATE_LIMIT_COMBINATIONS["skyscrapper"] = 3`),
- počítá spotřebu v `data/price_history.json` (`_meta.skyscrapper_requests`) a
  při vyčerpání kvóty zdroj přeskočí.

**Automatické řízení kvóty (od verze s adaptivním rozpočtem):**
- Aplikace **čte RapidAPI rate-limit hlavičky** (`x-ratelimit-requests-remaining`
  / `-limit` / `-reset`) a podle nich zná **skutečný** zbytek kvóty i čas
  resetu – přesnější než lokální počítadlo.
- Zbývající kvóta se **rozpočítá na zbytek období** (`remaining / dnů do
  resetu`), takže se nevyplýtvá hned první den → dlouhodobě rovnoměrné pokrytí.
- Při **vyčerpání kvóty (HTTP 429 / remaining 0)** se zdroj **automaticky
  vypne do resetu** (`_meta.disabled_until`) a po uplynutí lhůty se **sám
  zapne** – mezitím se na něj zbytečně neplýtvají requesty. Stav vidíš v denním
  souhrnu (`Sky Scrapper: ⏸ vypnuto … do …`).

> Každý zdroj je volitelný – pokud klíč chybí, zdroj se přeskočí a scan
> pokračuje dál. V denním souhrnu vidíš, které zdroje fungovaly.

### Amadeus (volitelný – brzy končí)

> ⚠️ **Amadeus Self-Service API bude ukončeno 17. července 2026.** Vzhledem
> k blížícímu se konci ho **nedoporučujeme nově nasazovat** – aplikace běží
> plně bez něj na Duffelu (+ volitelně Sky Scrapper / Travelpayouts).
> **Bez nastavených `AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET` se zdroj
> automaticky přeskakuje** (v denním souhrnu se vůbec neobjeví). Kód zůstává
> funkční jako volitelný zdroj a je v něm označen komentáři `TODO(sunset)`.

**Zdroj je aktuálně vypnutý v `config/agent.json`** (`"sources.amadeus":
false`) – při scanu se vůbec neinicializuje (nulová režie) a nehlásí žádná
varování. Zapnout jde přepnutím na `true` (např. přes záložku Nastavení
dashboardu) + nastavením `AMADEUS_ENV=production`. Python SDK `amadeus` byl
odstraněn z `requirements.txt` – nikde se neimportoval (zdroj volá REST přímo
přes `requests`) a jen zpomaloval každodenní instalaci v CI.

Pokud ho přesto chceš dočasně používat:

- `AMADEUS_ENV=test` (výchozí) → `test.api.amadeus.com`, **syntetická data** –
  **scanner zdroj v tomto režimu vypne** (smyšlené ceny nesmí do historie
  a alertů) a upozorní v denním souhrnu
- `AMADEUS_ENV=production` → `api.amadeus.com`, **reálné ceny** (stejný klíč,
  zdarma; v dashboardu je nutné aplikaci přepnout do produkce)

Free tier má limit **2 000 requestů/měsíc**. Aplikace cachuje výsledky
(stejný dotaz se neopakuje do 6 hodin) a počítá spotřebu v
`data/price_history.json` (`_meta.amadeus_requests`).

## Nastavení GitHub Actions

Workflow `.github/workflows/scan.yml` běží denně v **7:00 UTC** a lze ho
spustit i ručně (*Actions → Japan Flight Scan → Run workflow*).

V repozitáři nastav **Settings → Secrets and variables → Actions**:

Secrets – **povinné minimum**:
- `DUFFEL_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Secrets – **volitelné** (chybí-li, zdroj se přeskočí):
- `RAPIDAPI_KEY` (Sky Scrapper)
- `TRAVELPAYOUTS_TOKEN`
- `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` *(končí 17. 7. 2026 – viz výše)*

Variables (volitelné):
- `AMADEUS_ENV` = `test` nebo `production`
- ladění plánovače: `SCAN_DATE_SAMPLES`, `SCAN_EXPLORE_FRACTION`,
  `SCAN_COLD_START_TARGET_WEEKDAY`, `SCAN_COLD_START_TARGET_AIRPORT`

### Perzistence historie cen

`data/price_history.json` musí přežít mezi běhy. Použity jsou **dva
mechanismy** pro jistotu:

1. Soubor je **commitován** v repozitáři (počáteční stav).
2. Workflow používá `actions/cache@v5` pro předání mezi běhy (krok „Save
   price history" má `if: always()`, ať se historie uloží i při chybě scanu).

Pokud chceš historii držet jen v gitu, můžeš odebrat cache kroky a místo
toho po scanu soubor commitovat (např. `git add data/ && git commit && git
push` ve workflow s `permissions: contents: write`).

## Úprava hlídaných tras

Vše se konfiguruje v `config/routes.yaml`:

- `price_threshold_eur` – pod jakou cenou poslat alert
- `european_airports` / `japanese_airports` – seznamy **v pořadí priority**
  (méně prioritní letiště se ořežou první při překročení rate limitů)
- `routes` – jednotlivé trasy:
  - `type: roundtrip` – stejný origin/destination tam i zpět
  - `type: openjaw` – nezávislé `outbound` a `inbound` (origins/destinations)
  - hodnoty `all_european` / `all_japanese` použijí plné seznamy letišť
- `search_windows` – roky a měsíce hledání
- `stay_length` – min/max počet nocí

### Dynamická priorita letišť podle podílu dealů

Aplikace se **učí z historie**: před každým scanem spočítá pro každé letiště
**podíl pozorování pod prahem** (`deal_rate` v `src/airport_stats.py`)
a **přeřadí primární letiště** tak, aby ta nejakčnější byla na začátku
seznamu. Díky tomu při ořezání podle rate limitů (viz níže) přežijí právě
letiště, která nejčastěji generují dealy.

**Proč podíl dealů, a ne průměrná cena?** Letiště může mít vysoký průměr
(drahé základní ceny), ale zároveň hodně výprodejů pod prahem – a právě o ty
nám jde. Průměr by takové letiště nespravedlivě potopil. `deal_rate` je
odolný vůči drahým outlierům a přímo modeluje cíl aplikace (najít dealy).
Tiebreaker je **medián cen dealů** (levnější dealy = lepší); letiště zatím
bez dealů se řadí dle celkového mediánu.

- Bere se v potaz jen letiště s alespoň **3 pozorováními** (`MIN_SAMPLES`);
  letiště s málo daty si drží původní pořadí z `routes.yaml`.
- Pořadí se přepisuje za běhu (neměníš `routes.yaml`), takže priorita se
  postupně sama vylaďuje, jak přibývá historie.

**Kde to vidíš:** v **denním Telegram souhrnu** je sekce *„Priorita letišť
dle podílu dealů"* – letiště od nejakčnějšího (💚) po nejméně akční (💸)
s podílem cen pod prahem (`12/40`) a mediánem dealu. Letiště bez dostatku dat
jsou vypsána zvlášť. V logu scanu se navíc zaloguje přeřazení
(`Priorita EU letišť přeřazena dle cen: … → …`).

### Chytré plánování vzorkování (coverage-driven)

Databáze se plní **cíleně**, ne náhodně. Místo slepé rotace termínů
plánovač před každým scanem spočítá z historie **vážené pokrytí** (recency
decay – staré ceny „vyhasínají" s poločasem 30 dní, viz
`COVERAGE_HALFLIFE_DAYS`) pro čtyři faktory: **den odletu** (7), **den
návratu** (7) a **letiště** odletu/příletu.

**Greedy zaplňování nejřidších buněk.** Den návratu je řiditelný přes počet
nocí (`(den_odletu + nocí) mod 7`), takže lze cíleně trefit libovolnou
dvojici den-odletu × den-návratu. Plánovač vybírá termíny tak, aby maximálně
snížil deficit pokrytí napříč faktory – díky tomu se **všech 7 dnů odletu i
návratu a všechna letiště pokryjí za ~7–10 dní** (oproti ~3–4 týdnům
u náhodné rotace, kde se plýtvá na překryvy).

**Dvě fáze (explore → exploit):**
- **Studený start** – dokud nemá každý den / letiště aspoň
  `SCAN_COLD_START_TARGET` (3) vážených pozorování, jede se čistě podle
  deficitu (rovnoměrné pokrytí).
- **Ladění** – `SCAN_EXPLORE_FRACTION` (30 %) rozpočtu drží čerstvost
  (převzorkování vyhaslých buněk), zbytek **exploituje** nejakčnější dny
  a letiště (vyšší `deal_rate`), aby se zpřesnily odhady a chytly nové
  propady cen.

Pokrytí letišť se sleduje **podle role** – odletová (EU) a příletová (JP)
letiště mají vlastní statistiku, aby se nemíchala. Letiště s nedostatečným
čerstvým pokrytím se navíc řadí **dopředu** (přežijí ořezání dle rate
limitů), takže rychle nasbírají data.

Laditelné přes env proměnné. **Cíle studeného startu jsou pro dny a letiště
oddělené**, protože se plní jiným tempem: každý scan zasáhne ~všechna letiště
(cíl naplněn za 1 den), ale jen `SCAN_DATE_SAMPLES` dnů v týdnu (cíl trvá
~`dny × 7 / samples`). Jeden společný práh by ladění zkresloval.

| Proměnná | Default | Význam |
|----------|---------|--------|
| `SCAN_DATE_SAMPLES` | `2` | kolik dvojic (odlet, návrat) za běh |
| `SCAN_COLD_START_TARGET_WEEKDAY` | `3` | vážená pozorování/den v týdnu pro konec studeného startu |
| `SCAN_COLD_START_TARGET_AIRPORT` | `3` | vážená pozorování/letiště pro konec studeného startu |
| `SCAN_EXPLORE_FRACTION` | `0.3` | podíl průzkumných slotů ve fázi ladění (přesný pro libovolný zlomek) |

### Adaptivní ořezávání podle rate limitů

Každý zdroj má limit kombinací `origin × destination` na jeden běh
(`RATE_LIMIT_COMBINATIONS` v `src/config.py`). Pokud by konfigurace limit
překročila, seznamy se automaticky ořežou **od konce** (zachová se priorita):

```
duffel: 50, amadeus: 20, skyscrapper: 3, travelpayouts: 100, RSS zdroje: bez limitu
```

## Nastavení Telegram bota

1. V Telegramu napiš **[@BotFather](https://t.me/BotFather)** → `/newbot` →
   zvol jméno a username → dostaneš **token** (`TELEGRAM_BOT_TOKEN`).
2. Napiš svému novému botovi libovolnou zprávu (aby s tebou mohl mluvit).
3. Zjisti svoje **chat ID**: napiš **[@userinfobot](https://t.me/userinfobot)**
   → vrátí ti tvoje číselné ID (`TELEGRAM_CHAT_ID`). Pro kanál použij
   `@nazev_kanalu` nebo jeho ID.
4. Zprávy chodí jako HTML – kalendář se zobrazuje jako monospace blok.

## Typy notifikací

1. **Alert na nízkou cenu** – jen když cena `< price_threshold_eur` **nebo**
   pod historickým minimem trasy; s ASCII kalendářem odletu/příletu.
   Duplicitní alert (stejná cena+trasa do 24 h) se přeskočí.
2. **Deal alert** (RSS) – jen příspěvky mladší 48 h, s upozorněním
   „cena neověřena".
3. **Denní souhrn** – posílá se vždy, i bez nových dealů.

## Troubleshooting

**Jack's Flight Club scraping nefunguje** – nejčastější situace, protože
Jack's nemá veřejné API a scrapuje se HTML:
- Stránka mohla **změnit strukturu** → upravit selektory v
  `src/sources/jacks.py` (`fetch()`).
- **robots.txt** scraping zakázal → modul ho respektuje a vrátí prázdný
  seznam (v logu uvidíš „robots.txt zakazuje scraping").
- **Anti-bot ochrana / 403** → zkus jiný `User-Agent` nebo zdroj vypni.
- V každém případě to **nezastaví** zbytek scanu; v souhrnu uvidíš
  `Jack's ✗ (chyba)`.

**Miles & More mileage bargains:**

Kontrola běží **jen 1. dne v měsíci** – jindy se zdroj záměrně přeskočí
(v logu `Miles & More: přeskočeno`). Data se berou ze **strukturovaného
GraphQL endpointu**, který používá samotný web M&M:

```
POST https://api.miles-and-more.com/content/v3/offers/search
hlavička x-api-key (veřejný klíč webového frontendu, vestavěný v kódu)
```

Odpověď obsahuje pro každou leteckou nabídku `destinationIata/Name`,
`originList`, `promoMiles`/`regularMiles` a cestovní období. Kód z toho
filtruje nabídky s **cílem v Japonsku** a **původem v Evropě** a posílá je
jako deal alert (cena je v *mílích*, ne EUR). **Normálně nemusíš nic
nastavovat** – endpoint i klíč jsou vestavěné.

Pokud by endpoint přestal fungovat:
- vrátí-li 403/chybu, zdroj automaticky zkusí **fallback scraping HTML**
  stránky; když selže i ten, jen se zaloguje a v souhrnu se objeví
  `Miles & More ✗` – scan pokračuje dál.
- kdyby endpoint začal vyžadovat přihlášenou relaci, lze přidat hlavičky
  (typicky `Cookie`) přes secret `MILESANDMORE_HEADERS={"Cookie": "..."}`.
- endpoint/klíč jdou přepsat přes `MILESANDMORE_API_URL` /
  `MILESANDMORE_API_KEY`; `MILESANDMORE_IGNORE_ROBOTS=true` povolí HTML
  fallback i při zakazujícím robots.txt.

**Žádné ceny v souhrnu** – zkontroluj, že máš nastavené API klíče a že
nejsi na syntetickém testovacím režimu (`DUFFEL_TOKEN=duffel_test_…`,
`AMADEUS_ENV=test`) – ten scanner vypíná a hlásí v denním souhrnu ⚠️.

**Ceny v alertech neodpovídají Google Flights** – téměř jistě běžíš na
testovacím režimu zdroje (viz výše): test API vrací smyšlené ceny. Vygeneruj
`duffel_live_…` token, resp. nastav `AMADEUS_ENV=production`. Odkaz
„Zobrazit na Google Flights" v alertu otevírá předvyplněné vyhledávání
(stejná letiště, termíny, 1 dospělý, economy, ceny v EUR), takže srovnání
je 1:1; drobné rozdíly můžou být jen z rozdílné dostupnosti tarifů.

**Feedparser/lxml chybí lokálně** – RSS a scraping zdroje importují
`feedparser`/`bs4` líně; bez nich aplikace běží, jen tyto zdroje selžou.
Nainstaluj `pip install -r requirements.txt`.

## Struktura projektu

```
src/
  sources/        # zdroje (duffel, skyscrapper, amadeus, travelpayouts, RSS, scraping, miles_and_more)
  config.py       # konfigurace, letiště, rate-limity, trim_airports, CZECH_WEEKDAYS
  airport_stats.py # statistiky letišť/dnů (deal_sort_key, priority_order, format_*)
  calendar_renderer.py  # ASCII kalendář pro Telegram
  history.py      # historie cen + anti-duplicita + coverage/weekday statistiky + počítadla kvót
  notifier.py     # Telegram zprávy (3 typy, dělení dlouhých zpráv)
  scanner.py      # hlavní orchestrátor (python -m src.scanner)
config/routes.yaml
data/price_history.json
.github/workflows/scan.yml
tests/
AGENTS.md         # pokyny pro AI coding agenty (konvence, architektura, invarianty)
```

> Pracuješ na kódu s AI agentem (Claude Code apod.)? Přečti si
> [`AGENTS.md`](AGENTS.md) – shrnuje příkazy, architekturu a invarianty,
> které nesmí rozbít.

## Bezpečnost

`.env` **nikdy** necommituj – je v `.gitignore`. Klíče v GitHub Actions
předávej výhradně přes Secrets. `data/price_history.json` commitován je
(obsahuje jen ceny a počítadla, žádné tajné údaje).
