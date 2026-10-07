# Crypto Trading Bot

P01: fundament offline w Pythonie 3.12 — kontrakty danych, profile, zegary i ograniczony capture publicznego katalogu Crypto.com. G1 PASS oznacza wyłącznie poprawność fundamentu offline. Runtime pozostaje INCOMPLETE, V01–V11 UNKNOWN, a live BLOCKED. P02 ani system wykonywania zleceń nie są zaimplementowane.

## Środowisko i sprawdzenie

Sprawdzone: Python 3.12.14, uv 0.12.23, pytest 9.0.2, Hypothesis 6.151.9, Ruff 0.15.7. `uv.lock` blokuje zależności dev; kod runtime korzysta z biblioteki standardowej. Nie są potrzebne klucze, konto ani baza danych.

```sh
uv sync --frozen --python 3.12
uv run --frozen pytest -q
uv run --frozen ruff check src tests
```

Wymagany zakres odbioru P01:

```sh
uv run --frozen pytest tests/unit/test_contracts.py tests/unit/test_profiles.py tests/unit/test_clock.py tests/unit/test_raw_capture.py -q
```

Smoke test offline (bez sieci):

```sh
uv run --frozen python - <<'PY'
from pathlib import Path
from trading_bot.config.profiles import load_profiles
from trading_bot.config.validation import validate_profiles
for mode in ("research", "paper", "uat"):
    bundle = load_profiles(Path("configs") / f"{mode}.toml")
    report = validate_profiles(bundle.execution, bundle.risk, bundle.research)
    print(mode, report.status, bundle.execution.config_hash)
    assert report.status == "PASS"
PY
```

## Kontrakty i izolacja

`Money` i `Quantity` wymagają skończonego `Decimal` i jawnej tożsamości waluty/base. Dodawanie różnych jednostek jest błędem; arytmetyka i zegary nie zależą od globalnej precyzji Decimal. JSON zachowuje Decimal jako tekst, UTC jako świadomy timestamp i jawną wersję wire `$schema=1`. Rekordy są niemutowalne; porty giełdy są wyłącznie interfejsami bez implementacji transportu.

Profile `configs/research.toml`, `paper.toml` i `uat.toml` używają jawnego `synthetic:SYNTH_Q`, bez kursu/parytetu do realnego Q. Każda sekcja ma schema version i deterministyczny SHA-256. TOML odrzuca nieznane pola oraz liczby finansowe zapisane jako float. Zatwierdzonych progów 6/9/12%, limitów 1%/2%/2/50%/90% ani deadline D nie wolno zastąpić parametrami search. Search dopuszcza tylko jawne parametry hipotezy badawczej.

Walidator blokuje credential scope, trade transport i niezatwierdzone endpointy. Nie czyta `.env`, sekretów ani konfiguracji konta. Zmiana etykiety na `live`, w tym dopisanie fikcyjnych PASS dla wszystkich V, zawsze daje BLOCKED. Profil `uat` jest sanitizowanym fundamentem offline; jego PASS nie daje dostępu do prywatnego UAT. Split i metryki badań pozostają UNFROZEN.

`RealClock` używa monotonic nanoseconds; `SimulationClock` odtwarza timery deterministycznie, także przy skoku UTC. Terminy po recovery przekazuje się jako oryginalne UTC deadline: już przekroczony termin jest natychmiast due. Kolejkę trzeba jawnie odpytywać przez `pop_due()`; zegary nie uruchamiają ukrytych wątków.

## Publiczny raw capture

Oddzielny proces może wykonać anonimowy GET wyłącznie `public/get-instruments` na jednym z dwóch źródeł odczytanych w P00. Nie używa uwierzytelnienia, proxy, redirectów ani retry. Katalog zawiera również instrumenty inne niż spot; capture przechowuje je jako nieprzetworzone dane i nie kwalifikuje do handlu.

```sh
uv run --frozen python -m trading_bot.data.raw_capture \
  --environment production_public \
  --output artifacts/raw/p01-public-session-001 \
  --run-id p01-public-session-001 \
  --polls 1
```

`uat_public` wybiera wyłącznie anonimowy katalog UAT. Każda sesja wymaga nowego katalogu wyjściowego; istniejący `events.jsonl` kończy start błędem. Maksymalnie 100 planowych odczytów, co najmniej 1 s przerwy między odczytami rzeczywistego klienta, także po błędzie. Domyślnie 10 s timeout socketu i deadline odczytu body, 2 MB na body, 100 MB wszystkich payloadów, 1000 zdarzeń i 4 KiB metadanych na zdarzenie. Nie jest to gwarancja czasu całej sesji przy powolnym serwerze.

W katalogu sesji:

- `payloads/*.raw` zawierają oryginalne bytes, również duplikaty i odebrane body błędów HTTP.
- `events.jsonl` zachowuje envelope, SHA-256, UTC odbioru/availability, monotonic offset od początku sesji oraz DATA/DUPLICATE/GAP/RECONNECT.
- `capture-stop.json` ujawnia GAP po przekroczeniu limitu. Niepełne body nie jest przedstawiane jako pełny oryginalny payload.

Polling katalogu nie zapewnia ciągłego pokrycia rynku; przerwy są jawnie oznaczane. Brak źródłowego czasu/sequence zostaje `null`, bez wymyślania exchange timestamp. Błąd odczytu zapisuje GAP, a kolejny zaplanowany odczyt pozostaje osobną obserwacją. Pliki i indeks są fsyncowane; session odmówi wznowienia po przerwaniu zamiast cicho nadpisywać archiwum. To minimalne archiwum publiczne, nie transakcyjny ledger ani mechanizm recovery tradingu.

Surowe dane i artefakty lokalne są wyłączone z Git. Dowody G1 i ograniczenia odbioru: [P01 runbook](docs/runbooks/p01-foundation.md). Specyfikacja i plan: [docs](docs/2026-10-06-crypto-trading-bot-technical-design-v1.1.md), [P00–P14](docs/superpowers/plans/2026-10-07-crypto-trading-bot-v1-implementation-plan.md).
