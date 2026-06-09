# Japan Flight Tracker ✈️🇯🇵

Python aplikace pro automatické hlídání cen letenek z Evropy (primárně
Německo/ČR) do Japonska. Kombinuje více datových zdrojů, podporuje
**open-jaw** (otevřené čelisti) i multi-city itineráře a posílá notifikace
přes **Telegram**. Běží zdarma jednou denně přes **GitHub Actions**.

## Jak to funguje

Aplikace má dvě vrstvy zdrojů:

**Vrstva 1 – real-time API** (ověřené aktuální ceny):
- **Kiwi Tequila API** – primární zdroj, podporuje open-jaw přes čárkou
  oddělené IATA kódy
- **Amadeus Self-Service API** – sekundární, nativní open-jaw přes POST
  `originDestinations`
- **Travelpayouts Data API** – cache (až 7 dní stará), slouží jako záloha a
  pro detekci cenových trendů

**Vrstva 2 – kurátorské zdroje** (cena neověřená, dobré tipy):
- **Secret Flying** (RSS)
- **Cestujlevně.cz** (RSS, ceny v Kč → EUR)
- **Jack's Flight Club** (scraping veřejných dealů – nejkřehčí, viz
  [Troubleshooting](#troubleshooting))

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
| Kiwi Tequila | https://tequila.kiwi.com → registrace → vytvoř *Solution* | `KIWI_API_KEY` |
| Amadeus | https://developers.amadeus.com → My Self-Service Workspace → New App | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` |
| Travelpayouts | https://www.travelpayouts.com → Dashboard → API tokens | `TRAVELPAYOUTS_TOKEN` |
| Telegram bot | viz [Nastavení Telegram bota](#nastavení-telegram-bota) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

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
> Po tomto datu přejdi na Kiwi/Travelpayouts jako primární zdroj, nebo na
> placený Amadeus Enterprise. V kódu jsou označeny `TODO(sunset)` komentáře.

## Nastavení GitHub Actions

Workflow `.github/workflows/scan.yml` běží denně v **7:00 UTC** a lze ho
spustit i ručně (*Actions → Japan Flight Scan → Run workflow*).

V repozitáři nastav **Settings → Secrets and variables → Actions**:

Secrets:
- `KIWI_API_KEY`
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

### Adaptivní ořezávání podle rate limitů

Každý zdroj má limit kombinací `origin × destination` na jeden běh
(`RATE_LIMIT_COMBINATIONS` v `src/config.py`). Pokud by konfigurace limit
překročila, seznamy se automaticky ořežou **od konce** (zachová se priorita):

```
kiwi: 50, amadeus: 20, travelpayouts: 100, RSS zdroje: bez limitu
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

**Žádné ceny v souhrnu** – zkontroluj, že máš nastavené API klíče a (u
Amadeus) že nejsi na syntetickém testovacím prostředí (`AMADEUS_ENV`).

**Feedparser/lxml chybí lokálně** – RSS a scraping zdroje importují
`feedparser`/`bs4` líně; bez nich aplikace běží, jen tyto zdroje selžou.
Nainstaluj `pip install -r requirements.txt`.

## Struktura projektu

```
src/
  sources/        # datové zdroje (kiwi, amadeus, travelpayouts, RSS, scraping)
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
