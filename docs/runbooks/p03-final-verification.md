# P03.6 — final verification i review P03

## Zakres i verdict

Data: 2026-10-08. Gałąź: `codex/p03-execution-lifecycle`.
Review obejmuje cały P03.1–P03.5 od bazowego `7cc3577`, wszystkie zachowane
checkpointy `83ad40d`, `ed1a463`, `9f6ade9`, `167af5f` oraz naprawy P03.6.
Nie wykonano merge ani P04. Nie używano credentials, prywatnych endpointów,
UAT trading ani realnych transakcji. Testy nie kontaktują się z Crypto.com.

**G3 FAIL/BLOCKED — historyczny G3 PASS na `d33f716` został obalony. Live BLOCKED.**

Niezależny adversarial review odtworzył cztery P1 oraz P2 authentic copied
WriterGuard. Poniżej zachowano historyczne wyniki P03.6 jako zapis checkpointu,
a corrective pass opisano osobno. Naprawy nie stanowią nowego G3 PASS: wymagany
jest kolejny niezależny review. Nie wykonano merge ani P04.

## Corrective pass po niezależnym adversarial review d33f716

Zakres: P1.1–P1.4 oraz potwierdzony P2; bez P04, merge, nowych capability
Crypto.com lub zmiany specyfikacji. Wszystkie cztery P1 i authentic-copy P2
otrzymały behawioralne failing regressions przed poprawką (pierwszy przebieg:
9 FAIL; następnie osobne RED dla admission/release i oczekiwania na realny lock).
Dodatkowy RED wykazał, że terminal partial evidence musi przetrwać także
późniejsze uzupełnienie filli do pełnego targetu. Nie usunięto ani nie osłabiono
istniejących regresji. Zmiana starego API `now` na dostawcę `clock` jest jawna;
nie ma kompatybilnego fallbacku ze statycznym czasem.

| Finding | Naprawa i testy |
| --- | --- |
| P1.1 BUY po release | Claim oraz obie fazy pre-send sprawdzają aktywną, niezmienioną reservation zgodną z oryginalnym `_reserve` w P02 journal: account/intent/instrument/BUY/quantity/cash/fee commitments. Bieżące dostępne aktywa uwzględniają wszystkie pending rights i fee liabilities. Release przed claim i po claim daje zero submitów; nic nie rezerwuje środków ponownie. |
| P1.2 czas przed lockiem | Publiczny fake submit wymaga callable clock; aktualny czas jest pobierany po ownership/fence/account waits i ponownie przed callbackiem. Kontrola obejmuje durable submit window, approval i reservation expiry. Oddzielna sesja PostgreSQL blokuje ownership row; test potwierdza rzeczywiste oczekiwanie w `pg_stat_activity`, przesuwa SimulationClock o 6/61 s i zwalnia lock: zero submitów. |
| P1.3 terminal REJECTED/EXPIRED stop | Serial dopuszcza emergency SELL dopiero po terminalnym dowodzie zgodnym z actual trades i release starego SELL right. Native terminal CANCELED/REJECTED/EXPIRED używa wyłącznie jawnej, osobno zweryfikowanej absence/emergency capability w tym samym native profilu: kompletny pozytywny snapshot i ponowny dowód przed efektem. Brak dowodu nie tworzy SELL. Status nie jest zamieniany na NONE; nie ma native→serial fallbacku. |
| P1.4 terminal partial FOK | `002e_execution_incidents.sql` zapisuje append-only account-level incident w tej samej transakcji co order/ledger evidence. Prepare/claim/pre-send blokują nowe wejścia mimo coverage, późnych pełnych filli, zamknięcia pozycji i restartu repozytorium. Realne fille i ochrona pozostają. Exit jest nadal dostępny. Migracja zachowuje historyczne partial terminal evidence, bez wymyślonego receipt time; UPDATE/DELETE/TRUNCATE incidentów są zabronione. |
| P2 authentic copied WriterGuard | Błąd ownership/DB odcina współdzielony permit w FakeFenceAuthority i trwale fence'uje jego epoch. `copy.copy` nie odtwarza prawa po przywróceniu DB; pokryto błędy zarówno `check`, jak i `send`. Nieautentyczny guard bez permitu nie odcina autentycznego właściciela. Fail-stop nie wystawia operator cutoff receipt ani prawa do automatycznego takeover. |

Self-review sprawdził oba punkty admission, trwałość UNKNOWN, P02 journal jako
źródło commitments, transaction rollback boundary, status terminalny vs actual
trades, brak fallbacku, scope incidentu i permit identity/epoch. Wykryty w
końcowej regresji mismatch komunikatu błędu został poprawiony w kodzie:
nowy incident nadal raportuje `unresolved`; istniejącego testu nie zmieniono.

`002e` należy zastosować po `002d`, przed uruchomieniem nowego repository.
Migration upgrade test odtwarza starszy schemat i seeduje historyczny partial,
także gdy późniejsze fille uzupełniły target. Brak migracji nie jest tolerowany
przez cichy fallback. Nie dodano automatycznego clearance: jawna późniejsza
reconciliation/recovery policy musi zostać osobno zaprojektowana i zweryfikowana;
coverage, terminal status ani administrator SQL nie stanowią produktu clearance.

Aktualny status E01–E05: poprawki i regresje opisane powyżej są kandydatem do
ponownego niezależnego zaliczenia. E01 obejmuje nowe reservation/time/incident
checks, E02 zachowane realne skutki P02, E03/E04 nowe terminal emergency paths,
E05 shared permit fail-stop. **Żaden z tych statusów nie przywraca G3 PASS.**

Dowody corrective (finalny kod):

- Dedykowane regresje: **16 PASS**; real PostgreSQL.
- Wymagany target P03.6: **60 PASS**.
- Pełny suite P01–P03: **299 PASS**, 172,29 s, bez skipów: 150 unit,
  2 property, 122 real PostgreSQL integration, 25 fault tests.
- Ruff check: **PASS**; format: **54 files already formatted**;
  `git diff --check`: **PASS**.

Świadomie odroczone: clearance/recovery actor dla incidentu, runtime 24/7,
zewnętrzny production fencing i capability/UAT Crypto.com. Nie odnaleziono
sprzeczności specyfikacji wymagającej zmiany decyzji użytkownika w tym corrective
pass. Wynik testów fake/offline nie dowodzi semantyki realnej giełdy.

**G3 FAIL/BLOCKED: wymagany kolejny niezależny review corrective commita.
Live BLOCKED, V01–V11 UNKNOWN. Zatrzymanie bez merge i P04.**

## Historyczny review i naprawy P03.6

Niezależny reviewer przeczytał skumulowany diff P03 względem P02, design §1
oraz E01–E05, sprawdził publiczne ścieżki i P02 ledger/reservations. Przed
naprawami wydał **G3 FAIL**: cztery P1, bez P0. Odtworzył problemy na realnym
PostgreSQL i wykonał 28 testów unit. Dodatkowa weryfikacja review potwierdziła
P1 dotyczące dopuszczenia SELL przez entry API. Self-review rozszerzył kontrolę
na brakujące trwałe okno submit, residual po partial exit, admission i migrację.
Nie zmieniono wymagań designu, polityk wyjścia ani limitów ryzyka.

| Problem | Naprawa i dowód regresyjny |
| --- | --- |
| Owned `FakeExchange.submit` omijał trwały claim i mógł wysłać ten sam intent ponownie | Publiczny submit wymaga rzeczywistego `ExecutionRepository`, zgodnego niezmiennego BUY intentu i jednorazowego `DISPATCHING`. Przed efektem commit `SUBMISSION_UNKNOWN`, następnie recheck pod account lock. Testy raw submit, counterfeit repository i ponownego submitu z poprawnym repository. |
| Publiczne entry fill / normalny dispatcher omijały skonfigurowaną ochronę | Order evidence, P02 fill/fee, assessment, deadline, proposals, SELL reservation i audit są w jednej transakcji. Regresja obu ścieżek: public event i dispatcher. |
| Fresh ACTIVE po lokalnym cancel cofał `CANCEL_PENDING`, przywracał coverage i powodował duplicate cancel | ACTIVE pozostaje dowodem istnienia zlecenia, lecz nie odwołuje lokalnego cancel/exit. Regresja: zero coverage, ten sam cancel, brak drugiego enqueue. |
| Redelivery / fee enrichment znanego fillu starego stopa po replacement były odrzucane | Scoped, znany trade starej trwałej komendy wraca do deduplikacji P02; korekta fee nie nadpisuje nowego stop ID/qty ani deadline. Nowy, wcześniej nieznany trade wycofanego stopa nadal wymaga divergence reconciliation. |
| SELL mógł przejść przez entry FOK poza exit policy | Entry API jest BUY-only. SELL idzie wyłącznie przez protection/exit coordination i własne trwałe rights. Regresja odrzuca SELL przed mutation ledger/outbox. |
| Submit nie posiadał trwałego unresolved deadline | Additive `002d_submit_windows.sql`: immutable first-attempt / first-proof receipt time + 5 s, bez resetu przez ACK/fill/restart. `assess_unresolved` wiąże deadline z monotonic clock i latchuje UNKNOWN po terminie; nie ponawia polecenia i nie zwalnia reservation. |
| Legalny residual po częściowym exit zostawał bez ścieżki re-protection | Po ekonomicznie terminalnym SELL pozostały target otrzymuje nową wyłączną ochronę z zachowanymi lot deadlines. Jeśli termin/minimum blokuje stop, pozostaje jawny emergency/blocker; bez dokupowania lub zmiany polityki. Nie włączono partial take-profit strategy. |
| Nowe wejścia mogły powstać przy missing trades albo uncovered inventory | Prepare, claim i admission bezpośrednio przed send sprawdzają unresolved evidence oraz aktualny target/coverage skonfigurowanych instrumentów pod account lock. Terminal cumulative nie zastępuje rzeczywistych filli. Regresje także dla zmiany inventory między claim a send oraz możliwości wejścia po zakończonym pełnym exit. |
| Owned send działał bez skonfigurowanego protection lifecycle | Przed send wymagany jest trwały context zgodny z lifecycle audit. Starsze sending fixtures jawnie konfigurują fake protection; produkcyjny kod niczego nie konfiguruje automatycznie. |
| Nowa migracja mogła nadać starym próbom fikcyjny czas, a send dopuszczał wygasłe okno | Próba bez pierwotnego okna poza PREPARED wymaga manual review; ACK nie może go stworzyć. Send odrzuca brakujące lub wygasłe okno. Trzy osobne failing regresje. |

Przed implementacją napraw odnotowano behawioralne RED: początkowe 8 failure,
2 dla deadline/residual, 2 dla missing-trades/uncovered admission, po jednym
dla counterfeit admission, pre-send race, full-exit liveness i unconfigured
protection, a następnie 3 dla legacy/expiry. Błędy fixture i składni SQL
poprawiono przed oceną właściwego RED. Żaden test nie został usunięty/skipowany.

Dwa wcześniejsze testy wymagały zmiany oczekiwań po usunięciu luk:
competing reservation jest teraz odrzucana przez P02 już po atomowym fill +
protection reservation; partial exit oczekuje ponownej ochrony zamiast
permanentnego `BLOCKED_RESIDUAL_AFTER_EXIT`. Oryginalna poprawiona parametryzacja
CANCELED/ACTIVE nadal sprawdza oba przypadki replay create.

## Historyczna kwalifikacja E01–E05 na d33f716 (cofnięta)

Poniższa tabela dokumentuje historyczne twierdzenia, które nie są aktualnym
zaliczeniem invariantów. P1.1/P1.2/P1.4 podważyły admission/E01, P1.3
podważył E03/E04, a P2 podważył E05. Kwalifikacja dotyczyła fake exchange,
syntetycznych capability artifacts oraz rzeczywistego lokalnego PostgreSQL. Nie stanowi dowodu semantyki giełdy.

| Invariant | Status po review | Zakres dowodu |
| --- | --- | --- |
| E01 — crash / UNKNOWN / no blind retry | PASS (fake/offline) | PREPARED, DISPATCHING, send, ACK, fill-before-ACK, restart; durable intent przed efektem, UNKNOWN przed fake-send, one-shot admission, rezerwacje nie znikają. First-attempt deadlines nie zmieniają się przy ACK/restart; stara próba bez deadline jest blokowana. |
| E02 — single economic effects / monotone evidence | PASS (fake/offline) | Deduplikacja trade identity między kanałami, late fee delta, stare statusy i cancel-fill race, brak inferred fills. Historyczna redelivery stopa nie zmienia nowej ochrony. Monotoniczność dotyczy skutków i praw; celowe cancel/replacement to nowe audytowane fazy, a nie cofanie historii. |
| E03 — legal protection, partial/base fee/dust | PASS (fake/offline) | PENDING/UNKNOWN nie dają coverage; public fill aktualizuje protection atomowo, qty jest net of base fee/commitments; minimum pozostaje blockerem. Po partial exit legalny residual jest reprotected; dust nie oznacza fikcyjnego CLOSED. |
| E04 — exclusive SELL / replacement i exit races | PASS (fake/offline) | Native i serial są osobnymi zweryfikowanymi profilami fake, bez fallbacku; cancel ACK nie daje prawa SELL. Recheck actual trades i target, reservations pod account lock, ACTIVE nie anuluje cancel. Brak drugiego prawa przy UNKNOWN/replacement i brak oversell w testowanych races. |
| E05 — ownership/fencing / fail-stop | PASS (fake/offline) | Dwa control/dispatcher instances i osobne sesje PostgreSQL; jeden account-wide grant, rzeczywisty WriterGuard + niezależny wspólny fake authority. Utrata DB/ownership oraz cutoff blokują kolejne efekty, stale/copied/permissive guard nie wysyła, takeover jest wyłącznie manualny. |

API tworzące intenty i obserwujące skutki nie mają praw transmisji. Publiczne
fake entry send oraz lifecycle `execute_fake` sprawdzają ownership w punkcie
efektu. Entry send wymaga durable admission i skonfigurowanej ochrony; publiczne
entry observations dla skonfigurowanego instrumentu zawsze koordynują ochronę.
Publiczny entry API nie generuje SELL. Proposals i observation APIs nie wykonują
prywatnego RPC. Prywatne helpers i dostęp administratora do DB nie są granicą
bezpieczeństwa wobec złośliwego kodu w tym samym procesie.

Nie zmieniono implementacji P02. Jego accounting, dedup, late fees, inventory,
reservations, cash/base commitments, NAV/HWM, deposits, replay, rollback i
concurrent writers pozostają objęte pełną regresją, w tym testami property.

## Historyczne dowody checkpointu d33f716

Środowisko: Python 3.12, pytest 9.0.2, Hypothesis 6.151.9, psycopg 3.3.3,
Ruff 0.15.7, PostgreSQL 18.6 (Postgres.app). Lokalny izolowany cluster przez
Unix socket, bez TCP, danych konta i credentials. Każdy test integration ma
własny losowy schema i usuwa wyłącznie własny schema.

- Pierwszy wymagany target przed zmianami: **48 PASS**, 1,97 s.
- Baseline całego P01–P03.5: **262 PASS**, 137,53 s, bez skipów.
- Finalny wymagany target po wszystkich poprawkach: **60 PASS**, 3,36 s.
- Finalny pełny suite: **283 PASS**, 209,18 s, bez skipów: 150 unit, 2 property,
  106 real PostgreSQL integration, 25 fault tests.
- Ruff check: **PASS**; format: **53 files already formatted**; diff check: **PASS**.

```sh
P02_TEST_DSN='host=/sciezka/do/izolowanego/socket port=55432 dbname=postgres' .venv/bin/pytest tests/unit/test_order_reducer.py tests/unit/test_protection.py tests/unit/test_exit_coordinator.py tests/integration/test_intent_outbox.py tests/faults/test_dispatch_crashes.py -q
P02_TEST_DSN='host=/sciezka/do/izolowanego/socket port=55432 dbname=postgres' .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
```

## Ograniczenia i live blockers

- **V01–V11 pozostają UNKNOWN dla live.** Żaden synthetic artifact nie zalicza
  capability Crypto.com; FOK, native/serial exit, protection, identyfikacja,
  historie i fee nadal wymagają oficjalnych capability/UAT evidence gates P00.
- Fake transport oraz native atomic effects nie dowodzą rzeczywistej atomowości,
  fill orderingu, kompletności historii, rate limitów, reconnect ani latency.
  Parametr 5 s jest operacyjnym D; nie dostrojono go pod PnL i nie jest gwarancją.
- Fake fencing to współdzielony authority w procesie testowym. Testy dwóch
  instancji i realnych sesji DB nie są testem dwóch hostów, restartu broker
  service ani dowodem zewnętrznego odcięcia dostępu do Crypto.com. Do live
  potrzebny osobno zweryfikowany egress/process isolation i runbook z P03.5.
- Nie można cofnąć już dopuszczonego RPC. Cutoff kończy istniejące callbacks,
  po czym blokuje następne; dopiero ukończony cutoff pozwala manual takeover.
- UNKNOWN, brak kompletnej terminal/cumulative reconciliation, brak pozytywnego
  absence evidence i niehandlowalny residual pozostają blockerami, bez retry,
  minimum-floor buy, domyślnego market ani serial fallbacku dla native.
- Nowy, nieznany trade starego stopa po jego całkowitym rozliczeniu/retirement
  jest divergence, nie zwykłą redelivery. Pełny incident/reconciliation worker,
  runtime scheduling, recovery actor i exchange adapter należą do dalszych
  etapów. P03 dostarcza durable evidence/state oraz jawne offline assessment,
  nie autonomiczny runtime 24/7.
- `assess_unresolved` oraz protection clock wymagają wywołań runtime. Po pierwszym
  bind monotonic nie pozwala skokiem UTC przedłużyć timera; po restartcie nowy
  wrapper wiąże trwały UTC deadline. Przygotowanie runtime i pomiar rzeczywistych
  terminów nadal nie są wykonane. Migracja nie backfilluje czasu starych prób;
  takie rekordy wymagają manual review, nie ponownego submitu.
- Realny Risk Governor i atomic risk admission to P04. RiskApproval w P03 jest
  syntetycznym fixture. Badania, backtest, profitability, paper/live readiness
  i drawdown nie zostały zweryfikowane tym etapem. Execution profiles pozostają
  wyłączone; nie istnieje live assembly ani ładowanie credentials.

Runbook odcięcia i manual takeover: [P03.5](p03-5-writer-ownership.md).
Wcześniejsze raporty opisują historyczne partial checkpoints; ten raport
rejestruje także cofnięcie checkpointu d33f716 i wymagane ponowne review. **Po osobnym commicie zatrzymanie; bez merge i P04.**
