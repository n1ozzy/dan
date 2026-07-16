# DAN — projekt konsolidacji jednego produktu

Data: 2026-07-16
Status: specyfikacja do zatwierdzenia przed planem wykonawczym

## 1. Cel

Powstaje jeden produkt o nazwie **DAN**. Nie jest to nowy projekt obok obecnych
`dan`, `jarvis` i `DANv2`. Bazą techniczną jest aktualne repozytorium
`/Users/n1_ozzy/Documents/dev/jarvis`, które zostanie przemianowane i przejmie
wyłącznie sprawdzone elementy z pozostałych projektów.

Efekt dla Ozzy'ego:

- jeden nadzorowany runtime uruchamiany przy logowaniu;
- jeden panel, pamięć, kanon postaci, system głosu, broker i kolejka;
- jedno jawne miejsce dla każdego ustawienia;
- te same głosy, tempo, mastering i kolejność niezależnie od tego, czy źródłem
  jest panel, terminal, Claude, Codex, OpenClaw, standup, dobranocka czy Radio DAN;
- brak ukrytych fallbacków, starych skillów i kopii konfiguracji zmieniających
  zachowanie bokiem;
- repozytorium możliwe do przekazania koledze z Makiem M5 bez prywatnych danych
  Ozzy'ego.

## 2. Decyzja architektoniczna

### 2.1 Wybrana droga

Obecny kod Jarvisa staje się DAN-em. Zachowujemy jego dojrzałe elementy:

- trwały daemon i lokalne API;
- SQLite jako stan rozmów, pamięci, zdarzeń i kolejki;
- cienki panel menu bar;
- wspólny `TurnOrchestrator` dla tekstu i głosu;
- adaptery mózgów;
- PTT, STT, anti-echo, anulowanie i narzędzia;
- testy kontraktów oraz diagnostykę runtime.

Nazwa `Jarvis` znika z aktywnego produktu. Docelowe nazwy:

| Element | Nazwa docelowa |
|---|---|
| produkt i panel | DAN |
| pakiet Pythona | `dan` |
| CLI | `dan` |
| daemon | `dand` |
| launchd | `com.dan.dand` |
| prywatny runtime | `~/.dan/` |
| repo po cutoverze | `/Users/n1_ozzy/Documents/dev/DAN` |

`Jarvis` może pozostać tylko w historii Git i dokumentach opisujących migrację.
Nie może występować w aktywnym kodzie, konfiguracji, procesach, panelu, skillach
ani instrukcjach użytkowych.

### 2.2 Odrzucone drogi

Nie tworzymy:

- czystego repo od zera — byłby to piąty projekt i utrata sprawdzonych kontraktów;
- osobnego `voice_system`, Voice Hubu ani repo głosowego — byłby to drugi właściciel
  runtime i kolejna granica do rozjechania;
- trwałych mostów do starych repozytoriów — symlink nie jest konsolidacją;
- wielkiego wdrożenia fundamentu, Radia, instalatora i połączeń telefonicznych w
  jednym skoku.

## 3. Zweryfikowany stan wejściowy

Poniższe fakty zostały sprawdzone 2026-07-16, a nie przepisane ze starego planu.

### 3.1 Runtime

- działa stary feeder PID `40251`, stary broker PID `68009` i Supertonic PID
  `54584` na `127.0.0.1:7788`;
- `/tmp/dan-voice/req/` jest pusty, feeder czeka na końcu playlisty;
- działa `jarvisd` PID `48068` na `127.0.0.1:41741`;
- `jarvisd` został ręcznie uruchomiony przez `screen` z katalogu i środowiska
  `/Users/n1_ozzy/Documents/dev/menubar-controller`, mimo że ładuje kod z repo
  `jarvis`;
- agent `com.ozzy.jarvisd` jest załadowany, lecz nie działa. Ostatni start przegrał
  kolizję portu z ręcznie uruchomionym daemonem;
- obecny `jarvisd` publikuje mowę do starego `/tmp/dan-voice/req` w trybie
  `external_shared`. Nie dostaje potwierdzenia odtworzenia i nie potrafi anulować
  opublikowanego requestu. Jego własna kolejka SQLite nie jest końcowym
  właścicielem playbacku.

To jest podwójny runtime. Stan „API pokazuje OK” nie oznacza jeszcze, że DAN ma
jeden broker ani że barge-in przerwie już opublikowany dźwięk.

### 3.2 Rozjazd konfiguracji

Aktywny `/Users/n1_ozzy/.jarvis/jarvis.toml` ustawia głos `M3`, profil
`bastard` i prędkość runtime `1.35`. Aktualne
`/Users/n1_ozzy/.config/voice/personas.toml` ustawia:

- `dan = M3/raw/1.28`;
- `jarvis = M3/clean/1.35`;
- `danusia = F4/clean/1.28`.

Istnieje czwarte źródło: ignorowany przez Git
`/Users/n1_ozzy/Documents/dev/dan/state/overrides.json`, zapisywany między innymi
przez stary panel. Obecnie zawiera dla Jarvisa `jarvis_supertonic_voice = M2` i
`jarvis_speed = 1.4`. Resolver `dan_core/say.py` naprawdę wybiera jednak
`M2/clean/1.35`: osobny override głosu wygrywa z `personas.toml`, natomiast
`jarvis_speed` nie ma czytnika, a jawna persona bierze tempo z `personas.toml`.
Wywołanie bez jawnej persony wpada jeszcze w globalne `supertonic_speed = 1.2`.
Jeden plik zawiera więc jednocześnie aktywny override, martwy klucz i wartość
globalną działającą tylko na części tras.

Komentarze w `jarvis.toml` nadal opisują wcześniejsze `M2/clean`, mimo że wartości
wykonawcze mówią co innego. Panel i tabela `settings` w SQLite tworzą dodatkową
warstwę trwałych ustawień. To nie jest jeden kanon konfiguracji.

`personas.toml` nie zawiera tylko trzech głównych postaci. Jest w nim także
obsada dobranocki i Radia (`zdzicho`, `krysia`, `komentator`, `spiker`, `ksiadz`,
`typ_z_telefonu`, `blondyna`, `zagadka`, `radiowiec`, `zaneta`), profile gołych
kodów `M1`–`F5`, ustawienia `dsp` oraz komentarze z decyzjami odsłuchowymi
Ozzy'ego. Obok leży sześć różnych backupów `personas.toml.bak-*`. Backupy i
komentarze są materiałem dowodowym, nie równoległym runtime ani automatycznie
obowiązującym kanonem.

Aktywne blendy Supertonic nie są dziś wersjonowane. Dwadzieścia plików JSON pod
`~/.cache/supertonic3/custom_styles/` zawiera między innymi `M2M1`, `M1M3`,
`F1M1`, `FTRIO`, `ROBOT`, `ROBOT75` i serię `F2EXT*`; bieżące persony odwołują
się do części z nich. Cache nie może pozostać właścicielem assetu, bo zwykłe
czyszczenie cache zmieni lub wyłączy głosy.

Żaneta ma dwa jawnie różne tory. Kanon przygotowanych nagrań to pipeline
Chatterbox V3 z `tools/jarvis/chatterbox/v3/gen_zaneta.py`, referencją
`ref_lily_zmysl` i zaakceptowanymi artefaktami. `F2/raw/1.15` z DSP jest
fallbackiem live w starym torze. Nie znaleziono dowodu, że Chatterbox V3 jest
obecnie silnikiem żywej kolejki, ale jego generator, referencje, parametry i
werdykty są kanonem produkcyjnym, którego nie wolno zgubić.

Źródła nie zgadzają się również co do Jarvisa: aktywny `personas.toml` i bieżące
instrukcje wskazują `M3/clean/1.35`, a jeden dokument w repo bazowym nadal podaje
`M5/bastard/1.4`; starsze handoffy wskazują jeszcze inne wartości. Ostateczna
mapa głosów nie może powstać z pamięci ani przez wybranie najnowszego komentarza.
Wymaga tabeli decyzji, testu rzeczywistego resolvera dla każdej trasy i
zatwierdzonego odsłuchu każdej postaci. Nieaktualny komentarz w
`~/.config/voice/personas.toml` nadal twierdzi, że `dsp` czyta feeder, nie broker;
migracja usuwa takie kłamliwe komentarze razem z martwymi kluczami.

### 3.3 Żywi konsumenci poza repo

Aktywne zależności obejmują między innymi:

- `~/.agents/skills/gadanie/`, `dobranocka/`, `danusia-live/`, `gpt-say/`;
- skille persony i utrzymania persony pod `~/.agents/skills/` i
  `~/.claude/skills/`, w tym `dan-persona`, `maintaining-dan-persona`,
  `dobranocka`, `danusia-live`, `trio-live`, `higiena` i `screen-control`;
- symlinki skryptów `~/.claude/skills/gadanie/*.sh` oraz symlinki
  `trio-live` i `screen-control` w `~/.claude/skills/` i `~/.codex/skills/`;
- globalne `~/.claude/CLAUDE.md` i `~/AGENTS.md`, które ładują kanon lub
  zawierają ścieżki do starego repo;
- `~/.claude/bin/voice-standup.sh`;
- `~/.claude/hooks/tts-loud-thinking.sh`, `config-guard.sh` i
  `orphan-reaper.sh`;
- `~/.claude/skills/voice-doctor/`;
- `~/.codex/rules/default.rules` oraz aktywne skille i pamięci proceduralne
  Codexa ze ścieżkami do starego repo i locków głosowych;
- aktywny skill `~/.openclaw/workspace/skills/radio-dan/` — `openclaw skills
  list` pokazuje go jako `ready`;
- działający gateway OpenClaw z `ai.openclaw.gateway.plist`; skill
  `danv2-enhanced` istnieje, ale jest jawnie wyłączony, więc jest kandydatem do
  audytu/wycofania, a nie żywym runtime;
- plisty `com.dan.voice-broker`, `com.ozzy.voice-standup`, `com.ozzy.higiena`,
  `com.ozzy.menubar-controller` i `com.ozzy.jarvisd`; podczas weryfikacji
  voice-broker, voice-standup i higiena nie były załadowane, ale ich pliki nadal
  mogą wskrzesić stary tor;
- `~/.config/voice/personas.toml`, `pronunciations.toml`, `gains.json`;
- `~/.jarvis/jarvis.toml`, `jarvis.db`, `bin/jarvisd`, `backups/` i
  `model_cache.json`;
- kontrakty chwilowe pod `/tmp/dan-voice`, `/tmp/dan-listen`,
  `/tmp/dan-trio-live` oraz locki `dan-*`;
- niezależny wyłącznik hooka `~/.claude/hooks/tts-loud-thinking.sh` pod
  `/tmp/claude-loud-thinking/OFF`, który nie pasuje do skanu `/tmp/dan-*`.

Usunięcie starego repo bez migracji tych konsumentów wyłączy głos albo pozostawi
martwe instrukcje, które będą próbowały go wskrzesić.

### 3.4 Donorzy i test wejściowy

| Źródło | Stan | Rola |
|---|---|---|
| `Documents/dev/jarvis` | aktywne repo, jedyny nieśmieciowy szkielet produktu | baza DAN-a |
| `Documents/dev/dan` | aktywny tor głosu i radia, repo brudne | donor funkcji, konfiguracji i danych |
| `Documents/dev/DANv2` | duplikat/prototyp, repo mocno brudne | donor testów i zachowań tylko po porównaniu |
| `Documents/dev/menubar-controller` | osobne repo Git bez commitów | donor funkcji operatorskich panelu |
| `Desktop/djdan-visualizer.html` | samodzielny prototyp wizualizera | donor widoku Radio DAN |

Repo `dan` ma niezacommitowane zmiany w `config/persona/DAN.md` i
`tools/jarvis/voice_broker.py`; repo `DANv2` jest szeroko zmodyfikowane. To są
zmiany Ozzy'ego. Migracja najpierw zapisuje ich diff i sumy SHA-256, a następnie
przejmuje świadomie wybrane treści. Nie wolno ich nadpisać, stashować ani
„czyścić” dla wygody.

Kontrolny, celowo ograniczony zestaw testów w repo bazowym dał
`141 passed, 1 failed`. Nie jest to baseline całego produktu: pełna kolekcja
zawiera `2176` testów. Jedyny błąd w zestawie kontrolnym ujawnił świadomą, ale
zakazaną zależność runtime: `jarvis/brain/context_builder.py` wskazuje
bezpośrednio `/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`.
Przed Wydaniem 1 trzeba bez audio i poza aktywną kolejką sklasyfikować pełny
zestaw testów, a następnie zapisać raport przejść, błędów, skipów i testów
niebezpiecznych dla żywego runtime.

Repo bazowe jest obecnie na `spike/jarvis-local-runtime-check`, gdzie znajduje
się ta specyfikacja; nie wolno zakładać, że stary `main` jest właściwą bazą.
Istnieją też niezintegrowane gałęzie `claude/fix-brain-wiring`,
`claude/amazing-hawking-c80907`, `rescue/*`, `spike/*` i inne refy z pracą nad
personą, adapterami i runtime. Przed masowym rename każda taka gałąź dostaje
decyzję na poziomie commitów: `przejąć`, `już obecne`, `odtworzyć z testem` albo
`odrzucić z powodem`. Nie scalamy ich w ciemno.

### 3.5 Istniejący stan `~/.dan` i materiały wejściowe

`~/.dan/` nie jest pustą nową przestrzenią. Zawiera `memory.db` z tabelami
rozmów, tur, bloków pamięci i skompilowanych kontekstów oraz pusty katalog
`voice/`. Nie potwierdzono, że baza pochodzi z bieżącego Jarvisa; aktywne
odwołania prowadzą także do DANv2 i wyłączonego skilla OpenClaw. Dlatego nie
wolno jej nadpisać ani automatycznie uznać za schemat docelowy.

Jednorazowe `lsof` nie pokazuje obecnie uchwytu do `memory.db`, ale nie dowodzi
braku pisarza otwierającego bazę tylko na czas operacji. Statyczne odwołania
wskazują co najmniej `DANv2/memory/store.py`, `DANv2/dan.py`, narzędzia DANv2 i
wyłączony skill OpenClaw. Przed migracją trzeba ustalić wszystkie entrypointy
zapisu statycznie oraz krótkim monitoringiem dostępu podczas normalnych operacji,
a następnie je zatrzymać. Nieznany pisarz blokuje migrację i usunięcie źródła.

W katalogu `~/.jarvis/` żyją osobna baza, konfiguracja, zainstalowany `jarvisd`,
backupy i cache modeli. Migracja wymaga rozpoznania obu schematów, kopii SQLite,
liczników rekordów, jawnej polityki deduplikacji i raportu po imporcie. Samo
przemianowanie katalogu nie jest migracją danych.

Stary `Documents/dev/dan/docs/RADIO-DAN-KONSOLIDACJA-PLAN.md`,
`Documents/summary.md` i `Documents/opinia-planu.md` są wejściami badawczymi.
Pierwszy plan nie pozostaje aktywnym planem wykonawczym, bo zakłada odrębny
system głosowy odrzucony w tej specyfikacji. Przed archiwizacją trzeba jednak
wyciągnąć z tych plików zweryfikowane wymagania, testy regresji i decyzje Radia
oraz zapisać ich sumy SHA-256 w manifeście. Prywatnych podsumowań nie wolno
automatycznie commitować do publicznego repo.

## 4. Docelowy model własności

### 4.1 Jedna wartość — jeden właściciel

„Jedno miejsce” oznacza jeden produkt i jednego właściciela każdej wartości, a
nie jeden plik z całym światem. Podział jest jawny i bez nakładających się kluczy:

| Rodzaj prawdy | Jedyny właściciel |
|---|---|
| charakter DAN-a | wersjonowany `config/persona/DAN.md` |
| biblioteka głosów, tempo, mastering | wersjonowane `config/voice/personas.toml` |
| style i blendy Supertonic | wersjonowane `config/voice/custom_styles/` z manifestem |
| wymowa | wersjonowane `config/voice/pronunciations.toml` |
| gain i profile masteringu | wersjonowane pliki pod `config/voice/` |
| offline pipeline'y głosowe i ich parametry | kod adapterów w module głosu oraz manifesty pod `config/voice/pipelines/` |
| rozwiązanie konfiguracji renderu | jeden resolver wewnątrz `dand`, zapisujący `RenderSnapshot` |
| ustawienia tej instalacji | `~/.dan/config.toml` |
| dane właściciela | `~/.dan/owner.toml` |
| tokeny i sekrety | `~/.dan/secrets.env` z prawami `0600` |
| rozmowy, pamięć, kolejka, radio, zdarzenia | `~/.dan/dan.db` |
| globalny PTT i hotkey | wyłącznie `dand`, bez drugiego listenera w panelu lub skillach |
| logi i pliki chwilowe | `~/.dan/logs/` i `~/.dan/runtime/` |

Pliki wersjonowane zawierają dane produktu możliwe do udostępnienia. `~/.dan/`
zawiera wyłącznie dane instalacji i nigdy nie trafia do Git.

Instalator traktuje istniejące `~/.dan/` jako dane do migracji, nie pusty katalog.
Przed utworzeniem `dan.db` robi spójną kopię każdej zastanej bazy, rozpoznaje jej
schemat i zapisuje raport importu. Nie nadpisuje `memory.db`, nie kopiuje na żywo
plików WAL/SHM i nie usuwa źródła przed zaakceptowanym rollbackiem.

Docelowy `dan.db` jest ewolucją aktualnego schematu `jarvis.db` przez
wersjonowane migracje. Zachowuje jego kolejkę, zdarzenia, ustawienia, rozmowy i
model pamięci. Unikalne rekordy z `~/.dan/memory.db` są importowane z identyfikatorem
źródła oraz jawnym mapowaniem kolizji; nie zastępują tabel Jarvisa na podstawie
samej zgodności nazwy. Pełny schemat i migratory powstają w implementacji, ale
kontrakt zachowania danych jest ustalony przed planem.

Runtime nie scala kilku wartości tego samego ustawienia. Panel zapisuje przez API
do właściwego właściciela, nie tworzy override'u w SQLite. `dan config explain
<klucz>` pokazuje wartość, źródło i ścieżkę. Nieznany lub zdublowany klucz blokuje
start z czytelnym błędem.

API konfiguracji odrzuca nieznany, martwy lub należący do innego właściciela
klucz przed zapisem. Nie może powstać rekord w SQLite ani pliku stanu, którego
resolver nie czyta. Test `zapis -> restart -> config explain -> render` chroni
przed powrotem takich atrap jak obecne `jarvis_speed`.

Producent wysyła do API intencję mowy: tekst, personę, źródło, sesję oraz
dozwolone jawne parametry requestu. Nie rozwiązuje sam głosu ani masteringu.
Resolver wewnątrz `dand` odczytuje kanoniczną konfigurację dokładnie raz,
waliduje assety i zapisuje kompletny `RenderSnapshot` atomowo z rekordem kolejki.
Broker wykonuje zapisany snapshot i nie rozwiązuje go ponownie przy playbacku.
Panel, CLI, hook oraz skille nie importują własnych map ani resolvera.

`state/overrides.json` jest źródłem jednorazowego importu i raportu konfliktów,
nie elementem docelowej drabiny konfiguracji. Aktywne wartości są porównywane z
TOML i zatwierdzane; martwe lub nieczytane klucze są raportowane, nie przenoszone
na ślepo. Po cutoverze panel nie tworzy nowego pliku override obok właściciela
ustawienia.

Biblioteka głosów może być organizacyjnie rozdzielona na pliki `core` i `radio`,
ale oba pozostają wersjonowaną częścią produktu. Sama nazwa roli lub treść +18
nie czyni konfiguracji prywatną. Poza repo pozostają dopiero dane właściciela,
historia, nagrania prywatne oraz assety bez prawa redystrybucji.

### 4.2 Persona i prywatność

Istnieje jeden kanon postaci DAN-a. Nie powstają kopie dla Claude, Codexa,
OpenClaw, Radia ani instalacji kolegi.

Kanon postaci jest wersjonowalny i przenośny. Imię właściciela, prywatna historia,
fakty o Ozzym i relacja z konkretną osobą pochodzą z lokalnego `owner.toml` oraz
pamięci. Dzięki temu kolega dostaje tę samą postać DAN-a, ale nie prywatne dane i
nie cudzą historię.

Obecny `config/persona/DAN.md` jest źródłem zachowania w migracji, lecz przed
publikacją przechodzi jawny audyt rozdziału danych: mechanika charakteru zostaje
bez ugrzeczniania, a imię właściciela i właścicielskie fakty są parametrami z
`owner.toml`. Testy muszą udowodnić, że separacja prywatności nie tworzy drugiej,
łagodniejszej persony ani kopii promptu.

Adaptery hostów zawierają wyłącznie sposób wywołania `dan` i kontekst techniczny.
Nie mogą zawierać kopii persony, map głosów ani własnych fallbacków tonu. Brak
kanonu jest błędem widocznym, a nie powodem do uruchomienia grzecznej podróbki.

### 4.3 Daemon, panel i mózgi

`dand` jest jedynym procesem posiadającym stan produktu. Startuje przez jeden
agent `launchd` i pilnuje pojedynczej instancji. Panel jest cienkim klientem API:

- pokazuje stan daemonu, mózgu, pamięci, mikrofonu, brokera, kolejki i Radia;
- wysyła intencje start/stop/pauza/anuluj/zmień ustawienie;
- nie czyta `/tmp`, nie wykonuje `pgrep`, nie zabija procesów i nie zapisuje
  konfiguracji samodzielnie;
- po utracie daemonu pokazuje `offline`, zamiast uruchamiać alternatywny tor.

Claude, Codex i inni providerzy są wymiennymi mózgami. Nie są właścicielami
tożsamości, pamięci, kolejki ani panelu. Sesja providera nie jest pamięcią DAN-a.
Silnik TTS może działać jako dziecko nadzorowane przez `dand`, ale nie ma własnego
launchd, kolejki ani polityki playbacku. Jeden agent launchd posiada całe drzewo
procesów produktu.

Globalny listener PTT/hotkey jest zasobem wyłącznym tak samo jak player audio.
Uruchamia go i raportuje wyłącznie `dand`. Panel wysyła intencje przez API, a
stary listener musi być zatrzymany przed startem nowego; cold start sprawdza, że
jedno naciśnięcie tworzy dokładnie jedno zdarzenie PTT.

## 5. Głos: broker, kolejka i feeder

### 5.1 Jeden odtwarzacz

Broker działający wewnątrz `dand` jest jedynym właścicielem syntezy i playbacku.
Końcowy runtime nie korzysta z `external_shared`, `/tmp/dan-voice/req` ani
oddzielnego `voice_broker.py`.

Każdy producent mówi przez lokalne API lub CLI, na przykład:

```bash
dan speak --as dan "Tekst z polskimi znakami."
dan speak --as danusia "Moja kolej."
dan queue list
dan queue cancel <id>
dan queue flush --session radio
```

Hook, skill, panel, standup, Codex i OpenClaw nie uruchamiają brokera i nie
odtwarzają WAV-ów bezpośrednio.

### 5.2 Kontrakt kolejki

Przychodząca intencja i trwały rekord kolejki to dwa różne kontrakty. API
przyjmuje intencję producenta, a dopiero `dand` rozwiązuje render i tworzy rekord.
Każdy zapisany request posiada co najmniej:

- identyfikator, źródło, sesję i uczestnika;
- tekst w UTF-8/NFC;
- nazwę persony oraz niezmienny snapshot rozwiązanej konfiguracji renderu:
  silnik i jego wersję, głos/style, tempo, profil masteringu, DSP, wymowę, gain
  oraz wersje lub SHA-256 wszystkich użytych assetów;
- priorytet, pasmo (`live`, `normal`, `background`) i politykę przerwania;
- kolejność w obrębie wypowiedzi;
- status `queued`, `synthesizing`, `speaking`, `done`, `cancelled` albo `failed`;
- czas przyjęcia, startu syntezy, startu audio i zakończenia;
- błąd oraz potwierdzenie rzeczywistego odtworzenia.

Request nie może otrzymać statusu `queued`, dopóki wszystkie wymagane pola
snapshotu nie są wypełnione i zweryfikowane. Brak pola lub assetu kończy intencję
błędem przed zapisem do kolejki, zamiast tworzyć częściowy rekord, który broker
uzupełni po swojemu.

Kolejka jest trwała w SQLite. Broker bierze dokładnie jeden element do playbacku.
Anulowanie zatrzymuje generowanie, usuwa oczekujące fragmenty tej wypowiedzi i
kończy aktualny player przed przyjęciem następnej kwestii. Jeden proces, lock
instancji i test nakładania chronią przed dwoma odtwarzaczami.

Brak dostępnego silnika, głosu albo pliku konfiguracji kończy request błędem.
Nie ma cichego przejścia na XTTS, inny głos, inne tempo ani bezpośredni `afplay`.
Supertonic jest silnikiem live fundamentu. Istniejący Chatterbox V3 dla
przygotowanych kwestii Żanety wchodzi do Wydania 1 jako jawny pipeline offline,
a nie automatyczny silnik żywej kolejki. XTTS, ElevenLabs oraz Chatterbox live
mogą działać wyłącznie jako jawne, przetestowane adaptery; nie są cichym
fallbackiem.

W starym torze `dsp` nie ginie w feederze: `dan_core/say.py` rozwiązuje je z
`personas.toml`, wysyła w polu `profile`, a broker rozpoznaje surowy łańcuch
FFmpeg. To działa, lecz przeciążenie pola `profile` jest niejawne. Nowy kontrakt
przechowuje DSP lub nazwany preset renderu osobno i utrwala dokładnie to, co
zostało użyte do danego requestu.

### 5.3 Feeder bez polowania na plik

Feeder staje się schedulerem wewnątrz `dand`, a nie osobnym bashem sprawdzającym
co trzy sekundy rosnący plik.

- przygotowana playlista jest importowana transakcyjnie do sesji;
- dopisanie tekstu do starego pliku po imporcie niczego nie uruchamia;
- scheduler podaje następny segment dopiero przy wolnym miejscu i zgodnie z
  pasmem, pauzą oraz polityką przerwania;
- postęp jest w bazie, więc restart nie dubluje ani nie gubi segmentów;
- treść live trafia tym samym API jako nowy segment, bez drugiej kolejki;
- panel pokazuje „co gra”, „co czeka”, źródło, uczestnika i możliwość anulowania.

To usuwa dzisiejszą pułapkę, w której przypadkowa nowa linia w `live/*.playlist.txt`
natychmiast zaczyna grać.

## 6. Panel DAN

Rozwijany jest istniejący panel z `jarvis/panel`, po przemianowaniu na `dan/panel`.
Nie powstaje drugi widget.

Z osobnego `menubar-controller` przenosimy tylko funkcje przydatne operatorowi:

- widoczny stan daemonu, brokera, TTS, STT, kolejki i aktywnej wypowiedzi;
- pauza, wznów, pomiń, anuluj i bezpieczny restart przez API;
- zdrowie usług i jednoznaczne błędy;
- informacje o sesjach/modelach i użyciu, jeśli daemon potrafi je wiarygodnie
  dostarczyć;
- powiadomienie „padło” i „wróciło”.

Nie przenosimy jego bezpośrednich operacji na `/tmp`, `launchctl`, `pkill`, pliki
requestów ani hardkodowane ścieżki do repo. Najpierw powstaje endpoint daemonu,
potem kontrolka panelu.

## 7. Radio DAN — kolejny etap tego samego produktu

Radio nie jest osobnym daemonem, skillem ani projektem. Po ustabilizowaniu
fundamentu dostaje zakładkę `Radio DAN` w głównym panelu.

Docelowy model:

- sesja Radia ma tryb, temat, uczestników, segmenty i osobną pełną historię;
- DAN jest gospodarzem i właścicielem ciągłości;
- uczestnik ma jawne `identity + brain/provider + voice`;
- Ozzy może dołączyć mikrofonem, a Codex może być osobnym uczestnikiem;
- scheduler studia pilnuje kolejności, backpressure i maksymalnie jednej
  oczekującej wypowiedzi uczestnika, więc rozmówcy nie zapychają brokera;
- generowanie jest hybrydowe: live domyślnie, ale można dołączyć temat,
  scenariusz lub przygotowany segment;
- dobranocka, standup, roast, ping-pong, gość i telefon są formatami tej samej
  sesji, nie oddzielnymi systemami;
- pełna historia Radia zostaje w przestrzeni Radia; do głównej pamięci DAN-a
  trafiają wyłącznie zatwierdzone fakty i decyzje;
- wizualizer z `~/Desktop/djdan-visualizer.html` jest dawcą widoku reagującego na
  zdarzenia WebSocket, nie samodzielnym runtime;
- prawdziwy zdalny telefon jest osobnym późniejszym etapem. Pierwsza wersja to
  lokalne studio: Ozzy + DAN + opcjonalny Codex i postacie.

Radio ma osobną specyfikację i plan dopiero po przejściu bram fundamentu.

## 8. Migracja donorów

Każdy element przed przeniesieniem dostaje wpis w manifeście: źródło, konsument,
decyzja, test i docelowy właściciel.

### 8.1 `Documents/dev/dan`

Do porównania i selektywnego przejęcia:

- aktualny kanon persony;
- algorytmy brokera, Supertonic, mastering, wymowa i gain;
- pełną mapę obsady, kody `M1`–`F5`, DSP, komentarze odsłuchowe i sześć backupów
  `personas.toml` jako materiał do zatwierdzenia aktualnych wartości;
- ignorowany przez Git `state/overrides.json` jako aktywne źródło konfliktów,
  wraz z mapą: `klucz -> czytnik -> trasy -> faktyczny wynik -> decyzja importu`;
- dwadzieścia custom styles z cache Supertonic, przeniesione do wersjonowanych
  assetów z sumami SHA, źródłem, licencją i procedurą instalacji;
- pipeline Żanety Chatterbox V3: generator, parametry, referencje, zależności,
  zaakceptowane artefakty i fallback live — po rozstrzygnięciu praw dystrybucji;
- zachowanie `gadanie`, `dobranocka`, `trio-live`, standupu i hooków;
- zachowanie repozytoryjnego skilla `voice-report` z `.claude/skills/` i
  `.agents/skills/`; nie ma potwierdzonej globalnej kopii w `~/.codex/skills/`;
- formaty Radia, prozodia i sprawdzone scenariusze;
- Voice Lab, próbki, werdykty i `_sesja-glosy-2026-07-11` jako materiał
  badawczo-produkcyjny;
- dokumentacja awarii jako źródło testów regresji.

Nie kopiujemy całych katalogów ani starych wrapperów. Zachowanie przechodzi do
modułu DAN-a, a zewnętrzny skill staje się cienkim adapterem CLI.

### 8.2 `Documents/dev/DANv2`

DANv2 nie jest bazą i nie jest uruchamiany po cutoverze. Jego unikalne testy PTT,
VAD, anti-echo, streamingu i TTS porównujemy z bazą. Przenosimy test lub zachowanie
tylko wtedy, gdy nie istnieje już w DAN-ie i ma dowód jakości. Lokalne wartości
`M1/bastard`, `M3/raw` oraz kopie pipeline'u nie są kanonem.

### 8.3 Skille, hooki, OpenClaw i launchd

Migracja odbywa się atomowo dla wszystkich aktywnych hostów:

1. manifest obejmuje pliki zwykłe, symlinki, globalne instrukcje, reguły Codexa,
   plisty, zainstalowane binaria, każdego producenta głosu oraz wszystkie
   zastane warianty JSON i kontrakty `/tmp`;
2. instalator generuje cienkie adaptery wywołujące `dan` i wskazujące jeden
   kanon persony bez kopiowania jego treści;
3. testuje Claude, Codex, OpenClaw, standup, hook, dobranockę, `gpt-say`,
   `voice-report`, Trio/screen-control i panel;
4. zachowuje jawny odpowiednik wyłącznika hooka: sesyjne
   `dan voice hook off|on|status` oraz osobno udokumentowane ustawienie trwałe;
5. hook MessageDisplay działa fail-open: przy niedostępnym `dand` kończy szybko
   kodem `0`, nie blokuje odpowiedzi i nie uruchamia starego brokera ani fallbacku;
6. dopiero potem wyłącza stare skrypty i plisty;
7. skan wszystkich aktywnych korzeni musi zwrócić zero wykonywalnych odwołań do
   starych repo, `/tmp/dan-*` i `/tmp/claude-loud-thinking`.

Wspólny kontrakt adaptera maszynowego to CLI/API DAN-a, nie ścieżka do feedera:
`dan speak --json --as <persona> --session <id> --source <host> --stdin` przyjmuje
tekst UTF-8, a na stdout zwraca co najmniej `request_id` i status przyjęcia.
Kod `0` oznacza zapis kompletnego requestu, kod niezerowy — brak przyjęcia.
OpenClaw, Claude, Codex, `gpt-say`, standup i hook używają tego samego kontraktu;
wyjątkiem jest wyłącznie zewnętrzny wrapper MessageDisplay, który przy błędzie
loguje brak głosu i sam kończy `0`, aby nie zablokować odpowiedzi hosta.

`~/.openclaw/workspace/skills/radio-dan` jest żywe i musi zostać zastąpione
adapterem podczas cutoveru, nie skasowane wcześniej. Gateway
`ai.openclaw.gateway` pozostaje działającym hostem integracji; wyłączony skill
`danv2-enhanced` dostaje decyzję w manifeście, lecz nie jest przedstawiany jako
żywy runtime. Globalne `~/.claude/CLAUDE.md`, `~/AGENTS.md`,
`~/.codex/rules/default.rules` oraz symlinki skillów muszą po cutoverze wskazywać
nowego właściciela.

Skan rozróżnia aktywną instrukcję od historii. Reguły, symlinki, skille i
pamięci proceduralne, które mogą zostać wstrzyknięte do nowej sesji, są
migrowane albo wycofywane. Rollouty, logi, Git i dokumenty migracji mogą opisywać
stare ścieżki, ale są oznaczone jako historyczne i nie mogą być wykonywane ani
traktowane jako bieżąca prawda. Nie stosujemy ślepego `replace` po całym katalogu
domowym.

`~/.claude/archive/` nie jest źródłem aktywnej prawdy i nie jest modyfikowane.
Historyczne kwarantanny nie są kopiowane do produktu.
`_sesja-glosy-2026-07-11` nie jest kwarantanną — ma żywych konsumentów w Voice
Lab i generatorach.

## 9. Etapy dostarczenia

Projekt jest zbyt duży na jeden bezpieczny plan wykonawczy. Dzielimy go na trzy
wydania, zawsze w tym samym repo.

### Wydanie 1 — Fundament DAN

1. zamrożenie i manifest bieżących źródeł, wartości, assetów, baz, procesów,
   plistów, symlinków, producentów głosu, formatów requestów i materiałów
   wejściowych wraz z sumami SHA-256;
2. audyt wszystkich refów Git i wybór jednej gałęzi integracyjnej z jawną
   decyzją dla pracy WIP przed rename;
3. klasyfikacja bezpieczeństwa pełnych `2176` testów i zapis pełnego baseline'u
   w izolacji, bez aktywnego mikrofonu, kolejki i audio;
4. bezpieczne wyłączenie panelu, `jarvisd`, feedera i starego toru głosu przed
   edycją aktywnych plików i kopiami baz;
5. identyfikacja i zatrzymanie wszystkich pisarzy baz, następnie spójne backupy
   i kontrolowana migracja `~/.jarvis/jarvis.db` oraz zastanego
   `~/.dan/memory.db` do wersjonowanego schematu `~/.dan/dan.db`, z licznikami,
   deduplikacją, raportem i rollbackiem;
6. wewnętrzna zmiana nazw `jarvis` na `dan` oraz przełączenie ścieżek dopiero po
   przejściu testów migracji danych;
7. jeden model konfiguracji i jeden resolver w `dand`, rozliczenie każdego klucza
   `state/overrides.json` oraz rozdzielenie persony od prywatnego profilu;
8. natywny broker i trwała kolejka jako faktyczny właściciel playbacku;
9. migracja Supertonic, pełnej obsady, custom styles, masteringu, wymowy,
   pipeline'u Żanety i testów jakości;
10. migracja panelu oraz funkcji `menubar-controller` przez API;
11. migracja wszystkich konsumentów i wyłączników do wspólnego kontraktu
    `dan speak`, `dan queue` oraz API daemona;
12. instalator, launchd, diagnostyka i prosta dokumentacja;
13. kontrolowany cutover ścieżki i nazwy produktu.

Gałąź integracyjna powstaje z faktycznie zaakceptowanej linii runtime, nie
automatycznie z `main`. Audyt refów kończy się przed masowym rename, aby ważnych
zmian nie odtwarzać później ręcznie przez tysiące konfliktów nazw.

### Wydanie 2 — Radio DAN Studio

Zakładka Radia, sesje, uczestnicy, scheduler, feeder, tryby audycji, lokalny mic,
Codex jako uczestnik, formaty i wizualizer. Obecne Trio jest testem regresji
naturalnej rozmowy, a nie kodem do kopiowania w ciemno.

### Wydanie 3 — Dystrybucja i połączenia

Instalacja ze świeżego klona na M5, eksport/import bez prywatnych danych,
licencjonowane assety głosowe oraz — później — zdalny gość/telefon.

Pierwszy szczegółowy plan po akceptacji tej specyfikacji obejmuje tylko Wydanie 1.

## 10. Cutover i rollback

Prace zaczynają się w `/Users/n1_ozzy/Documents/dev/jarvis` na wybranej po
audycie refów gałęzi integracyjnej. Nie zaczynają się z katalogu domowego. Stary
aktywny runtime jest zatrzymany przed edycją plików, z których obecnie korzysta.

Przed każdym ruchem destrukcyjnym powstaje kopia poza repo oraz manifest SHA-256.
Bazy SQLite są zatrzymane lub kopiowane mechanizmem SQLite, a nie przez kopiowanie
żywych plików wraz z przypadkowym WAL. Nie używamy `git add -A`, nie stashujemy
cudzych zmian i nie commitujemy gigabajtowej kwarantanny.

Przed backupem zamykane jest przyjmowanie nowych requestów, a każdy stary
producent dostaje status `wyłączony`, `zmigrowany` albo `odrzucony`. Kolejka jest
opróżniona lub jawnie anulowana tak, aby nie pozostał żaden rekord `queued`,
`synthesizing` ani `speaking`. Pliki starej kolejki są zinwentaryzowane i
przeniesione do backupu z manifestem, nie kasowane ślepym `rm`.

Po zatrzymaniu wszystkich pisarzy i potwierdzeniu uchwytów przez `lsof` każda
baza przechodzi checkpoint WAL, kopię przez SQLite Backup API lub CLI `.backup`,
`PRAGMA integrity_check` oraz porównanie liczników. Zwykłe `cp` żywej bazy i
`VACUUM INTO` użyte bez zatrzymania pisarzy nie są akceptowanym backupem.

Ze względu na system plików macOS nie mogą jednocześnie istnieć aktywne katalogi
`dev/dan` i `dev/DAN`. Końcowa kolejność jest następująca:

1. nowy DAN przechodzi testy niezależności w obecnej ścieżce repo `jarvis`;
2. wszystkie procesy produktu są zatrzymane, a stare agenty launchd nie mogą ich
   automatycznie wskrzesić;
3. weryfikowane są finalne backupy baz, konfiguracji, assetów, plistów i raporty
   migracji danych;
4. stare `dev/dan` trafia poza aktywne `Documents/dev` do datowanego backupu;
5. `dev/jarvis` zostaje przemianowane na `dev/DAN`;
6. instalator aktualizuje launchd, adaptery, symlinki, instrukcje globalne,
   ścieżki i remote GitHub;
7. wykonywany jest cold start, test panelu, CLI, głosu, hooka, hostów i restartu;
8. dalsza praca odbywa się z nowej sesji w `Documents/dev/DAN`.

Rollback przed bramą usuwania zatrzymuje przyjmowanie requestów i `dand`, kończy
lub anuluje element w locie, przywraca backup konfiguracji, baz i plistów oraz
pozwala ponownie uruchomić stary tor. Request przerwany przez cutover ma jawny
status i nie jest automatycznie odtwarzany drugi raz; stan po rollbacku wymaga
`speaking = null` przed otwarciem kolejki. Stare repozytoria i bazy źródłowe nie
są kasowane w tym samym kroku co cutover.

## 11. Bramki akceptacji

### 11.1 Architektura

- dokładnie jeden `dand`, jeden label launchd i jeden właściciel audio;
- brak aktywnych nazw `Jarvis`, `DANv2` i starych ścieżek poza dokumentacją
  migracji oraz historią Git;
- brak aktywnych zapisów do `/tmp/dan-*`, `/tmp/claude-loud-thinking`,
  bezpośredniego `afplay` i uruchamiania brokera przez skille, hooki lub panel;
- `dan config explain` wskazuje jednego właściciela każdego ustawienia;
- dokładnie jeden resolver w `dand` tworzy `RenderSnapshot`; producenci i broker
  nie rozwiązują ponownie głosu, tempa, masteringu, DSP ani assetów;
- próba zapisu nieznanego lub martwego klucza przez API kończy się błędem i nie
  zmienia pliku ani bazy;
- brak trwałych override'ów głosu w SQLite, panelu, `state/overrides.json` i
  innych plikach stanu obok kanonicznej konfiguracji;
- każdy stary klucz override ma decyzję importu i test dowodzący, że martwy klucz
  nie wpływa na runtime;
- działa dokładnie jeden globalny listener PTT/hotkey, należący do `dand`.

### 11.2 Runtime i głos

- start po zalogowaniu i kontrolowany restart pozostawiają dokładnie jeden
  `dand`, bez osieroconych silników i odtwarzaczy;
- request ma potwierdzony stan od przyjęcia do faktycznego zakończenia playbacku;
- żaden request nie osiąga `queued`, jeśli snapshot nie zawiera silnika i jego
  wersji, głosu/style, tempa, masteringu, DSP, wymowy, gainu oraz SHA assetów;
- anulowanie i barge-in zatrzymują bieżące audio bez późnego ogona;
- test dwóch równoległych producentów nie powoduje nakładania głosów;
- polskie znaki przechodzą od CLI/API do syntezy bez utraty lub literowania;
- brak sztucznych długich pauz na przygotowanym zestawie zdań;
- głos, tempo i mastering każdej zatwierdzonej postaci zgadzają się z próbką
  referencyjną i odsłuchem Ozzy'ego;
- pełna obsada ma tabelę decyzji `persona -> engine -> style/głos -> tempo ->
  mastering/DSP -> dowód odsłuchu`, bez sprzecznych override'ów;
- macierz tras porównuje żądanie producenta, wynik resolvera, snapshot requestu i
  zdarzenie faktycznego playbacku; obejmuje personę jawną i domyślną, a każda
  różnica głosu, tempa, profilu lub DSP blokuje cutover;
- custom styles działają po wyczyszczeniu cache i instalacji ze świeżego klona;
- przygotowany pipeline Żanety odtwarza zaakceptowany rezultat albo jawnie
  zgłasza brak niedystrybuowalnego assetu; nie podmienia go cicho na inny głos;
- testy nie dotykają aktywnego mikrofonu, globalnej kolejki ani realnego audio bez
  jawnego testu live.

### 11.3 Konsumenci

- działają panel, CLI, Claude, Codex, OpenClaw, standup, hook, dobranocka,
  `gpt-say`, `voice-report`, Trio/screen-control i kanoniczne skille persony;
- każdy z nich przechodzi przez to samo API i tę samą kolejkę;
- OpenClaw i każdy adapter maszynowy przechodzą test kontraktu `dan speak
  --json ... --stdin`: UTF-8 na wejściu, `request_id` na stdout, jednoznaczny kod
  wyjścia i brak ścieżki do starego feedera;
- skan aktywnych instrukcji i adapterów w `~/AGENTS.md`, `~/.agents`,
  `~/.claude` z wyłączeniem archiwum, `~/.codex`, `~/.openclaw`,
  `~/Library/LaunchAgents` i aktywnych repo oraz `lsof/ps/launchctl` nie pokazuje
  starego runtime ani wykonywalnych starych ścieżek; historia i logi są jawnie
  wyłączone z tej bramki;
- `dan voice hook off|on|status` zastępuje stary wyłącznik hooka bez utraty
  możliwości szybkiego wyciszenia;
- przy wyłączonym `dand` hook MessageDisplay kończy kodem `0` poniżej jednej
  sekundy, nie blokuje odpowiedzi i nie startuje alternatywnego toru;
- diagnostyka odróżnia `opublikowano`, `zsyntetyzowano` i `naprawdę odtworzono`.

### 11.4 Prywatność i przekazanie koledze

- repo nie zawiera bazy, pamięci, historii, transkryptów, nagrań, logów, tokenów,
  sekretów, absolutnych ścieżek Ozzy'ego ani `owner.toml`;
- skan sekretów przechodzi dla całej historii i wszystkich refów Git, nie tylko
  bieżącego drzewa;
- licencje modeli i assetów pozwalają na redystrybucję; niedystrybuowalne pliki
  instaluje skrypt z legalnego źródła;
- manifest rozróżnia wersjonowane custom styles, referencje głosowe, modele i
  prywatne próbki; brak prawa dystrybucji blokuje publikację danego pliku;
- świeży klon na czystym profilu macOS/M5 przechodzi instalację, cold start,
  test polskich znaków, kolejki, przerwania i panelu;
- usunięcie `~/.dan/` z kopii eksportowej nie odbiera repo kodu, persony,
  masteringu, bezpiecznych ustawień domyślnych ani dokumentacji.

### 11.5 Bramka usunięcia donorów

Ozzy może usunąć stare projekty dopiero po spełnieniu wszystkich warunków:

- manifest każdego donora ma decyzję i test;
- manifest obejmuje każdego producenta głosu, jego stary format requestu i
  decyzję `zmigrowany`, `wyłączony` albo `odrzucony`;
- wszystkie gałęzie i refy WIP mają decyzję na poziomie zmian przed rename;
- migracja `jarvis.db` i zastanego `memory.db` ma zgodne liczniki, sumy oraz
  raport rekordów odrzuconych lub scalonych;
- backup SQLite przechodzi `integrity_check`, ma raport checkpointu i powstał po
  potwierdzeniu zera `queued`, `synthesizing` i `speaking` oraz zatrzymaniu
  wszystkich pisarzy;
- wszystkie procesy i entrypointy mogące pisać do obu baz są zidentyfikowane,
  zatrzymane na czas migracji i nie zapisują już do baz źródłowych po cutoverze;
- pełny bezpieczny baseline testów oraz docelowy zestaw regresji są zapisane;
- wymagania ze starego planu Radia, `summary.md` i `opinia-planu.md` są
  rozliczone w manifeście, a stare dokumenty oznaczone jako historyczne lub
  zarchiwizowane poza aktywną dokumentacją;
- pełny skan referencji zwraca zero aktywnych zależności;
- DAN działa po wylogowaniu/logowaniu lub równoważnym cold starcie;
- testy automatyczne, smoke testy i odsłuch są zaakceptowane;
- backup i rollback zostały sprawdzone;
- przez minimum siedem kolejnych dni normalnego użycia, obejmujących co najmniej
  dwa pełne cold starty po logowaniu, nie trzeba było uruchamiać starego toru;
- Ozzy zapisuje jawny operatorski sign-off kończący okres obserwacyjny.

Wtedy `dan`, `DANv2` i `menubar-controller` przestają być donorami i mogą zostać
usunięte przez Ozzy'ego. Wcześniej są archiwum migracyjne, nie aktywną prawdą.

## 12. Dokumentacja końcowa dla człowieka

Dokumentacja użytkowa ma być krótka i zadaniowa:

- `README.md` — instalacja i pierwsze uruchomienie;
- `docs/CO-JEST-GDZIE.md` — jedna tabela: element, właściciel, ścieżka;
- `docs/GLOS-I-KOLEJKA.md` — głosy, styles, mastering, render offline, broker,
  kolejka, feeder i sześć przykładów CLI;
- `docs/PANEL.md` — co oznaczają stany i przyciski;
- `docs/RADIO-DAN.md` — start, pauza, uczestnicy i formaty po Wydaniu 2;
- `docs/PRZENOSZENIE.md` — co trafia do Git, czego nigdy nie wysyłać i jak
  zainstalować na drugim Macu;
- `docs/ODZYSKIWANIE.md` — pięć komend diagnostycznych i rollback.

Bez kroniki wykopalisk, ściany tekstu i sprzecznych instrukcji. Szczegóły
implementacyjne zostają w testach, kontraktach i ADR-ach; Ozzy dostaje konkretne
komendy oraz prawdziwe stany runtime.

## 13. Definicja sukcesu

Sukces nie oznacza „przeniesiono pliki”. Sukces oznacza, że Ozzy zmienia głos,
tempo, mastering, personę albo zachowanie kolejki w jednym wskazanym miejscu i
każdy aktywny kanał używa tej zmiany. Może sprawdzić źródło wartości jedną
komendą, zobaczyć realny stan w jednym panelu, przerwać mowę bez ogona i wysłać
repo koledze bez własnej pamięci oraz sekretów.
