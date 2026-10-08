# Crypto Trading Bot v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Ten dokument jest planem, nie poleceniem rozpoczęcia wykonania. Wybór metody wykonania i zatwierdzenie planu należą do użytkownika.

**Goal:** Zbudować testowalny system spot-only, który rzetelnie oceni baseline breakout/retest i dopuści live wyłącznie po przejściu bramek bezpieczeństwa, danych i badań.

**Architecture:** Jeden modułowy writer execution, trwały ledger/intents w PostgreSQL, dane badawcze w Parquet. Strategia, risk, accounting i lifecycle są wspólne dla symulatora, paper i live; różnią się wyłącznie jawne porty danych, zegara i giełdy. Research i collectors nie posiadają produkcyjnych uprawnień trade.

**Tech Stack:** Propozycja do zatwierdzenia wraz z planem: Python 3.12, Decimal, dataclasses, asyncio; PostgreSQL + psycopg; PyArrow/Parquet; pytest + Hypothesis; Ruff; uv do środowiska i lockfile. Wersje zależności zostaną sprawdzone i zablokowane w P01. Bez frameworka ML, ORM, FastAPI, React, Kafki, Kubernetes i drugiej giełdy w ścieżce v1.

**Spec:** [Technical Design v1.1 — po self-review 2026-10-07](../../2026-10-06-crypto-trading-bot-technical-design-v1.1.md).

**Status:** Plan P00–P14 zatwierdzony przez użytkownika. P00 scalono w PR #1 (merge `494baf1`); użytkownik następnie dopuścił wykonanie wyłącznie P01.1–P01.5 na gałęzi `codex/p01-foundation`. P01 zakończono: G1 PASS wyłącznie dla fundamentu offline. P01 zaakceptowano i scalono do main (`255aa33`); użytkownik upoważnił wyłącznie P02.1–P02.5 na gałęzi `codex/p02-accounting-ledger`. P02 zakończono: G2 PASS offline; [raport P02](../../runbooks/p02-accounting-ledger.md). P02 zaakceptowano i scalono w PR #2 (`7cc3577`). Użytkownik dopuścił wyłącznie P03.1–P03.2; pozostałe podetapy i P04 wymagają kolejnej zgody. Raport: [P03.1–P03.2](../../runbooks/p03-1-2-execution-lifecycle.md). Wyniki odbioru: [P00 evidence](../../capabilities/evidence-policy.md), [P01 / G1](../../runbooks/p01-foundation.md).

## Global Constraints

- Crypto spot, long-only; bez leverage, margin, futures, borrowing i możliwości zadłużenia.
- Q jest jednostką księgową/drawdownu; PLN dodatkową walutą raportowania. Konkretny Q i jego polityka ryzyka przechodzą P00/P06.
- 6% peak-to-current drawdown: CAUTION; 9%: DEFENSIVE; 12%: HARD PAUSE i obowiązkowe review przed wznowieniem.
- Progi 6/9/12% nie są parametrami search/backtest tuning; 12% nie gwarantuje maksymalnej rzeczywistej straty.
- Profil startowy: 1% equity risk per entry; 2% aggregate planned open + pending risk; max 2 pozycje/wejścia; max 50% nominalnie w aktywie; max 90% łącznie. Mnożniki 1,00/0,70/0,40 i pozostałe D według specyfikacji.
- Domyślnie research/paper; produkcyjny live wymaga osobnej zgody na canary i zweryfikowanej konfiguracji. Testy nigdy nie przechodzą automatycznie na produkcję.
- Początkowy profil wejścia FOK LIMIT, bez cichego IOC/GTC/market fallbacku. Niewykonalny FOK oznacza review ścieżki execution, nie wymuszanie fillu.
- Timeframe R0: 5m; A confirmed breakout, B breakout + retest; time exit na 24. planowej granicy 5m po pierwszym fillu.
- Szeroki dynamiczny universe, w tym kwalifikujące się płynne memecoiny; brak specjalnej listy BTC/ETH zamiast radaru.
- Brak ADD, layered entries, dobrowolnych partial take-profits, sygnałowego State Engine, FOMO, ML/news/listings execution w live v1.
- New listings/news są zbierane read-only, gdzie praktyczne. ML shadow pojawia się dopiero, gdy istnieje model i legalne features; wcześniej zapisuje się potrzebne dane, bez fikcyjnych predykcji.
- Deadline/freshness/reconciliation D mierzone w UAT i replay, nigdy dopasowywane do lepszego PnL.
- UNKNOWN/FAIL capability V blokuje zależną ścieżkę. Brak danych historycznych lub edge może zatrzymać live nawet przy poprawnej infrastrukturze.
- Żadnej utraty ledger/audytu nie naprawia się cichym nadpisaniem salda. Brak wznowienia UNKNOWN przez blind retry.
- Brak wykonania tego planu przed jego zatwierdzeniem. Plan nie jest zgodą na realne transakcje, wysyłkę Telegram ani wdrożenie zewnętrzne; właściwe działania będą miały osobne, konkretne bramki.

## Review Focus

Pięć szczególnie zdradliwych klas wejść ma dedykowane testy w zadaniach, nie tylko ogólny zapis „obsłużyć edge cases”:

1. FOK wykonane w całości, ale fille docierają osobno, przed ACK i z opóźnioną opłatą — P02/P03/P06; poprawne inventory i ochrona bez fałszywego alarmu terminal partial ani dwóch pełnych stopów.
2. Dwa zatwierdzenia na tym samym starym snapshotcie, podczas restartu writer’a — P03/P04/P05; najwyżej jedno zużycie dostępnego budżetu i brak retransmisji.
3. Stop anulowany, baza znika przed market sell — P03/P05/P12; jawne uncovered inventory i trwały deadline, brak nieaudytowanego emergency writer’a.
4. Rewizja świecy i luka danych w chwili deadline wyjścia — P07/P09; brak przepisania dawnych decyzji, timer wyjścia działa bez nowej świecy.
5. Wpłata, depeg Q lub duży niezrealizowany zysk dają pozornie lepszy DD/risk budget — P02/P04/P06/P10; jednostkowy NAV, osobna blokada quote risk i mark-to-stop risk bez sztucznego resetu.

## 1. Organizacja wykonania i bramki

Plan ma jeden wspólny kontrakt typów i testów, a etapy są niezależnymi punktami odbioru. Nie dzielimy projektu na równoległe, niezgodne implementacje ledger/risk/backtest. P00 jest zakończonym assessmentem. P01 otrzymało osobną zgodę po merge P00; P02 otrzymało osobną zgodę po merge P01; P03 dopuszczono wyłącznie w zakresie P03.1–P03.2 po merge P02; P03.3–P03.6 i P04–P12 wymagają kolejnej zgody. Zgoda na P02 obejmuje accounting/ledger i testy lokalnego PostgreSQL. P13 i P14 wymagają kolejnych decyzji opisanych w ich sekcjach.

| Etap | Dostarczany wynik | Zależności | Bramka |
|---|---|---|---|
| P00 | Capability/data assessment, profil Q i rejestr evidence | zatwierdzony plan | G0: znana wykonalność profilu albo jawna blokada live |
| P01 | Typy, profile, clock, izolacja środowisk, minimalny raw capture | P00 | G1: deterministyczne kontrakty bez dostępu live |
| P02 | Accounting/ledger i rezerwacje | P01 | G2: uzgadnialny stan finansowy |
| P03 | Durable intents, order/protection state machines | P02 | G3: poprawne skutki asynchroniczne |
| P04 | Risk, DD i atomic admission | P02–P03 | G4: nieprzekraczalne uprawnienia nowych zleceń |
| P05 | Recovery, reconciliation i policy awarii | P03–P04 | G5: restart bez nowego ryzyka |
| P06 | Adapter Crypto.com i testy UAT capabilities | P00–P05 | G6: dowody kontraktowe profilu execution |
| P07 | PIT pipeline, universe, radar, archiwum obserwacji | P01 + publiczna część P06 | G7: wiarygodna dostępność danych |
| P08 | Event-driven backtester i model execution | P02–P05, P07; evidence P06 dla B1+ | G8: realistyczne granice symulacji |
| P09 | Baseline A/B i zarządzanie trade’em | P04, P07–P08 | G9: dokładnie zdefiniowane porównanie |
| P10 | Walk-forward, holdout, koszty/stress i raport edge | P08–P09 | G10: PASS/FAIL/INCONCLUSIVE badań |
| P11 | Ciągły paper/shadow na live data | P05–P09, wszystkie wymagane pre-paper tests | G11: zgodna ścieżka operacyjna |
| P12 | Kwalifikacja bezpieczeństwa, fault injection i pakiet go/no-go | P06, P10–P11 | G12: gotowość do decyzji o canary |
| P13 | Nadzorowany canary, potem ewentualna autonomia | G12 + osobna zgoda użytkownika | G13: dowód produkcyjnego wykonania |
| P14 | Oddzielne eksperymenty rozszerzeń | baseline/evidence i osobny zatwierdzony plan | bez automatycznej promocji do live |

Kolejność bazowa jest sekwencyjna. Wyjątki ograniczające utratę czasu: raw public capture może działać od P01 po potwierdzeniu read-only źródła; normalizacja P07 może ruszyć po publicznej części P06 bez czekania na prywatny UAT; paper P11 może zbierać dane podczas dłuższego P10, lecz nie daje zgody live. Prace offline nie muszą czekać na niedostępne konto, o ile nie udają pozytywnego wyniku capabilities. Żaden wyjątek nie pozwala wysłać zlecenia przez nieweryfikowaną ścieżkę.

### Wspólna procedura odbioru zadania

Każde zadanie z kodem zaczyna od konkretnego failing testu opisanego poniżej, uruchamia go i potwierdza właściwą przyczynę FAIL, następnie dodaje minimalną implementację, uruchamia testy danego zakresu i sprawdza wymagane invariants. Kończy się review zmiany, aktualizacją evidence i małym lokalnym commitem w zastanym repozytorium, dopiero po zatwierdzeniu planu. Użytkownik zezwolił na commit i push wyników pracy do wskazanego repozytorium, także etapami. Nie jest to zgoda na deployment ani transakcje.

Testy P00 i etapy dowodowe nie wymagają sztucznego TDD dokumentów. Niedostępny UAT/credential jest BLOCKED, nie PASS ani zaliczony skip. Niebezpieczne scenariusze testujemy wyłącznie w fake/simulatorze/UAT. Nie uruchamiamy „testów braku borrowing” na produkcji przez próbę rzeczywistego zadłużenia.

## 2. Mapa przyszłych plików i odpowiedzialności

Ścieżki w planie są względem katalogu projektu. To mapa docelowej struktury; w P01 powstają wyłącznie wskazane w tym etapie kontrakty, profile, clock, raw capture oraz ich testy.

| Obszar | Katalog / pliki | Odpowiedzialność |
|---|---|---|
| Kontrakty | `src/trading_bot/domain/` | jawne rekordy, Decimal, identyfikatory, event envelopes |
| Konfiguracja | `src/trading_bot/config/`, `configs/` | profile z wersją/hash, walidacja i isolation guards |
| Trwałość | `src/trading_bot/storage/`, `migrations/` | transakcje ledger/intents/outbox/checkpoints i migracje |
| Finanse | `src/trading_bot/accounting/` | księgowania, rezerwacje, inventory, NAV, fees/dust |
| Execution | `src/trading_bot/execution/` | intents, lifecycle, single writer, protection/exit coordination |
| Ryzyko | `src/trading_bot/risk/` | sizing, caps, modes, episodes, quote gate |
| Operacje | `src/trading_bot/operations/` | recovery, failure policy, watchdog, alerting, shutdown |
| Giełda | `src/trading_bot/exchanges/` | port giełdy, Crypto.com normalization, rate limits, capabilities |
| Dane | `src/trading_bot/data/` | capture, PIT, świece, metadata, universe, radar |
| Symulacja | `src/trading_bot/simulation/` | kolejka zdarzeń, matching/cost/latency models, fault exchange |
| Strategia | `src/trading_bot/strategy/` | cechy R0, setup A/B, ranking, deadlines, exit signals |
| Badania | `src/trading_bot/research/` | foldy, manifesty, metryki, uncertainty, gates, holdout seal |
| Tryby | `src/trading_bot/runtime/`, `src/trading_bot/cli.py` | jawne składanie backtest/paper/shadow/UAT/live |
| Obserwacje | `src/trading_bot/observers/` | read-only listing/news/model-shadow bez command portu |
| Testy | `tests/unit/`, `tests/property/`, `tests/integration/`, `tests/contract/`, `tests/replay/`, `tests/faults/` | dowody kontraktów i invariants |
| Evidence | `docs/capabilities/`, `docs/runbooks/`, `artifacts/` | wersjonowane dowody; duże dane i sekrety poza Git |

Jeden moduł nie musi być usługą. Nie tworzyć pustych katalogów przyszłego ML/GUI tylko dla zgodności z v1.0. Kwoty i ilości nie przechodzą przez float na ścieżce finansowej; konwersje do bibliotek statystycznych są jawne i dotyczą wyłącznie analizy.

### 2.1. Kontrakty współdzielone

Poniższe nazwy definiuje P01; kolejne etapy rozszerzają je wyłącznie wersjonowanym kontraktem, nie alternatywnymi strukturami o tym samym znaczeniu.

| Rekord / port | Wymagane znaczenie i pola |
|---|---|
| `Money`, `Quantity`, `InstrumentId` | Decimal + currency/base; kanoniczna tożsamość rynku i tokena, nie sam ticker |
| `EventEnvelope` | event_id, account_id/run_id, source, source_seq, event_time, received_at, available_at, schema_version, payload_ref/hash |
| `InstrumentRules` | spot/type/status, base/quote, tick/min/max, effective_at/available_at, version |
| `ExecutionProfile` | account/environment/endpoints, Q, allowed instruments/types, FOK, trigger source, exit policy, retry policy, fee policy, D deadlines, evidence hashes |
| `RiskProfile`, `ResearchProfile` | zatwierdzone limity/6-9-12 oraz zamrożone R0/data split/metric parameters; osobne hash’e |
| `CapabilityEvidence`, `GateReport` | capability_id, scope, PASS/FAIL/UNKNOWN/DISABLED_VERIFIED, artefakt i data; gate: PASS/BLOCKED/FAIL/INCONCLUSIVE + reasons |
| `Clock` | `utc_now()`, `monotonic_now()`, `schedule(deadline, event)`; rzeczywisty i deterministyczny zegar bez ukrytego globalnego czasu |
| `Fill`, `Fee`, `BalanceObservation`, `OrderObservation` | źródłowa tożsamość, qty/price/fee currency, cumulative qty i terminalność jako dowody, nie domniemania |
| `LedgerSnapshot` | version, salda, lots/inventory, fee commitments, reservations, NAV/units/HWM i reason codes niepewności |
| `OrderIntent`, `RiskApproval`, `Reservation` | stable ID, exact payload hash, qty/price bounds, risk/cash, ledger version, expiry i status wykorzystania |
| `ExecutionEvent`, `ExecutionState`, `CommandProposal` | zdarzenie/wewnętrzna projekcja i proponowana komenda; reducer nie wysyła I/O |
| `ProtectionState`, `ExitRequest` | target/covered/uncovered qty, per-lot oldest deadlines, competing order IDs; powód i scope wyjścia |
| `MarketSnapshot`, `CostEstimate`, `RiskDecision` | PIT book/reference/metadata; pełny rozkład i percentyl sizingu; APPROVE/DENY wraz z constraints |
| `RiskState`, `HealthState`, `RecoveryReport` | trwały mode/latches/timery, zdrowie niezależne od risk; unresolved evidence i permission do wznowienia |
| `CandleRevision`, `DatasetManifest`, `EligibilityDecision` | półotwarty przedział, rewizja/availability/coverage; lineage; kwalifikacja i reason codes |
| `FeatureSnapshot`, `Setup`, `StrategyState` | H/L/A/S/activity, as-of i dependencies; wspólne setup ID A/B, expiry/episode i stan retestu |
| `RunManifest`, `SimulationResult`, `ExperimentReport` | pełna tożsamość runu, zdarzenia/ledger/decisions, wyniki/gates/niepewność bez nadpisania wcześniejszej wersji |
| `ExchangePort` | `submit(intent)`, `cancel(intent)`, `observe_orders(scope)`, `observe_balances()`, `read_fills(cursor)`, `subscribe_private()`; wyniki normalized observations, nigdy obietnica fillu z ACK |
| `MarketDataPort` | `subscribe(scope)`, `snapshot(instrument)`; zwraca EventEnvelope/MarketSnapshot, bez trade permissions |

Dodatkowe identyfikatory (`SetupId`, `IntentId`, `RunId`, `Cursor`) są typowanymi kluczami tych rekordów. Wszystkie metody przyjmują clock/profile explicite przez konstruktor lub parametr. Warstwa strategy nie importuje transportu giełdy ani sekretów.

## P00 — Capability i data assessment przed budową rdzenia

**Cel:** ustalić, które założenia można zweryfikować, jakie dane istnieją i co blokuje wybrany profil live. Nie założyć zgodności na podstawie samej nazwy order type.

**Pliki:** `docs/capabilities/crypto-com-matrix.md`, `docs/capabilities/data-coverage.md`, `docs/capabilities/quote-profile.md`, `docs/capabilities/evidence-policy.md`, `docs/capabilities/test-cases.md`.

**Kontrakty/interfejsy:** katalog `V01 spot/no-debt`, `V02 FOK`, `V03 stop-market`, `V04 attached protection`, `V05 IDs/query/dedup`, `V06 cancel/exit`, `V07 orders/fills/history`, `V08 balances/fee`, `V09 metadata/rate limits`, `V10 historical data`, `V11 Q/valuation`. Każdy wpis ma scope, dowód, termin ponownej oceny i test docelowy P06/P07/P12. Nie używane attached/retry/native-linked mogą być DISABLED_VERIFIED dopiero po testach nieosiągalności.

**Małe zadania:**

- [x] P00.1: przejrzeć aktualne oficjalne dokumenty, przypisać dokładne endpointy/środowiska i niejasności do V01–V11. Utworzyć macierz; żaden wpis bez dowodu nie dostaje PASS.
- [x] P00.2: ustalić dostępność UAT i read-only danych konta, Q, fee tier, minima i rodzaj salda; brak dostępu zapisać jako blocker, bez proszenia o wklejanie sekretów do czatu.
- [x] P00.3: przygotować manifest źródeł OHLCV/trades/L2/trigger reference/listing-delisting i dostępnych okresów. Ujawnić koszt/licencję i luki; nie kupować danych w ramach assessmentu bez zgody.
- [x] P00.4: wybrać kandydata Q i opisać konkretną politykę monitorowania FX/depeg według §0.2 spec. Jeśli konto/dane nie pozwalają dokonać wyboru, oznaczyć runtime profile INCOMPLETE i prowadzić dalsze testy na jawnej walucie syntetycznej offline.
- [x] P00.5: przejść ręcznie scenariusze timeout BUY, częściowe komunikaty FOK, stop reject i cancel→sell outage; zapisać oczekiwany dowód dla każdego kroku.

**Odbiór P00 (2026-10-07):** G0 PASS w zakresie assessmentu/offline; V01–V11 UNKNOWN, runtime INCOMPLETE, live BLOCKED. Checkboxy oznaczają wykonanie assessmentu i tabletop, nie pozytywne testy UAT/konta. Self-review i niezależny review: [evidence-policy](../../capabilities/evidence-policy.md). P01 nie rozpoczęto.

**Testy/odbiór:** kompletność wszystkich 11 V, brak twierdzeń o atomicity bez źródła, rozdzielone UAT/production i scenariusze używane/wyłączone. Porównać literalnie z §8 spec.

**Definition of Done:** macierz nie ma ukrytych założeń; wybrany kandydat profilu ma PASS/UNKNOWN/FAIL z konsekwencją. G0 może dopuścić budowę offline mimo UNKNOWN, ale nie deklaruje gotowości live.

**Zależności:** zatwierdzenie planu. **Blokery następnego etapu:** sprzeczny kontrakt wymagający zmiany architektury; brak danych konta blokuje realny profil, lecz nie czysty ledger/state machine. Całkowity brak danych target venue blokuje późniejszą ocenę live, nie badania demonstracyjne.

## P01 — Kontrakty, profile, clock i minimalne zbieranie danych

**Cel:** zbudować najmniejszy testowalny fundament z twardą izolacją środowisk; rozpocząć praktyczny read-only capture bez czekania na ML.

**Pliki:** `pyproject.toml`, `uv.lock`, `.gitignore`, `README.md`; `src/trading_bot/domain/{money,events,records}.py`; `src/trading_bot/config/{profiles,validation}.py`; `src/trading_bot/operations/clock.py`; `src/trading_bot/data/raw_capture.py`; `configs/research.toml`, `configs/paper.toml`, `configs/uat.toml`; `tests/unit/test_contracts.py`, `test_profiles.py`, `test_clock.py`, `test_raw_capture.py`.

**Interfejsy:** rekordy z §2.1; `validate_profiles(execution, risk, research) → GateReport`; `append_raw(event: EventEnvelope) → payload_ref`; clock real/simulation. Plik produkcyjny nie jest generowany z prawdziwymi kluczami; live activation pozostaje nieosiągalne.

**Małe zadania:**

- [x] P01.1: dopiero po zatwierdzeniu planu sprawdzić zastany stan Git i instrukcje workspace, zachować metadane aplikacji, a następnie utworzyć pakiet wraz z failing testami serializacji Decimal, walut i UTC. Test `test_money_currency_mismatch_rejected` odrzuca dodanie Q do base; `test_precision_round_trip` zachowuje dokładną wartość przez zapis/odczyt.
- [x] P01.2: testy `test_live_profile_incomplete_denied`, `test_paper_rejects_production_trade_transport`, `test_risk_thresholds_not_search_parameters`: niepełne V, credential scope i próba search 6/9/12 kończą się jawnym błędem. Dodać walidację profili oraz hash/version.
- [x] P01.3: test `test_utc_jump_does_not_extend_monotonic_deadline` oraz deterministic timer replay; zaimplementować Clock i envelope serialization bez I/O giełdy w domenie.
- [x] P01.4: testy fake public feed z duplicate i reconnect; minimalny raw collector zapisuje oryginalny payload, odebranie i gap marker, nie generuje sygnałów. Może użyć jedynie read-only źródła potwierdzonego w P00; reszta public adaptera powstaje w P06.
- [x] P01.5: uruchomić `pytest tests/unit/test_contracts.py tests/unit/test_profiles.py tests/unit/test_clock.py tests/unit/test_raw_capture.py -q` oraz `ruff check src tests`; oczekiwane PASS/0 violations. Zablokować sprawdzone wersje w lockfile, dodać README i commit.

**Odbiór P01:** G1 PASS offline; 76 testów PASS, Ruff bez naruszeń, publiczny smoke i niezależny review zakończone. Szczegóły, TDD i ograniczenia: [runbook P01](../../runbooks/p01-foundation.md). P02 nie rozpoczęto.

**DoD:** deterministyczne rekordy/profile, pomyślny offline smoke test, brak side-effectów przy imporcie. Capture może działać od tego momentu jako oddzielny read-only proces i musi pokazywać pokrycie/braki.

**Zależności:** P00. **Blokery:** niejednoznaczne currency/time semantics, brak izolacji paper/live. Brak publicznego źródła blokuje collector, nie testy domeny.

## P02 — Accounting, ledger, inventory i rezerwacje

**Cel:** każde saldo i posiadana ilość mają odtwarzalny ekonomiczny powód; rezerwacje nie tworzą pieniędzy ani długu.

**Pliki:** `src/trading_bot/accounting/{ledger,reservations,inventory,nav}.py`; `src/trading_bot/storage/{transactions,ledger_repository}.py`; `migrations/001_ledger.sql`; `tests/unit/test_ledger.py`, `test_nav.py`; `tests/property/test_accounting_invariants.py`; `tests/integration/test_ledger_transactions.py`.

**Interfejsy:** `post_fill(fill: Fill) → LedgerSnapshot`; `post_fee_correction(fee: Fee) → LedgerSnapshot`; `reserve(reservation: Reservation, expected_version) → LedgerSnapshot`; `release(reservation_id, terminal_evidence) → LedgerSnapshot`; `value(snapshot, market, cost_estimate) → LedgerSnapshot` z NAV/units/HWM. Wszystkie skutki jednej operacji w jednej transakcji PostgreSQL; append-only entries oraz projekcje odbudowywalne.

**Małe zadania:**

- [x] P02.1: failing tests `test_same_fill_from_ws_and_rest_posts_once`, `test_fill_fee_in_base_changes_sellable_quantity`, `test_fee_revision_posts_delta_not_second_fill`; dodać ledger postings i dedup key scoped account/instrument.
- [x] P02.2: testy rezerwacji wolnych/zajętych Q i fee currency, częściowej konwersji pending→inventory oraz terminalnego zwolnienia. Nie zwalniać na timeout ani na samo cancel ACK.
- [x] P02.3: `test_deposit_preserves_unit_nav_and_hwm`, `test_reserved_cash_not_double_counted`, `test_unpriced_asset_marks_nav_uncertain`, `test_dust_remains_in_inventory`; zaimplementować NAV i lots z jawnie przypisanymi fee.
- [x] P02.4: property tests bilansowania per currency i odtwarzania stanu po dowolnym legalnym ciągu zdarzeń; integration rollback po awarii między wpisem ledger a projekcją.
- [x] P02.5: `pytest tests/unit/test_ledger.py tests/unit/test_nav.py tests/property/test_accounting_invariants.py tests/integration/test_ledger_transactions.py -q`; PASS także na prawdziwym lokalnym PostgreSQL, nie tylko mocku. Review i commit.

**Odbiór P02 (2026-10-08):** G2 PASS offline. Pełny suite: 131 PASS; P02: 46 unit, 2 property, 7 real PostgreSQL integration PASS. Ruff bez naruszeń; self-review i niezależny review zakończone. Ograniczenia i regresje: [runbook P02](../../runbooks/p02-accounting-ledger.md). P03 nie rozpoczęto.

**DoD:** wszystkie fille/fee/rezerwacje audytowalne, powtórzenie eventów nie zmienia wynikowego majątku, migracja i replay odbudowują identyczny snapshot.

**Zależności:** P01. **Blokery:** różnice balances versus ledger, niewyjaśniona fee currency, ciche korekty lub możliwość wydania reserved cash.

## P03 — Durable intents, order lifecycle i ochrona

**Cel:** oddzielić polecenie od skutku i zapewnić bezpieczną reakcję na brak odpowiedzi.

**Pliki:** `src/trading_bot/execution/{intents,reducer,dispatcher,ownership,protection,exits}.py`; `src/trading_bot/storage/execution_repository.py`; `migrations/002_execution.sql`; `tests/unit/test_order_reducer.py`, `test_protection.py`, `test_exit_coordinator.py`; `tests/integration/test_intent_outbox.py`; `tests/faults/test_dispatch_crashes.py`.

**Interfejsy:** `prepare_intent(approval, reservation, payload) → OrderIntent`; `apply(state: ExecutionState, event: ExecutionEvent) → (ExecutionState, list[CommandProposal])`; `assess_protection(inventory, protection, clock) → ProtectionState`; `request_exit(request: ExitRequest, state) → list[CommandProposal]`. Dispatcher wysyła wyłącznie trwałe komendy, po ownership check. RiskApproval w tych testach pochodzi z fixture; realny governor dopiero P04.

**Małe zadania:**

- [x] P03.1: failing crash tests na granicach PREPARED/DISPATCHING/send/ACK; `test_restart_never_resends_ambiguous_create` wymaga UNKNOWN, zachowanej rezerwacji i zerowej liczby dodatkowych submits. Dodać transakcję intent/outbox i brak domyślnego automatic retry.
- [x] P03.2: testy fill-before-ACK, cancel-fill race, late canceled, repeated source observations i status regression. Reducer nie cofa inventory ani terminalnych skutków na podstawie starego komunikatu.
- [ ] P03.3: `test_multi_fill_fok_not_terminal_partial`, `test_new_fill_during_unknown_stop_does_not_duplicate_coverage`, `test_oldest_uncovered_deadline_not_reset`: serializacja protection i qty target. Small partial poniżej minimum zostaje uncovered, nie znika jako dust przed rozstrzygnięciem zlecenia.
- [ ] P03.4: testy obu jawnych exit policies na fake exchange; bez zweryfikowanego profilu brak komendy. `test_cancel_ack_is_not_sell_permission` i `test_stop_fill_during_soft_exit_prevents_oversell` dowodzą właściwej koordynacji.
- [ ] P03.5: test dwóch dispatcherów oraz utraty ownership; pojedynczy writer i brak automatycznego takeover. Dodać runbook odcięcia starego procesu; DB lease nie jest jedyną ochroną przed drugim hostem.
- [ ] P03.6: `pytest tests/unit/test_order_reducer.py tests/unit/test_protection.py tests/unit/test_exit_coordinator.py tests/integration/test_intent_outbox.py tests/faults/test_dispatch_crashes.py -q`; review invariants E01–E05 i commit.

**Częściowy odbiór (2026-10-08):** P03.1–P03.2 zakończono; [raport](../../runbooks/p03-1-2-execution-lifecycle.md). Narrow OrderState/ledger helper nie kończy jeszcze planowanego czystego interfejsu event/proposals. G3 pozostaje niezaliczone; P03.3–P03.6 nie rozpoczęto.

**DoD:** pełny lifecycle na fake exchange, brak duplicate economic effects i blind retries; okna uncovered oraz unresolved są jawne i mają trwałe deadlines.

**Zależności:** P02. **Blokery:** brak rozstrzygnięcia ownership, oversell, timer reset przy fillu/restartach lub ukryty cancel/replace fallback.

## P04 — Risk Governor, drawdown i atomic admission

**Cel:** zatwierdzenia nie mogą ominąć limitów przez współbieżność, stale snapshot, re-entry lub pozorną dywersyfikację.

**Pliki:** `src/trading_bot/risk/{sizing,admission,modes,episodes,quote_guard}.py`; `migrations/003_risk_state.sql`; `tests/unit/test_sizing.py`, `test_drawdown_modes.py`, `test_episode_budget.py`, `test_quote_guard.py`; `tests/property/test_risk_invariants.py`; `tests/integration/test_atomic_admission.py`.

**Interfejsy:** `evaluate_entry(setup: Setup, ledger: LedgerSnapshot, market: MarketSnapshot, cost: CostEstimate, risk: RiskState) → RiskDecision`; `approve_and_reserve(decision, expected_ledger_version) → (RiskApproval, Reservation)` atomowo; `advance_risk(state, nav, clock) → RiskState`; `evaluate_quote(profile, observations) → HealthState`. Producent approval jest jedyną ścieżką nowych entry intents.

**Małe zadania:**

- [ ] P04.1: testy kosztowego sizingu i rounding w dół; fee/stop shortfall uwzględnione raz. Minimum powyżej legalnego rozmiaru powoduje DENY, nie zaokrąglenie ryzyka w górę. 99. percentyl ma source/uncertainty metadata; synthetic test costs nie są dowodem live.
- [ ] P04.2: `test_open_plus_pending_uses_max_initial_and_mark_to_stop_risk`, `test_winning_position_can_exhaust_risk_headroom`, `test_price_drift_caps_block_entry_without_forced_sell`; zastosować dokładną formułę §3.2 spec.
- [ ] P04.3: mode tests dla dokładnych 6/9/12%, skoku 5→13%, histerezy 6→5,5%, 6 h stabilności oraz braku automatycznego resume 13→4%. Progi nie są search parameters; latch zapisany transakcyjnie.
- [ ] P04.4: episode tests: nowy setup ID nie odnawia strat, zysk nie kompensuje limitu strat, maks. 2 wykonane wejścia, 3 świeczki cooldown i brak ponownego wejścia przy stale protection.
- [ ] P04.5: dwa równoczesne approvals na tym samym snapshotcie — drugie musi być przeliczone/odrzucone, nigdy zużyć tych samych Q lub slotu. Test TTL i zmiany risk mode pomiędzy approve a dispatch.
- [ ] P04.6: quote guard testy missing/stale/niezgodnych źródeł, depegu według wybranego profilu; brak cichego Q=USD. `pytest tests/unit/test_sizing.py tests/unit/test_drawdown_modes.py tests/unit/test_episode_budget.py tests/unit/test_quote_guard.py tests/property/test_risk_invariants.py tests/integration/test_atomic_admission.py -q`; review i commit.

**DoD:** defaults działają jako kontrola przed komendą, nie tylko alarm po fillu; wszystkie reductions nadal przechodzą kontrolę inventory bez blokowania ich przez HARD PAUSE.

**Zależności:** P02–P03; profil Q z P00. **Blokery:** możliwość obejścia governora, double reservation, utrata DD latch, niekompletny Q profile dla live. Testy offline korzystają z jawnego profilu syntetycznego, bez promocji evidence do produkcji.

## P05 — Reconciliation, recovery, watchdog i failure policy

**Cel:** restart nie odtwarza świata z nieaktualnego snapshotu i nie uruchamia nowych wejść przed odzyskaniem spójności.

**Pliki:** `src/trading_bot/operations/{reconciliation,recovery,failure_policy,watchdog,shutdown,alerts}.py`; `src/trading_bot/storage/checkpoints.py`; `migrations/004_checkpoints.sql`; `docs/runbooks/{recovery,manual-intervention,ownership,backup-restore}.md`; `tests/integration/test_reconciliation.py`, `test_recovery.py`; `tests/faults/test_failure_matrix.py`, `test_storage_outage.py`.

**Interfejsy:** `reconcile(local: LedgerSnapshot, observations: list[EventEnvelope]) → RecoveryReport`; `recover(profile, repository, exchange: ExchangePort, clock) → RecoveryReport`; `apply_failure(health: HealthState, event) → (HealthState, list[CommandProposal])`. Report odróżnia brak dowodów od różnicy finansowej i wskazuje entry permission.

**Małe zadania:**

- [ ] P05.1: fake snapshots z różnych chwil, duplicate history pages i fillami w tym samym timestampie; checkpoint nie pomija żadnego fillu ani nie rozlicza go podwójnie.
- [ ] P05.2: test `test_recovery_includes_advanced_orders`, `test_history_retention_gap_blocks_resume`, `test_external_order_not_adopted_or_cancelled`; odbudować ordinary/advanced/inventory/protection przed READY.
- [ ] P05.3: wszystkie wiersze failure matrix jako parametryzowane scenariusze; oczekiwany scope block, zachowane zlecenia, alert i dokładny warunek powrotu. 60 s zdrowia nie usuwa latch incydentu/DD.
- [ ] P05.4: `test_restart_overdue_exit_deadline_fires_without_new_candle`, `test_database_loss_after_stop_cancel_records_uncovered_on_recovery`; żadnych nieaudytowanych komend podczas utraty bazy. Alert sink testowy ma własny ograniczony retry i nie blokuje execution.
- [ ] P05.5: restore z backupu plus history catch-up; sprawdzić wszystkie trwałe latches, reservations i timers. Runbook określa ręczną interwencję, gdy historii brak; nie zgaduje brakujących filli.
- [ ] P05.6: `pytest tests/integration/test_reconciliation.py tests/integration/test_recovery.py tests/faults/test_failure_matrix.py tests/faults/test_storage_outage.py -q`; review runbooks, evidence i commit.

**DoD:** recovery kończy się READY tylko na spójnym stanie; baza/WS/API failures nie tworzą ukrytego writer’a; powtórny restart daje ten sam wynik.

**Zależności:** P03–P04. **Blokery:** divergence, utrata historii, niekontrolowane manual orders, brak trwałych timerów/latch.

## P06 — Crypto.com adapter i capability tests UAT

**Cel:** zastąpić fake exchange zweryfikowanym adapterem bez zmiany logiki bezpieczeństwa.

**Pliki:** `src/trading_bot/exchanges/{ports,capabilities}.py`; `src/trading_bot/exchanges/crypto_com/{transport,normalization,public_data,private_data,orders,advanced_orders,limits}.py`; `tests/contract/test_crypto_com_public.py`, `test_crypto_com_account.py`, `test_crypto_com_orders.py`, `test_crypto_com_protection.py`, `test_crypto_com_history.py`; `tests/fixtures/crypto_com/` (wyłącznie zanonimizowane payloady); aktualizacje `docs/capabilities/`.

**Interfejsy:** implementacje `ExchangePort` i `MarketDataPort`; `verify_capabilities(profile, evidence) → GateReport`; transport zwraca obserwacje/niepewność, nigdy automatyczny retry create. Reconciliation otrzymuje normalized ordinary + advanced streams. State machine nie zna payloadów Crypto.com.

**Małe zadania:**

- [ ] P06.1: public metadata/BBO/book/trades i sekwencje; testy snapshot+delta gap, crossed book, stale source, rate-limit backoff. Włączyć walidowany public raw capture z P01 i dodać metadata/listing observations.
- [ ] P06.2: account normalization i blacklist niedozwolonych pól/endpointów. `test_margin_flags_rejected_before_transport`, `test_buying_power_not_spendable_cash`, `test_missing_fee_currency_blocks_entry`; potwierdzić settings read-only, bez prób zadłużenia na produkcji.
- [ ] P06.3: UAT FOK success/no-fill/reject i wiele fill messages, query by ID, delayed history, cancel/ACK/terminal race. `test_unsupported_fok_never_sends_ioc`; UNKNOWN dowodzi zachowania w fake fault transport, nie przez ryzykowne powielanie production orders.
- [ ] P06.4: stop-market, reference source, saldo, quantity/tick/minimum, insufficient funds, activated/triggered/filled distinction; osobno test jednego wybranego exit policy. Attached/native-linked nieużywane muszą być wyłączone i sprawdzone jako nieosiągalne.
- [ ] P06.5: ordinary/advanced paging, retention, fill identity i fee corrections; zmierzyć ACK/protection/reconciliation latency oraz wpływ rate limits. Dla ścieżki WS emergency nie wystarczy działający REST; wymaga osobnych testów lub DISABLED_VERIFIED.
- [ ] P06.6: `pytest tests/contract -m crypto_com_public -q` oraz `pytest tests/contract -m crypto_com_uat -q` w odpowiednio odseparowanych środowiskach; wszystkie wymagane cases PASS, brak „skip zaliczony”. Raport wypełnia V01–V09/V11 z evidence scope, hash, wersją i datą.

**DoD:** profil używa tylko pozytywnie zweryfikowanych lub jawnie wyłączonych ścieżek; każda V ma rozstrzygnięcie i gate consequence. Latency D nierealne w UAT wymagają review parametrów operacyjnych, nie podmiany cen backtestu.

**Zależności:** P00–P05. **Blokery:** unsupported FOK/stop/no-debt, nierozstrzygalne query lub brak UAT. Można kontynuować research/paper z zaznaczonym simulator profile; nie zatwierdzać live. Brak FOK kieruje do oddzielnego projektu execution, bez patchowania in-flight fallbacku.

## P07 — PIT pipeline, dynamiczne universe i radar

**Cel:** decyzja widzi dokładnie to, co było dostępne wtedy; dane obejmują również rynki, które później zniknęły.

**Pliki:** `src/trading_bot/data/{normalization,pit_store,candles,instrument_registry,universe,radar,manifests}.py`; `src/trading_bot/observers/{listings,news}.py`; `tests/unit/test_candle_availability.py`, `test_universe.py`; `tests/replay/test_pit_revisions.py`; `tests/property/test_no_future_dependency.py`; `docs/capabilities/data-coverage.md`.

**Interfejsy:** `normalize(raw: EventEnvelope) → EventEnvelope`; `build_candles(events, clock) → list[CandleRevision]`; `as_known(dataset, decision_time) → observations`; `eligible(instrument, market, history, profiles) → EligibilityDecision`; `scan(as_of, universe) → candidate snapshots`; DatasetManifest rozróżnia rzeczywiste i modelowane availability.

**Małe zadania:**

- [ ] P07.1: testy półotwartych granic UTC, watermark 1 s, out-of-order i późnej rewizji; zero trades versus outage nie może dać tego samego rekordu. Zmiana przyszłych danych pozostawia wcześniejsze decyzje identyczne.
- [ ] P07.2: registry listing/delist/token migration/metadata effective versus available times; brak handlu przed dopuszczeniem i brak cofania dzisiejszego universe.
- [ ] P07.3: eligibility według §4.3 spec z reason codes dla każdego rynku; spread/depth sprawdzane w chwili wejścia, historyczne luki bez zastępowania dzisiejszym bookiem. BTC/ETH nie stanowią specjalnego ograniczenia universe.
- [ ] P07.4: oficjalne news/listing capture jako observer: first_seen/ingested/revisions/asset mapping, bez command port. Gdy źródło nie jest dostępne, jawny gap i brak zmyślonych events. ML wymaga na razie feature/audit archive, nie tworzenia modeli.
- [ ] P07.5: utworzyć fingerprinty, coverage per asset/period/type, PIT provenance oraz warmup rules. Dane innej giełdy oznaczyć jako exploratory, nie execution truth Crypto.com.
- [ ] P07.6: `pytest tests/unit/test_candle_availability.py tests/unit/test_universe.py tests/replay/test_pit_revisions.py tests/property/test_no_future_dependency.py -q`; audit kilku ręcznie odtworzonych timestamps i commit.

**DoD:** replay as-known jest deterministyczny, eligibility odtwarzalne, delisted/missing coverage jawne. V10 posiada dowód zakresu, nie deklarację „history available”.

**Zależności:** P01 i publiczna część P06; prywatny UAT nie jest wymagany do zapisu publicznych danych. **Blokery:** brak PIT metadanych lub źródła triggera ogranicza poziom badań/live, brak wystarczającej historii skutkuje INCONCLUSIVE.

## P08 — Backtester, matching, koszty i fault exchange

**Cel:** ten sam risk/lifecycle/accounting działa na jawnie ograniczonej symulacji wykonania.

**Pliki:** `src/trading_bot/simulation/{engine,exchange,matching,costs,latency,faults}.py`; `tests/unit/test_matching.py`, `test_costs.py`; `tests/replay/test_execution_timeline.py`, `test_shared_cash.py`; `tests/faults/test_simulated_exchange_outage.py`; `docs/capabilities/simulation-evidence.md`.

**Interfejsy:** `run(manifest: RunManifest, events, strategy) → SimulationResult`; simulated ExchangePort; `estimate_cost(order, market, calibration) → CostEstimate`; `match(intent, book, clock) → list[ExecutionEvent]`. Wynik oznaczony B0/B1 i limitations; żaden model nie zapisuje fillu bez timestampu powiązanego z osią zdarzeń.

**Małe zadania:**

- [ ] P08.1: failing timeline test sygnał→latency→matching→private lag→protection. Nie ma fillu na nieznanym close ani ochrony przed jej giełdową aktywacją.
- [ ] P08.2: FOK insufficient-depth/no-fill, split fill events przy pełnym matching, spread/depth haircut i price caps. Pasywne touched limit nie jest fill; maker path pozostaje wyłączony.
- [ ] P08.3: stop gap/stop reference missing/trading halt/partial market exit/dust. `test_stop_fills_at_next_executable_price_not_trigger`, `test_halt_keeps_inventory_open`, `test_b0_ambiguous_bar_reports_bounds`.
- [ ] P08.4: fee/spread/impact/latency attribution: komponenty raportowe rekonstruują różnicę cen, lecz nie są ponownie odejmowane od cash już wynikającego z fill prices. Sizing p99 i expected PnL używają odrębnych widoków tego samego distribution manifest.
- [ ] P08.5: wspólny cash dwóch jednoczesnych sygnałów i UNKNOWN reserve; jawne no-fill outcomes oraz zmiany metadata. Fault exchange implementuje każde zdarzenie failure matrix, nie tylko losowy slippage.
- [ ] P08.6: `pytest tests/unit/test_matching.py tests/unit/test_costs.py tests/replay/test_execution_timeline.py tests/replay/test_shared_cash.py tests/faults/test_simulated_exchange_outage.py -q`; ręcznie policzone golden scenarios, review i commit.

**DoD:** cash/ledger zgodne z oczekiwaniami golden fixtures, event ordering i kosztowe ograniczenia jawne; B0 nie uzyskuje flagi live-ready. Kalibracja pochodzi wyłącznie z wcześniejszych danych.

**Zależności:** P02–P05, P07; dowody P06 niezbędne do deklaracji zgodności z target venue. **Blokery:** optymistyczne fills, brak modelu ochrony, brak source availability, niekalibrowalne koszty.

## P09 — Baseline A/B i jednoznaczne decyzje

**Cel:** zaimplementować dokładnie R0, bez dodawania „pomocnych” filtrów podczas kodowania.

**Pliki:** `src/trading_bot/strategy/{features,breakout,retest,ranking,management}.py`; `configs/research-r0.toml`; `tests/unit/test_r0_features.py`, `test_breakout_a.py`, `test_retest_b.py`, `test_ranking.py`; `tests/replay/test_r0_management.py`.

**Interfejsy:** `features(history, as_of) → FeatureSnapshot`; `detect_breakout(features, eligibility) → Setup lub brak`; `advance_retest(setup, candle, observations) → Setup/status`; `rank(candidates, cutoff) → ordered setups`; `manage(state: StrategyState, event) → list[CommandProposal]`. Proposals są wejściem risk albo exit coordinator, nigdy adaptera.

**Małe zadania:**

- [ ] P09.1: features test: W to poprzednie 12 świec bez sygnałowej, A to mean TR poprzednich 72, volume median poprzednich 20; zerowy mianownik lub brak danych blokuje setup.
- [ ] P09.2: A tests na dokładnych granicach compression 3,0, activity 1,5 i close > H+0,1A; limit BUY zgodny z §6.2. Nie ma future high/low ani zmiany range po sygnale.
- [ ] P09.3: B tests na pierwszym kwalifikującym retescie, oknie 6 świec, low band, zamknięciu poniżej H−0,25A i S invalidation; brak retestu/no-fill pozostaje wynikiem, nie znika z porównania.
- [ ] P09.4: ranking i batch cutoff 2 s: różna kolejność nadejścia kandydatów przed cutoff daje ten sam ranking; spóźniony kandydat nie korzysta z drugiej próby. Aktywność B pozostaje zamrożona na oryginalnym wybiciu.
- [ ] P09.5: stałe S, soft exit i trwały time exit; `test_24th_boundary_exit_ignores_missing_candles`, `test_stop_event_precedes_soft_exit`, `test_restart_preserves_setup_expiry_and_exit_due`. Brak ADD/trailing/FOMO/ML paths potwierdzony testem.
- [ ] P09.6: `pytest tests/unit/test_r0_features.py tests/unit/test_breakout_a.py tests/unit/test_retest_b.py tests/unit/test_ranking.py tests/replay/test_r0_management.py -q`; ręcznie prześledzić wspólne setup IDs A/B i commit.

**DoD:** A/B różnią się wyłącznie zdefiniowanym wejściem; te same cash/risk/cost/data rules. Strategia pozostaje identyczna w replay i paper.

**Zależności:** P04, P07–P08. **Blokery:** niezgodność ze specyfikacją, zależność od kolejności poza kontraktem lub niejawny filtr poprawiający wynik.

## P10 — Protokół badawczy, holdout i falsification

**Cel:** uzyskać odtwarzalny verdict badań, także gdy przewagi nie uda się potwierdzić.

**Pliki:** `src/trading_bot/research/{manifests,splits,metrics,uncertainty,stress,benchmarks,gates,holdout}.py`; `tests/unit/test_splits.py`, `test_metrics.py`, `test_research_gates.py`; `tests/replay/test_walk_forward_state.py`; `tests/integration/test_holdout_access.py`; `artifacts/experiments/` jako przyszły katalog wyników poza zwykłym commitem danych.

**Interfejsy:** `build_folds(manifest) → chronological folds`; `evaluate(result: SimulationResult, manifest) → ExperimentReport`; `evaluate_research_gates(report) → GateReport`; `open_holdout(frozen_manifest, seal) → dataset scope`; każdy odczyt holdoutu jest rejestrowany. Nie obiecywać technicznej izolacji przed człowiekiem mającym raw dataset, ale wykazać brak odczytu przez pipeline przed zamrożeniem.

**Małe zadania:**

- [ ] P10.1: chronologiczne wspólne granice panelu, train/calibration provenance i purge faktycznych label intervals. `test_overlapping_labels_removed`, `test_fit_cannot_read_outer_test`, `test_preprocessing_receives_train_only`.
- [ ] P10.2: zdefiniować metrykę expectancy na trade/episode, liczbę executions osobno, niezakończone inventory oraz B0/B1 ograniczenia. Testy ręcznie policzonych PF/expectancy/NAV/DD i brak korzystnego pominięcia otwartej straty.
- [ ] P10.3: ciągły outer OOS bez resetu DD na foldzie; final holdout osobno flat NAV/HWM=1 zgodnie ze spec. `test_hard_pause_does_not_truncate_equity_report` wycenia dalszy overshoot i wyjścia.
- [ ] P10.4: 12 konfiguracji maksymalnie w zadeklarowanej rodzinie; wszystkie próby w rejestrze. Jeden frozen winner przed holdoutem, brak podmiany A↔B po wyniku. Block bootstrap całych panelowych bloków i sensitivity 1/3/7 dni; sanity check danych zależnych nie daje sztucznie wąskiego CI z liczby filli.
- [ ] P10.5: stress 1,5×fee/2×slippage łącznie, liquidity/latency/missed fills i gap/outage/depeg; oddzielić utratę edge od residual catastrophes. Benchmarks i ablations zgodnie ze spec, nie stroić progów drawdownu.
- [ ] P10.6: po testach `pytest tests/unit/test_splits.py tests/unit/test_metrics.py tests/unit/test_research_gates.py tests/replay/test_walk_forward_state.py tests/integration/test_holdout_access.py -q` uruchomić zamrożone eksperymenty, przygotować raport z uncertainty i decyzją PASS/FAIL/INCONCLUSIVE.

**DoD:** kompletny evidence package, nawet przy negatywnym wyniku. PASS implementacji pipeline nie oznacza PASS strategii. Brak 24 miesięcy/odpowiedniego OOS lub szeroki CI jest jawny i nie znika przez zmniejszenie wymagań.

**Zależności:** P08–P09. **Blokery:** leakage, tuning na holdoucie, niekalibrowane koszty, DD≥12% w podstawowej ocenie lub niepotwierdzona expectancy blokują live; paper do celów diagnostycznych może nadal działać bez zmiany verdictu badań.

## P11 — Paper/shadow i obserwacja 24/7

**Cel:** pełna ścieżka decyzyjna na realnie napływających danych, z kapitałem symulowanym i bez dostępu do produkcyjnych mutacji.

**Pliki:** `src/trading_bot/runtime/{composition,paper,shadow}.py`; `src/trading_bot/cli.py`; `src/trading_bot/observers/model_shadow.py` (tylko port zapisujący dostępne predykcje, bez trenowania); `tests/integration/test_mode_isolation.py`, `test_paper_pipeline.py`; `tests/replay/test_live_capture_parity.py`; `docs/runbooks/paper-operations.md`.

**Interfejsy:** `compose(mode, profiles, ports) → runtime`; paper: public MarketDataPort + simulated ExchangePort + własny ledger; shadow: te same proposals/approvals w odrębnym wirtualnym koncie, bez submit portu. A/B mają osobne run_id/portfolio; nie konkurują o symulowany kapitał drugiego wariantu.

**Małe zadania:**

- [ ] P11.1: `test_paper_cannot_resolve_production_trade_credentials`, `test_shadow_has_no_submit_transport`, `test_observer_cannot_emit_execution_command`; izolacja jest konstrukcyjna i testowalna, nie tylko flagą UI.
- [ ] P11.2: identyczny zapis live capture przetworzony offline i w paper daje te same cechy/setupy/approvals przy tym samym clock profile. Inne simulated fills wymagają jawnego model/seed difference, nie ukrytej logiki.
- [ ] P11.3: wymusić wszystkie pre-paper E/R/D/B/S/O cases ze spec, zanim run będzie liczony jako kwalifikowany paper. Alert sink produkcyjny tylko po jawnej konfiguracji/zgodzie odbiorcy; testy używają fake sink i nie wysyłają Telegram.
- [ ] P11.4: uruchomić długotrwały paper; dashboard zastępują lokalny raport health i audyt. Mierzyć feed/protection/ACK model latencies, queue backlog, market coverage, rejected opportunities, reconciliation i restart recovery.
- [ ] P11.5: co najmniej 21 dni i 50 symulowanych executions, ale niezależnie liczyć trade/episode coverage i wymagane scenariusze. Brak próby kontynuuje obserwację zamiast sztucznego generowania sygnałów.
- [ ] P11.6: `pytest tests/integration/test_mode_isolation.py tests/integration/test_paper_pipeline.py tests/replay/test_live_capture_parity.py -q`; raport stabilności, porównanie z validated distributions i review przed G11.

**DoD:** ciągły audyt, brak krytycznych niewyjaśnionych incydentów i zgodna logika replay/paper. News/listings/model output, jeśli dostępne, zapisują się w shadow i nie zmieniają live-equivalent orders.

**Zależności:** P05–P09, pre-paper tests; P10 może trwać równolegle czasowo. **Blokery:** runtime drift, stale data używane do wejść, utrata recovery, brak wymaganej obserwacji. Calendar days alone nie zaliczają etapu.

## P12 — Kwalifikacja execution, fault injection i go/no-go

**Cel:** zamknąć dowody wymagane do decyzji o małym canary; nie wykonywać jeszcze realnego handlu.

**Pliki:** `tests/faults/test_system_scenarios.py`, `test_split_brain.py`, `test_cancel_then_storage_loss.py`, `test_backup_restore.py`; `tests/integration/test_activation_gates.py`; `src/trading_bot/operations/activation.py`; `docs/runbooks/{incident-response,canary-checklist,live-stop}.md`; `docs/capabilities/live-readiness.md`; `deployment/Dockerfile`, `deployment/compose.yaml` (lokalnie/testowo; bez automatycznego provisioningu VPS).

**Interfejsy:** `evaluate_activation(profiles, capability_evidence, research_report, paper_report, risk_acceptance) → GateReport`. Raport ma exact hashes, environments, expiration i scope. Żaden pojedynczy flag `live=true` nie zastępuje kompletu evidence oraz zatwierdzenia człowieka.

**Małe zadania:**

- [ ] P12.1: uruchomić pełne sekwencje adversarial, nie tylko pojedyncze awarie: submit timeout + restart + late fill; cancel ACK + fill + stale balance; private WS down + REST 429; stop cancel + DB failure; process split brain; clock skew + pending deadlines.
- [ ] P12.2: test po każdej sekwencji: ledger conservation, brak nieautoryzowanych submits/oversell, prawidłowy UNKNOWN/latch/coverage oraz reasoned recovery. Wada P0 jest blockerem niezależnie od PnL.
- [ ] P12.3: backup/restore, disk-full, stale evidence/metadata i bezpieczny shutdown. Sekrety nie są w repo/logach/fixtures; role read-only i UAT są oddzielne. Zweryfikować deployment manifest przed ewentualnym zewnętrznym uruchomieniem.
- [ ] P12.4: przejść V01–V11: dla używanych ścieżek pozytywne dowody odpowiednie dla UAT/production read-only; disabled paths mają test nieosiągalności. Brak wymaganych danych/capabilities blokuje canary zamiast przenoszenia niebezpiecznego eksperymentu na realne pieniądze.
- [ ] P12.5: raport G10 + G11 + fault/evidence + residual risk. Osobno: stop gap, brak giełdy, cancel→sell gap, Q depeg i strata większa niż 12%. Profile operacyjne i risk zostają zamrożone z wersją; nie dopasowywać ich dla ratowania wyników.
- [ ] P12.6: `pytest tests/faults tests/integration/test_activation_gates.py -q`; kompletna lokalna suite offline oraz dedykowana UAT suite z pełnym evidence. Przedstawić użytkownikowi go/no-go i konkretny scope canary do osobnego zatwierdzenia.

**DoD:** jednoznaczny G12 PASS albo blocker list; dowody aktualne i odtwarzalne. PASS oznacza gotowość do decyzji, nie samoczynne włączenie API trade.

**Zależności:** P06, P10–P11; wszystkie wcześniejsze bramki bezpieczeństwa. **Blokery:** dowolny nierozstrzygnięty wymagany V, krytyczny test FAIL, brak edge gate, niezaakceptowany residual risk lub niezdolność małego kapitału do spełnienia minimum/protection.

## P13 — Nadzorowany live canary i dopiero potem autonomia

**Cel:** sprawdzić zgodność realnego execution z zatwierdzonym profilem przy wydzielonej małej alokacji.

**Pliki:** `src/trading_bot/runtime/live.py`; `configs/live-canary.example.toml` bez sekretów; `tests/integration/test_canary_limits.py`; `docs/runbooks/canary-observations.md`; evidence w `artifacts/canary/`.

**Interfejsy:** live composition używa tego samego risk/ledger/state machine co paper. Activation token jest jawnie powiązany z profile hash, kontem, limitem kapitału i dopuszczonym trybem; nowy hash unieważnia aktywację. Rzeczywiste klucze dostarczane bezpiecznie poza repo/czatem.

**Kroki wyłącznie po G12 i osobnej zgodzie użytkownika:**

- [ ] P13.1: dodać live composition i failing test `test_canary_cannot_exceed_authorized_allocation` na simulatorze. Przed realnym submit powtórzyć mode-isolation, activation, execution/risk/recovery regression oraz dotknięte UAT contract tests dla nowego build hash, odświeżyć G12 i wykonać read-only reconciliation; istniejące orders lub obce holdings blokują start. Kod runtime dodany w tym etapie nie dziedziczy automatycznie evidence starszego builda.
- [ ] P13.2: nadzorowane minimalne legalne zlecenia w zatwierdzonym zakresie; sprawdzić FOK/result, fee, coverage, exit i recovery evidence bez celowego wywoływania outage przy otwartej pozycji. 100 PLN jest orientacją, nie prawem do naruszenia minima.
- [ ] P13.3: porównać rzeczywiste zdarzenia z kontraktem; niespodzianka zatrzymuje kolejne wejścia. Nie korygować założeń paper po fakcie, aby ukryć rozbieżność.
- [ ] P13.4: po pozytywnym review production evidence osobno dopuścić autonomię w tej samej alokacji. Monitorować DD/latch i residual exposure; brak automatycznego dodawania kapitału.
- [ ] P13.5: scaling wyłącznie po odrębnym review obserwacji, kosztów i capacity. Zysk z kilku transakcji nie jest bramką skalowania.

**Testy/DoD:** `pytest tests/integration/test_canary_limits.py -q` przechodzi przed jakimkolwiek live; rzeczywiste observations dowodzą tylko sprawdzonych production capabilities. G13 nie jest dowodem statystycznej przewagi większego kapitału.

**Zależności:** G12 + osobne zatwierdzenie zakresu realnych działań. **Blokery:** brak zgody, production mismatch, brak ochrony/rozstrzygnięcia order, kapitał niespełniający ograniczeń, DD latch.

## P14 — Kolejne eksperymenty, poza implementacją live v1

**Cel:** rozwijać system tylko o mechanizmy z mierzalnym wkładem w wynik netto.

**Pliki:** przyszłe osobne dokumenty `docs/experiments/ml-advisor.md`, `news-context.md`, `new-listings.md`, `layered-entry.md`, `state-management.md`, `fomo-filter.md`; moduły kodu dopiero po zatwierdzeniu konkretnego eksperymentu.

**Interfejsy:** observer output z provenance trafia do audytu; dopiero zatwierdzony advisor może wpływać na Strategy Proposal. Nigdy nie dostaje ExchangePort, klucza ani prawa do edycji RiskProfile.

**Kroki:**

- [ ] P14.1: wybrać jedną hipotezę i własny protokół bez ponownego użycia zużytego holdoutu jako nietkniętego testu.
- [ ] P14.2: ML: jeden target zgodny z rzeczywistą polityką wykonania/wyjścia, legalne features, purge/calibration i porównanie identycznej strategii z modelem/bez.
- [ ] P14.3: news/listings: wykazać first-seen PIT, mapping i coverage od pierwszych transakcji; osobne execution/liquidity stress przed jakąkolwiek zgodą live.
- [ ] P14.4: probe/layered/State Engine/FOMO: osobno sprawdzić wartość po kosztach i lost opportunities; przed ADD/partial exit ponownie przejrzeć protection/re-entry/aggregate risk.
- [ ] P14.5: dopiero pozytywne wyniki i osobny plan pozwalają rozszerzyć runtime; bez automatycznej promocji z shadow.

**Testy/DoD:** każdy eksperyment ma kontrolę ablation, niezmienione safety invariants i ocenę uncertainty; negatywny wynik kończy eksperyment bez zmiany live baseline.

**Zależności:** wiarygodny baseline i dane; rozpoczęcie eksperymentu wymaga osobnego planu, nie musi czekać na live, jeśli odbywa się całkowicie offline. **Blokery:** brak danych PIT, brak przyrostowej przewagi, rozszerzenie nieprzetestowanych ścieżek execution.

## 3. Pokrycie specyfikacji i testów

| Specyfikacja / test ID | Zadania dowodzące wymagania |
|---|---|
| §0, §8; scope, no-debt, V, Q | P00, P01, P06, P12 |
| §1; E01–E05 | P02, P03, P05, P06, P12 |
| §2; O01 | P05, P06, P11, P12 |
| §3; R01–R03 | P02, P04, P05, P10 |
| §4; D01–D03 | P01, P07, P10 |
| §5; B01–B02 | P08, P10, P12 |
| §6; S01–S02 | P07, P09, P10 |
| §7; paper/live/O02 | P11, P12, P13 |
| §9–10; kolejność i self-review corrections | P00–P13, finalny odbiór planu |
| ML/news/listings/probe jako późniejsze eksperymenty | P07/P11 archive, P14 osobne protokoły |

### Evidence i blokery — zasady wspólne

- Wynik testu zapisuje revision/config/dataset hash, środowisko, datę i zanonimizowane dowody. Raport blokera wskazuje właściciela etapu oraz dozwolone niezależne dalsze prace.
- UNKNOWN nie staje się PASS przez upływ czasu, działający mock lub brak błędu w logu.
- Testy syntetyczne dowodzą logiki; UAT dowodzi zachowania obserwowanego tam; public docs opisują kontrakt dostawcy; canary sprawdza produkcję. Żaden poziom nie podszywa się pod inny.
- Brak edge, niewystarczające dane i awaria safety są różnymi wynikami. Żaden nie uzasadnia zwiększenia ryzyka lub poluzowania 12% dla uzyskania zielonego raportu.
- Approval planu pozwala wykonać zadania implementacyjne w zatwierdzonym zakresie; nie jest zgodą na przyszłe transfery, finansowanie, realne zlecenia ani eksperymenty spoza P00–P12.

## 4. Self-review planu i punkt zatrzymania

Sprawdzono pokrycie sześciu kontraktów, zgodność nazw portów/rekordów, zależności etapów, przypisanie pięciu Review Focus do testów i rozdzielenie research od live readiness. Plik nie zawiera implementacji ani gotowych funkcji/test bodies; konkretne nazwy testów, asercje opisowe i komendy są instrukcjami przyszłej weryfikacji.

Nie wykonywano wymienionych komend pytest/Ruff, capability checks, UAT ani canary — program jeszcze nie istnieje. Wszystkie checkboxy pozostają niezaznaczone. Kolejny krok to review i zatwierdzenie planu przez użytkownika, w tym sposobu wykonania. Do tego momentu nie tworzyć plików źródłowych, repozytorium ani zależności.

Preferowany sposób przyszłego wykonania: małe zadania z niezależnym przeglądem accounting/execution/risk, ponieważ błąd w ich wspólnych interfejsach może ujawnić się dopiero po realnym fillu. Użytkownik może wybrać wykonanie przez głównego agenta lub tryb z podagentami; samo zapisanie tej rekomendacji nie uruchamia delegacji.
