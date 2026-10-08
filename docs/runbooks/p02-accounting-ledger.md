# P02 — Accounting, ledger, inventory i reservations

## Zakres i baza

Użytkownik zaakceptował P01 i upoważnił wyłącznie P02.1–P02.5 po merge P01. P01 scalono zwykłym merge `255aa33` i wypchnięto do `main`. Baza gałęzi `codex/p02-accounting-ledger`: `255aa33`. P03 nie rozpoczęto. Prace i odbiór: 2026-10-07/08, metodą TDD z self-review i niezależnym review.

Implementacja obejmuje deterministyczny ledger double-entry, inventory/FIFO/dust, rezerwacje, wycenę NAV/HWM, append-only journal i transakcyjność PostgreSQL. Brak credentials, prywatnego API Crypto.com oraz transakcji giełdowych. Runtime pozostaje INCOMPLETE, V01–V11 UNKNOWN, live BLOCKED.

## Kontrakty i decyzje

| Powiązanie etapów | Rozstrzygnięcie |
|---|---|
| P02.1 → P02.2: fill/fee/rezerwacje | Jeden reducer; aktualizacja inventory, principal, pending risk i fee commitments jest atomowa. Deduplikacja używa `(account, instrument, trade_id)`, niezależnie od kanału. |
| P02.1/2 → P02.3: salda/lots/NAV | Aktywa liczone raz, zarezerwowany cash nie jest dodatkowym majątkiem. Fee księgowana w rzeczywistej walucie. |
| P02.1–3 → P02.4: replay | Niezmienny journal, postings i deterministyczny reducer odtwarzają identyczny snapshot i wpisy. |
| P02.4 → P02.5: integracja | Rzeczywisty PostgreSQL obowiązkowy; brak bazy jest błędem konfiguracji, nie zaliczonym skip. |
| P02.1–5: spójność zakresu | Nazwane testy i moduły odpowiadają spec; bez implementacji P03 lifecycle/writer i P04 governor. |

Ruling: zachowano checkout i osobną gałąź wskazaną przez użytkownika, jak w P01. Bez kopiowania zmian i dodatkowego worktree.

Ruling: rozszerzone `Fill`, `Reservation`, `InventoryLot` i `CostEstimate` mają wire schema 2; schema 1 pozostaje odczytywalna z bezpiecznymi defaults. Legacy Fill bez stabilnego trade ID nie kwalifikuje się do księgowania. Legacy cost basis nie kwalifikuje się do wyceny po bids. Nie zmieniono wersji wszystkich niezależnych rekordów P01.

Ruling: fee to skumulowany komponent per waluta i scoped fill; revision księguje wyłącznie delta. Zmiana waluty wymaga jawnego wyzerowania poprzedniego komponentu i nowego zapisu, bez domniemanej konwersji. Adapter P06 musi dostarczać taki znormalizowany kontrakt.

Ruling: external funding P02 wyłącznie w Q i z `FundingEvidence` potwierdzającym pauzę, reconciliation, konto oraz wersję ledgeru. Bez automatycznych transferów. Wsparcie funding innych walut wymaga osobnego kontraktu ownership/cost basis i testów; nie ogranicza to fee w posiadanej base/quote/trzeciej walucie.

Ruling: wycena używa wykonalnych bids i kosztu `REMAINING_AFTER_BIDS`. Brak ceny/głębokości zachowuje konserwatywną wycenę i reason codes, bez poprawiania HWM. Zewnętrzny funding nie resetuje NAV/HWM, także po całkowitej wypłacie kapitału.

## Znalezione i poprawione problemy

Wszystkie poniższe zachowania mają regresje RED → GREEN; część wykrył self-review, część niezależne wykonane kontrprzykłady:

1. SELL fee w base oraz fee w trzeciej walucie zmieniały saldo bez odpowiadającej zmiany inventory. Zmiany quantity i ownership są teraz spójne.
2. Nieznane fee pozostawało zajęte po rozliczeniu. Commitment jest scoped per fill/currency; terminalność orderu nie usuwa należnej opłaty, a finalne zero ją rozstrzyga.
3. Settlement/rewizja fee mogły ponownie zużywać bufor niewykonanej części orderu; większa fee mogła wydawać zarezerwowany principal. Konsumpcja dotyczy konkretnej części fillu, a po całej operacji sprawdzane są wszystkie ocalałe commitments.
4. Powtórzone waluty i zerowe fee buffers dawały niepoprawną granicę nieznanych opłat. Rezerwacja wymaga jednego dodatniego bufora per currency.
5. Late quote fee po partial exit obciążało w całości pozostały lot. Koszt rozdzielany jest pomiędzy część pozostałą i zbytą.
6. Rebate po pełnym wyjściu zwiększało saldo bez inventory. Powstaje jawny lot, bez ponownego fillu. Holding instrument i `origin_instrument` są rozdzielone; kolizja trade ID na dwóch rynkach nie nalicza quantity delta dwukrotnie.
7. FIFO mogło sprzedać lot potrzebny do pokrycia późnej base fee, mimo globalnego bufora. Chroniona ilość źródłowego lotu pozostaje do rozliczenia tego commitmentu.
8. Lot IDs były niejednoznaczne między rynkami. Używają pełnego scope. Embedded fee musi wskazywać dokładnie containing fill, nie inny znany trade.
9. Wycofanie wszystkich jednostek i ponowna wpłata resetowały NAV/HWM; tick wyceny pustego portfela mógł uniemożliwiać ponowną wpłatę. Historia epoki i ostatni poprawny unit NAV pozostają zachowane.
10. Finalne potwierdzenie zerowej fee mogło pozostawiać nieaktualny NAV użyteczny do funding. Settlement wymaga ponownej wyceny.
11. Terminal evidence o niewłaściwej jednostce cumulative quantity zwalniało rezerwację. Sprawdzane są konto/order/instrument/base i zgodność ze znanymi fillami; timeout/cancel ACK nie zwalniają.
12. Connection factory z autocommit mogło rozdzielać zapisy journal/postings/projection. Repozytorium zawsze otwiera jawną transakcję. Odczyty również porównują projekcję z audytem; rebuild jest jawny.
13. Proporcjonalne rozliczenie przez dzielenie przed mnożeniem traciło precyzję. Mnożenie jest exact, dzielenie używa jawnego deterministycznego kontekstu 80 cyfr.
14. Dodano kontrolę zgodności sald non-Q z ilością posiadaną w lotach przed publikacją każdej zmieniającej operacji; niespójność nie trafia do stanu.

Dodatkowo niezależny run Hypothesis wykazał FlakyFailure przy domyślnym deadline 200 ms (413,47 ms, replay 181,94 ms). To błąd założenia czasu testu, nie wykazana rozbieżność ledgeru. Oba bounded property tests mają `deadline=None`; pierwszy generuje cykle fill/fee/exit, drugi 40 deterministycznych przykładów z rezerwacjami, partial fills, known/unknown Q/base/token fee, korektami, release, funding, wyceną i dust. Replay, bilans per waluta, własność inventory oraz spendability są sprawdzane po kolejnych zdarzeniach.

## Testy i środowisko

Baseline P01: 76 PASS. Python 3.12.14, psycopg/binary 3.3.3, pytest 9.0.2, Hypothesis 6.151.9, Ruff 0.15.7; zależności utrwalone w `uv.lock`, locked offline sync PASS.

PostgreSQL 18.6 z istniejącego Postgres.app. Osobny cluster `/private/tmp/cryptobot-p02-pg/data`, socket `/private/tmp/cryptobot-p02-pg/socket`, port identyfikujący socket 55432, `listen_addresses=''`; brak TCP/hasła. Każdy test tworzy losowy schemat i usuwa tylko swój schemat. Ograniczenie sandboxu wymagało eskalowanego uruchomienia testów Unix socket; nie zaliczono odrzuconej próby jako PASS.

Siedem testów integracyjnych obejmuje concurrent duplicate fill, concurrent reservation stale-version/double-spend, rzeczywisty błąd serwera po journal/postings a przed projekcją (rollback), autocommit rollback, odczyt uszkodzonej projekcji, rebuild oraz append-only/per-currency balance enforcement. Brak mocks i skip.

TDD wykonawcy: początkowe 11 testów ledger RED, następnie 11 GREEN; brakujące NAV/dust i unknown commitments RED → GREEN; kolejne konkretne review regressions RED → GREEN. Ostateczny zakres unit ledger/NAV: 46 PASS. Poprzedni wymagany target run: 51 PASS w 126,02 s przed ostatnimi poprawkami; nie stanowi finalnego odbioru obecnego commitu.

Końcowy niezależny review: 53 PASS (46 unit + 7 real PostgreSQL), bez otwartych P0/P1/P2 blokujących P02. Rekomendacja G2 PASS uzależniona od końcowego pełnego zestawu koordynatora. Ruff check/format oraz diff check PASS.

Końcowa weryfikacja koordynatora 2026-10-08:

```sh
P02_TEST_DSN='host=/private/tmp/cryptobot-p02-pg/socket port=55432 dbname=postgres' .venv/bin/pytest -q
```

**131 PASS w 83,68 s**: 122 unit (76 regresji P01 + 46 P02), 2 property, 7 real PostgreSQL integration. Zakres P02: 55 PASS, bez skip. Ruff `check src tests`: All checks passed; `format --check src tests`: 31 files already formatted; `git diff --check`: exit 0. `uv sync --locked --offline`: PASS, 14 resolved packages. Raport nie zalicza wcześniejszych nieudanych prób jako dowodu PASS.

## Ograniczenia i następny etap

Pełny replay przy operacji/odczycie jest świadomym ograniczeniem P02; nie kwalifikuje opóźnień ani przepustowości runtime 24/7. Wydajność i checkpointy wymagają dalszych pomiarów przed paper/live. Dust klasyfikowane jest według jawnie przekazanego legalnego minimum; pełne venue rules pochodzą dopiero z P06/P07. CostEstimate dla NAV musi mieć odrębny basis od sizingu. Funding evidence w testach jest syntetyczne, nie potwierdza konta giełdy.

## Verdict

**G2 PASS — wyłącznie Accounting P02 offline.** P02.1–P02.5 zakończono; self-review i niezależny review wykonano, znalezione problemy poprawiono i sprawdzono regresjami. Brak otwartego blokera P02. Testowy proces PostgreSQL zostaje zatrzymany po odbiorze; odtworzenie testów wymaga ponownego uruchomienia własnego lokalnego clusteru i wskazania `P02_TEST_DSN`.

Ograniczenia P00/konta/UAT/danych obowiązują; V01–V11 UNKNOWN, runtime INCOMPLETE, live BLOCKED. P03 nie rozpoczęto i wymaga kolejnej zgody użytkownika. P02 pozostaje na osobnej gałęzi do review/merge; commit nie zawiera danych konta, credentiali, surowych capture ani lokalnych plików PostgreSQL.
