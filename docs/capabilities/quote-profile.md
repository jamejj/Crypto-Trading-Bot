# P00.4 — kandydat jednostki Q i polityka quote-risk

Data: **2026-10-07 (Europe/Warsaw)**. **V11 = UNKNOWN; runtime profile = INCOMPLETE; live = BLOCKED.** To propozycja przed analizą PnL, bez aktywacji, kodu monitora, konwersji, zasilenia konta czy transakcji. Reguły [§0.2 specyfikacji](../2026-10-06-crypto-trading-bot-technical-design-v1.1.md) pozostają obowiązujące: jeden Q dla accounting/DD, PLN dodatkowo, żadnej automatycznej zmiany Q lub sprzedaży przy alarmie.

## Wybór kandydata, nie profilu konta

**Rekomendowany kandydat: `Q = USD`, z tożsamością ekonomiczną `Crypto.com USD Bundle` (USD + USDC), nie założenie „czysty fiat USD” ani `Q = USDC`.** Uzasadnienie: obserwowany publiczny katalog ma 415 par quote USD wobec 7 EUR, co lepiej pasuje do zatwierdzonego szerokiego dynamicznego radaru. Liczba par nie dowodzi płynności, FOK, stop-market, 24m historii czy regionalnej dostępności. USD Bundle niesie dodatkowe ryzyko USDC, platformy i rozliczenia; brak jego akceptacji albo dowodu wartości blokuje kandydata, nie uruchamia fallbacku.

**DOCUMENTED:** [USD Bundle](https://help.crypto.com/en/articles/6660134-usd-bundle), aktualizacja 2026-04-29, opisuje skonsolidowane USD i USDC oznaczone w UI jako USD, handel `/USD`, usunięcie par `/USDC`, możliwość wypłaty USD lub USDC i prezentowanie dawnych transakcji `/USDC` jako USD. To ważna semantyka jednostki i historycznego mappingu, a nie dowód bieżącej struktury salda lub wykonalnego redemption. [API changelog](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest-change-log) również opisuje alias USD Bundle → USD. Bot musi zachować kanoniczną tożsamość z datą, nie wnioskować z samego tickera.

| Kandydat | DOCUMENTED / OBSERVED | Ocena i ograniczenie |
|---|---|---|
| EUR fiat | [Retail Exchange SEPA](https://help.crypto.com/en/articles/7890434-retail-users-eur-fiat-deposit-and-withdrawal-via-sepa-exchange) wymienia Polskę i proces KYC. OBSERVED: ADA/BTC/DOGE/ETH/PAXG/SOL/XRP_EUR, wszystkie 7 rekordów `CCY_PAIR`, `tradable=true` w publicznym snapshotcie | Prostsza tożsamość bez stablecoinowego parytetu, ale dziś bardzo wąski universe. Warunkowa alternatywa po jawnym review zmiany zakresu; nie cichy zamiennik szerokiego radaru. Konto/no-debt/fees/FX UNKNOWN |
| USD / USD Bundle | Źródło powyżej; [retail USD SWIFT Exchange](https://help.crypto.com/en/articles/8498843-retail-usd-fiat-deposit-and-withdrawal-via-swift-exchange) wymienia Polskę; OBSERVED 415 quote USD | Preferowany kandydat do dalszej weryfikacji szerokiego universe. Nie udowodniono, że saldo jest cash/no-debt, ani że USD fiatu/USDC są zawsze realizowalne przy nominalnym parytecie |
| USDC token jako odrębny Q | USD Bundle dokumentuje usunięcie `/USDC`; OBSERVED zero quote USDC | Nie wybieramy osobnego Q=USDC dla obecnego katalogu. USDC jest składnikiem ryzyka Bundle, ale to nie ten sam market symbol. Starszych danych nie wolno przemianować bez PIT mappingu |
| USDT token | OBSERVED 123 quote USDT i USDT_USD; brak ustalonego dowodu dostępności tych rynków dla konkretnego konta/jurysdykcji | Brak rekomendacji na start. Kwestie EEA/MiCA i ograniczenia par wymagają aktualnego dowodu od właściwego podmiotu Crypto.com; nie wywodzimy z publicznego katalogu ani legalnego dostępu, ani blanket zakazu. Profil runtime UNKNOWN |

Snapshot produkcyjny: 2026-10-06 około 22:24:58 UTC / 2026-10-07 lokalnie, hash `f271fc0236caf43606f582ca3913d0a08f587885ee3ea0fe97d8695b61ae4161`; szczegóły w [data-coverage](data-coverage.md) i [evidence-policy](evidence-policy.md). UAT ma inny katalog. `CCY_PAIR` i `tradable=true` nie dowodzą no-debt: publiczne rekordy zawierają także leverage/margin fields, a ustawienia konta nie są znane.

## Polska i brak danych konta

Polska nie występuje na odczytanej [liście spot restricted locations](https://help.crypto.com/en/articles/6320975-spot-trading-geo-restrictions), która może się zmienić. Listy SEPA/SWIFT dotyczą funkcji transferów, nie pełnych uprawnień API/rynku. Nie znamy rezydencji/KYC, podmiotu umowy, typu konta, regionu produktów, fee tier, finansowania ani konfiguracji zadłużenia. Lokalna strefa Europe/Warsaw nie potwierdza jurysdykcji konta. **Nie wydano konkluzji prawnej o dostępie użytkownika.** Main App, Exchange App i Exchange API to różne powierzchnie; dostępność App/portfela fiat nie potwierdza Exchange API spot.

Nie użyto credentials, prywatnego REST/WS ani UI konta. Publiczny host UAT odpowiadał, ale dostęp do właściwego konta i jego zaproszenia pozostaje UNKNOWN. Żaden odczyt dokumentacji czy publicznej listy nie jest testem UAT trading. Brak ustawień blokujących dług, aktualnej stawki/currency fee, dozwolonych par i potwierdzonego Bundle mappingu oznacza INCOMPLETE.

## Proponowany profil Q-risk D — do zamrożenia przed badaniami

Identity: jeden ledger Q oznaczony `USD_BUNDLE_CRYPTOCOM`, quote symbol API `USD`; waluta referencyjna **fiat USD**; PLN reporting. Wpłaty/wypłaty i historyczne `/USDC` zachowują oryginalną walutę oraz mapping/effective date. Ewentualna zmiana składu Bundle lub zasad redemption unieważnia V11 i blokuje wejścia do review. Przeczytana dokumentacja nie dostarcza obserwowanej wyceny samego roszczenia Bundle.

Źródła niezależne od nominalnego parytetu — **kandydaci, runtime UNKNOWN, nie aktywne fallbacki**:

- USDC/fiat USD: [Coinbase public product ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker), produkt `USDC-USD`, oraz niezależny [Kraken public PreTrade](https://docs.kraken.com/api/docs/rest-api/get-pre-trade), para `USDC/USD`. Dokumentują publiczne bid/ask; faktyczna dostępność par, timestamp quote versus timestamp ostatniej transakcji, status/suspend i ciągłość wymagają read-only kontraktowego pomiaru. Mid i executable bid należy zapisywać oddzielnie. Ticker `time` nie może bez testu uchodzić za czas aktualizacji bid/ask.
- Fiat USD/PLN: [NBP](https://api.nbp.pl/en.html), tabela A USD oraz EUR; niezależna kontrola przez [ECB](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html): PLN/EUR podzielone przez USD/EUR. Daily reference nie jest live FX ani wykonalną ceną przewalutowania. Wyłącznie dane opublikowane/odebrane przed decyzją; żadnej interpolacji przyszłymi kursami.
- Crypto.com bieżące metadata/status i informacje o Bundle są kontrolą tożsamości, nie niezależnym potwierdzeniem parytetu. `USDT_USD`, jeżeli kiedyś użyty diagnostycznie, mierzy USDT względem Bundle; nie dowodzi USDC/fiat USD ani bezpieczeństwa Bundle.

**Model wartości konserwatywnej D:** dla cash w Q raportowana referencyjna wartość na jednostkę `min(1, min(valid USDC/USD bid źródeł))`; upside USDC nie podwyższa wyceny cash Bundle. Traktuje całą nieznaną strukturę Bundle jak ekspozycję na jego słabszy składnik. Jest to jawny proxy/model ryzyka, **nie obserwowana cena roszczenia ani gwarancja realizacji**. Do PASS wymagany dodatkowo dowód mapowania wybranego konta i dozwolonej metody wyceny roszczenia; jeśli proxy nie można uzasadnić, profil pozostaje BLOCKED. Brak obu niezależnych bidów nie daje wartości 1.

| Parametr D | Konkretna propozycja | Reakcja |
|---|---|---|
| Częstotliwość monitorowania USDC/USD | odczyt co 5s lub poprawny WS; ocena quote gate co 1s; oba źródła muszą działać | Nie deklarować aktywnego monitora bez kontrakt/replay testów |
| Freshness | maks. 15s od poprawnego odbioru oraz 15s zweryfikowanego wieku quote; uncertainty zegara maks. 1s | Brak/stale/nieznany timestamp/invalid quote natychmiast blokuje nowe wejścia; continuity timer się zeruje |
| Rozbieżność źródeł | różnica midów / mniejszy mid >0,25% przez 30s | Quote-risk pause; nie „głosować” na wygodniejsze źródło |
| Wczesny depeg | `abs(mid / (1 USD) − 1) >0,30%` na dowolnym zdrowym źródle przez 60s | Quote-risk pause nowych/pending BUY; zlecenie cancel nie jest dowodem terminalności |
| Silny depeg | `abs(mid / (1 USD) − 1) ≥1,00%` na dowolnym zdrowym źródle w jednej poprawnej próbce | Natychmiast trwały latch i review; również upside odchylenie jest alarmem |
| Depeg/halt komunikat albo zmiana Bundle | suspend/redemption restriction/status change, zmiana składu lub utrata mapowania | Natychmiast blokada i latch; publiczny komunikat zapisany z first-seen/ingested |
| FX poll / publikacja | NBP+ECB co 15min w dni publikacji; snapshot z publication date i observed receive time; oczekiwana nowa publikacja do 18:00 Europe/Warsaw w właściwym dniu kalendarza | Brak spodziewanej publikacji po deadline, nieznany kalendarz lub brak bazowej wyceny blokuje nowe wejścia |
| Weekend/święto FX | ostatnia znana publikacja as-of, oznaczona stale-calendar; dopuszczona tylko do następnego rzeczywiście zaplanowanego publication deadline | Bez fikcyjnego kursu 24/7. Daily-policy musi jawnie zaakceptować brak intraday FX; jeśli wymagany live FX, trzeba osobno zatwierdzić źródło i profil, bez cichego zamiennika |
| Kontrola FX | dzienna zmiana USD/PLN ≥3% albo >1% różnicy NBP vs cross ECB dla zgodnej daty publikacji | Quote-risk pause/review; różne daty nie stanowią dowodu rozbieżności, lecz kontrolę coverage |
| Wznowienie po łagodnym alarmie/stale | oba źródła ciągłe i świeże przez 30min; oba `abs(mid / (1 USD) − 1) ≤0,15%`, różnica ≤0,15%; aktualny FX, metadata i reconciliation | Dopiero jawne potwierdzenie operacyjne; brak automatycznej konwersji lub resetu DD |
| Wznowienie po silnym depegu/status/Bundle change | te same warunki co wyżej + manual review tożsamości, rynku, salda i evidence profilu | Latch trwa przez restart; decyzja człowieka nie zastępuje brakujących danych |

Wartości D ustalone jako propozycja ryzyka, nie parametry optymalizacji PnL. Ocena „przez N sekund” wymaga ciągłych poprawnych próbek bez luki >15s; grace period nie pozwala wejść przy missing valuation. Monotonic timers w runtime, trwałe UTC deadline/latch w recovery. Błędy odczytu, cross-source disagreements i alarmy zapisuje się z reason codes.

Q-risk ma osobny stan od DD 6/9/12% i blokuje nowe ryzyko bez resetowania NAV/HWM. Bot nadal rozstrzyga pending orders, księguje fille i zarządza znanym inventory/protection według wybranego ExecutionProfile. Alarm nie uruchamia automatycznej sprzedaży, FX, stablecoin swap, wypłaty ani zakupu fee tokena. Finansowe raporty pokazują jednocześnie unit NAV/DD w Q, wartość w PLN i fiat USD, depeg proxy oraz niepewność/FX age. Wzrost jednostkowego NAV Q podczas utraty wartości Q nie jest dowodem wzrostu realnej wartości portfela.

## Dowód wymagany do zamknięcia V11

PASS wymaga wybranego konta/jurysdykcji/environment, ekonomicznej tożsamości Q i zasad Bundle, potwierdzonych spot permissions/no-debt, fee currencies/tier/minimów, rynków i PIT mappingu; następnie działającego monitora z niezależną wyceną i źródłami FX, kalendarzem, freshness, alarmami i recovery. Testy obejmują depeg 0,5%/1%/5%, upside depeg, brak jednego/obu źródeł, weekend i święto, late publikację, halt/redemption, change-of-bundle, restart latch i pozorną poprawę DD w Q. Historia wyceny przez cały okres badań pozostaje V10 UNKNOWN.

Do testów offline można jawnie użyć `SYNTHETIC_Q`, ze sztuczną wyceną/stress i bez portu transmisji; nie nazywać go wybranym produkcyjnym USD ani udanym paper/live. Ten dokument nie daje DISABLED_VERIFIED żadnej ścieżce: wymaga to testu nieosiągalności. Ponowna ocena przed P06/P07, wyborem runtime profilu i każdą zmianą podmiotu konta, produktu, Bundle, metadata, fee lub źródła wyceny.
