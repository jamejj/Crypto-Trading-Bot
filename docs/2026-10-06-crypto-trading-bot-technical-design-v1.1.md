# Autonomous Crypto Trading Bot — Technical Design v1.1

Data utworzenia: 2026-10-06; finalny self-review i doprecyzowania: 2026-10-07

Status: READY FOR PLANNING; implementacja wymaga zatwierdzenia planu, live odrębnej bramki

Zakres: sześć kontraktów P0, baseline badawczy oraz kryteria ich weryfikacji

Punkt odniesienia: Technical Design v1.0 z 2026-10-05 i adversarial review

Pierwszy kandydat execution: Crypto.com Exchange

## 0. Cel, zakres i moc ustaleń

Celem jest sprawdzenie, czy breakout po kompresji i wzroście aktywności na szerokim universe płynnych rynków daje dodatnią out-of-sample expectancy netto, przy akceptowalnym drawdownie. Wynik „brak potwierdzonej przewagi” jest pełnoprawnym wynikiem badań. Projekt nie obiecuje rentowności ani maksymalnej ceny wyjścia.

Wymagania zatwierdzone przez użytkownika:

- Crypto spot, long-only; bez leverage, margin, futures, borrowing i możliwości zadłużenia.
- Autonomiczne działanie 24/7; ML doradza, nie kontroluje zleceń ani limitów bezpieczeństwa.
- Szeroki radar płynnych rynków. Płynne memecoiny nie są wykluczone przez samą kategorię.
- 6% peak-to-current drawdown: CAUTION; 9%: DEFENSIVE; 12%: HARD PAUSE i obowiązkowe review przed wznowieniem.
- 12% to granica akceptacji strategii, a nie gwarantowane maksimum straty. Progi nie są parametrami optymalizowanymi do backtestu.
- Osobne porównanie confirmed breakout entry i breakout + retest entry. Probe + confirmation pozostaje późniejszym eksperymentem.
- New listings są obserwowane i zapisywane od początku, ale nie mogą handlować live v1 bez osobnej walidacji.
- Na tym etapie powstaje wyłącznie dokumentacja.
- Doprecyzowania z 2026-10-07: Q jest główną jednostką księgową/drawdownu, PLN dodatkową; profil 1%/2%/2 pozycje/50%/90% jest zatwierdzonym punktem startowym badań i operacji.
- R0 działa na 5m, z time exit 24 × 5m. Rozbudowany sygnałowy State Engine, FOMO, ML, news, layered entries i New Listing execution są odrębnymi późniejszymi eksperymentami. Operacyjna maszyna stanów zleceń jest obowiązkowa od początku.
- FOK jest pierwszym profilem do weryfikacji, nie niezmienną cechą docelowego bota. Brak wsparcia blokuje ten profil live i prowadzi do review osobnej ścieżki IOC/marketable-limit; nie oznacza porzucenia projektu.
- Parametry czasowe D są weryfikowane pomiarem/UAT i nie mogą być dostrajane dla poprawy PnL. Brak pozytywnej weryfikacji V blokuje ścieżkę, która na niej polega.

Konwencje:

- **MUST / MUST NOT**: wymagany kontrakt zachowania w proponowanym projekcie.
- **D**: default projektowy do pomiaru i walidacji. Profil ryzyka wyliczony powyżej jest zatwierdzony jako punkt startowy; pozostałe D są konkretnymi propozycjami do wykonania po zatwierdzeniu planu. Żaden D nie oznacza dowodu skuteczności ani prawa do strojenia pod PnL.
- **V**: zdolność giełdy lub założenie wymagające weryfikacji. Dokument nie przedstawia go jako działającego mechanizmu.
- **R**: parametr hipotezy badawczej, zamrażany przed oceną OOS.

W v1.1 poniższe kontrakty zastępują sprzeczne lub mniej precyzyjne fragmenty v1.0. Szczególnie: dawny próg 8%, ogólne „idempotent retries”, „existing positions remain protected” oraz domyślne wejścia warstwowe nie obowiązują w poprzedniej postaci.

### 0.1. Przyjęte uproszczenia

Jeden modułowy serwis execution z jednym właścicielem konta; osobny proces badań bez kluczy live. PostgreSQL przechowuje trwałe intencje, ledger i zdarzenia; Parquet dane badawcze. Bez mikroserwisów, drugiej giełdy i GUI na ścieżce krytycznej.

Live v1: jeden wybrany wariant baseline, jedno wejście na pozycję, bez ADD i dobrowolnych częściowych realizacji zysku. Obsługa częściowych filli pozostaje obowiązkowa. ML, news i New Listing Mode działają wyłącznie jako obserwacja/shadow. Re-entry podlega limitowi epizodu. Trailing wymagający wymiany ochrony jest późniejszym eksperymentem; baseline używa stałej invalidacji i reguł wyjścia opisanych w kontrakcie 6.

### 0.2. Jednostka rozliczeniowa — zatwierdzona

Podstawową jednostką księgową i drawdownu jest jedna waluta quote Q wybrana po weryfikacji dostępności konta, rynku, opłat i danych. PLN jest raportowany dodatkowo. Jest to zatwierdzona decyzja. Zmiana na PLN wymaga nowego review obejmującego cały pomiar equity, koszty FX i historyczne dane point-in-time, nie tylko prezentację wyników.

W v1 nie ma automatycznej konwersji między quote assets. Wybór Q jest obowiązkową konfiguracją wdrożenia, a nie zgadywaną nazwą stablecoina. Q nie jest uznawane za bezwarunkowo bezpieczne: raport mierzy także jego wartość w PLN i referencyjnej walucie fiat oraz scenariusze depegu. Brak wiarygodnej wyceny lub naruszenie wcześniej zapisanej polityki ryzyka Q blokuje wejścia. Wyłączenie Q nie uruchamia samodzielnie sprzedaży do innego aktywa.

Profil Q jest artefaktem pierwszego etapu capability/data assessment: tożsamość i typ aktywa, źródła wyceny niezależne od jego nominalnego parytetu, częstotliwość/świeżość, progi i czas potwierdzenia alarmu, reakcja oraz reguła wznowienia. Dla fiat określa kalendarz i dostępność FX; dla stablecoina również kryterium depegu. Wartości są ustalane po wyborze Q według ryzyka instrumentu, przed analizą PnL. Niekompletny profil lub brak działającego monitora blokuje live. Nie ma domyślnego założenia Q=USD ani zastępowania brakującego kursu wartością 1.

## 1. Execution Safety Contract

### 1.1. Autorytet i granice

Łańcuch: Strategy Proposal → Risk Approval + Reservation → Durable Intent → Execution → ExchangeAdapter → Exchange Evidence → Ledger/Reconciliation.

Strategia i ML nie mają kluczy ani bezpośredniego dostępu do adaptera. Risk Approval zawiera instrument, stronę, maksymalną ilość i wydatek, granicę ceny, stop, wersję konfiguracji, identyfikator snapshotu portfela i termin ważności. Approval nie jest ponownie używalnym pozwoleniem. Przed wysłaniem execution w jednej lokalnej transakcji sprawdza aktualność stanu i konsumuje rezerwację; zmiana danych, budżetu lub trybu wymaga nowej oceny.

Emergency exit ma osobną, ograniczoną autoryzację redukcji potwierdzonego inventory. PAUSE blokuje zwiększenie ekspozycji, ale nie blokuje takiego wyjścia. Wyjście nadal podlega kontroli ilości, konkurujących zleceń i spot-only.

Adapter MUST:

- dopuszczać tylko instrumenty sklasyfikowane jako spot w aktualnych metadanych;
- wymuszać SPOT tam, gdzie dany endpoint ma takie pole; dla innych ścieżek potwierdzić równoważne zachowanie;
- odrzucać pola/instrukcje margin, leverage, isolation i borrowing;
- nie udostępniać wypłat, transferów, kredytu ani zmiany finansowania konta;
- operować na posiadanych środkach, nie na buying power zawierającym collateral lub kredyt;
- używać arytmetyki dziesiętnej i jawnych reguł zaokrągleń;
- nie uznawać symbolu, flagi REDUCE_ONLY ani braku pola leverage za samodzielny dowód spot-only.

Konfiguracja konta wykluczająca dług i uprawnienia klucza są osobnymi zabezpieczeniami V. Jeśli nie da się potwierdzić zakazu zadłużenia, konto nie może przejść bramki live.

### 1.2. Osobne obiekty i stany

| Obiekt | Stany / znaczenie |
|---|---|
| Intent | PREPARED, DISPATCHING, SUBMISSION_UNKNOWN, RESOLVED, ABORTED_BEFORE_SEND |
| Order | SUBMITTED, ACKNOWLEDGED, ACTIVE, PARTIALLY_FILLED, CANCEL_PENDING, FILLED, CANCELED, REJECTED, EXPIRED; lokalna projekcja dowodów giełdowych |
| Inventory | Ilość ekonomicznie posiadana, rezerwowana i możliwa do sprzedaży; aktualizowana przez fille, fee i uzgodnione korekty |
| Protection | NONE, PENDING, CONFIRMED, INSUFFICIENT, UNKNOWN, TRIGGERED, EXITING, FAILED |
| Position | ENTERING, OPEN, EXIT_PENDING, FLAT_WITH_DUST, CLOSED; niezależne od sygnałowych etykiet rynku |

ACK nie oznacza przyjęcia przez matching engine, fillu ani ochrony. CANCELED nie oznacza zerowego wykonania. Pozycja powstaje ekonomicznie przy pierwszym fillu; opóźnienie jego obserwacji jest mierzone jako residual exposure. Fille nie mogą znikać wskutek przyjścia starszego statusu order.

### 1.3. Trwałość, retry i pojedynczy właściciel

Przed transmisją jedna transakcja zapisuje intent, niezmienny client order ID, dokładny payload/hash, approval, rezerwację i zadanie wysłania. Baza i giełda nie tworzą wspólnej transakcji; okno niepewności jest częścią kontraktu.

Jeśli proces mógł wysłać żądanie, ale brak jednoznacznego wyniku, stan staje się SUBMISSION_UNKNOWN. Po restarcie nie jest to automatycznie nowe zadanie do wysłania. Execution szuka zlecenia i filli po dostępnych identyfikatorach, uwzględnia opóźnienie widoczności historii i paginację. Pojedyncze NOT_FOUND nie jest dowodem braku zlecenia.

W v1 niejednoznaczny create nie jest automatycznie ponawiany. Retransmisja identycznego intent jest dopuszczalna dopiero po potwierdzeniu zakresu, czasu retencji i semantyki deduplikacji endpointu V. Bez takiej gwarancji unresolved intent pozostaje zarezerwowany i blokuje zwiększenie ekspozycji na koncie do rozstrzygnięcia. Nowe ID służy wyłącznie nowej, odrębnie zatwierdzonej intencji.

Fille są deduplikowane po stabilnej tożsamości giełdowej w zakresie konta/instrumentu; różne kanały tego samego fillu nie tworzą dwóch zapisów ekonomicznych. Korekta fee lub fillu jest odrębnym audytowanym zdarzeniem.

Jeden proces posiada wyłączność na transmisję. Utrata ownership natychmiast wyłącza wysyłkę. V1 nie ma automatycznego failover do drugiego hosta; przed uruchomieniem zastępcy trzeba potwierdzić zatrzymanie/odcięcie starego procesu. Sam lease w bazie nie jest wystarczającym fencingiem procesu, który utracił kontakt z bazą, ale nadal ma dostęp do giełdy.

### 1.4. Rezerwacje i spot accounting

BUY rezerwuje maksymalny wydatek przy granicznej cenie plus fee buffer, a także budżet ryzyka. SELL rezerwuje ilość netto posiadanego aktywa. Rezerwacje zwalniają wyłącznie potwierdzone terminalne wyniki i rozliczenie filli, nigdy sam timeout.

Fee reserve obejmuje wszystkie waluty, w których wybrany profil konta może pobrać opłatę. Nieznana waluta lub nieograniczona wartość fee blokuje nowe wejście do ustalenia profilu. Brak środków na fee nie uruchamia borrowing, konwersji ani zakupu tokena rabatowego. Cumulative filled quantity większe niż suma odebranych trade records jest rozbieżnością do uzgodnienia, a nie dodatkowym fillem księgowanym drugi raz.

Każdy fill zwiększa/zmniejsza inventory i księguje fee w rzeczywistej walucie. Nieznana jeszcze opłata otrzymuje konserwatywną rezerwę. Zamiana rezerwacji pending na inventory risk jest atomowa lokalnie i nie liczy tego samego fillu dwa razy.

Zwykły SELL oraz warunkowy stop nie mogą niezależnie dysponować całą tą samą ilością. Lokalny koordynator wyjść przypisuje wyłączne prawo do sprzedaży. Natywne powiązanie zleceń może zastąpić koordynację tylko po potwierdzeniu jego semantyki V. Niezależne konkurujące zlecenia ochronne nie sumują się jako dodatkowe coverage.

### 1.5. Wejście v1 i partial fills

**D:** baseline wysyła jedno marketable LIMIT FOK po zatwierdzeniu ryzyka. FOK ogranicza zwykłe partial fills wejścia, ale jest dopuszczone wyłącznie po potwierdzeniu działania dla wybranego rynku i konta. Brak FOK oznacza brak live v1 w tej konfiguracji, a nie cichy fallback do GTC/IOC. Koszt utraconych FOK fills jest uwzględniany w badaniach. IOC pozostaje osobnym eksperymentem.

Odrzucenie lub brak wykonania nie uruchamia kolejnych prób pogoni za ceną. Granica ceny i freshness nie są rozszerzane, aby wymusić fill. Brak fillu jest wynikiem setupu.

Jedno w pełni wykonane FOK może składać się z wielu filli, których komunikaty docierają osobno. Pierwszy częściowy komunikat nie jest sam w sobie naruszeniem FOK: natychmiast księguje inventory i uruchamia ocenę ochrony, a reszta nadal zajmuje rezerwację. Incydentem jest potwierdzony terminalny wynik z dodatnią, ale niepełną ilością lub nierozstrzygnięty wynik przekraczający termin: zachować rzeczywiste fille, zablokować wejścia, rozstrzygnąć/anulować resztę, objąć ilość netto ochroną i eskalować. Obsługa częściowych filli oraz częściowych wyjść jest obowiązkowa niezależnie od FOK.

Każdy potwierdzony przyrost inventory wywołuje natychmiastową ocenę coverage. Nie czeka się na pełne wykonanie parent order ani okresowy reconciliation. Attached protection nie jest liczone jako aktywne, zanim giełdowe warunki jego aktywacji faktycznie zajdą.

Aktualizacje ochrony są serializowane na instrumencie. Nowy fill aktualizuje target quantity, lecz nie wysyła drugiej pełnej ochrony, gdy pierwsza jest PENDING/UNKNOWN. Każdy niepokryty przyrost ma własny czas początku ekspozycji; kolejny fill ani retry nie resetuje terminu najstarszej niechronionej ilości. Jeśli mały pierwszy fill nie spełnia minimum ochrony, pozostaje jawnie niechroniony i podlega temu samemu deadline; nie wolno go przemilczeć ani dokupić do minimum. Powiększenie coverage stosuje reguły replacement z §1.7 i mierzy powstałą lukę.

### 1.6. Definicja ochrony i okna ryzyka

Protection CONFIRMED oznacza: rozpoznane zlecenie aktywne po stronie giełdy, właściwy instrument/strona/ilość/trigger, dozwolony tryb spot, dostępne środki i brak konfliktu rezerwacji. To stan warunkowej ochrony, nie gwarancja wykonania lub ceny.

Pokrywana ilość nie może przewyższać posiadanego inventory netto. Ochrona nie jest zwiększana przez podwójne zliczenie OCO lub kilku niezależnych zleceń na te same środki. Coverage UNKNOWN po utracie dostępu nie jest automatycznie uznawane za NONE ani CONFIRMED.

**D — profil operacyjny do sprawdzenia na danych i testach:**

| Parametr | Wartość projektowa | Reakcja |
|---|---|---|
| Freshness book dla nowego wejścia | maks. 1 s od odbioru ostatniego poprawnego snapshotu/update oraz maks. 1 s zweryfikowanego opóźnienia źródła z uwzględnieniem niepewności zegara | blokada wejść na instrumencie |
| Ważność decyzji wejścia | maks. 2 s od udostępnienia kompletnej świecy sygnałowej | sygnał wygasa |
| Brak rozstrzygnięcia submit | 5 s bez terminalnego dowodu lub jawny timeout wcześniej | UNKNOWN blokuje wejścia natychmiast po rozpoznaniu niepewności; 5 s to deadline eskalacji, nie okres swobodnego handlu |
| Potwierdzenie ochrony | cel ≤2 s od odebrania fillu | natychmiast wysyłka ochrony; po 2 s eskalacja |
| Brak potwierdzonej ochrony | 5 s od odebrania fillu | procedura emergency exit i blokada wejść |
| Reconciliation period | 10 s w zdrowym stanie; także event-driven po incydencie | przy braku udanego uzgodnienia przez 30 s operational pause |

Nie są to gwarancje giełdy. Oddzielnie mierzy się fill-time → received-time oraz received-time → protection-confirmed. Utrata sieci może spowodować przekroczenie czasu i nominału ekspozycji bez ochrony. Te przekroczenia są rejestrowane i modelowane; przywrócenie ochrony nie kasuje incydentu. Parametry operacyjne można zmienić na podstawie pomiarów infrastruktury i review, nie w celu podniesienia PnL backtestu.

Terminy używają monotonic clock w działającym procesie oraz trwałych UTC deadline do recovery. Po restarcie upłynięty termin działa od razu; nie rozpoczyna się nowe 5 s. Nieznane opóźnienie źródła lub niezaufany zegar oznacza blokadę nowych wejść. Nominał przejściowo niechroniony jest ograniczony zaakceptowaną wielkością wejścia; każde dodatnie uncovered inventory blokuje następne wejście do rozstrzygnięcia ochrony. Nie jest to gwarancja, że luka zakończy się w 5 s.

Jeśli cena przekroczyła invalidację zanim ochrona została potwierdzona, system przechodzi do wyjścia, zamiast wystawiać stop z nieaktualnym triggerem. Przed wyjściem trzeba rozstrzygnąć zlecenia ochronne, które mogły zostać przyjęte mimo timeoutu.

Dust to jawnie ewidencjonowana reszta, której nie można legalnie sprzedać lub chronić ze względu na tick/minimum. Nie jest traktowana jako CLOSED ani jako chroniona. Nie wolno automatycznie dokupować do minimum. **D:** tolerancja techniczna zwykłego dustu ≤0,1% equity łącznie; większa reszta blokuje wejścia i wymaga obsługi incydentu. Gap może wytworzyć większą niesprzedawalną resztę — limit nie jest gwarancją.

### 1.7. Wyjście i zmiana stopa

Baseline: stały stop-market exchange-side, o ile dostępny i zweryfikowany dla rynku. STOP_LIMIT nie jest cichym substytutem. Dla zamierzonych wyjść czasowych/strukturalnych koordynator najpierw ustala stan istniejącego stopa.

Preferencja: zweryfikowane natywne powiązanie lub operacja atomowa V. Jeśli jej brak, kontrakt dopuszcza sekwencję cancel → terminal confirmation + fills reconciliation → market sell pozostałej ilości. Okno bez ochrony jest jawne, monitorowane według tych samych terminów i testowane z awarią po każdym kroku. Nie wysyła się drugiej sprzedaży przy nierozstrzygniętym stanie pierwszej. Jeśli nie można zaakceptować tego residual risk, dana ścieżka execution nie kwalifikuje się do live; nie deklarujemy nieistniejącej atomowości.

V1 ma jedną wybraną w ExecutionProfile politykę wyjścia: NATIVE_LINKED_VERIFIED albo SERIAL_CANCEL_THEN_MARKET_VERIFIED. Brak wsparcia pierwszej nie przełącza w locie na drugą. Druga wymaga własnych testów, jawnego wyboru przed startem i akceptacji residual risk przed live. Podczas utraty bazy po cancel automat nie wysyła nieaudytowanej sprzedaży; luka jest incydentem, który może trwać do recovery. Nie istnieje ukryty emergency writer omijający ledger.

Przy częściowej sprzedaży remaining inventory podlega ponownej ochronie albo dalszemu zamykaniu według aktualnego stanu. Po terminalnym pełnym wyjściu usuwa się osierocone zlecenia ochronne i potwierdza ich brak. Re-entry jest zabronione, dopóki poprzednia sprzedaż i ochrona są unresolved.

### 1.8. Recovery

Każdy start jest RECOVERING; live flag nie omija recovery. Kolejność:

1. Potwierdzić ownership, tożsamość konta, środowisko, uprawnienia i konfigurację.
2. Odtworzyć trwałe intencje, rezerwacje i DD latch; nie wysyłać starych zadań automatycznie.
3. Uruchomić prywatne strumienie i buforować komunikaty; pobrać salda, zwykłe/advanced orders oraz historię od checkpointu z nakładką.
4. Zdeduplikować fille i scalić snapshoty/zdarzenia z uwzględnieniem niespójnych czasów odczytu; powtarzać odczyt do uzyskania spójnego stanu.
5. Rozstrzygnąć wszystkie intencje UNKNOWN oraz reconcile inventory, fee i rezerwacje. Niewyjaśnione różnice nie stają się automatycznie korektą salda.
6. Sprawdzić ochronę, dust i osierocone zlecenia; ograniczyć nowe ryzyko, obsłużyć znane inventory.
7. Dopiero po udanym uzgodnieniu i świeżych danych dopuścić nowe wejścia, jeśli DD latch i operational state na to pozwalają.

Checkpoint jest zatwierdzany dopiero razem z trwałym ledgerem. Odczyt historii obejmuje nakładające się zakresy, stabilne identyfikatory i wszystkie strony. Taki sam timestamp nie jest unikalnym ID. Przerwa przekraczająca retencję historii lub utrata audytu blokuje automatyczne wznowienie. Obce zlecenia, depozyty i manualne transakcje są incydentem do uzgodnienia; bot nie przejmuje ich ani nie anuluje w ciemno.

## 2. Failure Matrix

Tryb ryzyka i tryb operacyjny są niezależne. Obowiązuje bardziej restrykcyjne uprawnienie do zwiększania ekspozycji. Przy każdym zatrzymaniu wejść należy objąć nim także oczekujące BUY; wysłany cancel nie oznacza, że zlecenie nie zdąży się wykonać.

| Zdarzenie | Nowa ekspozycja | Istniejące zlecenia/inventory | Warunek wznowienia |
|---|---|---|---|
| Stale/gap/crossed book jednego rynku | blokada tego rynku | zachować znaną ochronę; nie wyliczać nowego stopa z wadliwych danych | nowy poprawny snapshot, ciągłość i świeżość |
| Brak wspólnych danych quote/equity | blokada całego konta | utrzymać ochronę; risk state UNKNOWN | wiarygodna wycena i reconciliation |
| Zerwany publiczny WS | blokada dotkniętych rynków | ochrona exchange-side pozostaje, jeśli potwierdzona; czasowe wyjścia przez zdrowe prywatne API | reconnect, snapshot, warmup |
| Zerwany prywatny WS | blokada całego konta | uzgadniać REST z limitem zapytań; nie zakładać braku filli | odtworzenie streamu i reconciliation |
| REST nie działa, private WS działa | blokada nowych wejść w v1 | dozwolona tylko wcześniej zweryfikowana ścieżka ochrony/wyjść przez WS | pełne uzgodnienie po odzyskaniu REST |
| Submit/cancel timeout lub niejasne 5xx | blokada zwiększania ekspozycji konta | UNKNOWN, zachować rezerwacje, szukać dowodów | jednoznaczny wynik i accounting |
| Jawne odrzucenie wejścia bez fillu | brak ponowienia tego setupu | zwolnić środki po potwierdzeniu terminalności | kolejny niezależny setup, jeśli brak incydentu systemowego |
| Stop odrzucony/niewystarczający | blokada całego konta | anulować resztę wejścia, obsłużyć emergency exit po rozstrzygnięciu konkurujących orders | latch incydentu zdjęty po review |
| Terminalne niepełne wykonanie FOK | blokada całego konta | księgować, chronić/wyjść, zbadać semantykę; pojedynczy częściowy komunikat nie wystarcza do stwierdzenia naruszenia | review adaptera i dowód poprawności |
| Rate limit | blokada nowych wejść przy zagrożeniu terminów | priorytet: ochrona, unknown resolution, wyjścia; backoff z jitterem, bez retry storm | odbudowany budżet API i świeże reconciliation |
| Baza niedostępna/pełny dysk | żadnych nowych komend zwiększających ryzyko | nie wysyłać nieaudytowanych mutacji; zachować exchange-side protection, alarm z niezależnej ścieżki | baza, replay i recovery |
| Crash/restart hosta | RECOVERING | istniejące zlecenia mogą nadal działać | procedura 1.8 |
| Drugi writer/utrata ownership | żadnych nowych komend z procesu bez ownership | nie wykonywać failover przed odcięciem poprzednika | fencing i reconciliation |
| Dryf zegara/auth/key revoked | blokada wejść | nie usuwać ochrony; nie próbować innych uprawnień | naprawa czasu/autoryzacji i recovery |
| Saldo/order spoza bota | blokada konta | zachować dowody; nie sprzedawać obcych aktywów automatycznie | wyjaśnienie własności i uzgodnienie |
| Trading halt/delist/maintenance | brak wejść; anulować oczekujące gdzie możliwe | ochrona może nie wykonać się; wyjście tylko gdy handel dostępny | explicit status + reconciliation; delist wymaga review |
| Awaria całej giełdy | brak wejść | brak gwarancji wyjścia; alarm i zapis ostatniego znanego stanu | recovery, ocena gap loss i ochrony |
| Zmiana tick/minimum/fee/status | wstrzymanie rynku do walidacji | ocenić legalność stopów i pozostałego inventory | aktualne metadata i poprawne zlecenia |
| ML/news niedostępne | brak wpływu na baseline v1 | zapisać brak wyniku shadow | wznowienie obserwacji; bez wpływu na execution |
| Telegram niedostępny | istniejące safety działa | retry alertów poza kolejką execution; lokalny alarm health | dostarczenie zaległych krytycznych alarmów; brak kanału alarmowego przy incydencie blokuje nowe wejścia |
| DD ≥12% | trwały HARD PAUSE | anulować oczekujące wejścia; nadal zarządzać pozycjami | obowiązkowe review i jawna autoryzacja |

Rutynowe problemy danych mogą wracać automatycznie po świeżym snapshotcie, udanym reconciliation i **D: 60 s stabilnego zdrowia**. Incydenty ochrony, dublowania, księgowości i granica DD pozostają zatrzaśnięte do review. Auto-restart procesu nie usuwa żadnego latch.

PAUSED: blokada wejść, kontynuacja management. STOP_REQUESTED: kontrolowane przejście do stanu bez ryzyka według wybranej polityki wyjścia, następnie shutdown. STOPPED: brak procesu; nie oznacza automatycznie braku giełdowych orders. EMERGENCY_FLATTEN: jawne żądanie redukcji potwierdzonego inventory, podlegające koordynacji wyjść; nie jest synonimem HARD PAUSE i nie gwarantuje natychmiastowego zamknięcia.

## 3. Risk Contract

### 3.1. Equity, high-water mark i progi użytkownika

Equity E uwzględnia wolne i zarezerwowane środki, inventory netto, należne fee oraz koszt wykonalnej sprzedaży inventory do jednostki rozliczeniowej. Nie wolno sumować osobno salda i tej samej pozycji. Mid-price nie jest domyślną ceną likwidacji. Przy zbyt płytkiej książce wycena ma status niepewny i scenariusz stresowy; brak oferty nie oznacza pozycji wartej ostatnią cenę bez ograniczeń.

Wpłaty/wypłaty neutralizuje unitized NAV: liczba jednostek zmienia się według NAV przed przepływem; wynik na jednostkę i high-water mark nie zmieniają się od samego przepływu. W v1 external funding odbywa się w pauzie po reconciliation, bez automatycznych transferów bota. Opłaty handlowe należą do strategii; koszty hostingu są raportowane osobno jako wynik działalności.

HWM = najwyższy poprawny NAV od początku epoki oceny; DD = 1 − NAV/HWM. HWM, NAV, latch i stan trybu są trwałe. Pomiar **D: co 1 s i po każdym zdarzeniu finansowym**. Nie resetuje się go codziennie, po restarcie ani po wpłacie. Krótkie przekroczenie pomiędzy obserwacjami jest możliwe i raportowane jako ograniczenie próbkowania. Brak wiarygodnego NAV powoduje operational pause, nie poprawę DD.

| Próg użytkownika | Tryb | Wpływ projektowy D |
|---|---|---|
| DD <6% | NORMAL | mnożnik nowego ryzyka 1,00 |
| DD ≥6% | CAUTION | 0,70 |
| DD ≥9% | DEFENSIVE | 0,40 |
| DD ≥12% | HARD PAUSE | 0; obowiązkowe review przed wznowieniem |

Przekroczenie kilku progów naraz daje od razu najostrzejszy tryb. Pogorszenie jest natychmiastowe po poprawnej obserwacji. Mnożnik dotyczy nowych zatwierdzeń i limitu łącznego nowo dopuszczanego ryzyka, nie rozszerza stopów.

Tabela określa progi eskalacji, nie bezstanowe przypisanie trybu przy każdym ticku. Stan CAUTION/DEFENSIVE utrzymuje się po zejściu poniżej 6/9%, dopóki nie zajdzie reguła histerezy. NORMAL przy DD<6% jest stanem początkowym lub wynikiem zatwierdzonej deeskalacji; nie nadpisuje zatrzaśniętego HARD PAUSE.

**D — histereza:** DEFENSIVE → CAUTION dopiero po DD <8% przez 6 h poprawnych obserwacji; CAUTION → NORMAL po DD <5% przez 6 h. Poprawa odbywa się o jeden poziom; nowy próg pogorszenia ma pierwszeństwo, utrata wiarygodności przerywa czas stabilizacji. Niższy limit po przejściu trybu nie wymusza samoistnej sprzedaży pozycji; blokuje dalsze ryzyko, a istniejące pozycje zachowują wcześniej zdefiniowane wyjścia. Rzeczywiste naruszenie inventory/cash limits jest odrębnym incydentem.

HARD PAUSE jest zatrzaśnięty nawet po odbiciu equity. Brak automatycznego wznowienia po czasie, zysku shadow lub restarcie. Review zawiera rozkład strat, wykonanie, residual risk, zgodność danych i ocenę hipotezy. Wznowienie tej samej epoki jest możliwe po review dopiero poniżej 12%, co najmniej w DEFENSIVE. Jeżeli DD pozostaje ≥12%, potrzebna jest jawna decyzja o nowej epoce, punkcie odniesienia i limicie kapitału; historia poprzedniej epoki oraz lifetime DD pozostają w raporcie. Nowa epoka nie służy automatycznemu odnawianiu budżetu strat.

### 3.2. Sizing

**Zatwierdzone research/operational defaults — stały profil pierwszego porównania A/B:**

- Base risk per entry: 1,00% equity × mnożnik trybu.
- Łączna planowana strata open + pending: maks. 2,00% equity × mnożnik trybu przy dopuszczaniu nowego wejścia.
- Maks. 2 jednoczesne pozycje lub niezakończone wejścia.
- Maks. nominał jednego aktywa: 50% equity; łączna ekspozycja open + pending: 90% equity; minimum 10% rezerwy Q po fee commitments.
- Brak podnoszenia wielkości na podstawie ML confidence, ostatnich zysków lub etykiety „exceptional”.

Te defaults nie są niezmiennymi parametrami przyszłego bota i nie stanowią obietnicy drawdownu. Ich wpływ jest raportowany; nie są przeszukiwane wraz z parametrami alpha w pierwszym porównaniu. Zmiana wymaga wersji risk profile i osobnego review, przed kolejną oceną OOS.

Wyznaczyć największą legalną ilość q spełniającą wszystkie ograniczenia. Planowana strata obejmuje: debit wejścia z fee minus ostrożny credit przy wykonaniu stopa z exit fee, spreadem, impact i opóźnieniem. Model kosztu zależy od q; gdy jest nieliniowy, nie stosować prostego dzielenia przez stały procent.

Równoważnie, dla znanej ilości netto: L_plan = koszt nabycia brutto − wpływ ze sprzedaży netto w scenariuszu stopa. Osobno liczyć pozostałe ryzyko bieżące do stopa względem aktualnej wyceny; dla pending liczyć pełne zaakceptowane ryzyko. Zysk jednej pozycji nie jest ujemnym ryzykiem kompensującym drugą. W v1 nie ma ADD ani trailing pozwalającego „uwolnić” budżet przez niepotwierdzony stop.

Do testu agregatu 2% dla każdego open inventory stosować max(0; koszt nabycia pozostałej ilości wraz z przypisanymi fee − wpływ netto przy stopie; aktualna wartość likwidacyjna netto − wpływ netto przy stopie). Pending/unresolved dokłada niewypełnioną część zaakceptowanego planowanego ryzyka; po fillu ta część przechodzi do open, bez podwójnego liczenia. Chroni to również przed nowym wejściem finansowanym pozornie „wolnym” budżetem, gdy istniejąca pozycja ma duży niezabezpieczony zysk do oddania. Wzrost mark-to-stop risk albo nominału wskutek cen ponad limit nie wymusza automatycznie sprzedaży zwycięzcy: blokuje nowe ryzyko i jest raportowany. Przekroczenie wskutek komendy, utraty kontroli nad ilością lub błędu rezerwacji jest incydentem.

Ilość zaokrągla się w dół, a następnie ponownie sprawdza cenę, fee i minimum. Jeśli zwiększenie do minimum narusza limit, transakcja odpada. Przed wejściem należy sprawdzić możliwość sprzedaży/ochrony także przy zakładanej invalidacji; nie da się zagwarantować minimum po dowolnym gapie.

Model wejścia używa limit price jako górnej granicy wydatku. Model stopa uwzględnia co najmniej koszt agresywnego wyjścia i ogon poślizgu oszacowany wyłącznie na danych dostępnych przed ocenianym okresem. **D:** do sizingu używać fee taker oraz 99. percentyla łącznego niekorzystnego execution shortfall względem triggera z właściwego bucketu płynności/zmienności. Percentyl obejmuje spread, impact i latency, więc nie dodaje się ich ponownie jako tych samych kosztów. Manifest kalibracji określa źródło próby, liczebność, okres, definicję bucketów i niepewność; gdy 99. percentyla nie da się wiarygodnie oszacować, profil nie przechodzi live. Do średniej expectancy używa się całego rozkładu wykonania, a nie wyłącznie percentyla sizingu. Bez wiarygodnego modelu kosztów można prowadzić screening i shadow, ale nie zatwierdzać live.

### 3.3. Korelacja i wspólne ryzyko

W v1 wszystkie crypto longs traktuje się jako wspólną ekspozycję rynkową; brak kredytu dywersyfikacyjnego z niskiej korelacji krótkiego okna. Ten sam token w różnych parach jest jednym aktywem. Raport dodatkowo pokazuje beta/correlation, wspólne sektory i koncentrację, ale nie zwiększa przez nie limitu ryzyka.

Scenariusze obowiązkowe: wszystkie aktywa spadają jednocześnie; spread i impact rosną wspólnie; quote asset traci wartość w fiat; brak możliwości wyjścia przez ustalony okres. Raport zawiera stratę całego portfela, pozostałą płynność, dust oraz przekroczenia 12%. Nie zakłada niezależnego wykonania stopów na historycznych cenach.

Warstwa zwykłych kosztów sizingu i warstwa residual stress są oddzielne. Nie deklarujemy, że limity stop-risk ograniczają gap loss do 2% lub DD do 12%. Scenariusze i wyniki residual risk wymagają jawnej akceptacji przed live; dokument nie przypisuje użytkownikowi nieuzgodnionego maksymalnego gap loss.

### 3.4. Re-entry i epizody

Setup ID jest deterministycznie związany z instrumentem, czasem wybicia i zamrożonym zakresem. Warianty A/B mają wspólny identyfikator porównawczy, ale niezależne portfele badawcze. Jeden wariant nie ma więcej niż jednego wejścia na setup.

**D:** epizod obejmuje pierwszą próbę i kolejne próby tego aktywa w następujących 24 h; maks. 2 wykonane wejścia oraz suma bezwzględnych strat zakończonych trade’ów + ryzyko otwarte/pending ≤2 × początkowy risk budget epizodu. Wynik każdego trade’u jest liczony netto po kosztach, ale zyskowne trade’y nie odejmują wcześniejszych strat z budżetu. Zmniejszenie equity/ostrzejszy tryb może limit tylko obniżyć. Po wyjściu minimum 3 zamknięte świece 5m i nowy setup. Próba bez żadnego fillu nie jest transakcją, lecz kończy dany setup. Nowy identyfikator setupu nie resetuje epizodu. Koniec 24 h nie zwalnia ryzyka otwartej pozycji; nowy epizod może zacząć się dopiero po zakończeniu poprzednich orders i reconciliation.

## 4. Point-in-Time Data Contract

### 4.1. Czas i dostępność

Każde zdarzenie przechowuje: źródło, konto/rynek, stabilne ID i sekwencję jeśli dostępne, event_time, received_at UTC, lokalny monotonic receive offset, available_at, wersję schematu oraz raw payload lub odnośnik do niego. available_at jest chwilą, od której kompletne dane po walidacji mogły zostać użyte przez decyzję. Nie może poprzedzać odbioru ani opublikowania wymaganych zależności.

Feature przy decyzji t może zależeć wyłącznie od rekordów z available_at ≤ t. Event-time służy do grupowania zdarzeń, ale nie daje prawa do cofnięcia ich dostępności. As-of joins nie używają najbliższej późniejszej obserwacji. Zmiana danych po t nie może zmienić historycznej decyzji odtworzonej w trybie as-known.

### 4.2. Świece i brakujące dane

Świece mają przedziały półotwarte UTC, np. [10:00,10:05). W baseline używa się tylko zamkniętych, zwalidowanych świec. **D:** świeca budowana z transakcji jest publikowana po 1 s watermark od końca przedziału, jeżeli feed jest ciągły i świeży. Źródło giełdowe musi mieć równie jawną regułę finalności. Ten sam tryb budowania obowiązuje live/replay.

Późne trade’y tworzą rewizję z nowym received_at/available_at; nie zmieniają dawnych decyzji. Features kolejnych decyzji mogą użyć znanej wtedy rewizji. Snapshot cech zapisuje konkretną wersję danych. Świeca godzinowa o 10:07 nie jest świecą zamkniętą; baseline nie używa finalnego high/close tej godziny.

Brak transakcji na zdrowym feedzie i brak danych wskutek awarii to różne stany. Wyłącznie pierwszy może tworzyć jawnie oznaczoną świecę z zerowym wolumenem. Nie wolno uzupełniać outage zerowym wolumenem ani przyszłą ceną. Wymagany ciągły lookback musi być kompletny; po outage warmup trwa do odzyskania wystarczających poprawnych danych.

Historyczne OHLCV bez czasu odbioru otrzymują oznaczenie modeled availability i jawny latency model. Nie są pełnym dowodem zgodności PIT, a użyte proxy latency podlega stress testing.

### 4.3. Universe i radar

Instrument registry jest wersjonowany w czasie: listing, pierwszy możliwy handel, halt/delist, base/quote, mapowanie tokena/migracji, tick, minima, status konta i uprawnienia rynku. Universe nie jest dzisiejszą listą monet cofniętą w czasie. Kategorie nie pochodzą z wiedzy o przyszłym sukcesie aktywa.

**D/R — pierwszy profil kwalifikacji:** jeden Q; spot tradable; co najmniej 30 dni od pierwszego rzeczywistego handlu i 30 dni kompletnej historii wymaganych świec; rolling 24 h volume ≥1 mln równowartości USD liczone z ówczesnych danych FX; spread ≤30 bp w chwili decyzji; notional order ≤1% poprzedniego 5m quote-volume i ≤5% widocznej głębokości po stronie wejścia w pasmie 50 bp. To początkowy profil badawczy, nie potwierdzone optimum ani uniwersalna definicja płynności.

Warunki kosztów, dostępności ochrony i legalnej wielkości nadal mają pierwszeństwo. Wiek 30 dni jest operacyjną granicą „dojrzałego universe” v1; sama etykieta NEW LISTING nie upoważnia do wcześniejszego live.

Radar zapisuje wynik kwalifikacji i reason codes wszystkich ocenianych rynków. Archiwizacja świec/tradeów i statusów obejmuje możliwie całe dostępne universe oraz nowe listingi. Włączenie L2 dopiero po shortlistingu nie upoważnia do twierdzenia, że historycznie znamy koszty wszystkich kandydatów. Rynki bez L2 mają jawny niższy poziom danych; przed nowym wejściem potrzebny jest poprawny aktualny snapshot i sprawdzona polityka kosztów.

Coverage report zawiera także delisted/failed assets, luki i rynki wykluczone z powodu braku danych. Nie ekstrapoluje się wyniku kompletnej podgrupy na nieobserwowaną część rynku.

### 4.4. Features, news i ML

Range/pivot musi określać czas potwierdzenia. Baseline używa rolling highs/lows znanych świec, bez pivotów potwierdzanych przyszłością. VWAP jest narastający do decyzji. MFE/MAE przyszłego okresu służy tylko etykietom; jako feature wolno użyć wyłącznie zakończonego historycznego okna.

News zachowuje published_at, first_seen_at, ingested_at, wersję treści i mapowania aktywa. Pierwsza dostępność jest co najmniej first_seen/ingested, nie tylko deklarowany czas publikacji. Edycja źródła nie przepisuje historii. News w v1 nie wpływa na wykonanie.

Preprocessing, selekcja cech, kalibracja oraz modele kosztów są dopasowywane wewnątrz dozwolonej części folda. Podział wszystkich aktywów odbywa się na tych samych granicach czasu. Feature provenance musi wskazywać użyte zakresy i wersje.

### 4.5. Replay i tożsamość eksperymentu

Zapisywane są kolejność odbioru/przetwarzania, timer events, konfiguracja obowiązująca w danej chwili, checkpointy, model hash, seed, dependency versions i manifest danych. Decyzje zawierają snapshot features, approval, rezerwację, action/reason codes i powiązania do zleceń/filli/ochrony.

Replay as-known reprodukuje decyzje przy tej samej kolejności zdarzeń i wersji logiki. Symulacja filli jest osobną warstwą i nie staje się „prawdziwym replay” tylko dlatego, że jest deterministyczna. Błędy i korekty finansowe zapisuje się addytywnie, nie przez ciche nadpisanie historii.

## 5. Backtest Execution Contract

### 5.1. Poziomy wiarygodności

| Poziom | Dane | Dozwolony wniosek |
|---|---|---|
| B0 | OHLCV + historyczne metadata/fee, modelowane koszty i latency | screening hipotezy i analiza wrażliwości; niewystarczający do live |
| B1 | trades + BBO/L2 z informacją dostępności i pokrycia | replay wykonalności w granicach obserwowanych danych |
| B2 | live market capture + paper/shadow + kontrakt API/UAT | walidacja pipeline i szacunku execution; nadal bez gwarancji produkcyjnych filli |
| B3 | ograniczony live canary | pomiar rzeczywistej semantyki i kosztów; nie dowód skalowalnego edge |

Źródło danych z innej giełdy nie jest substytutem historii wykonania na Crypto.com. Może wspierać eksplorację, z jawnym oznaczeniem domain mismatch.

### 5.2. Oś zdarzeń

Zdarzenie rynkowe → dostępność danych → wyliczenie cech → decyzja → risk/commit → network delay → przyjęcie giełdowe → matching → fill → user-feed delay → księgowanie → wysłanie/aktywacja ochrony.

Każda faza ma czas. Użycie ceny w chwili sygnału jako fillu jest zabronione, jeśli matching następuje później. Baseline close jest informacją do decyzji, nie gwarantowaną ceną transakcji. Symulator odtwarza również brak ochrony przed jej przyjęciem.

Opóźnienia są mierzone lub jawnie modelowane; testy obejmują zależność opóźnienia od dużej zmienności. Nie losuje się niezależnego taniego slippage w momentach wspólnego zaniku płynności. Brak empirycznej kalibracji ogranicza poziom dowodu do B0.

### 5.3. Wejścia i wyjścia

FOK LIMIT: fill tylko jeśli po dotarciu zlecenia da się wykonać całą ilość w granicy ceny na wiarygodnej książce. Crossing używa ask dla BUY, bid dla SELL. Głębokość jest pomniejszana o konserwatywny haircut kalibrowany na dostępnych wcześniejszych danych; nie zakłada się, że widoczne quote’y pozostaną dostępne. Z próbkowanych snapshotów nie odtwarza się nieznanej kolejki. Niepewne wykonania są raportowane scenariuszowo, a nie automatycznie uznawane za sukces.

Baseline nie korzysta z pasywnych wejść maker. Późniejszy eksperyment maker wymaga modelu kolejki, anulowań i adverse selection; touch price nie wystarcza. Dla baseline zakłada się fee taker, chyba że rzeczywiste dowody konkretnego wykonania pozwalają zaksięgować inaczej.

Stop: uruchamiany dopiero przez wybrane i zweryfikowane źródło triggera. Cena fillu pochodzi z następnej wykonalnej płynności po triggerze i opóźnieniu, nie z poziomu stopa. Brak danych referencyjnych triggera nie jest uzupełniany bez oznaczenia przez last trade. Brak płynności/trading halt może pozostawić inventory otwarte.

Wyjścia obejmują cancel/protection race, partial fills, fee w base, precision, minima, price bands i dust. Niewykonana część nie znika z portfela. Nie stosuje się retrospektywnej sprzedaży przed delistingiem, jeśli wtedy nie było znanego sygnału/komunikatu i wykonalnego rynku.

### 5.4. OHLCV i niejednoznaczność

W B0 wejście następuje najwcześniej po dostępności świecy sygnałowej, z modelem latency i kosztów. Jeżeli w jednej świecy może zajść kilka kolidujących zdarzeń, wynik nie jest wybierany pod zysk: raportuje się przedział ścieżek, a do podstawowego screeningu używa ustalonej niekorzystnej kolejności. Pozytywny wynik tylko na korzystnej ścieżce nie przechodzi bramki.

Nie rozstrzyga się retestu intrabar z przyszłą kolejnością high/low. Definicja baseline używa potwierdzenia na close. B0 nie potwierdza FOK availability, szybkich stopów ani New Listing Mode.

### 5.5. Koszty i portfolio

Ledger księguje osobno fee w rzeczywistej walucie, spread, impact, latency slippage i FX. Jeśli bid/ask jest już ceną wykonania, nie odejmuje się spreadu ponownie. Historyczne fee/tier/promocje muszą mieć wersję czasową; dla planowanego małego konta stosuje się koszty osiągalne bez nieuzgodnionych rabatów i dodatkowego stakingu.

Jedna symulacja wariantu ma wspólny cash, approvals i rezerwacje. Nie sumuje niezależnych wyników trade’ów, których nie można było jednocześnie sfinansować. Każdy odrzucony setup otrzymuje powód: brak edge rule, risk, cash, koszt, brak danych, brak fillu lub ograniczenie operacyjne.

Raport oddziela wynik strategii przed hostingiem od ekonomicznego wyniku po infrastrukturze. Automatyczne reinwestowanie odbywa się tylko w przydzielonym kapitale i limitach; brak automatycznego zasilania konta.

### 5.6. Stress suite

Obowiązkowe: 1,5× fee, 2× modelowanego slippage, szerszy spread, spadek dostępnej głębokości, missed fills, opóźnione wejście/wyjście, parametr perturbation oraz kombinacje tych zjawisk. Dodatkowo scenariusze opisowe, nie prognozy prawdopodobieństwa: wspólny gap −10/−20/−40%, brak handlu 1 min/15 min/2 h/24 h, depeg Q oraz terminalna utrata wartości aktywa. Podczas outage nadal zachodzą ceny i giełdowe fille tam, gdzie giełda faktycznie działa; nie zamraża się rynku razem z botem.

Raport: planowana versus zrealizowana strata, DD oraz overshoot ponad 12%, ilość/czas bez ochrony, nierozstrzygnięte orders, dust, udział niewypełnionych okazji i zależność strat między aktywami. Nie wymaga się rentowności przy terminalnej utracie aktywa; wymaga się jawnej wielkości ekspozycji i mechanizmu straty.

## 6. Baseline Research Protocol

### 6.1. Hipotezy i jednostki oceny

H-A: potwierdzone wybicie po kompresji i wzroście aktywności ma dodatnią expectancy netto na kwalifikującym universe.

H-B: oczekiwanie na retest tego samego wybicia zmienia expectancy oraz wynik portfela na tyle korzystnie, by uzasadnić opóźnienie i utracone okazje.

Nie zakłada się z góry przewagi B ani A. Każdy wariant ma identyczne dane, universe, kapitał, koszty, risk profile, daty oceny i wyjścia. Dwa oddzielne portfele; nie wykonują równocześnie live.

Fill to pojedyncze wykonanie. Order to polecenie giełdowe. Trade obejmuje całe wejście i wszystkie jego wyjścia do flat/dust. Epizod obejmuje serię prób określoną w 3.4. Przedziały niepewności nie traktują filli jednego trade’u jako niezależnych obserwacji.

Główna ocena: expectancy netto na zakończony trade, z uwzględnieniem otwartego inventory na końcu okresu, oraz wynik netto całego portfela w czasie. Wybór wariantu uwzględnia DD, ekspozycję, turnover, fill rate i liczbę okazji. Maksymalizacja średniej z kilku wybranych zwycięzców nie jest funkcją celu.

### 6.2. Jednoznaczny baseline R0 — propozycja badawcza

Wszystkie liczby poniżej to R, nie twierdzenia o skuteczności. Timeframe: 5m. Brak dodatkowego filtra trendu BTC, news, ML, FOMO score i regime classifier w R0.

Dla właśnie zamkniętej świecy t:

- W = poprzednie 12 zamkniętych świec t−12…t−1.
- H = max high w W; L = min low w W.
- A = średnia arytmetyczna true range poprzednich 72 świec do t−1, z poprzednim close do obliczenia pierwszego TR; A musi być dodatnie.
- Kompresja: (H−L)/A ≤3,0.
- Wzrost aktywności: quote-volume świecy t / mediana quote-volume poprzednich 20 świec ≥1,5; mianownik dodatni.
- Potwierdzenie wybicia: close_t > H + 0,1A.
- Invalidation S = L −0,1A, dodatnia i niższa od dopuszczalnego wejścia.

H, L, A, S i breakout time zostają zamrożone dla setupu. Wybicie nie jest oceniane na zakresie zawierającym świecę t. Dane i eligibility muszą być poprawne w chwili decyzji.

**A — confirmed breakout:** po dostępności t natychmiast przedłożyć kandydata do najbliższego batcha z sekcji 6.3, a po rankingu do risk/execution. Maksymalna cena BUY = close_t + min(0,25A; 0,005 × close_t), zaokrąglona w dół do tick; wszystkie ograniczenia ryzyka i kosztów są nadal sprawdzane. Jeśli bieżący ask przewyższa limit lub sygnał wygasł, nie wchodzić.

**B — breakout + retest:** obserwować maksymalnie kolejnych 6 zamkniętych świec. Pierwsza świeca u kwalifikuje retest, jeśli low_u ≤ H+0,1A, low_u ≥ H−0,25A i close_u > H+0,1A. Wcześniejsza obserwacja ceny ≤S lub zamknięcia <H−0,25A anuluje setup. Pierwszy kwalifikujący close daje wejście z identyczną formułą limitu, lecz close_u zamiast close_t i z zamrożonym A. Brak retestu w oknie oznacza brak trade’u. Nie przeszukuje się późniejszych świec dla lepszego wejścia.

W B nie wymaga się drugiego skoku volume na retest; aktywność została potwierdzona na wybiciu. Jego dodanie byłoby osobną hipotezą. Brak fillu kończy setup w obu wariantach.

**Wspólne wyjścia:**

1. Exchange-side stop-market przy stałym S, źródło triggera zgodne z wybranym profilem giełdy V.
2. Soft exit po pierwszym zamknięciu 5m poniżej H−0,25A po wejściu.
3. Time exit przy 24. planowej granicy 5m ściśle po czasie pierwszego fillu; trwały deadline jest wyznaczony od razu, niezależnie od dostarczania danych świecowych. Obejmuje około 115–120 min od fillu. Brak świec nie przedłuża holding time. Jeśli fill został wykryty po deadline, wyjście jest należne natychmiast po bezpiecznym ustaleniu inventory.
4. Wyjście awaryjne zgodnie z kontraktem operacyjnym.

Brak fixed take-profit, trailing, ADD i dobrowolnego partial exit. Priorytet: faktyczne zdarzenia giełdowe/stop → emergency → soft → time; jedna aktywna intencja wyjścia. Soft exit oparty na close nie anuluje już wykonanego lub nierozstrzygniętego stopa. Maksymalny planowany holding time nie jest gwarancją zamknięcia podczas outage.

Dla invalidacji setupu B obserwacja ceny oznacza poprawny trade z dostępnością przed decyzją (w B0: ujawnione low zamkniętej świecy). Dla ochrony pozycji obowiązuje osobno źródło triggera z ExecutionProfile. Niezgodność lub brak tych danych ogranicza poziom dowodu; nie wolno wstecznie użyć przyszłego low. Granice S i cen wejścia są kwantyzowane według wybranego tick: BUY limit w dół, SELL stop trigger w górę; następnie ponownie waliduje się relację triggera do bieżącej ceny referencyjnej i przelicza ryzyko. Nieważny wynik kończy setup, zamiast rozszerzać stop dla uzyskania akceptacji.

### 6.3. Równoczesne okazje

Scheduler używa logicznych batchy zamknięć 5m. Po dostępności wymaganych danych zbiera kandydatów do wspólnego cutoff **D: 2 s po końcu świecy**, nie dłużej niż ważność decyzji. Kandydat spóźniony nie korzysta z danych przyszłych ani z drugiej szansy po poznaniu wyniku innych.

Ranking: malejąca relacja wzrostu aktywności na świecy wybicia; potem rosnący szacowany koszt round-trip; potem stabilny identyfikator instrumentu. Dla B używa się zamrożonej relacji aktywności oryginalnego wybicia. Ranking nie patrzy na późniejszy zwrot. Przed każdym przyznaniem kapitału następuje ponowna ocena budżetu wspólnego portfela. Jeżeli dostępne parametry cutoff nie pozwalają zachować wymogów latency, profil jest niewykonalny i wymaga review, nie ukrytej korekty timestampów.

### 6.4. Dane i podziały

Przed oceną należy zamrozić manifest dat, coverage, asset identity, fee i wszystkich parametrów. **D:** docelowo co najmniej 24 miesiące historii dla części badawczej OHLCV; nie przedstawiać jej jako 24 miesięcy B1, jeśli L2 obejmuje krótszy okres. Brak adekwatnej historii oznacza wynik niewystarczający do oceny, a nie ciche osłabienie bramki.

Proponowany schemat: ostatnie 6 miesięcy to zapieczętowany final holdout. Wcześniej rolling walk-forward: 6 miesięcy train/modelowania kosztów, 1 miesiąc validation, 1 miesiąc outer test, krok 1 miesiąc. Wyłącznie okna przed holdoutem. Daty ustala się według pokrycia przed odczytaniem wyników; wszystkie aktywa mają te same granice.

Dla baseline deterministycznego train służy przede wszystkim kalibracji modeli kosztów i ewentualnemu wyborowi wcześniej zadeklarowanych parametrów. Nie dopasowuje się go do wyników outer test. Features mogą używać historii sprzed początku okna; nie wolno resetować wskaźników tak, by dać wariantom różne warunki.

Przy etykietach usuwa się z train/calibration przykłady, których pełny przedział wyniku wchodzi w oceniany okres. Purge wyznacza faktyczny czas końca etykiety, wliczając opóźnione wyjście; embargo odpowiada zdefiniowanym zależnościom. V1 nie uczy się na danych późniejszych od testu.

Portfolio OOS jest symulowane ciągle; DD/HWM nie resetują się na granicy folda. Zmiana konfiguracji dotyczy nowych setupów, a otwarte pozycje zachowują przypisaną wersję zarządzania. Na końcu raportuje się NAV oraz modelowany koszt zamknięcia pozostałego inventory; nie pomija się niezakończonych strat.

Final holdout jest osobnym, wcześniej zadeklarowanym eksperymentem zamrożonej konfiguracji: start flat, NAV=1 i HWM=1, wyłącznie historyczny warmup features. Nie przenosi wybiórczo dochodowych pozycji z development. Raport ujawnia tę granicę oraz oddzielnie ciągły outer OOS; nie łączy ich w jedną krzywą sugerującą nieprzerwany handel. Podczas HARD PAUSE nadal wycenia się inventory i wykonuje zaplanowane wyjścia — nie urywa się raportu na dokładnie 12%. Niewykonalne zamknięcie na końcu okresu pozostaje unrealized/stressed, a nie fikcyjnie zrealizowaną transakcją.

### 6.5. Budżet eksperymentów i statystyka

Najpierw R0-A i R0-B bez strojenia. Następnie jedna zadeklarowana rodzina wrażliwości: lookback zakresu {8,12,16}, próg aktywności {1,5;2,0}, wariant {A,B}: 12 konfiguracji łącznie. Pozostałe parametry pozostają stałe. Wyniki wszystkich prób są zachowane. Nowe filtry/model families oznaczają nową rodzinę badań i nie korzystają z final holdoutu do wyboru.

W development preferowany jest stabilny obszar parametrów, nie pojedynczy najwyższy peak. Jeśli warianty są nierozróżnialne, preferować prostszy operacyjnie A, ale tylko gdy sam przejdzie bramki. Wyboru parametrów w foldzie dokonuje się na train/validation, a outer test ocenia zamrożoną procedurę wyboru. Obejrzenie wyników outer test i zmiana reguł strategii zmienia te wyniki w dane development; nie przedstawiać ich potem jako nietkniętego OOS nowej strategii. Przed otwarciem holdoutu zamrozić dokładnie jedną konfigurację do decyzji go/no-go. Porównanie drugiego wariantu na holdoucie może być wyłącznie wcześniej oznaczoną analizą opisową i nie służy zamianie zwycięzcy po fakcie.

Raport obejmuje paired differences A/B dla tych samych okresów i wspólnych setupów, a także nieparowane okazje B bez fillu/retestu. Nie wolno porównać wyłącznie trade’ów, które wykonały się w obu wariantach.

Uncertainty: block bootstrap wspólnych bloków czasu całego panelu/portfela, z zachowaniem korelacji między rynkami i grupowania epizodów. **D:** przedziały 95%, 10 000 replik, analiza wrażliwości długości bloków 1/3/7 dni. Długość podstawowa wybierana na development według zależności, przed holdoutem. Rekonstrukcja drawdownu z bootstrapu jest analizą niepewności, nie dokładną symulacją odmiennej ścieżki zachowania Governora.

### 6.6. Kryteria wyniku i obalenia

**D — proponowane bramki do zatwierdzenia przed oceną:**

- Dodatnia expectancy netto wybranej konfiguracji w zewnętrznym OOS i final holdout.
- Dolna granica wcześniej określonego 95% przedziału expectancy >0. Przedział przecinający zero oznacza wynik niejednoznaczny, nie sukces.
- OOS i holdout nie osiągają 12% DD w podstawowym modelu wykonania; przekroczenie uruchamia HARD PAUSE w symulacji i review, nie jest „naprawiane” podniesieniem progu.
- Utrzymana dodatnia punktowa expectancy przy wspólnym 1,5× fee i 2× baseline slippage; osobne scenariusze ekstremalne są oceną residual risk, nie wymaganiem zysku w katastrofie.
- Brak krytycznego leakage, nieudokumentowanej luki danych lub dominującego optymizmu wykonania.
- Minimum **D: 200 zakończonych trade’ów i 100 epizodów w zewnętrznym OOS; 50 trade’ów w holdoucie** jest tylko dolnym progiem interpretowalności. Nie zastępuje szerokości przedziału, liczby reżimów i niezależności obserwacji. Niewystarczająca próba wydłuża obserwację.
- PF, Sharpe, Sortino, win rate, tail loss, exposure, turnover i wynik po segmentach są diagnostyką. PF≥1,20 z v1.0 nie jest samodzielną bramką.

Mechanizm hipotezy uznajemy za niepotwierdzony, jeśli wyniki nie przechodzą bramek; za praktycznie obalony w badanym zakresie, jeśli przewaga znika po poprawnym timing/cost model lub utrzymuje się wyłącznie na pojedynczym parametrze/wyjątkowym aktywie bez powtarzalnego mechanizmu. Brak odpowiednich danych daje „nierozstrzygnięte”, a nie naukowy dowód braku edge.

Badać koncentrację zysku, ale nie usuwać automatycznie dużych zwycięzców właściwych momentum. Przedstawić leave-one-episode/asset-out i tail contribution jako wrażliwość. Nie wymagać zysku w każdym segmencie; segment z niedopuszczalnym ryzykiem można wyłączyć wyłącznie w development, po czym zamrozić universe.

Benchmarks: posiadanie Q, BTC/ETH buy-and-hold w tej samej jednostce rozliczeniowej, prostszy breakout bez kompresji/wzrostu aktywności i random-entry sanity check z podobnymi ograniczeniami. Nie wymagać bezwarunkowego pokonania BTC przy innej ekspozycji; porównać także ryzyko, czas w rynku i koszty.

Holdout jest używany raz dla decyzji. Po niepowodzeniu można utworzyć nową hipotezę, ale wcześniejszy holdout staje się development; nowy niezależny okres trzeba uzyskać. Kolejne sprawdzanie tych samych danych co tydzień nie tworzy nowych holdoutów.

## 7. Weryfikacja kontraktów i bramki etapów

### 7.1. Przed kwalifikowanym paper

| ID | Test | Wymagany rezultat |
|---|---|---|
| E01 | crash przed/po zapisie intent, przed/po send, przed/po ACK/fill | brak ślepego ponowienia; zachowane rezerwacje |
| E02 | duplicate/out-of-order/fill-before-ACK/cancel-fill race | pojedynczy skutek ekonomiczny i poprawny remaining inventory |
| E03 | partial fill, fee w base, stop reject, partial exit, dust | prawidłowe coverage; incydent widoczny; brak fikcyjnego CLOSED |
| E04 | timeout replacement/soft exit i późny stop trigger | brak nieuprawnionego drugiego SELL |
| E05 | dwa procesy, utrata bazy, utrata ownership | co najwyżej jeden aktywny nadajnik; zatrzymanie mutacji zgodne z kontraktem |
| R01 | DD skokowo 5→13%, restart, depozyt, odbicie | HARD PAUSE pozostaje; HWM nie znika; brak auto-resume |
| R02 | NAV z fee, rezerwacje, zaokrąglenie/minimum, równoczesne candidates | brak nadmiernego wydatku, długu i przekroczenia approval |
| R03 | re-entry z nowym setup ID w tym samym epizodzie | limit epizodu nadal działa |
| D01 | zmiana przyszłych danych/news/pivotów/rewizji | wcześniejsze decyzje as-known pozostają identyczne |
| D02 | niedomknięte świece, source lag, gap versus zero trades | brak przyszłej informacji i fałszywej kompletności |
| D03 | listing/delist, brakujące przegrane aktywa, zmiany metadanych | jawne eligibility i coverage limitations |
| B01 | same-bar ambiguity, gap stop, FOK miss, liquidity outage | wynik bez korzystnego zgadywania i z utrzymanym inventory |
| B02 | fee/spread decomposition i shared cash | brak podwójnego naliczenia i niemożliwych równoległych trade’ów |
| S01 | replay całego baseline A/B | deterministyczne setupy, terminy i wersje wyjść |
| S02 | granice foldów, nakładające się etykiety, transformacje ML | brak przekroczenia granicy informacji |
| O01 | wszystkie wiersze failure matrix | oczekiwany tryb, alert, rezerwacje i warunek recovery |
| O02 | izolacja paper/shadow od live | brak technicznej ścieżki wysłania produkcyjnego zlecenia |

Testy nie wymagają dochodowego baseline’u. Zbieranie danych i shadow bez wykonywania zleceń może rozpocząć się wcześniej po własnej walidacji read-only pipeline, lecz nie zalicza bramki paper execution.

### 7.2. Przed live canary

- Przejście powyższych testów oraz testów kontraktowych API w dostępnym UAT; różnice względem produkcji jawnie zapisane.
- Potwierdzone spot-only/no-debt na rzeczywistym koncie i działanie wybranego typu ochrony.
- Pomiar opóźnień i kompletności prywatnych strumieni; przekroczenia D-SLO zdiagnozowane.
- Minimum 21 dni paper oraz 50 symulowanych order executions pozostaje tylko dolnym progiem operacyjnym; wymagane są również pełne lifecycle, recovery i scenariusze fault injection.
- Przejście bramek research, kompletna analiza residual risk i zamrożony manifest konfiguracji.
- Restore z backupu, alerting oraz ręczna procedura ograniczenia ekspozycji sprawdzone.
- Jawna zgoda na mały live canary; API trade pozostaje wyłączone do tego etapu.
- Pierwsze rzeczywiste operacje to nadzorowane testy semantyki i kosztów, potem dopiero dopuszczenie autonomii w zatwierdzonym zakresie.
- 100 PLN jest orientacyjną alokacją. Jeżeli nie pozwala przejść minimum/fee/protection testów, live pozostaje zablokowany; bot nie zwiększa kapitału samodzielnie.

Przed skalowaniem: odpowiednia liczba rzeczywistych epizodów i okresów, zgodność kosztów i residual loss z modelem, brak krytycznych incydentów oraz osobne review. Canary nie dowodzi capacity większego kapitału.

Rozdzielenie dowodów: przed pierwszym nadzorowanym live wymagane są pozytywne dowody UAT/kontraktowe dla używanych ścieżek, produkcyjna weryfikacja read-only konta oraz zgoda na jawny residual risk różnic środowisk. Brak dowodu podstawowych zdolności (spot-only, FOK, ochrona, rozstrzyganie orders) nie może być obchodzony „testem za realne pieniądze”. Zgodność rzeczywistego wykonania i kosztów z tym kontraktem sprawdza dopiero nadzorowany canary; niespodzianka blokuje dalsze transakcje. Do autonomicznego live nie przechodzi żadna używana zdolność pozostająca UNKNOWN/FAIL. Niebezpieczne scenariusze awarii wstrzykuje się do UAT/simulatora, nie do realnego konta z otwartą ekspozycją.

## 8. Macierz weryfikacji Crypto.com i nierozstrzygnięte zależności

Publiczne dokumenty nie są dowodem zachowania konkretnego konta. Brak potwierdzenia nie jest domyślną zgodą na fallback.

Każdy V ma identyfikator, właściciela etapu, zakres endpoint/account/environment/instrument, evidence hash/date, test i konsekwencję FAIL/UNKNOWN. Statusy: UNKNOWN, PASS, FAIL, DISABLED_VERIFIED. Ostatni wymaga testu, że dana nieużywana ścieżka jest nieosiągalna; nie zastępuje PASS dla funkcji używanej. Aktualizacja endpointu, konta, fee lub metadanych unieważnia dotknięty zakres evidence. UNKNOWN/FAIL może pozwalać na offline research, ale nigdy na zależną ścieżkę live.

| Zależność V | Co trzeba potwierdzić | Jeśli niepotwierdzona |
|---|---|---|
| SPOT/no-debt i dostępność konta/rynku/Q | uprawnienia, ustawienia finansowania, supported fields | brak live |
| FOK LIMIT | wsparcie, atomic fill semantics, reject/status | baseline live niedopuszczony; IOC wymaga osobnego review |
| Stop-market na spot | trigger source, minima, saldo, expiry, reject i częściowe wykonanie | brak live na danym rynku |
| Attached protection | moment aktywacji, zachowanie po cancel partial parent | nie traktować jako ochrony partial fill |
| client_oid | scope/retencja dedup, wyszukiwanie, eventual consistency | brak automatycznej retransmisji ambiguous submit |
| cancel/amend/linked exit | terminalność, race, atomowość, utrata protection | sekwencja z jawnym oknem ryzyka lub blokada live |
| Ordinary i advanced reconciliation | endpointy, strumienie, paginacja, retencja | recovery nie może być uznane za zakończone |
| Balances/fees | cash versus collateral, waluta fee, rounding, dust | brak zatwierdzenia sizingu |
| Metadata/rate limits | aktualność tick/min/max, bands i budżet obsługi | blokada dotkniętych operacji |
| Historyczne dane Crypto.com | PIT universe, L2/trades, trigger reference, delisted coverage | ograniczenie wniosków do poziomu dostępnych danych |
| Profil Q i źródła wyceny | wybór Q, metadata, źródła i policy depeg/FX według §0.2 | brak live bez kompletnego profilu i monitora |

### 8.1. Źródła i interpretacja

- [Crypto.com — Attached TP/SL](https://help.crypto.com/en/articles/11501438-setting-attached-take-profit-tp-stop-loss-sl-orders): opisuje aktywację po pełnym wykonaniu parent order oraz brak rezerwacji środków przy utworzeniu. Wniosek projektowy: nie liczyć nieaktywnego attached stopa jako coverage partial fill. Ponownie odczytano 2026-10-06.
- [Crypto.com — Advanced Order Management](https://exchange-developer.crypto.com/exchange/v1/docs/api/rest/advanced-order-management): dokumentuje osobne operacje i strumień dla zaawansowanych zleceń oraz typy triggerów. Wniosek projektowy: odrębne reconciliation ochrony. Ponownie odczytano 2026-10-06.
- [Crypto.com — Trading](https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket/trading): przegląd operacji asynchronicznych; odczytany podczas review v1.0 w tej sesji. Kontrakt nie utożsamia ACK z wykonaniem.
- [Crypto.com — Create Order](https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket/ws-user-api-private-create-order-dma): pola spot/margin i instrukcje zleceń odczytane podczas review v1.0; ponowny odczyt tej strony w tym kroku był niedostępny. Nie uznano tego za potwierdzenie zachowania konta.
- [Crypto.com — TP/SL](https://help.crypto.com/en/articles/4453247-stop-loss-and-take-profit-orders) oraz [Minimum Order Size](https://help.crypto.com/en/articles/10511090-crypto-com-exchange-minimum-order-size-faq): odczytane podczas review v1.0; do aktualnego profilu wdrożenia trzeba ponownie potwierdzić limity i warunki.

Projektowe terminy, polityki retry, wielkości ryzyka i strategia R0 są naszymi propozycjami, a nie gwarancjami dostawcy API.

## 9. Kolejność dalszych prac i status gotowości

1. Doprecyzowania projektu z 2026-10-07 i finalny self-review są podstawą implementation planning.
2. Plan obejmuje najpierw weryfikację capabilities/danych oraz testowalne modele accounting, intent, recovery i risk; wymaga zatwierdzenia przed wykonaniem.
3. Następnie pipeline PIT, simulator, baseline A/B i walidacja OOS; execution/reconciliation są rozwijane razem z testami awarii, przed kwalifikowanym paper.
4. Paper/shadow, bramka live, nadzorowany canary; ML/listingi/warstwowe wejścia jako późniejsze oddzielne eksperymenty.

**Verdict v1.1: READY FOR PLANNING.** Po opisanych korektach nie zidentyfikowano P0 projektu bez przypisanej reguły zachowania, testu i bezpiecznej konsekwencji. Pozostają jawne capability/data gates V oraz dowody wymagane przed paper/live. To wynik przeglądu dokumentacji, nie pozytywny wynik testów systemu, dowód edge ani zgoda na implementację/live.

Dodatni backtest nie jest wymagany do planowania badań. Brak zdolności giełdy do spełnienia kontraktu może zatrzymać odpowiednią ścieżkę live i wymagać nowego review, bez porzucania niezależnych badań. Następny dokument: [Implementation Plan](superpowers/plans/2026-10-07-crypto-trading-bot-v1-implementation-plan.md).

## 10. Finalny self-review — 2026-10-07

Przegląd objął sprzeczności między sekcjami, ukryte fallbacki, osiem klas P0 z review v1.0 oraz pokrycie wymagań z ostatniej wiadomości. Nie wykonywano kodu bota, testów giełdy ani badań rentowności.

| Finding | Korekta w projekcie | Dowód wymagany w planie |
|---|---|---|
| Q nadal opisane jako niepotwierdzone | zatwierdzone Q/PLN; profil Q jawnie gate’owany | P00/P04/P06: brak kursu/parytetu nie daje automatycznej zgody |
| „D” zacierało zatwierdzenie profilu ryzyka | rozdzielone user thresholds, zatwierdzone defaults i parametry pomiarowe | P01/P04: manifest i ochrona przed cichą zmianą |
| FOK mogło wyglądać na warunek sensu całego projektu | blokuje tylko profil live; osobny review IOC, brak fallbacku | P06: UNSUPPORTED_FOK nie emituje IOC |
| Opis ryzyka nie wskazywał, która miara wchodzi do agregatu | jawny max initial-loss/current-to-stop dla open + pending | P04: zwycięzca z dużym giveback blokuje nowe ryzyko |
| Nominalne limity mogły wymuszać sprzedaż po wzroście ceny | rozdzielenie admission caps i naruszenia przez błędne komendy | P04: price drift blokuje wejście, nie generuje samoistnego SELL |
| Tabela DD mogła omijać histerezę | eskalacja stanowa i trwały latch; brak bezstanowego resetu | P04/P05: 6→5,5%, restart i 13→4% |
| Seria filli mogła resetować termin ochrony lub tworzyć konkurujące stopy | per-uncovered-lot deadline i serializacja target quantity | P03/P06: fills podczas PENDING/UNKNOWN protection |
| Cancel→market mogło być cichym fallbackiem | jawny ExecutionProfile przed startem i osobne dowody | P03/P06/P12: brak profilu oznacza blokadę |
| Counting candles mogło przedłużać time exit podczas outage | trwały deadline na 24. planowej granicy 5m | P05/P09: brak public feed nie przesuwa deadline |
| Final holdout i ciągły OOS miały niejasny stan początkowy | niezależny holdout flat z deklarowanym NAV/HWM; jawna granica | P10: brak resetu foldów, brak mieszania krzywych |
| UAT/live gates mogły wymagać dowodu live przed pierwszym live | rozdzielone UAT + read-only → nadzorowany canary → autonomia | P12/P13: brak wykonania produkcyjnego przed osobną zgodą |
| Referencje i fee miały możliwe ciche substytuty | źródła triggera, tick rounding i fee reserves jawnie walidowane | P02/P06/P08: brak danych/fee nie tworzy wartości domyślnej |

Pozostałe P0 są objęte kontraktami: durable intent/UNKNOWN/single writer (§1), no-debt (§1.1), recovery i matrix (§1.8–2), PIT/survivorship (§4), realism execution (§5), zamrożona hipoteza i falsification (§6). Ich skuteczność musi zostać wykazana przez zaplanowane testy. Potencjalne przyszłe niepowodzenie testu ponownie otwiera finding i blokuje zależną bramkę.
