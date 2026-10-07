# P01 — fundament offline i odbiór G1

## Zakres i baza

P00 zostało scalone zwykłym merge w PR #1; baza P01: `494baf1` (rodzice `9b975b3`, `13870f9`). Gałąź robocza: `codex/p01-foundation`. Użytkownik upoważnił wyłącznie P01.1–P01.5 po merge P00, z TDD, self-review i niezależnym review. P02 nie jest częścią tej zmiany.

G1 ocenia fundament offline, serializację, izolację profili, zegar oraz minimalny publiczny raw capture. Nie zamyka V01–V11, nie kwalifikuje paper execution/live i nie dowodzi rentowności. Nie korzystamy z prywatnych kluczy, prywatnego API ani mutacji produkcyjnych/UAT.

## Decyzje wykonawcze

- Python 3.12 w lokalnym `.venv`; zależności narzędziowe utrwalone w `uv.lock`. Brak zmiany systemowego Pythona. Brak instalacji PostgreSQL, bibliotek ML i silnika execution w P01.
- Istniejący checkout na osobnej, wskazanej przez użytkownika gałęzi; zastane metadane Git i dokumenty P00 zachowane.
- Dowody testów P01 obejmują zachowanie kodu offline. Statusy capability z P00 pozostają UNKNOWN; nawet syntetyczny zestaw PASS nie uprawnia P01 do uruchomienia live.
- Collector używa wyłącznie źródła publicznego potwierdzonego w P00. Snapshot katalogu nie jest historią rynku, feedem transakcyjnym ani dowodem ciągłości książki.

## Granica kontraktów P01

Plan §2.1 wymaga zdefiniowania wspólnych nazw już w P01. Są to rekordy danych i deklaracje portów; nie implementują księgowań, rezerwacji, reduktora execution, strategii ani adaptera tradingowego. Ich semantyka operacyjna i testy ekonomiczne należą do etapów właścicielskich P02+. Samo istnienie `ExchangePort` nie udostępnia transportu ani credentials.

## Ograniczenia odbioru

- Przykładowe profile służą do budowy offline. Nazwy `paper` i `uat` nie uruchamiają tradingu ani prywatnego transportu. Q pozostaje syntetyczne.
- Loader TOML obsługuje pola obecnych profili; zagnieżdżone rekordy instrumentów/evidence nie są jeszcze obsługiwane z TOML (nieznany format jest odrzucany). Rozszerzenie wymaga testów przy pierwszym etapie używającym tych rekordów w konfiguracji.
- Czasomierz P01 nie jest trwałym schedulerem; persistence/recovery deadline’ów należy do P03/P05.

## Publiczny smoke test

2026-10-07, `14:52:17.393663 UTC`: jeden anonimowy `GET https://api.crypto.com/exchange/v1/public/get-instruments` przez collector P01. Wynik procesu: exit 0, `attempts=1, successes=1, failures=0`. Odpowiedź: `code=0`, 988 instrumentów, 549 911 bajtów. Zapisano jeden event DATA; hash ponownie obliczony z zapisanych bajtów zgadza się z envelope:

`35ae45be7fea3c91715f7f95027e65777b22d866fa17f65a2f27d2be1a61012e`

Lokalny wynik jest poza Git, w `/private/tmp/cryptobot-p01-smoke-20261007-r1`; może zostać usunięty przez system. Repo zawiera wyłącznie ten opis i hash, nie payload. Brak credentials, odczytów konta, UAT trading lub mutacji. Obserwacja potwierdza działanie capture tego publicznego endpointu w tej chwili, nie kompletność historii rynku ani capabilities live. Instrumentów nie filtrowano do trade universe: to oryginalny katalog, obejmujący również instrumenty niedopuszczone do spot v1.

## Self-review i niezależny review

Self-review oraz niezależny reviewer znaleźli i skierowali do poprawki następujące przypadki: numeryczny/bool Decimal na wire, wpływ kontekstu Decimal na exact addition i timery, brak pól lotów/ochrony w kontraktach, offset monotonic capture oraz pominięcie odstępu polling po błędach HTTP. Poprawki mają testy regresyjne. Końcowa weryfikacja kodu: 76 testów PASS oraz Ruff bez naruszeń. Niezależny reviewer powtórzył testy, sprawdził brak efektów I/O podczas importu wszystkich 13 modułów i nie znalazł otwartych P0/P1/P2 w kodzie P01. Niezależny końcowy przegląd README i `.gitignore` również zakończono bez uwag. Rekomendacja review: G1 PASS wyłącznie dla fundamentu offline.


## TDD i odtwarzalność

Zapis cykli wykonawcy (RED oznacza uruchomiony test przed implementacją lub poprawką, nie zaplanowany test):

| Zakres | RED | GREEN |
|---|---|---|
| P01.1 — Decimal, waluty, UTC i wire | 13 brakujących kontraktów | 13 PASS |
| Wire odrzuca numeric/bool Decimal | 3 regresje | 16 PASS łącznie |
| P01.2 — profile i isolation guards | 22 brakujące kontrakty | 38 PASS łącznie |
| P01.3 — clock i replay | 8 brakujących kontraktów | 46 PASS łącznie |
| DTO lotów/approval oraz kontekst dodawania | 3 regresje | 49 PASS łącznie |
| P01.4 — raw capture | 18 brakujących kontraktów | 67 PASS łącznie |
| Kontekst Decimal a timer | 1 regresja | 68 PASS łącznie |
| Charakterystyka istniejącej granicy HTTP | 3 testy od razu PASS; bez deklaracji RED | 71 PASS łącznie |
| Cadence po błędzie, offset i limit metadanych | 3 regresje | 74 PASS łącznie |
| Wersja wire i waluty Fill | 2 regresje | 76 PASS łącznie |

Środowisko: Python 3.12.14; uv 0.12.23; pytest 9.0.2; Hypothesis 6.151.9; Ruff 0.15.7. Runtime używa biblioteki standardowej. `uv sync --locked --offline`: exit 0, sprawdzono 9 pakietów. Lockfile nie wymaga konta ani credentials.

Weryfikacja odbiorowa koordynatora:

```text
.venv/bin/pytest tests/unit/test_contracts.py tests/unit/test_profiles.py tests/unit/test_clock.py tests/unit/test_raw_capture.py -q
76 passed in 0.33s

.venv/bin/ruff check src tests
All checks passed!

git diff --check
exit 0
```

Wynik testów nie zastępuje UAT ani dowodu ekonomicznego. Testy przyszłych silników ledger/risk/execution nie są wliczone w P01.

## Werdykt odbioru

**G1 PASS — P01.1–P01.5 zakończone w zakresie fundamentu offline.** Self-review oraz niezależny review wykonano; brak otwartych ustaleń blokujących odbiór. Nie ma blokera dalszej budowy offline wynikającego z P01. Otwarte ograniczenia danych/konta/UAT opisane w P00 nadal obowiązują.

Runtime pozostaje INCOMPLETE; V01–V11 UNKNOWN; live BLOCKED. Gałąź P01 jest oddzielna od `main`. P02 nie rozpoczęto i wymaga kolejnej zgody użytkownika. Brak produkcyjnych credentials, prywatnych kluczy i danych konta w zmianie; surowy publiczny smoke test pozostał poza repozytorium.
