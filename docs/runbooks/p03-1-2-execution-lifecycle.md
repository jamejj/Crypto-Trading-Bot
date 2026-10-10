# P03.1–P03.2 — durable intents i obserwacje zleceń

## Zakres

P02 zaakceptowano i scalono w PR #2 zwykłym merge `7cc3577` (2026-10-08).
Użytkownik dopuścił wyłącznie P03.1–P03.2 na `codex/p03-execution-lifecycle`.
Implementację wykonał Sol Medium metodą TDD; koordynator wykonał self-review.
Pełny niezależny review P03 pozostaje przyszłą bramką po pozostałych zadaniach.
P03.3–P03.6 i P04 nie rozpoczęto. G3 nie jest zaliczone; live nadal BLOCKED.

## Dostarczone kontrakty

- Jedna jawna transakcja PostgreSQL zapisuje rezerwację P02, intent, zatwierdzenie z fixture, dokładny payload/hash, stały client ID oraz outbox. Tożsamości intentu i approval nie można ponownie wykorzystać na tym koncie.
- Claim utrwala DISPATCHING przed wywołaniem transportu. Restart zmienia nierozstrzygnięte DISPATCHING na SUBMISSION_UNKNOWN i nie wysyła create ponownie. Timeout zachowuje rezerwację; unresolved blokuje przygotowanie i dispatch kolejnego create na koncie.
- Scope account/instrument/order jest sprawdzany przed skutkami. Fille księguje ledger P02 po stabilnej tożsamości trade, niezależnie od kanału. Stan order i skutki finansowe zapisują się w jednej transakcji.
- Status nie tworzy domniemanego fillu ani nie zwalnia rezerwacji. Cumulative quantity bez trade records pozostaje dowodem do późniejszego reconciliation. Pełne znane wykonanie nie cofa się przez spóźnione CANCELED/ACTIVE/ACK. Terminalny status nie usuwa późnego rzeczywistego fillu.
- Powtórzona obserwacja jest idempotentna dla projekcji; sprzeczne dane z tym samym source ID są odrzucane. Przyjęte obserwacje są audytowane, także powtórzone dostarczenia. Source ID musi identyfikować konkretną obserwację, nie sam kanał.
- Ważność fixture approval i reservation sprawdzana jest przed claim. Wygaśnięty PREPARED przechodzi ABORTED_BEFORE_SEND bez wysyłki; rezerwacja zostaje konserwatywnie zachowana do jawnego rozstrzygnięcia.
- Zapisany scoped fill lub status istniejącego order rozstrzyga niepewność create nawet przed lokalnym ACK; PREPARED z takim dowodem nie może zostać ponownie wysłany.

## Świadome granice implementacji

Transport jest wyłącznie `FakeExchange`; `FakeDispatcher` odrzuca inne typy transportu.
Payload fixture ma tylko client ID i profil LIMIT/FOK; ilość, strona, instrument i cena
są w niezmiennym typed OrderIntent. Nie jest to wire format Crypto.com ani dowód V.

Reducer tego podetapu to `apply(OrderState, Fill|OrderObservation, Ledger)`:
projekcja dowodów i księgowanie wykonywane w transakcji repozytorium. Planowany
czysty interfejs `ExecutionState/ExecutionEvent → state/proposals` nie jest jeszcze
ukończony. Ten helper nie zastępuje docelowego kontraktu pełnego P03.
Nie ma protection, exit coordination, ownership/fencing, governora ani recovery
giełdowego. RESOLVED oznacza rozstrzygnięte istnienie orderu, nie terminalność,
rozliczenie wszystkich filli ani ochronę pozycji. Zachowanie rezerwacji po terminalnym
statusie jest świadomie konserwatywne; ich zwolnienie nie zostało tutaj zautomatyzowane.

## Self-review i poprawki

Dodano regresje dla wygasłego PREPARED, sprzecznej tożsamości obserwacji,
nieprawidłowej jednostki/statusu/ilości, kontowego blokowania unresolved oraz
istniejącego orderu przy lokalnym PREPARED. Awaria projekcji orderu po zapisaniu
ekonomicznego journalu wycofuje cały zapis, również przy factory z autocommit.
Trigger testowy sprawdza, że awaria wystąpiła rzeczywiście po zapisie `_fill`.

TDD: początkowe testy reducer i DB/crash RED przed implementacją; późniejsza
regresja rollbacku RED → GREEN. Finalny scoped run: **29 PASS** — 9 unit,
12 integration PostgreSQL i 8 crash/fault (także PostgreSQL), bez skip.
Końcowy pełny run koordynatora: **160 PASS w 91,47 s**, bez skip — 131 regresji
P01/P02 i 29 testów P03.1–P03.2. Ruff check: All checks passed; format check:
41 files already formatted. `git diff --check`: PASS. Self-review zakończony;
brak otwartego blokera tego ograniczonego zakresu. Nie stanowi to G3 PASS.

## Odtworzenie testów

Wymagany izolowany lokalny PostgreSQL z jawnym `P02_TEST_DSN` (ten sam testowy
kontrakt co P02). Każdy test zakłada własny losowy schemat i usuwa tylko ten schemat.
Migracje 001 oraz 002 są stosowane w tym schemacie; klaster nie jest częścią repo.

```sh
P02_TEST_DSN='host=/sciezka/do/socket port=55432 dbname=postgres' .venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

Żadnych credentials, prywatnych endpointów ani realnych transakcji. V01–V11
pozostają UNKNOWN. Zatrzymujemy pracę po commicie P03.1–P03.2; kolejne podetapy
wymagają zgody użytkownika.
