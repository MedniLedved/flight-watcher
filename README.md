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
- **Amadeus Self-Service API** – nativní open-jaw přes POST
  `originDestinations`
- **Travelpayouts Data API** – cache (až 7 dní stará), slouží jako záloha a
  pro detekci cenových trendů

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

| Zdroj | Kde získat | Proměnná(é) |
|-------|-----------|-------------|
| Duffel | https://duffel.com → registrace → Dashboard → Developers → Access tokens (token `duffel_test_...` pro test, `duffel_live_...` pro produkci) | `DUFFEL_TOKEN` |
| Sky Scrapper | https://rapidapi.com/apiheya/api/sky-scrapper → Subscribe (Basic/Free) → zkopíruj `X-RapidAPI-Key` | `RAPIDAPI_KEY` |
| Amadeus | https://developers.amadeus.com → My Self-Service Workspace → New App | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` |
| Travelpayouts | https://www.travelpayouts.com → Dashboard → API tokens | `TRAVELPAYOUTS_TOKEN` |
| Telegram bot | viz [Nastavení Telegram bota](#nastavení-telegram-bota) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

### Duffel: test vs. produkce

Token sám určuje režim – `duffel_test_...` vrací testovací data, `duffel_live_...`
reálné ceny (vyžaduje aktivovaný účet). Není potřeba zvláštní přepínač.

### Sky Scrapper: pozor na kvótu

Free tier má jen **100 requestů/měsíc** (~3/den). Aplikace proto:
- cachuje skyId/entityId letišť na disk (`data/skyscrapper_airports.json`),
  aby se `searchAirport` nevolal opakovaně,
- má nejnižší limit kombinací (`RATE_LIMIT_COMBINATIONS["skyscrapper"] = 3`),
- počítá spotřebu v `data/price_history.json` (`_meta.skyscrapper_requests`) a
  při vyčerpání kvóty zdroj přeskočí.

> Každý zdroj je volitelný – pokud klíč chybí, zdroj se přeskočí a scan
> pokračuje dál. V denním souhrnu vidíš, které zdroje fungovaly.

### Amadeus: test vs. produkce

- `AMADEUS_ENV=test` (výchozí) → `test.api.amadeus.com`, **syntetická data**
- `AMADEUS_ENV=production` → `api.amadeus.com`, **reálné ceny** (stejný klíč,
  zdarma; v dashboardu je nutné aplikaci přepnout do produkce)

Free tier má limit **2 000 requestů/měsíc**. Aplikace cachuje výsledky
(stejný dotaz se neopakuje do 6 hodin) a počítá spotřebu v
`data/price_history.json` (`_meta.amadeus_requests`).

> ⚠️ **Amadeus sunset:** Self-Service API bude ukončeno **17. července 2026**.
> Po tomto datu se spolehni na Duffel/Sky Scrapper/Travelpayouts jako primární
> zdroje, nebo přejdi na placený Amadeus Enterprise. V kódu jsou označeny
> `TODO(sunset)` komentáře.

## Nastavení GitHub Actions

Workflow `.github/workflows/scan.yml` běží denně v **7:00 UTC** a lze ho
spustit i ručně (*Actions → Japan Flight Scan → Run workflow*).

V repozitáři nastav **Settings → Secrets and variables → Actions**:

Secrets:
- `DUFFEL_TOKEN`
- `RAPIDAPI_KEY`
- `AMADEUS_CLIENT_ID`
- `AMADEUS_CLIENT_SECRET`
- `TRAVELPAYOUTS_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Variables (volitelné):
- `AMADEUS_ENV` = `test` nebo `production`

### Perzistence historie cen

`data/price_history.json` musí přežít mezi běhy. Použity jsou **dva
mechanismy** pro jistotu:

1. Soubor je **commitován** v repozitáři (počáteční stav).
2. Workflow používá `actions/cache@v4` pro předání mezi běhy.

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

**Žádné ceny v souhrnu** – zkontroluj, že máš nastavené API klíče a (u
Amadeus) že nejsi na syntetickém testovacím prostředí (`AMADEUS_ENV`).

**Feedparser/lxml chybí lokálně** – RSS a scraping zdroje importují
`feedparser`/`bs4` líně; bez nich aplikace běží, jen tyto zdroje selžou.
Nainstaluj `pip install -r requirements.txt`.

## Struktura projektu

```
src/
  sources/        # zdroje (duffel, skyscrapper, amadeus, travelpayouts, RSS, scraping, miles_and_more)
  config.py       # konfigurace, letiště, rate-limity, trim_airports
  calendar_renderer.py  # ASCII kalendář pro Telegram
  history.py      # historie cen + anti-duplicita alertů + Amadeus počítadlo
  notifier.py     # Telegram zprávy (3 typy)
  scanner.py      # hlavní orchestrátor (python -m src.scanner)
config/routes.yaml
data/price_history.json
.github/workflows/scan.yml
tests/
```

## Bezpečnost

`.env` **nikdy** necommituj – je v `.gitignore`. Klíče v GitHub Actions
předávej výhradně přes Secrets. `data/price_history.json` commitován je
(obsahuje jen ceny a počítadla, žádné tajné údaje).
