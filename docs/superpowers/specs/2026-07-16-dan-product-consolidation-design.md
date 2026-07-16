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

Komentarze w `jarvis.toml` nadal opisują wcześniejsze `M2/clean`, mimo że wartości
wykonawcze mówią co innego. Panel i tabela `settings` w SQLite tworzą dodatkową
warstwę trwałych ustawień. To nie jest jeden kanon konfiguracji.

### 3.3 Żywi konsumenci poza repo

Aktywne zależności obejmują między innymi:

- `~/.agents/skills/gadanie/`, `dobranocka/`, `danusia-live/`, `gpt-say/`;
- symlinki skryptów `~/.claude/skills/gadanie/*.sh`;
- `~/.claude/bin/voice-standup.sh`;
- `~/.claude/hooks/tts-loud-thinking.sh`, `config-guard.sh` i
  `orphan-reaper.sh`;
- `~/.claude/skills/voice-doctor/`;
- aktywny skill `~/.openclaw/workspace/skills/radio-dan/` — `openclaw skills
  list` pokazuje go jako `ready`;
- plisty `com.dan.voice-broker`, `com.ozzy.voice-standup`,
  `com.ozzy.menubar-controller` i `com.ozzy.jarvisd`;
- `~/.config/voice/personas.toml`, `pronunciations.toml`, `gains.json`;
- `~/.jarvis/jarvis.toml` i `~/.jarvis/jarvis.db`.

Usunięcie starego repo bez migracji tych konsumentów wyłączy głos albo pozostawi
martwe instrukcje, które będą próbowały go wskrzesić.

### 3.4 Donorzy i test wejściowy

| Źródło | Stan | Rola |
|---|---|---|
| `Documents/dev/jarvis` | aktywne repo, jedyny nieśmieciowy szkielet produktu | baza DAN-a |
| `Documents/dev/dan` | aktywny tor głosu i radia, repo brudne | donor funkcji, konfiguracji i danych |
| `Documents/dev/DANv2` | duplikat/prototyp, repo mocno brudne | donor testów i zachowań tylko po porównaniu |
| `Documents/dev/menubar-controller` | osobny niecommitowany program | donor funkcji operatorskich panelu |
| `Desktop/djdan-visualizer.html` | samodzielny prototyp wizualizera | donor widoku Radio DAN |

Repo `dan` ma niezacommitowane zmiany w `config/persona/DAN.md` i
`tools/jarvis/voice_broker.py`; repo `DANv2` jest szeroko zmodyfikowane. To są
zmiany Ozzy'ego. Migracja najpierw zapisuje ich diff i sumy SHA-256, a następnie
przejmuje świadomie wybrane treści. Nie wolno ich nadpisać, stashować ani
„czyścić” dla wygody.

Kontrolny zestaw testów w repo bazowym dał `141 passed, 1 failed`. Jedyny błąd
to świadoma, ale zakazana zależność runtime:
`jarvis/brain/context_builder.py` wskazuje bezpośrednio
`/Users/n1_ozzy/Documents/dev/dan/config/persona/DAN.md`. Fundament nie ma więc
jeszcze prawa nazywać się niezależnym.

## 4. Docelowy model własności

### 4.1 Jedna wartość — jeden właściciel

„Jedno miejsce” oznacza jeden produkt i jednego właściciela każdej wartości, a
nie jeden plik z całym światem. Podział jest jawny i bez nakładających się kluczy:

| Rodzaj prawdy | Jedyny właściciel |
|---|---|
| charakter DAN-a | wersjonowany `config/persona/DAN.md` |
| biblioteka głosów, tempo, mastering | wersjonowane `config/voice/personas.toml` |
| wymowa | wersjonowane `config/voice/pronunciations.toml` |
| gain i profile masteringu | wersjonowane pliki pod `config/voice/` |
| ustawienia tej instalacji | `~/.dan/config.toml` |
| dane właściciela | `~/.dan/owner.toml` |
| tokeny i sekrety | `~/.dan/secrets.env` z prawami `0600` |
| rozmowy, pamięć, kolejka, radio, zdarzenia | `~/.dan/dan.db` |
| logi i pliki chwilowe | `~/.dan/logs/` i `~/.dan/runtime/` |

Pliki wersjonowane zawierają dane produktu możliwe do udostępnienia. `~/.dan/`
zawiera wyłącznie dane instalacji i nigdy nie trafia do Git.

Runtime nie scala kilku wartości tego samego ustawienia. Panel zapisuje przez API
do właściwego właściciela, nie tworzy override'u w SQLite. `dan config explain
<klucz>` pokazuje wartość, źródło i ścieżkę. Nieznany lub zdublowany klucz blokuje
start z czytelnym błędem.

### 4.2 Persona i prywatność

Istnieje jeden kanon postaci DAN-a. Nie powstają kopie dla Claude, Codexa,
OpenClaw, Radia ani instalacji kolegi.

Kanon postaci jest wersjonowalny i przenośny. Imię właściciela, prywatna historia,
fakty o Ozzym i relacja z konkretną osobą pochodzą z lokalnego `owner.toml` oraz
pamięci. Dzięki temu kolega dostaje tę samą postać DAN-a, ale nie prywatne dane i
nie cudzą historię.

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

Każdy request posiada co najmniej:

- identyfikator, źródło, sesję i uczestnika;
- tekst w UTF-8/NFC;
- głos, profil, tempo i jawnie wybrany silnik;
- priorytet, pasmo (`live`, `normal`, `background`) i politykę przerwania;
- kolejność w obrębie wypowiedzi;
- status `queued`, `synthesizing`, `speaking`, `done`, `cancelled` albo `failed`;
- czas przyjęcia, startu syntezy, startu audio i zakończenia;
- błąd oraz potwierdzenie rzeczywistego odtworzenia.

Kolejka jest trwała w SQLite. Broker bierze dokładnie jeden element do playbacku.
Anulowanie zatrzymuje generowanie, usuwa oczekujące fragmenty tej wypowiedzi i
kończy aktualny player przed przyjęciem następnej kwestii. Jeden proces, lock
instancji i test nakładania chronią przed dwoma odtwarzaczami.

Brak dostępnego silnika, głosu albo pliku konfiguracji kończy request błędem.
Nie ma cichego przejścia na XTTS, inny głos, inne tempo ani bezpośredni `afplay`.
Supertonic jest silnikiem fundamentu. XTTS, ElevenLabs i Chatterbox mogą wrócić
wyłącznie jako jawne, przetestowane adaptery; nie są fallbackiem.

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
- mapy głosów po zatwierdzeniu aktualnych wartości;
- zachowanie `gadanie`, `dobranocka`, `trio-live`, standupu i hooków;
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

1. instalator generuje cienkie adaptery wywołujące `dan`;
2. testuje Claude, Codex, OpenClaw, standup, hook i panel;
3. dopiero potem wyłącza stare skrypty i plisty;
4. skan aktywnych ścieżek musi zwrócić zero odwołań do starych repo i `/tmp/dan-*`.

`~/.openclaw/workspace/skills/radio-dan` jest żywe i musi zostać zastąpione
adapterem podczas cutoveru, nie skasowane wcześniej. `~/.claude/archive/` nie
jest źródłem aktywnej prawdy i nie jest modyfikowane. Historyczne kwarantanny nie
są kopiowane do produktu. `_sesja-glosy-2026-07-11` nie jest kwarantanną — ma
żywych konsumentów w Voice Lab i generatorach.

## 9. Etapy dostarczenia

Projekt jest zbyt duży na jeden bezpieczny plan wykonawczy. Dzielimy go na trzy
wydania, zawsze w tym samym repo.

### Wydanie 1 — Fundament DAN

1. zamrożenie i manifest bieżących źródeł oraz wartości;
2. bezpieczne wyłączenie panelu, `jarvisd`, feedera i starego toru głosu przed
   edycją aktywnych plików;
3. wewnętrzna zmiana nazw `jarvis` na `dan` oraz nowe ścieżki `~/.dan`;
4. jeden model konfiguracji i rozdzielenie persony od prywatnego profilu;
5. natywny broker i trwała kolejka jako faktyczny właściciel playbacku;
6. migracja Supertonic, głosów, masteringu, wymowy i testów jakości;
7. migracja panelu oraz funkcji `menubar-controller` przez API;
8. migracja konsumentów do `dan speak` i `dan queue`;
9. instalator, launchd, diagnostyka i prosta dokumentacja;
10. kontrolowany cutover ścieżki i nazwy produktu.

### Wydanie 2 — Radio DAN Studio

Zakładka Radia, sesje, uczestnicy, scheduler, feeder, tryby audycji, lokalny mic,
Codex jako uczestnik, formaty i wizualizer. Obecne Trio jest testem regresji
naturalnej rozmowy, a nie kodem do kopiowania w ciemno.

### Wydanie 3 — Dystrybucja i połączenia

Instalacja ze świeżego klona na M5, eksport/import bez prywatnych danych,
licencjonowane assety głosowe oraz — później — zdalny gość/telefon.

Pierwszy szczegółowy plan po akceptacji tej specyfikacji obejmuje tylko Wydanie 1.

## 10. Cutover i rollback

Prace zaczynają się w `/Users/n1_ozzy/Documents/dev/jarvis` na osobnej gałęzi.
Nie zaczynają się z katalogu domowego. Stary aktywny runtime jest zatrzymany przed
edycją plików, z których obecnie korzysta.

Przed każdym ruchem destrukcyjnym powstaje kopia poza repo oraz manifest SHA-256.
Nie używamy `git add -A`, nie stashujemy cudzych zmian i nie commitujemy
gigabajtowej kwarantanny.

Ze względu na system plików macOS nie mogą jednocześnie istnieć aktywne katalogi
`dev/dan` i `dev/DAN`. Końcowa kolejność jest następująca:

1. nowy DAN przechodzi testy niezależności w obecnej ścieżce repo `jarvis`;
2. wszystkie procesy produktu są zatrzymane;
3. stare `dev/dan` trafia poza aktywne `Documents/dev` do datowanego backupu;
4. `dev/jarvis` zostaje przemianowane na `dev/DAN`;
5. instalator aktualizuje launchd, adaptery, ścieżki i remote GitHub;
6. wykonywany jest cold start, test panelu, CLI, głosu i restartu;
7. dalsza praca odbywa się z nowej sesji w `Documents/dev/DAN`.

Rollback przed bramą usuwania zatrzymuje `dand`, przywraca backup konfiguracji i
plistów oraz pozwala ponownie uruchomić stary tor. Stare repozytoria nie są
kasowane w tym samym kroku co cutover.

## 11. Bramki akceptacji

### 11.1 Architektura

- dokładnie jeden `dand`, jeden label launchd i jeden właściciel audio;
- brak aktywnych nazw `Jarvis`, `DANv2` i starych ścieżek poza dokumentacją
  migracji oraz historią Git;
- brak aktywnych zapisów do `/tmp/dan-*`, bezpośredniego `afplay` i uruchamiania
  brokera przez skille, hooki lub panel;
- `dan config explain` wskazuje jednego właściciela każdego ustawienia;
- brak trwałych override'ów głosu w SQLite i panelu.

### 11.2 Runtime i głos

- start po zalogowaniu i kontrolowany restart pozostawiają dokładnie jeden
  `dand`, bez osieroconych silników i odtwarzaczy;
- request ma potwierdzony stan od przyjęcia do faktycznego zakończenia playbacku;
- anulowanie i barge-in zatrzymują bieżące audio bez późnego ogona;
- test dwóch równoległych producentów nie powoduje nakładania głosów;
- polskie znaki przechodzą od CLI/API do syntezy bez utraty lub literowania;
- brak sztucznych długich pauz na przygotowanym zestawie zdań;
- głos, tempo i mastering każdej zatwierdzonej postaci zgadzają się z próbką
  referencyjną i odsłuchem Ozzy'ego;
- testy nie dotykają aktywnego mikrofonu, globalnej kolejki ani realnego audio bez
  jawnego testu live.

### 11.3 Konsumenci

- działają panel, CLI, Claude, Codex, OpenClaw, standup, hook i podstawowy adapter
  dobranocki;
- każdy z nich przechodzi przez to samo API i tę samą kolejkę;
- skan plików aktywnych i `lsof/ps/launchctl` nie pokazuje starego runtime;
- diagnostyka odróżnia `opublikowano`, `zsyntetyzowano` i `naprawdę odtworzono`.

### 11.4 Prywatność i przekazanie koledze

- repo nie zawiera bazy, pamięci, historii, transkryptów, nagrań, logów, tokenów,
  sekretów, absolutnych ścieżek Ozzy'ego ani `owner.toml`;
- skan sekretów przechodzi dla całej historii i wszystkich refów Git, nie tylko
  bieżącego drzewa;
- licencje modeli i assetów pozwalają na redystrybucję; niedystrybuowalne pliki
  instaluje skrypt z legalnego źródła;
- świeży klon na czystym profilu macOS/M5 przechodzi instalację, cold start,
  test polskich znaków, kolejki, przerwania i panelu;
- usunięcie `~/.dan/` z kopii eksportowej nie odbiera repo kodu, persony,
  masteringu, bezpiecznych ustawień domyślnych ani dokumentacji.

### 11.5 Bramka usunięcia donorów

Ozzy może usunąć stare projekty dopiero po spełnieniu wszystkich warunków:

- manifest każdego donora ma decyzję i test;
- pełny skan referencji zwraca zero aktywnych zależności;
- DAN działa po wylogowaniu/logowaniu lub równoważnym cold starcie;
- testy automatyczne, smoke testy i odsłuch są zaakceptowane;
- backup i rollback zostały sprawdzone;
- przez ustalony okres obserwacyjny nie trzeba było uruchamiać starego toru.

Wtedy `dan`, `DANv2` i `menubar-controller` przestają być donorami i mogą zostać
usunięte przez Ozzy'ego. Wcześniej są archiwum migracyjne, nie aktywną prawdą.

## 12. Dokumentacja końcowa dla człowieka

Dokumentacja użytkowa ma być krótka i zadaniowa:

- `README.md` — instalacja i pierwsze uruchomienie;
- `docs/CO-JEST-GDZIE.md` — jedna tabela: element, właściciel, ścieżka;
- `docs/GLOS-I-KOLEJKA.md` — broker, kolejka, feeder i sześć przykładów CLI;
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
