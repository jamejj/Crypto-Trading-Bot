# P00 — Capability tests i tabletop

2026-10-07. Ten dokument jest protokołem testowym, nie kodem ani raportem pozytywnych testów giełdy. Status wszystkich przyszłych testów poniżej: **NOT RUN / BLOCKED** (brak implementacji, prywatnego UAT i zweryfikowanego konta). Testy offline nie dowodzą zachowania giełdy. UAT wymaga osobnego dostępu; nie prosimy o sekrety w czacie. W P00 wykonano wyłącznie dokumentacyjne przejście czterech scenariuszy oraz publiczne obserwacje wymienione w [evidence-policy](evidence-policy.md).

## Protokół i kryteria

Każdy test zapisuje profil, wejście, kontrolowaną kolejność zdarzeń, expected/observed, ledger i rezerwacje przed/po, raw events w prywatnym evidence store, opóźnienia i wynik. Brak obserwacji nie jest dowodem braku zlecenia. PASS tylko przy zgodności wszystkich asercji; rozbieżność FAIL, niedostępny wymagany dowód UNKNOWN/BLOCKED. Testy uszkodzeń i prób niedozwolonych operacji wyłącznie fake/UAT, nigdy produkcyjny rachunek.

| ID / V | Zakres i etap | Obowiązkowy dowód / asercje |
|---|---|---|
| TC01 / V01 | P01/P04/P06/P12, offline + UAT + read-only konta | Endpoint/type/payload allowlist; brak borrow/margin/withdraw; zakaz auto-financing; cash vs collateral. BUY ponad cash i SELL ponad owned odrzucane. Dwa równoległe approvals nie zużywają tych samych środków. Brak możliwości zadłużenia musi wynikać także z konfiguracji konta; nie testować długu na produkcji. |
| TC02 / V02 | P03/P06, UAT | FOK LIMIT pełne wykonanie z wieloma fillami oraz brak wystarczającej płynności, price bounds, timeout i fill-before-ACK. Końcowo full albo zero zgodnie z kontraktem, pośrednie wiadomości nie muszą być atomowe. Terminalny partial to FAIL profilu. Unsupported nie wysyła IOC/GTC/market. |
| TC03 / V03 | P03/P06/P12, UAT | Spot STOP_LOSS, źródło triggera, status aktywacji, minimum/rounding, opłaty w base, niewystarczające środki przy triggerze, gap i częściowy exit. Coverage tylko potwierdzone, wykonywalne i bez konkurencji; stop nie gwarantuje ceny. |
| TC04 / V04 | P03/P06/P12 | Attached partial/full parent, cancel parent, reject child. W v1 domyślnie nieużywane: dopiero test nieosiągalności profilu pozwala DISABLED_VERIFIED; dokumentacja nie wystarcza. |
| TC05 / V05 | P03/P05/P06 | Crash każdej granicy commit/send/ACK, client_oid lookup, duplicate ID i zakres/retencja, eventual visibility. Jedna intencja i skutek ekonomiczny; UNKNOWN nie powoduje retransmisji. Brak w pierwszym query nie daje NOT_ACCEPTED. |
| TC06 / V06 | P03/P05/P06/P12 | Cancel ACK vs terminal, fill podczas cancel, stop trigger podczas soft exit, timeout cancel i utrata DB po terminal cancel. Nigdy dwa niezależne prawa SELL. Serial cancel→market albo native-linked wybierane przed startem po dowodach, nie jako fallback. |
| TC07 / V07 | P05/P06 | Ordinary + advanced orders, fills, reconnect, pagination, out-of-order, duplikaty i granice retencji. Recovery kompletne dopiero po uzgodnieniu historii, sald i obu klas otwartych zleceń. Luka historii utrzymuje blokadę. |
| TC08 / V08 | P02/P06 | Rzeczywista semantyka cash/available/reserved/collateral, fee w base/quote/innej walucie, późna korekta fee, rounding, dust, wpłata/wypłata i unitized NAV. Brak fałszywego inventory/PNL lub resetu DD. |
| TC09 / V09 | P01/P06/P12 | Tick/lot/min-notional/status zmieniające się w czasie; throttling, 429, backoff, reconnect/heartbeat i clock skew. Rezerwa limitów dla ochrony/recovery. Zmierzone D freshness/deadline; sprzeczne limity wyjaśnione przed dopuszczeniem ścieżki. |
| TC10 / V10 | P07/P08/P10 | Coverage per asset/date/field, delisted i migracje tickerów, missing vs zero, revisions/available_at, L2 sequence gaps, trades dedup, trigger reference. Zmiana przyszłych danych nie zmienia dawnych decyzji. Bez pełnych danych wyłącznie jawny B0/INCONCLUSIVE, nie kwalifikacja live. |
| TC11 / V11 | P02/P04/P06/P12 | Tożsamość USD Bundle, konto/Q, źródła FX/depeg, staleness/disagreement, weekend i outage, wpłaty i NAV. Q gate niezależny od 6/9/12; brak kursu nie oznacza parytetu. PLN raport nie zmienia Q DD. Przejście na inną walutę tylko po review profilu. |

Testy przekrojowe: single writer i fencing, utrata trwałości, restart po deadline, pełne round-trip ledger, isolation UAT/live, stale book, stop minimum po fee, równoczesne ryzyka pozycji skorelowanych oraz brak auto-resume HARD PAUSE. Pełna lista pre-paper i pre-live pozostaje w §7 specyfikacji; P00 niczego z niej nie zalicza.

## P00.5 — wykonane przejście dokumentacyjne

Poniższe wyniki to **TABLETOP COMPLETE**, nie PASS API. Prześledzono stan oraz wymagany dowód według §1–2 designu. Liczby są syntetyczne i nie są saldami użytkownika. Każdy scenariusz ma otwarte testy powyżej.

### TT01 — BUY przyjęty, odpowiedź utracona

1. Przed wysłaniem trwała intencja, payload hash, approval, rezerwacja cash/risk i single-writer ownership. Dowód docelowy: commit ledger/intentu poprzedza send.
2. Giełda przyjmuje BUY, klient dostaje timeout: SUBMISSION_UNKNOWN. Rezerwacja pozostaje; ACK nie jest fill. Nowy client_oid ani identyczny resend nie są dozwolone.
3. Recovery odczytuje orders, fills i balances, także po reconnect. Jeżeli fill przyszedł pierwszy, księguje go raz i uruchamia ochronę faktycznego inventory bez czekania na ACK.
4. Brak wyniku w jednym query nie rozstrzyga. Wymagany pozytywny dowód terminalności/niewysłania lub pełne uzgodnienie; w przeciwnym razie blokada i incydent bez uwolnienia budżetu.

Wynik: kontrakt nie wymaga retry dla postępu. Residual risk: pozycja może już istnieć, zanim system zobaczy fill; niedostępna giełda może uniemożliwiać ochronę/wyjście. TC02/05/07 mają wykazać zachowanie, nie tylko ten zapis.

### TT02 — FOK full, komunikaty fill przychodzą osobno

1. Syntetyczny BUY 10 jednostek kończy się pełnym wykonaniem, lecz wiadomości 4 i 6 docierają osobno, druga przed ACK. Każdy trade_id księgowany raz; cumulative qty nie jest dodatkowym fillem.
2. Pierwsze potwierdzone inventory powoduje ocenę coverage i blokadę kolejnych wejść przy uncovered qty. Pending/UNKNOWN ochrony nie jest covered.
3. Fee w base zmniejsza sellable quantity; nieznana opłata wymaga jawnej rezerwy. Ilość stopa wynika z bezpiecznie ustalonego inventory netto po rounding, a nie ze zleconych 10.
4. Nowy fill aktualizuje target, lecz nie tworzy drugiego pełnego stopa podczas PENDING/UNKNOWN pierwszego. Deadline najstarszego uncovered lot nie resetuje się. Za mały lot pozostaje jawnym ryzykiem, bez dokupowania do minimum.
5. Dopiero terminalność parent i complete fills rozstrzygają FOK. Terminalny partial łamie wymaganie profilu i blokuje nowe wejścia, lecz nie usuwa istniejącego inventory.

Wynik: oddzielono terminalność order od transportu filli; TC02/03/08 muszą sprawdzić możliwość i czas ochrony. Jeżeli D-SLO niewykonalne, profil wymaga review, a nie ukrytego zwiększenia deadline pod PnL.

### TT03 — stop rejected

1. BUY fill jest faktem; odrzucenie stopa z powodu minimum, balance lub błędnego triggera nie odwraca BUY.
2. Rejection ustawia coverage na rzeczywiście potwierdzoną ilość (dla nowego stopa: zero), zapisuje uncovered qty/deadline i blokuje wejścia. Intent ochrony UNKNOWN nie jest traktowany jak reject ani automatycznie ponawiany.
3. Koordynator uzgadnia istniejące stopy i sellable inventory. Redukcja tylko przez wcześniej zweryfikowany profil wyjścia, z trwałą intencją, działającym writerem i brakiem konkurencyjnego prawa SELL.
4. Jeżeli wyjście poniżej minimum lub giełda niedostępna: trwały incydent/dust i blokada; nie CLOSED, nie dokupienie do minimum, nie wymyślony fill. Deadline jest celem reakcji, nie gwarancją zamknięcia.

Wynik: brak obowiązkowego, nieaudytowanego market fallbacku. TC03/06/08 obejmują residual loss większy od planowanego stop risk.

### TT04 — cancel stop, awaria przed SELL

1. Soft exit w profilu SERIAL_CANCEL_THEN_MARKET_VERIFIED: trwały exit request i cancel intent; oczekiwanie na potwierdzoną terminalność. Sam cancel ACK nie pozwala SELL.
2. Jeżeli stop w międzyczasie wykonał część ilości, ledger uwzględnia fill i zmniejsza remaining. Przy cancel UNKNOWN nie wysyła się konkurencyjnego market sell.
3. Po terminal cancel coverage spada; utrata DB lub ownership przed nowym trwałym SELL intent zatrzymuje mutacje. Nie ma emergency writera omijającego ledger. Inventory pozostaje niechronione, alert/deadline nie znikają.
4. Po recovery odtworzyć exit request, actual orders/fills/balances i ownership. Gdy wcześniejszy SELL mógł zostać wysłany, najpierw rozstrzygnąć jego stan; nie wysyłać drugiego. Upłynięty deadline nie zaczyna się od nowa.

Wynik: luka ochrony jest jawna i potencjalnie nieograniczona w czasie awarii. Akceptacja residual risk oraz TC06/07 są obowiązkowe przed live. Jeśli taki profil okaże się nieakceptowalny, live BLOCKED; native linked wymaga własnych dowodów, bez cichej zmiany.

## Warunki następnych testów

P00 nie weryfikuje rentowności, latencji, 24-miesięcznej kompletności danych ani kontraktu konkretnego konta. Zgoda na P01 pozwoli dopiero tworzyć offline kontrakty/testy. P06 wymaga dostępnego UAT i bezpiecznej konfiguracji poza publicznym Git. P12 wymaga pełnego pakietu dowodów; P13 osobnej zgody na nadzorowany live. Brak UAT nie zostaje zastąpiony próbą realnej transakcji.
