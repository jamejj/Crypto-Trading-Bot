# Crypto Trading Bot

P01: fundament offline w Pythonie 3.12 — kontrakty danych, profile, zegary i ograniczony capture publicznego katalogu Crypto.com. G1 PASS oznacza wyłącznie poprawność fundamentu offline. Runtime pozostaje INCOMPLETE, V01–V11 UNKNOWN, a live BLOCKED. P02 dodaje lokalny accounting i trwały ledger PostgreSQL; pełny runtime tradingowy pozostaje przyszłym etapem; częściowy P03 działa wyłącznie na fake exchange.

## Środowisko i sprawdzenie

Sprawdzone: Python 3.12.14, uv 0.12.23, pytest 9.0.2, Hypothesis 6.151.9, Ruff 0.15.7. `uv.lock` blokuje zależności runtime/dev; P02 używa `psycopg[binary]==3.3.3`. Klucze ani konto giełdowe nie są potrzebne. Pełny zestaw testów wymaga jawnie wskazanego, izolowanego lokalnego PostgreSQL.

```sh
uv sync --frozen --python 3.12
# P02_TEST_DSN wskazuje wyłącznie izolowany lokalny klaster testowy.
P02_TEST_DSN="host=/sciezka/do/socket port=55432 dbname=postgres" uv run --frozen pytest -q
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

`Money` i `Quantity` wymagają skończonego `Decimal` i jawnej tożsamości waluty/base. Dodawanie różnych jednostek jest błędem; arytmetyka i zegary nie zależą od globalnej precyzji Decimal. JSON zachowuje Decimal jako tekst, UTC jako świadomy timestamp i jawną wersję wire. Rozszerzone `Fill`, `Reservation`, `InventoryLot` i `CostEstimate` zapisują `$schema=2` i odczytują schema 1 z bezpiecznymi wartościami domyślnymi; pozostałe rekordy zachowują schema 1. Legacy fill bez `trade_id` nie może zostać zaksięgowany; domyślnie opłata pozostaje niepotwierdzona. Rekordy są niemutowalne; porty giełdy są wyłącznie interfejsami bez implementacji transportu.

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


## Accounting P02

`Ledger` jest czystym, deterministycznym modelem offline. `LedgerRepository` stosuje ten sam reducer pod blokadą wiersza konta PostgreSQL i jawną transakcją, także gdy connection factory używa autocommit. Caller dostarcza connection factory i wybrany schemat; moduł nie otwiera domyślnego połączenia. Migracja `migrations/001_ledger.sql` tworzy append-only journal i postings, waliduje bilans per currency oraz zapisuje atomowo odbudowywalny snapshot. Odczyt i zapis porównują projekcję z replay; rozbieżność wymaga jawnego `rebuild()`, bez cichego poprawiania salda. Pełny replay jest świadomym ograniczeniem wydajności P02.

Fill ma stabilne `(account, instrument, trade_id)` niezależne od kanału. Sprzeczne ekonomiczne obserwacje są odrzucane. Fee jest skumulowanym komponentem per waluta dla tej samej tożsamości fillu; `source_id` opisuje obserwację, nie tworzy drugiej opłaty. Wyższa revision księguje delta; zmiana waluty wymaga jawnego wyzerowania starego komponentu i zaksięgowania nowego. Rezerwacja ma jeden dodatni fee buffer per currency. Niepotwierdzone fee wymaga ograniczonego bufora, który przechodzi proporcjonalnie z pending do należnego commitmentu; terminalność zlecenia nie usuwa należnego fee.

Rezerwacje blokują wydanie zajętego cash/base/fee i wymagają aktualnej wersji ledgera. Partial fill przenosi proporcjonalne ryzyko i fee commitment, zachowując budżet niewypełnionej części. Timeout i cancel ACK nie zwalniają rezerwacji; terminal evidence musi mieć zgodne konto, order, instrument, quantity base i cumulative fills. FIFO lots zachowują koszt, source fees, ryzyko i jawny dust. Późne base fee nie może obciążyć innego lotu; brak dostępnego lotu odrzuca całą operację i wymaga reconciliation. Rebate po pełnym wyjściu tworzy jawny lot z zerowym dodatkowym kosztem, bez udawania kolejnego fillu. `origin_instrument` zachowuje tożsamość źródłowego fillu niezależnie od instrumentu trzymanego aktywa rebate; fee delta zmienia ilość tylko raz w scope tego źródła. FIFO zachowuje ilość potrzebną na należną base fee źródłowego lotu. Każda publikowana operacja sprawdza zgodność inventory z saldem aktywa.

`value(snapshot, markets, costs)` wycenia salda dokładnie raz po wykonalnych bids i odejmuje należne fee oraz wyłącznie koszty oznaczone `CostEstimate.basis=REMAINING_AFTER_BIDS`. Domyślne `TRIGGER_SHORTFALL` nie jest dopuszczalne w tym widoku. Mid, spread zawarty w bid i historyczna fee nie są odejmowane ponownie. Brak mark/depth pozostawia konserwatywną wartość i reason codes niepewności; nie podnosi HWM. Zewnętrzne funding v1 obsługuje wyłącznie Q i wymaga `FundingEvidence` z pauzą, reconciliation i aktualną wersją stanu; nie wykonuje transferów. Jednostkowe NAV/HWM pozostaje zachowane również po całkowitej wypłacie i ponownej wpłacie.

Testy integracyjne nie są pomijane przy braku bazy: bez `P02_TEST_DSN` pełny suite celowo kończy się błędem konfiguracji. Każdy test tworzy losowy własny schemat i usuwa tylko ten schemat. Weryfikowane są również rollback przy awarii projekcji, dwa równoczesne writery, autocommit, DB append-only oraz odbudowa projekcji. Dowody i ograniczenia odbioru: [P02 runbook](docs/runbooks/p02-accounting-ledger.md).

Generowane testy sprawdzają pełny replay po kolejnych zdarzeniach i na lokalnym środowisku mogą zająć około 2–3 minut. Ich deadline Hypothesis jest wyłączony: sprawdzają semantykę, bez kwalifikacji opóźnień ani przepustowości runtime live.

## Lifecycle P03 offline

P03.1–P03.6 obejmuje durable intents, obserwacje zleceń, ochronę, dwie jawne polityki wyjścia oraz single writer/fencing wyłącznie na fake exchange/PostgreSQL. G3 PASS wyłącznie dla fake/offline/PostgreSQL po finalnym review i naprawach P03.6. P04 nie rozpoczęto. Default profiles pozostają wyłączone dla execution; V01–V11 UNKNOWN, live BLOCKED. Granice i wyniki: [P03.1–P03.2](docs/runbooks/p03-1-2-execution-lifecycle.md), [P03.3–P03.4](docs/runbooks/p03-3-4-protection-exits.md), [corrective review P03.3–P03.4](docs/runbooks/p03-3-4-corrective-review.md), [P03.5 ownership/fencing](docs/runbooks/p03-5-writer-ownership.md), [final verification P03 / G3](docs/runbooks/p03-final-verification.md).
