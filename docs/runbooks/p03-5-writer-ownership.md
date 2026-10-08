# P03.5 — single writer, ownership i fencing

## Zakres i granice

Wyłącznie P03.5, po `9f6ade9`, na `codex/p03-execution-lifecycle`.
Dotychczasowe commity zachowano; brak merge do main. P03.6 i P04 nie rozpoczęto.
**G3 niezaliczone. Live BLOCKED, V01–V11 UNKNOWN.**

`execution/ownership.py` definiuje `WriterToken`, `WriterGuard`,
`OwnershipControl` i niezależny `FakeFenceAuthority`. Migracja
`002c_writer_ownership.sql` dodaje account-wide ownership i immutable audit.
Przypisanie obejmuje account, runtime, process owner, rosnący epoch,
authority identity i unikalny nonce. Runtime nie tworzy drugiej przestrzeni
ownership dla tego samego konta. Nie ma TTL dającego prawo do takeover,
automatycznego failover ani przejęcia przy konstruowaniu obiektu/restartcie.

Wysłanie wymaga zgodnego ACTIVE tokenu w PostgreSQL **oraz** aktywnego permitu
w osobnym fake authority. Token odczytany z DB nie tworzy permitu dla nowego
procesu. Guard bez permitu i nieznany/pozorny guard są odrzucane. Scope jest
wiązany do jednego współdzielonego authority dla wszystkich nadajników.
Authority jest reprezentacją zewnętrznego egress gate w testach, nie dowodem
zatrzymania hosta ani zweryfikowanym mechanizmem Crypto.com.

## Punkty egzekwowania

FakeDispatcher sprawdza ownership przed claim, a FakeExchange ponownie w
punkcie transmisji. FakeLifecycleExchange i publiczne LifecycleRepository
`execute_fake` także wymagają rzeczywistego WriterGuard; brak ukrytej
publicznej ścieżki pomijającej kontrolę. Observation/accounting/reconciliation
API nie uzyskują przez to praw do transmisji.

W punkcie dopuszczenia efektu guard blokuje rekord ownership, sprawdza trwały
token i wywołuje callback pod blokadą authority. Stała kolejność to DB row →
authority. `cut_off` jest niezależny od DB: kończy dopuszczone wcześniej
callbacki, po czym zamyka kanał. Nie cofa już wysłanego RPC. Dopiero ukończony
cutoff stanowi barrier dla następcy; nowy writer nie startuje podczas draining.

Odrębny cutoff blokuje transmisję nawet przy pozornie poprawnym lokalnym stanie
DB lub przy przywróceniu starej projekcji ownership. Każdy odcięty epoch ma
trwały w życiu authority tombstone: spóźniona aktywacja tej samej generacji nie
może go otworzyć. Receipt jest scoped, wydawany przez authority, nie można
zastąpić go kopią danych ani replayem wykorzystanej autoryzacji.

Utrata ownership lub błąd PostgreSQL zatrzaskują WriterGuard.stopped. Naprawa
połączenia nie wznawia go automatycznie. Sprawdzenie następuje przy każdym
wysłaniu, nie przez działający w tle monitor; nie dodano schedulera/runtime 24/7.
Nie jest to granica bezpieczeństwa przeciw złośliwemu kodowi modyfikującemu
prywatne pola Python. Jest to kontrakt zaufanego execution code i fake transportu.

## Manualne odcięcie i przejęcie — fake/offline

1. Zatrzymaj wydawanie nowych poleceń z procesu operatora. Odczytaj bieżący token,
   account/runtime/process identity i audit. Sam wpis ACTIVE ani wygasły lease
   nie dowodzą, czy poprzednik nadal ma dostęp do venue.
2. Wywołaj `authority.cut_off(old_token)` na **tym samym niezależnym authority**,
   którego używają wszystkie stare nadajniki. Poczekaj na zakończenie barrier;
   zachowaj zwrócony receipt. Brak dostępu, brak zakończenia albo niezgodny
   authority to BLOCKED. Nie zastępuj go nowym pustym obiektem jako „dowodem”.
3. Wywołaj `control.manual_revoke(old_token, receipt, reason)`. Revoke i audit
   są transakcyjne. Awaria zapisu pozostawia zewnętrzny kanał zamknięty nawet
   jeśli DB nadal pokazuje ACTIVE. Napraw DB i ponów świadomą czynność operatora;
   nigdy nie otwieraj starego guardu. Idempotentny ponowny revoke tej samej
   generacji nie dodaje kolejnego ekonomicznego skutku.
4. Rozstrzygnij znane observations/fills. Nie zwalniaj reservations z powodu
   revoke, nie kasuj outbox i nie zmieniaj UNKNOWN na PREPARED. Pełne recovery
   i reconciliation produkcyjne należą do P05; ten etap ich nie zastępuje.
5. Operator może wywołać `manual_takeover(old_token, receipt, runtime, new_owner,
   reason)` dopiero po durable REVOKED. Nowy process identity ma być odrębny.
   Operacja zwiększa epoch, zapisuje nowy token i audit, a po commit otwiera
   nowy permit. Konkurencyjne granty nie mogą mieć dwóch ACTIVE rekordów.
6. Sprawdź `new_writer.check(account)` na właściwej bazie i tym samym authority.
   Utrzymuj profile live wyłączone. Nowy writer nadal nie może ponawiać komend
   UNKNOWN ani odzyskiwać reserved inventory przez samo przejęcie ownership.

## Crash/restart boundaries

- Przed record lub przed commit: transakcja cofa token/audit, kanał nie został
  otwarty. Powtórzenie jest nową jawną czynnością operatora, nie auto-startem.
- Po commit, przed aktywacją kanału: DB może pokazywać ACTIVE bez permitu.
  Zwykły restart/initial grant nie przejmuje rekordu. Odczytaj nowy token,
  uzyskaj pozytywny cutoff dla niego, revoke i manual takeover z nowym epoch.
- Po aktywacji: nawet jeśli operator/worker zginął przed otrzymaniem handle,
  traktuj kanał jako potencjalnie aktywny. Wymagany jest jego cutoff i revoke.
- Po utracie ownership między intent claim i submit: brak submit, trwałe
  UNKNOWN i reservations. Manualnie dopuszczony następca także nie retryuje.
- Utrata DB w trakcie fake efektu: istniejące granice UNKNOWN/no blind retry
  pozostają. Stary guard jest stopped; ledger/reservations nie są czyszczone.

Authority jest niezależnym, współdzielonym obiektem testowym reprezentującym
zewnętrzny gate. Jego restart/utrata **nie** jest restartem writera: permitów
nie rekonstruuje się z DB. Świeży authority ma inną identity i nie potwierdza
odcięcia poprzedniego. Taka sytuacja pozostaje BLOCKED w tym ograniczonym etapie.
Przykładowe fixture granty są jawne i mieszczą się tylko w test utilities;
nie dodano automatycznego grantowania w kodzie runtime.

## Wymagana procedura przed przyszłym live

Nie wykonano żadnej z poniższych czynności na realnym środowisku. Przed live
potrzebny jest zweryfikowany zewnętrzny fencing, a nie promocja synthetic receipt:
identyfikacja wszystkich starych hostów/procesów i supervisorów, wyłączenie ich
auto-restartu, potwierdzone zatrzymanie oraz odcięcie dostępu do venue np. przez
kontrolowany egress gate. Rotacja/revocation uprawnień wymaga potwierdzonej
semantyki venue; nie zakładamy, że samo żądanie revoke kończy aktywne sesje.

Jeśli stary host nie jest osiągalny albo jego odcięcia nie da się udowodnić,
nowy writer nie startuje. Sama baza/lease, flaga paused, PID na jednym hoście
ani brak heartbeat nie są takim dowodem. Już dopuszczone RPC mogą pozostawić
fille i zlecenia; potrzebne są reconciliation oraz accounting przed wznowieniem.
Żadnego emergency writera omijającego te warunki. Nie dodano credentials,
endpointów prywatnych, kluczy ani realnych transakcji.

## TDD, self-review i znalezione problemy

Początkowy behavioral RED: nieowned dispatcher wykonywał submit. Pozostałe
początkowe testy wskazywały brak nowego API ownership. Dalsze rzeczywiste RED
odtworzyły bypass lifecycle/raw execute_fake, lock inversion przy revoke,
pozorny zawsze zezwalający guard i ponowne otwarcie odciętej generacji przez
spóźniony grant. Wszystkie usunięto. Dodano checkpoint po aktywacji do testów
crash; wcześniejszy brak tego checkpointu nie jest dowodem wykonania realnego
RPC po crash.

Dotychczasowe fixture transporty dostały jawny manual synthetic grant.
Dwa testy fault P03.4 po błędzie DB oczekują teraz PermissionError stopped,
zamiast benign False ze starego handle. W obu sprawdzono dodatkowo manualnie
dopuszczonego następcę: istniejący UNKNOWN nadal zwraca False, bez ponowienia.
Nie osłabiono invariantów ledger, reservations, protection ani exit coordination.
Jedną omyłkę adaptacji fixture (pominięte repo w destructuring) poprawiono;
nie jest liczona jako behavioral RED produktu.

Self-review P03.5 obejmuje granice ownership/transport, dwie niezależne sesje PG,
crash/commit, kolejność blokad, downgrade starego stanu DB, cutoff draining,
stopped latch, niewznawianie UNKNOWN, scope i immutable audit. Końcowy review
całego P03/P03.6 nie został uruchomiony.

## Wyniki

- Target P03.5 + wcześniejsze fake dispatch/lifecycle: **91 PASS w 18,55 s**.
- Pełna regresja P01–P03.5: **262 PASS w 178,73 s**, bez skipów:
  150 unit, 2 property, 92 real PostgreSQL integration, 18 fault tests.
- Nowe P03.5: 16 integration i 10 fault cases. Testy concurrency używają
  odrębnych rzeczywistych sesji PostgreSQL oraz dwóch dispatcher/control
  instances; fake authority jest wspólnym brokerem w procesie testowym.
  Nie udajemy testu rozproszonego broker service ani dwóch realnych hostów.
- Ruff check PASS; format check: 52 files already formatted; diff check PASS.
- Self-review zakończony dla ograniczonego P03.5, bez otwartego blokera fake
  zakresu. Zależności live, rozproszone fencing/recovery oraz końcowy review
  P03.6 pozostają nierozstrzygnięte; **G3 nadal niezaliczone**.

```sh
P02_TEST_DSN='host=/sciezka/do/izolowanego/socket port=55432 dbname=postgres' .venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
git diff --check
```

Wymagany jest własny izolowany PostgreSQL. Testy zakładają losowe schematy,
usuwają wyłącznie własne schematy i nie korzystają z Crypto.com API. Po osobnym
commicie P03.5 zatrzymujemy pracę na tej gałęzi, bez merge i bez P03.6/P04.
