# Corrective review P03.3–P03.4

## Zakres

Naprawa na istniejącej gałęzi `codex/p03-execution-lifecycle`, po `ed1a463`;
`83ad40d` i cały wcześniejszy stan zachowano. Sol Medium wykonał TDD, a
koordynator przejrzał kod, kontrakty i regresje. Nie rozpoczęto P03.5,
P03.6 ani P04. **G3 nadal niezaliczone; live BLOCKED, V01–V11 UNKNOWN.**

## Naprawione błędy

- `test_observed_stop_never_replays_original_fake_create` faktycznie używa
  parametrów ACTIVE/CANCELED, wraz z odpowiednią terminalnością i parametrami
  aktywnego stopa. Obie gałęzie blokują replay starego create. To naprawa testu:
  poprawiony test przeszedł także na wcześniejszej implementacji; nie deklarujemy
  fikcyjnego RED produkcyjnego błędu dla ACTIVE.
- Kolejny fill zwiększający target ponad confirmed coverage uruchamia replacement
  według wybranej, osobno zweryfikowanej polityki. Serial wystawia jeden cancel,
  zachowuje prawa SELL do terminalnego wyniku i zgodnych rzeczywistych trades,
  a następnie rezerwuje i wystawia świeży stop na bieżącą ilość netto.
  Przekroczony deadline kieruje do emergency exit zamiast późnego replacement.
- Dla rzeczywistego NONE po upływie deadline powstaje trwały emergency request.
  MARKET_SELL wymaga pozytywnego dowodu braku konkurencyjnego stopa/SELL oraz
  osobnej capability emergency. Nie tworzymy fikcyjnej terminalnej obserwacji
  stopa ani `reconciled=True` tylko dlatego, że lokalne `stop_id` jest puste.
- Zakończony replacement nie otwiera ponownie emergency po historycznym terminie;
  CONFLICT nie ukrywa upływu rzeczywistych uncovered deadlines.
- Native replacement i emergency po late base fee używają ilości netto;
  przyrost reservation jest nieujemny, a zero nie tworzy nowej reservation.
  Native replacement respektuje minimum ochrony przy admission i wykonaniu.
- Zwrotne ID cancel odpowiada zapisanej komendzie również dla częściowego
  requestu; to regresja tożsamości komendy, nie włączenie częściowych take-profitów.
  Target równy zero po pełnym wyjściu nie jest klasyfikowany jako dust blocker.
- Niepoprawna strona zlecenia w snapshotcie nie może ominąć kontroli absence.
  Zmiana inventory, fee, stanu giełdy lub niepewność komendy blokują stale SELL.

## Jawny kontrakt fake

`SyntheticExitVerification` oddziela potwierdzenie wyjścia, replacement,
emergency i wariantu bez stopa. Artefakty mają prefiks `synthetic:` oraz scope
konta, instrumentu i polityki. Samo potwierdzenie soft exit nie daje nowych
uprawnień replacement/emergency. Nie zmieniono wyłączonych profili domyślnych.

Native replacement oraz native emergency są odrębnymi operacjami atomowymi
fake: wykorzystują dotychczasową reservation i rezerwują jedynie brakujący
przyrost. W transakcji skutku wycofują stare prawa i tworzą jedno nowe prawo
na aktualny target. Nie uruchamiają serial fallbacku. Wariant MARKET_SELL przy
NONE wymaga osobnej jawnej weryfikacji absence/emergency również dla native.
To rozszerzenie kontraktu syntetycznego, nie dowód semantyki Crypto.com.

`SyntheticAbsenceEvidence` wiąże dowód z ledger version i dokładną net quantity.
Snapshot pochodzi z osobnego magazynu obserwacji fake venue, obejmuje wszystkie
orders/trades konta dla tego base, ma jawny source/sequence i świeżość do 1 s.
Sprawdzane są completeness, scope, trades zgodne z ledgerem, brak aktywnego
SELL także na innym rynku tego samego base, brak niepewnych lokalnych komend
oraz brak konkurencyjnych rezerwacji. Przed skutkiem wymagany jest świeży dowód
z aktualną wersją ledger i niezmienioną generacją fake venue. Udane operacje
fake aktualizują ten osobny magazyn własnymi skutkami; cancel ACK zapisuje
CANCEL_PENDING, nie terminalne anulowanie. Restart magazynu fake wymaga
jawnego zasiania obserwacji fixture, nie rekonstrukcji z lokalnego ledgeru.

Admission, reservations, komendy i projekcje pozostają transakcyjne PostgreSQL.
Claim utrwala UNKNOWN przed fake skutkiem. Błąd po claim zachowuje ambiguity
oraz reservation i blokuje ponowienie. Błąd admission cofa cały admission.
Cancel ACK nadal nie daje pozwolenia SELL; rzeczywisty fill stopa ma pierwszeństwo
przed starą proponowaną ilością. Nie dokupujemy do minimum.

Minimum ochrony i minimum wyjścia są odrębne; domyślne 1 jednostka dla exit to
parametr syntetycznego fixture, nie limit Crypto.com. Ilość poniżej obu minimów
pozostaje inventory z deadline i `BLOCKED_BELOW_EXIT_MINIMUM`; nie znika jako
dust ani nie jest oznaczana jako zamknięta.

## Świadomie odroczony zakres

P03.5–P03.6, ownership/fencing, końcowy adversarial review całego P03, recovery
produkcyjnego transportu i reconciliation P05 pozostają przyszłymi etapami.
Ocena deadline następuje przy wywołaniu assessment/event API; nie dodano procesu
24/7 ani schedulera. Nie jest to gwarancja sprzedaży w 5 s przy awarii.
UNKNOWN stop lub niepewny replacement blokuje równoległą sprzedaż; emergency
request pozostaje trwały i wymaga rozstrzygnięcia dowodami/recovery. Kolejny fill
po wykonanym wyjściu pozostaje jawnym residual incident, bez udawania flat.

Fake wykonuje pełne syntetyczne fille po trigger price bez exit fee. To test
lifecycle i praw do sprzedaży, nie realistyczny model slippage/PnL ani backtest.
Nie dodano credentials, prywatnego Crypto.com API, realnych zleceń ani live profilu.

## Sprzeczności specyfikacji / decyzje

Wcześniejsze odroczenie całego replacement i ścieżki NONE było niezgodne z
§1.5–1.7; zostało wycofane i zastąpione kontraktami powyżej. Nie zmieniono
wymagania deadline ani zatwierdzonych progów ryzyka. Brak weryfikacji rzeczywistej
semantyki native replacement/emergency/absence nadal blokuje odpowiadającą
ścieżkę live. Przyszły adapter wymaga capability evidence oraz review zgodności
z Technical Design; flagi synthetic nie mogą go odblokować.

Resztka poniżej minimum wyjścia jest ograniczeniem venue: nie ma uczciwej
obietnicy natychmiastowej sprzedaży. Zostaje jawny blocker, wymagający osobnego
operacyjnego rozstrzygnięcia przed live; nie wprowadzono ukrytego fallbacku.

## Weryfikacja

TDD uchwyciło rzeczywiste RED dla brakujących serial/native replacement i
emergency, błędnej strony w absence, niewłaściwych incremental reservations,
braku aktualizacji fake venue, późnego dispatch, niespójnego cancel ID oraz
niepoprawnego dust status po pełnym wyjściu. Błędy fixture zostały poprawione,
ale nie są liczone jako dowód RED zachowania produkcyjnego.

Po zamrożeniu implementacji target: **76 PASS w 15,11 s**, w tym 57 real
PostgreSQL integration i 19 unit. Dodano 38 przypadków integration względem
pierwotnych 19; ponadto naprawiono istniejącą gałąź ACTIVE. Regresje obejmują
positive/negative absence, obie polityki, stałe i zakończone deadlines,
net inventory/minima, cancel-fill race, rollback, restart i no replay.

Pełna regresja koordynatora: **236 PASS w 156,76 s**, bez skipów:
150 unit, 2 property, 76 real PostgreSQL integration i 8 fault tests.
Całość obejmuje regresje P01–P03.4; nie zmieniano ustawień Hypothesis ani
nie ograniczano suite do nowych przypadków.
Ruff check: PASS; format check: 49 files already formatted; diff check: PASS.

```sh
P02_TEST_DSN='host=/sciezka/do/izolowanego/socket port=55432 dbname=postgres' .venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
git diff --check
```

Self-review implementera oraz review koordynatora rozpatrzyły zarzuty względem
§1.5–1.7. Końcowy niezależny adversarial review całego P03 pozostaje bramką
P03.6. Po osobnym commicie naprawczym zatrzymujemy pracę; G3 niezaliczone.
