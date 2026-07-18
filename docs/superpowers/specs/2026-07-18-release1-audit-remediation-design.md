# DAN Release 1 — projekt napraw po audycie 15 kroków

**Data:** 2026-07-18

**Gałąź integracyjna:** `agent/dan-release1-integration`

**Stan produkcyjny na wejściu:** kod `1852d7f`, tag
`dan-v1-foundation-candidate`

**Wybrany wariant:** A — etapowa naprawa safety-first

## 1. Cel

Celem jest naprawienie potwierdzonych ustaleń z niezależnego audytu wszystkich
15 kroków planu Release 1 bez ukrywania regresji w zaakceptowanym długu,
rozszerzania architektury o kolejne źródła prawdy ani naruszania działającej
produkcji przed osobną bramką wdrożeniową.

Naprawy mają doprowadzić do nowego, audytowalnego kandydata Release 1. Wdrożenie
kandydata rozpocznie nową siedmiodniową obserwację. Dopiero jej poprawne
zakończenie, dwa odrębne dowody zimnego startu oraz jawny sign-off Ozzy'ego
pozwalają utworzyć finalny tag `dan-v1-foundation`. Merge do `main` pozostaje
osobną decyzją po sign-offie.

## 2. Stan i źródła prawdy

Prace odbywają się wyłącznie na `agent/dan-release1-integration`. Produkcja nie
jest utożsamiana z bieżącym HEAD-em gałęzi: do osobnego wdrożenia nadal działa
kod `1852d7f`. Commit dokumentacyjny po cutoverze nie rozpoczyna ponownie
obserwacji, ale każde wdrożenie zmienionego kodu już tak.

Obowiązują następujące źródła prawdy:

- persona: `config/persona/DAN.md`, odczytywana bez kopii i bez przepisywania;
- głosy i wymowa: `config/voice/personas.toml` oraz
  `config/voice/pronunciations.toml`, ładowane przez jeden resolver;
- stan rozmowy, kolejki i runtime'u: daemon `dand` oraz jego API;
- audio: wyłącznie `dand`; Supertonic serve jest jego nadzorowanym dzieckiem;
- mózg: jedna trwała sesja Claude CLI z checkpointem i kontrolowanym recycle;
- trwałe dane: właściwe bazy SQLite wraz z ich jawnym kontraktem migracji;
- dowody release'u: wersjonowane narzędzia w repo i hashowalne artefakty.

Zapis `cold Claude CLI only` w bieżącym `AGENTS.md` jest sprzeczny z późniejszą
decyzją produktową, planem trwałej sesji oraz działającym runtime'em. Nie jest
podstawą napraw. Dokumentacja ma zostać doprowadzona do prawdy o jednej trwałej
sesji; zmiana kanonu persony ma powodować recycle przed następnym wejściem.

Nie wolno wprowadzać drugiego resolvera konfiguracji, drugiej kolejki, drugiego
odtwarzacza, trybów mock/dev produktu, bramek approval ani łańcucha providerów.

## 3. Zakres i wyłączenia

Zakres obejmuje tylko potwierdzone defekty albo braki dowodu z audytu. Task 8
nie dostaje wymyślonego endpointu `/voice/status`: jego literalny kontrakt
`/voice/runtime` już istnieje. Historyczne dokumenty i nazwy ADR nie podlegają
hurtowemu przepisywaniu, chyba że są aktywną instrukcją wykonawczą.

Poza zakresem tej naprawy pozostają:

- merge do `main`, usuwanie donorów i finalny tag;
- uruchamianie live TTS podczas testów automatycznych;
- zmiana routingu audio, urządzenia wyjściowego lub głośności macOS;
- nowe funkcje produktowe niezwiązane z ustaleniami audytu;
- przejmowanie nieustabilizowanego patcha roboczego Fable'a, który obejmuje
  dokumentację oraz konfigurację głosu;
- przepisywanie historii tylko po to, by wyglądała współcześnie.

## 4. Kolejność napraw

### Partia 0 — stabilizacja powierzchni roboczej

Przed kodem należy:

1. zaczekać, aż patch Fable'a przestanie się zmieniać, i przypisać jego pliki do
   właściciela;
2. zapisać fingerprint HEAD-u, statusu i istniejącego kandydata;
3. utworzyć aktualny checkpoint audytowy bez nadpisywania historycznych
   manifestów Task 1;
4. usunąć z lokalnej powierzchni testowej ignorowane cache `jarvis/` w sposób
   kontrolowany oraz dodać regresję wykrywającą ich ponowne pojawienie się;
5. nie mieszać zmian Fable'a z żadnym commitem naprawczym.

Partia 0 nie zmienia produkcji ani tagu kandydata.

### Partia 1 — bezpieczeństwo danych i cutoveru

Ta partia naprawia Task 3 i Task 12 przed wszystkim, co mogłoby ponownie
dotknąć produkcji.

#### Rodzina plików SQLite

Operacje backupu, migracji i rollbacku traktują bazę jako rodzinę:

- plik główny;
- `-wal`;
- `-shm`;
- `-journal`.

Rollback operacji `remove` usuwa lub odtwarza całą zapisaną w journalu rodzinę,
nie tylko plik główny. Każda ścieżka jest wcześniej rozstrzygnięta i sprawdzona;
nie wolno budować destrukcyjnego celu z niezweryfikowanego globu.

#### Realna bramka intake

`intake_closed` przestaje być samym wpisem tekstowym. Daemon udostępnia realny,
testowalny stan bramki, który:

- odrzuca nowe wejścia przed pierwszą mutacją cutoveru;
- pozwala dokończyć lub jawnie anulować rozpoczęte operacje;
- pozostaje zamknięty podczas stopu, migracji i walidacji;
- otwiera się dopiero po udanym starcie albo zakończonym rollbacku;
- po awarii ma stan rozstrzygany z journalu, a nie z domysłu procesu.

CLI cutoveru używa jawnie wstrzykniętych adapterów hosta do stop/start oraz
`launchctl`. Brak adaptera blokuje operację przed mutacją. Dry-run pozostaje
całkowicie niemutujący.

#### Walidacja migracji

Migrator ma ścisłą listę dozwolonych sidecarów FTS zamiast ignorowania każdej
tabeli pasującej do `memory_fts_*`. Po utworzeniu celu wykonuje co najmniej:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- porównanie wymaganych tabel, schematów i liczby wierszy;
- deterministyczny dowód zachowania danych dla tabel kanonicznych;
- walidację wyniku checkpointu i błąd przy `busy` lub niepełnym checkpointcie.

Wyścig między wykryciem otwartego pliku i backupem jest obsłużony blokadą albo
ponowną walidacją bezpośrednio przed snapshotem. Sukces nie może zależeć tylko
od wcześniejszego wyniku `lsof`.

Journal jest append-only, a krytyczne przejścia są fsyncowane wraz z katalogiem.
Resume rekoncyliuje stan dysku z ostatnią ukończoną fazą; nie przeskakuje w
ciemno do następnego kroku.

### Partia 2 — własność runtime'u i integracje hosta

Ta partia naprawia Task 9 i Task 11.

`ChildSupervisor` otrzymuje działający watchdog i budżet restartów. Supertonic
serve może być wyłącznie dzieckiem uruchomionym i obserwowanym przez `dand`;
adopcja obcego procesu i fallback do równoległego CLI są błędem własności.
Przekroczenie budżetu restartów daje jawny stan degraded zamiast pętli śmierci.

Cykl życia PTT obejmuje własność grace timera: timer jest anulowany przy nowym
zdarzeniu, restarcie i shutdownie. Zwolnienie `hold` jest związane ze źródłem,
które utworzyło lease. Restart najpierw zamyka intake, potem kończy aktywne
zadania i dzieci.

Scheduler i `StandupJob` są rzeczywiście tworzone przez `DaemonApp`, startowane
po gotowości storage i zatrzymywane przed zamknięciem storage. Ich aktywność
jest widoczna w stanie daemona.

Installer obejmuje manifestem wszystkie artefakty: venv, wrappery, hooki,
adaptery, plisty i pliki konfiguracyjne. Apply jest staged, backup-first i
odwracalny; verify sprawdza treść oraz uprawnienia. Uninstall korzysta z
manifestu i backupu, a nie z ręcznej, niepełnej listy.

Aktywny hook Claude MessageDisplay ma wskazywać na instalowany adapter DAN i
wywoływać `dan speak`. Stary hook uruchamiający `dan_core.say` nie może pozostać
aktywną konfiguracją. Zmiana pliku w HOME następuje dopiero podczas osobnego,
jawnie zatwierdzonego wdrożenia hosta.

Doctor/installer wykonuje prawdziwy preflight wymaganych uprawnień TCC i
Accessibility i odróżnia `unknown`, `denied` oraz `granted`.

### Partia 3 — persona, konfiguracja i głos

Ta partia naprawia Task 5, Task 6 i Task 7 oraz domyka zastrzeżenia Task 8.

#### Trwała sesja i świeży kanon

Każde wejście otrzymuje aktualny hash kanonu. Gdy hash różni się od hasha
bootstrapu bieżącej sesji, transport jest recyclowany **przed** wysłaniem
następnego tekstu, a nowa sesja startuje z aktualnym `DAN.md`. Nie zapisujemy
nowego hasha dopiero po odpowiedzi. Nieprawidłowy lub nieczytelny kanon kończy
żądanie jawnym błędem; nie ma friendly fallbacku.

#### Jeden resolver konfiguracji

`config explain`, runtime i panel używają tego samego resolvera. Plik example
nie może udawać efektywnej konfiguracji. Martwa projekcja `persona.profile`,
hardcoded casting i `voice.playback_binary` są usuwane z aktywnego kontraktu.
Gotowość playbacku pochodzi ze stanu rzeczywistego CoreAudio playera i
nadzorowanego procesu TTS.

Ładowanie instalacyjnego TOML podlega tym samym regułom właściciela i walidacji
co konfiguracja repo. Casting nie jest kopiowany do kodu ani dokumentów.

#### Bramka akceptacji głosu

Skrypt akceptacyjny Żanety ma zapisaną tożsamość źródła: ścieżkę logiczną,
wersję i SHA-256 zatwierdzonego skryptu. Uruchomienie odbywa się izolowanym
interpreterem (`-I`) z minimalnym środowiskiem. Nieufny stdout nie może samym
napisem `RATIO` zatwierdzić materiału; wynik ma ustrukturyzowany format,
walidowany zakres i powiązanie z wejściowym WAV-em.

#### Atomowość anulowania kolejki

`cancel_session` wybiera i aktualizuje rekordy w jednej transakcji. Zwrócone ID
pochodzą z faktycznie zmienionych rekordów, a eventy odpowiadają dokładnie temu
zbiorowi. Enqueue współbieżny z anulowaniem nie może zostać anulowany w bazie i
zniknąć z wyniku API.

### Partia 4 — panel, bezpieczeństwo testów i release engineering

Ta partia naprawia Task 2, Task 4, Task 10 i Task 13.

Panel nie dopisuje lokalnej, udającej prawdę wiadomości użytkownika. Renderuje
wyłącznie stan zwrócony lub opublikowany przez daemon: queued, running, done,
failed albo unknown. Błąd transportu pozostawia jawny błąd/unknown, a nie pustą
kolejkę ani fałszywą rozmowę.

Z aktywnej ścieżki produktu znikają provider chain, mock/dev product modes i
disabled-by-policy UI. Pozostaje jedna trwała sesja Claude CLI. Panel wysyła
intencje do API i nie przejmuje sesji, procesów, kolejki ani playbacku.

Bezpieczeństwo testów nie polega wyłącznie na analizie tekstu jednego testu.
Warstwa audio ma jawny adapter testowy i fail-closed guard, dzięki któremu
helper importowany przez test nie uruchomi `afplay`, CoreAudio ani TTS. Testy
automatyczne mockują granicę TTS. Klasyfikator nadal raportuje ryzykowne testy,
ale nie jest jedyną barierą.

Kontrolowany clean clone nie zawiera namespace package `jarvis` z lokalnych
cache. Gate skanuje ścieżki fizyczne, importy i allowlistę legacy nazw. Venv i
komendy testowe nie mogą mieć shebangów wskazujących stare repo.

Build i instalacja offline korzystają z jawnego, kompletnego wheelhouse'u.
Gate obejmuje build sdist/wheel, instalację do pustego venv, doctor oraz audyt
zawartości paczki. Release audit domyślnie sprawdza aktywne powierzchnie HOME
wymienione w manifeście; aktywna referencja legacy jest błędem, nie ostrzeżeniem
ignorowanym bez `--strict-home`. Assety mają licencję, źródło i SHA.

### Partia 5 — dowody, nowy kandydat i obserwacja

Ta partia domyka Task 14 i Task 15. Nie jest uruchamiana, dopóki wszystkie
wcześniejsze partie nie przejdą swoich bramek.

Rejestrator obserwacji staje się wersjonowanym narzędziem repo. Dzień jest
wyliczany z daty, unikalny i monotoniczny; nie można wpisać siedmiu dni w jednej
dobie argumentem CLI. Metryki adapterów i użycia starego runtime'u są wyliczane
z realnych dowodów, nie wpisywane jako stałe. Skan obejmuje PID, PPID, argv,
otwarte porty i cwd, również ścieżki backupów/migracji.

Dwa cold starty muszą pochodzić z dwóch odrębnych cykli logowania/startu i mieć
różne identyfikatory cyklu. Dwa raporty z tej samej sesji nie spełniają bramki.

Po pełnych zielonych testach powstaje nowy, niemutowalny tag kandydata, np.
`dan-v1-foundation-candidate.2`; istniejący tag `dan-v1-foundation-candidate`
nie jest force-move'owany. Wdrożenie jest osobną, jawną operacją i zeruje licznik
obserwacji. Stary ledger pozostaje dowodem historycznym, nie jest przepisywany.

## 5. Mapowanie audytu na partie

| Krok | Werdykt audytu | Partia / decyzja |
|---|---|---|
| 1 | PASS WITH RESERVATIONS | 0 — aktualny checkpoint bez zmiany historii |
| 2 | FAIL | 4 — test safety i odtwarzalny baseline |
| 3 | FAIL | 1 — migracja i integralność SQLite |
| 4 | FAIL lokalnego checkoutu | 0/4 — cache, import i allowlista rename |
| 5 | FAIL | 3 — świeży kanon w trwałej sesji, config truth |
| 6 | FIX FIRST | 3 — pinning i izolacja bramki Żanety |
| 7 | FIX FIRST | 3 — playback truth i atomowe cancel |
| 8 | PASS WITH RESERVATIONS | 3 — tylko wspólny resolver, bez nowego API |
| 9 | FAIL | 2 — supervisor, PTT, TCC i lifecycle |
| 10 | FIX FIRST | 4 — daemon-owned truth w panelu |
| 11 | FAIL | 2 — hook, scheduler i pełny installer |
| 12 | STOP | 1 — rollback, intake, executory i resume |
| 13 | FAIL | 4 — clean clone, build, HOME audit i assety |
| 14 | STOP | 5 — prawdziwe bramki i nowy kandydat |
| 15 | NOT DUE / recorder FAIL | 5 — wersjonowana obserwacja od zera |

## 6. Obsługa błędów i stany awaryjne

Każda operacja o skutku trwałym ma rozróżniać:

- `not_started` — brak mutacji;
- `in_progress` — intake zamknięty, faza zapisana w journalu;
- `committed` — walidacja po operacji zakończona;
- `rolling_back` — jawna rekonstrukcja z journalu;
- `rolled_back` — stan sprzed operacji zweryfikowany;
- `blocked` — brak bezpiecznej automatycznej kontynuacji.

Nie wolno zamieniać wyjątku na pustą listę, zielony status ani `exit 0` bez
dowodu. Stan `unknown` jest poprawniejszy niż zmyślone `healthy`.

Wykonanie zatrzymuje się natychmiast, gdy:

- dowód integralności lub zachowania danych nie przechodzi;
- wykryto drugiego właściciela audio/runtime'u;
- persona albo efektywna konfiguracja różni się między powierzchniami;
- pojawia się niezinwentaryzowany proces legacy;
- patch innego właściciela przecina pliki bieżącej partii;
- pełny gate ma nową awarię bez sklasyfikowanej przyczyny.

## 7. Strategia testów i recenzji agentów

Każdy potwierdzony defekt przechodzi ten sam cykl:

1. test regresyjny RED odtwarzający konkretną wadę;
2. najmniejsza zmiana produkcyjna dająca GREEN;
3. testy modułu i całej partii;
4. niezależny agent sprawdzający zgodność ze specyfikacją;
5. niezależny review jakości, bezpieczeństwa i skutków ubocznych;
6. poprawki i ponowna recenzja aż do braku ustaleń krytycznych/wysokich;
7. wąski commit obejmujący jedną spójną zmianę.

Współdzielony worktree oznacza jednego agenta implementującego naraz. Pozostali
mogą wykonywać wyłącznie odczytowe analizy nieprzecinających się powierzchni.
Root integruje zmiany, pilnuje indeksu i uruchamia bramki. Agent nie recenzuje
własnej implementacji jako jedyny reviewer.

Hierarchia weryfikacji:

- test regresyjny;
- testy danego subsystemu;
- pełna kolekcja z porównaniem do zamrożonego baseline'u;
- clean-clone build/install/doctor;
- hermetyczny dry-run i rollback rehearsal;
- odczytowa weryfikacja hosta;
- dopiero po zatwierdzeniu: wdrożenie i obserwacja live.

Automatyczne testy nie mówią przez głośnik. Odsłuch operatora jest oddzielną,
jawną bramką i nie może zostać zastąpiony statusem enqueue albo kodem wyjścia.

## 8. Git, commity i wdrożenie

Nie wolno resetować, czyścić ani commitować zmian należących do Fable'a/Ozzy'ego.
Każdy commit jest przygotowany z jawnej listy ścieżek i sprawdzony przez
`git diff --cached`. Zgoda Ozzy'ego z 2026-07-18 obejmuje commity w tej sesji;
nie obejmuje pushowania, force-move tagów ani wdrożenia.

Kod produkcyjny pozostaje nietknięty do osobnej bramki deploy. Po wdrożeniu
nowego kandydata:

1. rozpoczyna się nowy ledger siedmiodniowej obserwacji;
2. donorzy pozostają;
3. finalny tag nie powstaje przed sign-offem;
4. merge do `main` nadal nie jest automatyczny.

## 9. Definicja sukcesu

Projekt napraw jest zakończony dopiero wtedy, gdy:

- wszystkie potwierdzone findingi krytyczne i wysokie mają test RED/GREEN;
- rollback całej rodziny SQLite jest hermetycznie udowodniony;
- intake jest realnie zamykany, a resume rekoncyliuje stan po przerwaniu;
- istnieje dokładnie jeden daemon, jedna sesja mózgu i jeden właściciel audio;
- zmiana kanonu trafia do następnego wejścia trwałej sesji;
- panel, config explain i doctor pokazują stan daemona bez lokalnych domysłów;
- scheduler, hooki i instalator działają z pełnego manifestu;
- testy nie mogą przypadkiem uruchomić realnego audio;
- clean clone buduje i instaluje paczkę offline;
- release audit wykrywa aktywne referencje legacy i nieakceptowalne assety;
- wersjonowany recorder nie pozwala sfabrykować siedmiu dni;
- nowy kandydat przechodzi pełne bramki przed wdrożeniem;
- siedem dni obserwacji po tym wdrożeniu kończy się jawnym sign-offem Ozzy'ego.

Do tego momentu Release 1 pozostaje kandydatem, nie fundamentem finalnym.
