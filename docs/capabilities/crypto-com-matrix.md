# Crypto.com — macierz capabilities P00

**Data assessmentu:** 2026-10-07, Europe/Warsaw. **Zakres:** P00.1/P00.2; dokumentacja i publiczne odczyty bez uwierzytelnienia. **Profil oceniany:** planowany spot-only, long-only, bez długu; wejście `LIMIT` + `FILL_OR_KILL`, giełdowy `STOP_LOSS` przechodzący w MARKET, jedna jawnie wybrana polityka wyjścia. Konto, Q, instrumenty docelowe i transport poleceń nie zostały zatwierdzone ani zweryfikowane.

Podstawa wymagań: [specyfikacja v1.1, zwłaszcza §1.1–1.8 i §8](../2026-10-06-crypto-trading-bot-technical-design-v1.1.md) oraz [plan P00/P06/P12](../superpowers/plans/2026-10-07-crypto-trading-bot-v1-implementation-plan.md). Politykę przechowywania i unieważniania evidence określa [evidence-policy.md](evidence-policy.md); scenariusze zawiera [test-cases.md](test-cases.md).

## Werdykt i znaczenie statusów

**G0: PASS wyłącznie dla kompletności assessmentu i możliwości dalszych prac offline. Live: BLOCKED. Runtime ExecutionProfile: INCOMPLETE.** To nie jest PASS żadnej używanej capability, pozytywny wynik UAT, gotowość kwalifikowanego paper ani zgoda na P01. Rozpoczęcie P01 pozostaje osobną bramką użytkownika. Nie znaleziono podstaw do ukrytego fallbacku FOK→IOC/GTC/market ani stop-market→stop-limit.

`DOCUMENTED` oznacza treść oficjalnego źródła; `OBSERVED_PUBLIC` oznacza konkretną odpowiedź publicznego endpointu. Są to klasy evidence, nie statusy dopuszczenia live. Status `PASS` wymaga dowodu dla jawnego konta/środowiska/instrumentu/ścieżki i odpowiednich testów. `UNKNOWN` oznacza brak rozstrzygającego dowodu; `FAIL` wymaga wykazanego naruszenia w określonym zakresie. `DISABLED_VERIFIED` wymaga istniejącego kodu i testu nieosiągalności nieużywanej ścieżki. Nie ma jeszcze takiego kodu/testu; sam zamiar wyłączenia nie wystarcza.

Wszystkie V01–V11 pozostają **UNKNOWN w zakresie planowanego live**. Dokumentacja zawiera pozytywne przesłanki wykonalności, ale nie sprawdzono prywatnego konta, UAT trading, prywatnych strumieni, adaptera ani recovery. Publiczne dane nie rozstrzygają tych braków.

## Środowiska i rzeczywisty zakres odczytów

| Środowisko | REST root | WS user | WS market | Co ustalono |
|---|---|---|---|---|
| Production | `https://api.crypto.com/exchange/v1/{method}` | `wss://stream.crypto.com/exchange/v1/user` | `wss://stream.crypto.com/exchange/v1/market` | Endpoints DOCUMENTED [S01]; wykonano publiczny `GET public/get-instruments`. Brak odczytów prywatnych i transakcji. |
| UAT Sandbox | `https://uat-api.3ona.co/exchange/v1/{method}` | `wss://uat-stream.3ona.co/exchange/v1/user` | `wss://uat-stream.3ona.co/exchange/v1/market` | Endpoints DOCUMENTED [S01]; publiczny katalog odpowiada. Konto UAT i jego uprawnienia UNKNOWN. |

[Oficjalny Help API, aktualizacja 2026-07-20](https://help.crypto.com/en/articles/3511424-api) podaje dostęp UAT przez zaproszenie dla kont instytucjonalnych [S02]. Nie ustalono, czy użytkownik ma takie konto/zaproszenie. Dostępność hosta i publicznego katalogu **nie dowodzi dostępności prywatnego UAT** ani możliwości uzyskania klucza przez klienta retail. Brak UAT blokuje wymagany dowód kontraktowy execution; fake/replay wspierają prace offline, ale nie zastępują tego dowodu przed live. Nie należy obchodzić blokady testem za realne pieniądze.

Odczytano oficjalne strony developer i Help wymienione w rejestrze źródeł. Strony `create-order` REST/WS, account leverage i account settings zweryfikowano także przez DOM przeglądarki: część bezpośrednich odczytów web zwracała błąd dostępności. Po wznowieniu sesji odczytano także sekcję `private/create-order` drukowanej [referencji REST](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest-single-page/), generation date `20261006-04:50`: enum obejmuje FOK, a `spot_margin` ma SPOT/MARGIN bez podanego defaultu. To DOCUMENTED, bez testu wykonania. Początkowe przerwanie odczytu przez limit narzędzia nie pozostaje blockerem tego porównania.

W P00 wykonano publiczne odczyty katalogu production/UAT około **2026-10-06 22:25 UTC = 2026-10-07 00:25 Europe/Warsaw**. Pełny manifest odczytów jest w [evidence-policy.md](evidence-policy.md). Zweryfikowano lokalne odpowiedzi i ich SHA-256:

| Evidence | Wynik katalogu | SHA-256 oryginalnego publicznego JSON |
|---|---|---|
| production `public/get-instruments` | 989 rekordów; 570 `CCY_PAIR`, 409 perpetual, 10 futures | `f271fc0236caf43606f582ca3913d0a08f587885ee3ea0fe97d8695b61ae4161` |
| UAT `public/get-instruments` | 1268 rekordów; 800 `CCY_PAIR` | `3526611531af19460d879456f8f4a4a0b99d1217d9d1399aedf43d1547edc5da` |

Nie wysłano `public/auth`, żadnego prywatnego requestu, create/cancel/amend, zmiany ustawień ani transferu. Nie tworzono kont ani kluczy, nie szukano sekretów, nie testowano borrowing. Odczyt stron opisujących mutacje nie jest wykonaniem tych mutacji. Dokument nie zawiera rzeczywistych account IDs ani danych finansowych. Hash całej strony dokumentacji: **NOT_CAPTURED**; źródła mają URL i datę odczytu, a nie fikcyjny immutable snapshot.

## Macierz V01–V11

Wspólna data evidence: **2026-10-07**. Wspólna ponowna ocena: **2026-10-14**, ponadto przed P06/P07/P12 i natychmiast po zmianie API, transportu, konta, fee, rynku, trigger reference lub metadanych. PASS dla jednej pary/UAT nie rozszerza się automatycznie na produkcję ani cały universe. Testy niżej są wymaganiami przyszłych etapów, a nie wykonanymi testami.

| ID / właściciel | Status live / scope | Pozytywne evidence | Brakujący dowód / test | Konsekwencja UNKNOWN/FAIL |
|---|---|---|---|---|
| **V01 spot/no-debt** — P06/P12 | **UNKNOWN**; wybrane konto production + UAT, obie strony BUY/SELL, ordinary i advanced | DOCUMENTED: wspólny API obsługuje spot, margin i derivatives; `spot_margin` ma `SPOT`/`MARGIN`, leverage konta dopuszcza 1 [S01,S03–S05]. OBSERVED_PUBLIC: spot `BTC_USD` ma włączone margin i max leverage 50 [S17]. | Uprawnienia/region, ustawienia konta, saldo cash i brak długu; efekty jawnego `SPOT`, reguły braku cash/base i fee; denylist margin/isolation/leverage/borrow w adapterze; reject braku własnych środków w bezpiecznym UAT/fake. | Brak live; symbol spot, leverage=1, brak pola leverage i REDUCE_ONLY nie wystarczą. |
| **V02 FOK LIMIT** — P06/P12 | **UNKNOWN**; wybrany spot instrument, account i wybrany REST/WS command port | DOCUMENTED: bieżące obie strony create-order zawierają `FILL_OR_KILL`; Trading opisuje pełne natychmiastowe wykonanie albo anulowanie [S03,S04,S07]. | UAT: pełny fill z wielu komunikatów, zero fill, terminal partial, reject, timeout po send, fill przed ACK, opóźnione fee; dowód terminalności i reconciliation; pomiar latency. | Baseline FOK live zablokowany. Brak cichego IOC/GTC/market fallbacku; ewentualny alternatywny profil wymaga osobnego review. |
| **V03 stop-market** — P06/P12 | **UNKNOWN**; standalone spot SELL `STOP_LOSS`, netto posiadane inventory | DOCUMENTED: `private/advanced/create-order`, `STOP_LOSS`→MARKET, źródła triggera [S08,S09]. Help opisuje brak rezerwacji przy utworzeniu i reject przy triggerze [S10]. | Konto/UAT: przyjęcie i aktywacja, poprawny spot mode, trigger feed dla konkretnej pary, minima/bands, utrata środków, expiry, reject, partial market fills i dust; ochrona pierwszego fillu i deadline. | Brak live na rynku; pending/ACK nie jest coverage. STOP_LIMIT nie jest substytutem. |
| **V04 attached protection** — P06/P12 | **UNKNOWN**; opcjonalny profil attached/native-linked, brak potwierdzonego wyłączenia | DOCUMENTED: OTO/OTOCO/SPOT_ATTACH, `attach_order_id`; Help aktywuje po pełnym wykonaniu parent [S08,S09,S11]. | Jeżeli używane: UAT parent partial/cancel/full, aktywacja i saldo/fee. Jeżeli nieużywane: przyszły kod/test nieosiągalności attached i derivatives. | Nie liczyć inactive child jako coverage partial fill. Do wyłączenia obowiązuje UNKNOWN, nie DISABLED_VERIFIED. |
| **V05 IDs/query/dedup** — P06/P12 | **UNKNOWN**; ordinary + advanced, account/instrument/environment | DOCUMENTED: `client_oid` do 36 znaków; query ordinary/advanced po `order_id` lub `client_oid` [S03,S12,S13]; kod DUPLICATE_CLORDID [S01]. | Scope i retencja server dedup, read-after-write/opóźnienie indeksowania, query po restarcie/timeout, kolizje i powtórzony ID/payload, stabilny fill ID między kanałami. | Ambiguous submit pozostaje UNKNOWN; rezerwacje zachowane; zakaz automatycznej retransmisji na podstawie timeout/NOT_FOUND. |
| **V06 cancel/exit** — P06/P12 | **UNKNOWN**; wybrana polityka NATIVE_LINKED albo SERIAL_CANCEL_THEN_MARKET | DOCUMENTED: cancel jest asynchroniczny [S14,S15]; amend robi cancel→create [S16]; istnieją linked strategie [S08]. | Cancel-fill/trigger race; terminal confirmation + fills; jedno uprawnienie SELL; partial exit; crash/baza outage po cancel; measured uncovered deadline; osobny dowód atomowości, jeśli używana. | Brak live zależnego wyjścia. ACK cancel nie zwalnia inventory/rezerwacji i nie uprawnia drugiego SELL. Brak dowodu atomowego cancel→market. |
| **V07 orders/fills/history** — P06/P12 | **UNKNOWN**; recovery ordinary, standalone advanced i linked | DOCUMENTED: open/detail/history ordinary i advanced, `user.order`/`user.trade`; trades ma trade/order/client IDs oraz fee [S07,S12,S13,S18–S20]. | Stronicowanie bez pominięć na granicach ms/ns i równych timestamps, retencja, standalone stop scope, mapping advanced→exchange order→fill, reconnect/replay/duplicate/out-of-order i kompletność terminalności. | Recovery nie kończy się przy nierozstrzygniętych orders/fills/fee; zakaz wznowienia nowych wejść. |
| **V08 balances/fee** — P06/P12 | **UNKNOWN**; cash Q/base, rezerwacje, fee currency i tier konkretnego konta | DOCUMENTED: `private/user-balance` rozdziela aggregate margin/cash i pozycje saldowe [S21]; endpointy account/instrument fee w bps [S22,S23]; fee i credits w trades [S20]. | Read-only konto: cash/free/reserved/borrowed, Q/bundle, fee tier/rabat/credits, base/Q/CRO i rounding; UAT fees-before/after-fill, dust i minima po fee. | Brak sizingu live; available margin nie jest wolną gotówką. Nieznane fee/currency blokują wejścia, bez borrowing/konwersji/token buy. |
| **V09 metadata/rate limits** — P06/P07/P12 | **UNKNOWN**; cały zakwalifikowany universe i budżet recovery/protection | DOCUMENTED: public metadata i minimum notional FAQ [S17,S24]. OBSERVED_PUBLIC: tick/type/tradable/margin w katalogach. Limity opisane sprzecznie [S01,S02,S25]. | Freshness/PIT/status, minima/max/bands każdego rynku, legalność ochrony, realny limit dla command/query/streams i wspólnych pul, 429/backoff z zachowanymi deadline. | Blokada dotkniętych operacji. Jednorazowy GET katalogu nie dowodzi poprawnego adaptera/budżetu ani no-debt. |
| **V10 historical data** — P00/P07/P12 | **UNKNOWN**; historyczne dane venue, PIT universe, L2/trades/trigger reference | Osobny assessment w [data-coverage.md](data-coverage.md). | Weryfikacja manifestu coverage/retencji/delistów i poziomu B0/B1/B2; bez rozszerzenia dowodów execution na dane historyczne. | Ograniczone wnioski badawcze; brak dowodu danych do kwalifikacji live. |
| **V11 Q/valuation** — P00/P06/P07/P12 | **UNKNOWN**; realny runtime Q i raportowanie PLN | Osobny assessment w [quote-profile.md](quote-profile.md). | Konto i symbol/bundle, cash Q, źródła FX/depeg/valuation, monitor i fail-closed policy. | Runtime profile INCOMPLETE; offline używa jawnego syntetycznego Q, bez domyślnego parytetu. |

## Ustalenia szczegółowe i nierozstrzygnięte kontrakty

### V01: account leverage, margin i no-debt

`POST private/change-account-leverage` dokumentuje `account_id` i integer `leverage` w zakresie **1–100**; obowiązuje niższa z dźwigni konta i instrumentu [S05]. `POST private/get-account-settings` zwraca maksymalny leverage konta [S06]. Są to dostępne mechanizmy konfiguracji/odczytu, **nie wykonano ich**. Nie znaleziono w odczytanych źródłach gwarancji „account leverage=1 wyłącza margin/borrowing we wszystkich ścieżkach”. Help opisuje leverage jako czynnik initial margin; zmiana nie zmienia maintenance margin [S26]. Smart Cross Margin obejmuje collateral spot i ujemne salda [S27].

Aktualne formularze zwykłego create-order REST i WS dokumentują opcjonalne `spot_margin` z enumem `SPOT`/`MARGIN`, ale **nie podają defaultu tego pola** [S03,S04]. Braku domyślnego trybu nie zastępuje się domysłem. `MARGIN_ORDER`, `ISOLATED_MARGIN`, `leverage`, `isolation_id`, `isolated_margin_amount` są jawnie obecne w API. Dla standalone advanced aktualny odczyt REST nie pokazuje takiego samego pola `spot_margin` jak zwykłe DMA: reguły konta i payload advanced wymagają osobnego wyjaśnienia i UAT [S09].

Publiczny `BTC_USD` jest `CCY_PAIR`, a jednocześnie ma `margin_buy_enabled=true`, `margin_sell_enabled=true`, `max_leverage="50"`. To konkretny dowód, że klasyfikacja spot nie izoluje no-debt. W P06 należy zamrozić konto i profil finansowania, zweryfikować leverage=1 i rzeczywiste uprawnienia read-only, oraz oddzielnie dowieść braku długu na command paths. Proponowane ograniczenie konta do 1 pozostaje kandydatem do weryfikacji, nie wykonaną zmianą i nie samodzielnym PASS. Adwersarialne próby niedoboru cash/base/fee wykonuje się w fake lub dostępnym UAT, nigdy przez produkcyjne zadłużenie.

### V02: FOK i rozbieżności dokumentacji

Odczyt z 2026-10-07 bieżących stron **REST `private-create-order-dma` i WS `ws-user-api-private-create-order-dma`** pokazuje identyczne `time_in_force`: `GOOD_TILL_CANCEL`, `IMMEDIATE_OR_CANCEL`, `FILL_OR_KILL` [S03,S04]. Nie potwierdzono aktualnego braku FOK w REST ani faktycznej rozbieżności enumów REST/WS. Dodatkowy odczyt sekcji create-order w drukowanej referencji REST (generation date `20261006-04:50`) również potwierdził te trzy enumy; nie znaleziono w tych odczytach mismatch FOK. Starszy/cached opis lub inny produkt nie może rozstrzygać docelowego endpointu. Dokumentacji `/fcm/v1/` nie używa się jako dowodu dla `/exchange/v1/`.

Oba przykłady create-order łączą `POST_ONLY` i `FILL_OR_KILL`; lista reason codes opisuje `43005 POST_ONLY_REJ`, gdy POST_ONLY ma TIF inne niż GTC [S01,S03,S04]. To sprzeczność przykładu z opisem ograniczenia, nie dowód wykonalnego payloadu. Planowany marketable FOK nie powinien kopiować POST_ONLY/SMART_POST_ONLY ani zmieniać TIF. Kod `43003 FILL_OR_KILL` opisuje niewykonane/anulowane FOK; nie wystarcza do ustalenia wszystkich stanów terminalnych.

Pozytywna deklaracja całkowitego wykonania nie oznacza jednego komunikatu fill. Kilka trade records może nadejść przed ACK i osobno; dopiero dowód końcowej ilości/terminalności odróżnia prawidłowy pełny FOK od terminal partial. UAT musi potwierdzić rzeczywisty matching i odtwarzanie skutków, a fake/replay wcześniej sprawdzić accounting/ochronę.

### V03/V04: ochrona i źródło triggera

`POST private/advanced/create-order` z `type=STOP_LOSS`, `side=SELL`, `quantity`, `ref_price` jest udokumentowaną ścieżką market stop [S09]. Źródła: `MARK_PRICE`, `INDEX_PRICE`, `LAST_PRICE`; overview wskazuje MARK_PRICE jako default [S08]. Help omawia mark trigger, również dla spot, i reject braku środków przy triggerze [S10]. To wymaga wyboru źródła i zweryfikowania dostępności feedu dla konkretnego rynku. Lokalna ostatnia cena albo świeca nie jest automatycznie ceną triggera giełdy. TP/SL nie rezerwuje środków przy tworzeniu; lokalna rezerwacja inventory musi zapobiegać konkurującym sprzedażom.

Attached Help opisuje aktywację dopiero po **pełnym** wykonaniu parent i dziedziczenie spot/margin; brak środków/minimum przy triggerze może odrzucić child [S11]. OTO/OTOCO nie potwierdza ochrony wcześniejszego częściowego inventory ani atomowego przełączenia stop→wyjście. Dopóki profil attached nie jest jawnie wybrany i sprawdzony, nie używa się go jako argumentu coverage. Wyłączenie attached/native-linked/retry będzie mogło dostać DISABLED_VERIFIED dopiero po przyszłych testach nieosiągalności.

### V05/V06/V07: rozstrzygnięcie polecenia i recovery

Zwykłe i advanced order-detail przyjmują `order_id` albo `client_oid` [S12,S13]. Generować trwały własny ID; brak `client_oid` może użyć nonce, co nie daje wymaganej unikalności [S03]. Kod DUPLICATE_CLORDID sygnalizuje wykrywanie powtórzenia, ale nie dokumentuje w tym assessmentcie czasu przechowywania i scope server dedup. Nie uznano tego za idempotentny submit z bezpiecznym retry.

Create/cancel/amend ACK potwierdza request, nie stan giełdowy [S07,S14–S16]. Ordinary `private/amend-order` robi cancel→create; nie ma tu dowodu atomowej zamiany ochrony ani cancel→market [S16]. Kandydat SERIAL_CANCEL_THEN_MARKET musi przed SELL rozstrzygnąć terminalny cancel oraz fille i pozostałą ilość. Jeśli po cancel znika baza/writer ownership, luka ochrony jest trwałym incydentem; nie powstaje emergency writer. NATIVE_LINKED nie przełącza się automatycznie na sekwencję.

Do reconciliation wymagane są ordinary `get-open-orders`, `get-order-detail`, `get-order-history`, `get-trades`, a także advanced `get-open-orders`, `get-order-detail`, `get-order-history` i ewentualnie `get-order-list-detail`. Kanały `user.order`, `user.trade`, `user.advanced.order` są dokumentowane [S07,S08]. W opisach advanced cancel/history występuje także literalnie **`user.advance.order`** [S15,S19]; ta rozbieżność z `user.advanced.order` wymaga weryfikacji właściwej nazwy subskrypcji, nie cichego aliasu.

`get-trades` ma default/max 100, zakres czasu start inclusive/end exclusive, ms lub ns; dokument zaleca ns do paginacji [S20]. Advanced history również ma default/max 100 i takie granice, ale opis zakresu mówi OTO/OTOCO [S19]. Zakres standalone STOP_LOSS, retencja, kompletne przejście przy >100 rekordach oraz mapping `exchange_order_id`→ordinary fill pozostają do udowodnienia. Ordinary history podaje default 100 i pola cumulative/status [S18]. Sekcja Transaction History drukowanej referencji REST deklaruje dostęp do ostatnich 6 miesięcy i kontakt z supportem dla starszych danych. Jest to dokumentowana granica historii konta, nie pomiar kompletności poszczególnych ordinary/advanced endpoints ani retencja publicznych market trades. Recovery musi zachować własne trwałe archiwum; brak historii poza retencją blokuje uzgodnienie, nie uprawnia rekonstrukcji samym saldem.

### V08/V09: cash, fee, minima i budżet API

`private/user-balance.total_available_balance` jest definiowane przez margin balance minus initial margin; nie jest dowodem wolnej gotówki Q. Potrzebne są walutowe `quantity`, `reserved_qty`, cash/borrow/isolated observations i lokalne rezerwacje [S21]. Fee endpointy podają **bps** dla konta i instrumentu [S22,S23]; fill records zawierają rzeczywistą walutę i kwotę opłaty oraz fee credits. Ujemne `fees` oznacza debit; ujemne `fee_credits` zużycie credits [S20]. Nie potwierdzono tieru, promocji, rabatu, fee currency ani salda użytkownika.

Public metadata dokumentuje tick/decimals, type, tradable, max leverage, margin flags [S17]. Odpowiedź katalogu nie zawiera kompletnego uniwersalnego kontraktu min/max notional ani wszystkich price bands. Help Minimum Order Size podaje minimum notional **1 USD dla wszystkich spot także API**, ilość jako maksimum quantity tick i notional/effective price, oraz zaokrąglenie minimum w górę do ticka. Dla cross pair bierze pod uwagę index quote [S24]. To DOCUMENTED reguła z datą, nie niezmienna stała ani powód zwiększenia ilości powyżej risk approval. Jeśli minimum przekracza sizing, setup odpada. Netto po fee i możliwość legalnej ochrony/sprzedaży po spadku wymagają osobnych testów.

Limity są sprzeczne: developer common reference podaje per-method/per-key create/cancel/cancel-all 15/100 ms, detail 30/100 ms, trades/history 1/s, inne private 3/100 ms; public 100/s per-method/per-IP; WS user 150/s i market 100/s [S01]. Help API podaje 10/URL/s, private per-account i market per-IP [S02]. Public overview mówi dodatkowo o globalnej puli [S25]. Nie wybrano arbitralnie większych limitów. W P06 trzeba wyjaśnić właściwy kontrakt środowiska i scope, a P12 dowieść budżetu ochrony/recovery bez testu przeciążeniowego produkcji. Wstępny konserwatywny budżet nie jest dowodem SLO ani PASS V09.

## Co wymaga dostępu do konta i następnego etapu

| Brakujący element P00.2 | Obecny wynik | Bezpieczny docelowy dowód |
|---|---|---|
| UAT konto i kwalifikacja instytucjonalna | UNKNOWN; publiczna reachability potwierdzona, invitation niepotwierdzone | Potwierdzenie dostępu/onboardingu przez użytkownika; osobny zakres prac UAT. Bez sekretów w repo/czacie. |
| Konto production, regionalne instrumenty i Q | UNKNOWN | Zatwierdzony read-only snapshot ustawień/uprawnień/metadanych konta, z redakcją prywatnych danych. |
| Account leverage, margin/borrow, cash/free/reserved | UNKNOWN | Read-only account settings/balances; osobna decyzja użytkownika przed jakąkolwiek zmianą konta. |
| Fee tier/rates/currency/credits | UNKNOWN | Prywatne read-only fee i balance observations; UAT fill accounting w P06. |
| FOK, stop trigger, cancel terminalność i mapping advanced | NOT_TESTED | Po uzyskaniu dostępu testy kontraktowe P06; scenariusze awarii P12; bez produkcyjnych testów długu/awarii. |
| Deadline/freshness/rate budget | NOT_MEASURED | UAT/replay + publiczny read-only capture po właściwej bramce; bez dostrajania dla PnL. |

Wynik G0 nie uznaje braku UAT za FAIL exchange ani nie obiecuje jego uzyskania. Jeśli UAT nie będzie dostępny, dopuszczalne są niezależne kontrakty ledger/state machine i jawne badania demonstracyjne offline; planowany live nadal pozostaje zablokowany. Zmiana wymagań UAT/execution wymaga jawnego review zamiast pominięcia testu.

## Rejestr oficjalnych źródeł

Wszystkie źródła odczytano 2026-10-07; cytowane nazwy pól są literalnymi identyfikatorami API. Rejestr nie stanowi zgody na użycie opisanych prywatnych endpointów.

| ID | Źródło | Zakres evidence |
|---|---|---|
| S01 | [Crypto.com Exchange API v1 — common reference](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/crypto-com-exchange-api-v-1) | Root production/UAT, wspólny spot/margin/derivatives, rate limits i reason codes. |
| S02 | [Help — API](https://help.crypto.com/en/articles/3511424-api) | UAT invitation institutional; limity 10/URL/s, poziom account/IP, read-only default keys. |
| S03 | [REST private/create-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-create-order-dma) | Bieżący DOM: FOK, spot_margin, margin fields, ACK, IDs, przykład POST_ONLY+FOK. |
| S04 | [WS private/create-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket/ws-user-api-private-create-order-dma) | Bieżący DOM: te same enumy i pola; bez testu execution. |
| S05 | [private/change-account-leverage](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-change-account-leverage) | Wartości 1–100, niższy limit account/instrument. |
| S06 | [private/get-account-settings](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-account-settings) | Odczyt maximum account leverage, STP. |
| S07 | [Trading](https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket/trading) | FOK description, async operations, ordinary query i stream names. |
| S08 | [Advanced Order Management](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/advanced-order-management) | Trigger sources/default, STOP_LOSS, linked endpoints i user.advanced.order. |
| S09 | [private/advanced/create-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-advanced-create-order) | STOP_LOSS→MARKET, ref_price/ref_price_type, attach fields, instrument-based contingency. |
| S10 | [Help — Stop-Loss and Take-Profit Orders](https://help.crypto.com/en/articles/4453247-stop-loss-and-take-profit-orders) | Mark trigger, brak rezerwacji przy utworzeniu, reject/min/max. |
| S11 | [Help — Attached TP/SL](https://help.crypto.com/en/articles/11501438-setting-attached-take-profit-tp-stop-loss-sl-orders) | Pełny parent fill przed aktywacją, dziedziczenie spot/margin, saldo przy triggerze. |
| S12 | [private/get-order-detail](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-order-detail) | Query po obu IDs, cumulative/status/reason. |
| S13 | [private/advanced/get-order-detail](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-advanced-get-order-detail) | Query IDs, list/leg/exchange_order_id/reject/trigger fields. |
| S14 | [private/cancel-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-cancel-order) | Asynchroniczny cancel po order/client ID. |
| S15 | [private/advanced/cancel-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-advanced-cancel-order) | Advanced cancel; opis user.advance.order. |
| S16 | [private/amend-order](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-amend-order) | Cancel→create, priorytet i ACK. |
| S17 | [public/get-instruments](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/public-get-instruments) | Metadata fields; publiczne odpowiedzi z hashami powyżej. |
| S18 | [private/get-order-history](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-order-history) | History default limit, timestamps, cumulative/status. |
| S19 | [private/advanced/get-order-history](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-advanced-get-order-history) | Scope OTO/OTOCO, limit 100, start/end, nazwa kanału. |
| S20 | [private/get-trades](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-trades) | Pagination granice/limit, IDs, fees/credits i currency. |
| S21 | [private/user-balance](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-user-balance) | Cash/margin aggregate, walutowe quantity/reserved, borrow optional i isolated. |
| S22 | [private/get-fee-rate](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-fee-rate) | Spot tier, effective spot rates w bps. |
| S23 | [private/get-instrument-fee-rate](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/private-get-instrument-fee-rate) | Per-instrument effective rates w bps. |
| S24 | [Help — Minimum Order Size FAQ](https://help.crypto.com/en/articles/10511090-crypto-com-exchange-minimum-order-size-faq) | Minimum notional 1 USD, effective price, cross quote index, roundup. |
| S25 | [Reference and Market Data](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/reference-and-market-data) | Public read-only, odświeżanie metadata i global rate pool opis. |
| S26 | [Help — Account Leverage](https://help.crypto.com/en/articles/12274905-account-leverage) | Wpływ leverage na IM; brak zmiany MMR. |
| S27 | [Help — Margin Balance / Smart Cross Margin](https://help.crypto.com/en/articles/5311584-margin-balance-details-and-smart-cross-margin-policy) | Spot collateral, negative balances i MAL; brak gwarancji no-debt przez 1x. |
