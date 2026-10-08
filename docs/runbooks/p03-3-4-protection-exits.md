# P03.3–P03.4 — protection lifecycle i jawne exit policies

## Zakres i baza

Ten raport opisuje pierwotny commit `ed1a463`. Późniejszy corrective review
ujawnił brak rzeczywistej gałęzi ACTIVE w regresji oraz niekompletny replacement
i emergency exit dla NONE. Wcześniejsze uznanie braku replacement za dopuszczalne
odroczenie zostało wycofane. Aktualne zachowanie i wyniki opisuje
[raport corrective review](p03-3-4-corrective-review.md).

Wykonano wyłącznie P03.3–P03.4 na istniejącej gałęzi
`codex/p03-execution-lifecycle`, po `83ad40d`. P03.1–P03.2 zachowano.
Jedyna zmiana ich implementacji to wydzielenie prywatnego `_apply_event`,
aby fill i protection mogły użyć tej samej transakcji PostgreSQL.
Sol Medium wykonał TDD; koordynator wykonał self-review. Pełny niezależny
review P03 pozostaje przyszłą bramką. P03.5, P03.6 ani P04 nie rozpoczęto.
**G3 pozostaje niezaliczone; live BLOCKED, V01–V11 UNKNOWN.**

## Protection

`assess_protection(snapshot, context, clock)` zwraca niemutowalny
`ProtectionState`. Ilość wynika z rzeczywistych lotów po zaksięgowanej base fee,
z pomniejszeniem o nierozliczone zobowiązania fee w base. `PENDING/UNKNOWN`
ma zero coverage. Stop większy od bieżącego targetu oznacza CONFLICT,
a nie lokalnie przycięte CONFIRMED. Parametry potwierdzanego stopa muszą mieć
właściwe konto, instrument, SELL, trigger oraz ilość; rozbieżność między
cumulative quantity i znanymi fillami nie daje coverage.

Pierwszy partial komunikat FOK nie oznacza terminalnego częściowego wykonania.
Nowy fill podczas PENDING/UNKNOWN zwiększa target, ale nie generuje drugiego
pełnego stopa. Partial poniżej jawnego minimum pozostaje uncovered z deadline;
nie ma dokupowania, ukrywania go jako dust ani zamiany na inny typ stopa.

Każdy uncovered lot ma trwały deadline UTC, początkowo 5 s od pierwszej
lokalnej oceny ekspozycji. Kolejne fille i oceny nie odnawiają starego deadline.
`ProtectionClock` wiąże terminy z monotonic clock w procesie; cofnięcie zegara
UTC po związaniu nie przedłuża terminu. Po restarcie upłynięty deadline jest
natychmiast OVERDUE. Zakończone coverage i późniejsze nowe uncovered to osobne
okno ekspozycji; samo PENDING nie zamyka starego okna.

`LifecycleRepository` używa istniejącej blokady konta P02 i projekcji per
instrument. Fill, ledger, ocena, deadlines, SELL reservation stopa, immutable
komenda i audit są w jednej jawnej transakcji. P02 sprawdza commitments tego
samego base również między różnymi rynkami. Brak wolnej ilości ujawnia konflikt,
zamiast tworzyć drugie prawo do sprzedaży. Migracja `002b_protection_exits.sql`
rozszerza schemat bez przepisywania migracji 001/002.

## Wyjścia

`request_exit(request, state)` zwraca typed `LifecycleCommand`, rozszerzający
`CommandProposal`. Repozytorium odczytuje świeży ledger pod blokadą i utrwala
propozycję wraz z prawem do sprzedaży. Dopuszczalne polityki są jawnie wybrane:

- `SERIAL_CANCEL_THEN_MARKET_VERIFIED`: cancel ACK pozostawia rezerwację.
  Dopiero scoped terminalny wynik stopa i równość cumulative quantity z sumą
  rzeczywistych scoped trades pozwalają zwolnić jego reservation i zarezerwować
  MARKET_SELL pozostałej net quantity. W fake ta równość jest kontraktem
  terminalności źródła; uzgodnienie oraz wersja są zapisywane pod blokadą.
  Początek serial window i deadline są trwałe. Nie deklarujemy atomowości cancel/sell.
- `NATIVE_LINKED_VERIFIED`: fake operacja atomowa wykorzystuje istniejące prawo
  stopa, zamiast dodać drugą niezależną SELL reservation. Obsługiwane jest tylko
  pełne wyjście zgodne z ilością istniejącego stopa i inventory. Inny rozmiar
  blokuje propozycję. Brak aktywnego zgodnego stopa nie uruchamia fallbacku serial.

Bez własnej scoped `SyntheticExitVerification` danej polityki nie powstaje
komenda wyjścia. Nie można zmienić polityki podczas rozpoczętego wyjścia.
UNKNOWN stop/sell nie daje zgody na kolejną sprzedaż. Fake claim zapisuje UNKNOWN
przed skutkiem; awaria zapisu pozostawia reservation i blokuje ponowienie.
Ilość i prawo do sprzedaży są ponownie sprawdzane przed fake wykonaniem.
Fill stopa między decyzją a wykonaniem unieważnia nadmierną ilość sprzedaży.

Spóźniony cancel ACK nie nadpisuje terminalnego wyniku. Scoped ACTIVE/terminal
dowód istnienia stopa rozstrzyga jego create, więc stara komenda nie jest ponawiana.
Sprzeczna tożsamość obserwacji, terminalność lub malejące cumulative quantity
są odrzucane; starszy status nie usuwa ekonomicznych skutków.

## Self-review i poprawki

Regresje wykryły i uszczelniły: oversized stop po base fee, native fallback,
niezgodny rozmiar native exit, błędny trigger/side/quantity, utratę jawnego UNKNOWN
po claim, sprzeczne obserwacje i terminalne statusy, spóźnione cancel ACK/create,
ACTIVE z brakującymi trades oraz stale native SELL po fillu stopa.
Sprawdzono też rollback default/autocommit, restart deadline'ów, immutable audit,
wykonanie obu fake policies i konflikt rezerwacji base między rynkami.

TDD: 13 początkowych unit RED → GREEN; osobne RED dla oversized stop/fallback,
brakującej warstwy PG i regresji self-review → GREEN. Końcowy target: **67 PASS
w 3,59 s**, w tym 38 nowych — 8 protection unit, 11 exit unit i 19 real PG
integration; pozostałe 29 to regresje P03.1–P03.2. Pełny run koordynatora:
**198 PASS w 81,62 s**, bez skipów — 160 wcześniejszych regresji i 38 nowych.
Ruff check: All checks passed; format check: 48 files already formatted;
`git diff --check`: PASS. Self-review zamknięty, bez otwartego blokera tego
ograniczonego fake/offline zakresu. Nie jest to G3 PASS.

## Granice odbioru

Nie ma automatycznej wymiany ani ponownego wystawiania stopa. Potwierdzony stop
za mały dla kolejnego fillu pozostawia uncovered increment i deadline; serial
exit może zamknąć net inventory po wymaganym uzgodnieniu. Ten podetap nie kończy
pełnej orkiestracji, re-entry guards, watchdog/recovery ani interfejsu event/proposals
całego P03. Nie wdraża single writer/fencing z P03.5. Lokalne blokady PostgreSQL
nie są dowodem wyłączności procesu wobec giełdy.

Default configs pozostają z exit DISABLED. Synthetic verification nie zalicza
capability V. Fake fills mają cenę równą triggerowi oraz zerowe fee wyjścia:
testują lifecycle, wzajemną wyłączność i accounting, bez modelowania ceny/fee
rzeczywistego wykonania. Minimum ochrony i profil stop-market są fixtures, nie
zweryfikowanymi regułami venue. Nie ma credentials, transportu giełdowego,
prywatnych endpointów ani realnych transakcji.

Pełne testy wymagają własnego izolowanego PostgreSQL i jawnego `P02_TEST_DSN`.
Każdy integration test zakłada losowy schemat i usuwa tylko ten schemat.

```sh
P02_TEST_DSN='host=/sciezka/do/socket port=55432 dbname=postgres' .venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

Zatrzymujemy pracę po osobnym commicie P03.3–P03.4 na tej samej gałęzi.
