# P00 — Evidence policy i odbiór

Data assessmentu: 2026-10-07 (Europe/Warsaw). Zakres: P00.1–P00.5; dokumentacja oficjalna i anonimowe publiczne GET. Bez kodu bota, kluczy, danych konta i zleceń. Zgoda użytkownika na plan nie upoważnia obecnie do rozpoczęcia P01.

## Znaczenie dowodów

| Status capability | Znaczenie |
|---|---|
| PASS | Wszystkie wymagania danego zakresu potwierdzone odpowiednimi dowodami i testami. Nie rozszerzać na inne konto, środowisko, instrument lub wersję. |
| FAIL | Dowód sprzeczności z wymaganiem w konkretnym zakresie; nie sam brak dostępu. |
| UNKNOWN | Brak rozstrzygającego dowodu, konflikt źródeł lub niewykonany wymagany test. BLOCKED opisuje przyczynę/konsekwencję, nie jest piątym statusem V. |
| DISABLED_VERIFIED | Nieużywana ścieżka jest technicznie nieosiągalna, co wykazały testy. Sam zapis „nie używamy” nie wystarcza. |

Rozdzielamy DOC (deklaracja dostawcy), OBS-PUBLIC (wąska obserwacja anonimowego endpointu), TEST-OFFLINE, TEST-UAT, READONLY-ACCOUNT i CANARY. Dokumentacja enumu FOK nie jest testem atomowości. Publiczny UAT nie dowodzi dostępu do prywatnego UAT. Test UAT nie dowodzi identycznego zachowania produkcji. Brak implementacji uniemożliwia obecnie DISABLED_VERIFIED. Status zbiorczy V pozostaje UNKNOWN, gdy choć jeden wymagany składnik nie został potwierdzony.

Każdy przyszły rekord evidence musi zawierać V/test ID, requirement, środowisko, wersję profilu i adaptera, instrument i zakres konta (prywatnie), źródło, czas UTC zebrania i czas zdarzenia, wynik/oczekiwanie, hash artefaktu, ograniczenia i osobę/etap odbioru. Publiczna wersja zawiera wyłącznie zredagowany wynik. Nie publikujemy identyfikatorów kont, balances, order IDs, headers autoryzacji, credentials, `.env` ani zrzutów prywatnych odpowiedzi.

Zmiana API, uprawnień, konta, profilu Q, instrument rules, fee lub adaptera unieważnia dotknięty zakres. Dowody sprawdza się ponownie przed P06 i G12; metadata/fees/freshness dodatkowo przy admission zgodnie z przyszłym kontraktem. Brak daty ważności dostawcy nie oznacza bezterminowego PASS. Sprzeczne dokumenty zapisuje się jako konflikt, którego nie rozstrzyga korzystniejszy backtest.

## Faktycznie wykonane obserwacje

Anonimowe GET wykonano w oknie 2026-10-06 22:24:58–22:25:10 UTC (2026-10-07 czasu Warszawy), bez uwierzytelnienia. Odpowiedzi HTTP 200, JSON `code=0`. Wąski wynik: endpointy publiczne odpowiadały. Nie jest to PASS V01/V09 ani SLA.

| Endpoint | Obserwacja | SHA-256 surowej odpowiedzi |
|---|---|---|
| `https://api.crypto.com/exchange/v1/public/get-instruments` | 989 rekordów: 570 CCY_PAIR, 409 PERPETUAL_SWAP, 10 FUTURE | `f271fc0236caf43606f582ca3913d0a08f587885ee3ea0fe97d8695b61ae4161` |
| `https://uat-api.3ona.co/exchange/v1/public/get-instruments` | 1268 rekordów: 800 CCY_PAIR, 458 PERPETUAL_SWAP, 10 FUTURE | `3526611531af19460d879456f8f4a4a0b99d1217d9d1399aedf43d1547edc5da` |

Produkcja: liczby CCY_PAIR według quote: USD 415, USDT 123, BTC 14, EUR 7, CRO 4, PYUSD 5, ETH 2. Nie zastosowano filtra płynności, eligibility konta ani historycznej dostępności. To nie liczba rynków dopuszczonych do strategii. BTC_USD: `tradable=true`, `max_leverage=50`, oba margin flags true. Flag nie interpretujemy jako nieuniknionego długu; pokazują, że sam filtr CCY_PAIR nie dowodzi no-debt.

Surowe publiczne odpowiedzi zachowano tymczasowo poza repo. Hash identyfikuje tę obserwację; bez trwałego archiwum nie zapewnia późniejszej reprodukcji payloadu. Trwały raw capture należy do P01 po nowej zgodzie. Wyniki innych prób publicznych, jeśli wykonane, mają odrębny zakres w [data-coverage](data-coverage.md).

## Źródła i ograniczenia dostępu

- [API Help](https://help.crypto.com/en/articles/3511424-api), odczyt 2026-10-07: prywatny UAT wymaga zaproszenia dla kont instytucjonalnych. Typ konta i dostęp użytkownika są UNKNOWN. Publiczne endpointy tego nie rozstrzygają. Źródło podaje także ogólny limit 10 calls/URL/s; bardziej szczegółowe limity referencji wymagają rozstrzygnięcia w V09.
- [API reference](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest-common-api-reference): środowiska, limity i błędy; szczegółowe źródła per V w [macierzy](crypto-com-matrix.md).
- [USD Bundle](https://help.crypto.com/en/articles/6660134-usd-bundle): tożsamość Q i migracje symboli; konsekwencje w [quote-profile](quote-profile.md) i [data-coverage](data-coverage.md).

Nie kontaktowano się z supportem i nie wnioskowano o konto/UAT. Jeżeli UAT nie będzie dostępny, obecny kontrakt G6/G12 pozostaje zablokowany. Symulator ani rzeczywiste zlecenie „na próbę” nie zastępują tej bramki. Alternatywna ścieżka dowodowa wymaga osobnego review i decyzji użytkownika.

## Kryterium G0

G0 ocenia kompletność assessmentu i możliwość dalszej budowy offline, zgodnie z P00 w zatwierdzonym planie. Nie wymaga fikcyjnego PASS wszystkich V. G0 PASS jest dopuszczalne, jeżeli zakres niepewności jest jawny, przyszłe testy i skutki blokad są określone, a kontrakt offline nie wymaga ukrytej decyzji o rachunku lub giełdzie. G0 BLOCKED oznacza brak tego minimum lub nierozwiązaną sprzeczność kontraktu offline. Live pozostaje BLOCKED przy UNKNOWN wymaganej capability niezależnie od G0.

## Blokery i właściciele zamknięcia

| ID | Blocker | Zależna bramka / sposób zamknięcia |
|---|---|---|
| B01 | Konto, podmiot/jurysdykcja, dostęp do prywatnego UAT UNKNOWN | P06/G6: potwierdzić dostęp; bez UAT wymagane osobne review alternatywy. Nie blokuje czystych testów offline. |
| B02 | No-debt, cash, fee currency/tier i Q konta UNKNOWN | V01/V08/V11, live: produkcyjne read-only ustawienia i bezpieczne testy kontraktowe; żadnych testów rzeczywistego długu. |
| B03 | FOK/stop/cancel/IDs/history i recovery nieprzetestowane; konflikty dokumentacji | V02–V07/V09, G6/G12: TC02–TC09, prywatny UAT, rozstrzygnięcie zakresu endpointów i limitów. |
| B04 | Historyczne OHLCV/trades/L2/PIT universe/fees/reference bez zwalidowanego pokrycia i praw użycia | V10, G7/G8/G10: audyt źródeł i manifest per rynek/dzień; bez danych brak kwalifikacji live ani twierdzenia o edge. |
| B05 | Kandydat USD Bundle bez potwierdzonej wyceny roszczenia i działającego monitora | V11, G6/G12: dowód tożsamości i wyceny, źródła i TC11. Proxy USDC nie jest dowodem wartości Bundle. |
| B06 | Brak implementacji/testów izolacji nieużywanych ścieżek | DISABLED_VERIFIED obecnie niedostępny; przyszłe P01/P06/P12 po odrębnej zgodzie. |

## Self-review i niezależny review — 2026-10-07

Self-review objął wszystkie pięć artefaktów, zgodność P00.1–P00.5 i V01–V11 ze specyfikacją, cztery tabletop, znaczenie G0, no-debt, brak fallbacków, źródła i zakres publicznej publikacji. Poprawiono granicę audytu danych na ostatni zakończony dzień UTC przed snapshotem (bez użycia przyszłego dnia), rozdzielono intent terminalność od komunikatów fill oraz status planu od upoważnienia do P01. Dodatkowy odczyt drukowanej referencji REST potwierdził FOK; nie awansowano V02 do PASS. Dokumentowaną retencję historii konta 6 miesięcy oddzielono od nieudowodnionej retencji publicznych market trades.

Niezależny reviewer `p00_independent_review` (odrębny agent, nie autor artefaktów) sprawdził pięć dokumentów, specyfikację, plan i wybrane oficjalne źródła. Wynik: brak findingów P0/P1/P2 blokujących assessment; rekomendacja **G0 PASS wyłącznie dla offline**. Review nie obejmuje testu giełdy, rentowności ani pełnego archiwum danych. Jawne B01–B06 pozostają otwarte; nie są ukrywane przez pozytywny odbiór dokumentacji.

Weryfikacja dokumentów: komplet 5/5 plików, 11/11 V z UNKNOWN i konsekwencją, 11/11 TC, 4/4 tabletop; kontrola lokalnych odsyłaczy, whitespace oraz przegląd publikowanych plików pod kątem sekretów i danych konta. Brak kodu silnika i plików konfiguracji z sekretami. Nie uruchamiano testów nieistniejącego programu.

**Wynik P00: G0 PASS.** Assessment P00.1–P00.5 zakończony; runtime profile INCOMPLETE, live BLOCKED. Dalsza budowa offline jest technicznie możliwa po nowej zgodzie użytkownika. **P01 nie rozpoczęto.** [Test cases](test-cases.md) określają przyszłe dowody; żaden TC01–TC11 nie został tu uznany za wykonany test capability.
