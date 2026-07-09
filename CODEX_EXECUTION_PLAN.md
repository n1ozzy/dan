# CODEX EXECUTION PLAN — Jarvis (spike/jarvis-local-runtime-check)

> Autor planu: sesja przeglądu 2026-07-09. Repo: `~/Documents/dev/jarvis`.
> Każdy task ma: **Problem → Obserwacja → Root cause → Co zrobić → PROMPT dla Codexa → Dowód działania**.
> **Dowód = realne działanie**, nie „test na zielono". Weryfikacja musi pokazać, że
> naprawiana rzecz FAKTYCZNIE robi to, po co była naprawiana — na żywym runtime.

---

## ⚠️ ZASADY GLOBALNE (obowiązują w KAŻDYM tasku — wklej do każdej sesji Codexa)

- **⚠️ KOORDYNACJA (2026-07-09):** inna sesja edytowała/edytuje kod równolegle. **Przed startem KAŻDEGO taska: `git pull`/rebase najnowszego stanu + `git status --short` + zweryfikuj `plik:linia` grepem** — linie i pliki mogły się przesunąć względem tego planu. Nie startuj na starym drzewie.
- **Branch:** `spike/jarvis-local-runtime-check`. Drzewo bywa niezacommitowane — **przed startem taska zrób commit/stash bieżącego stanu**, żeby dało się odróżnić Twoje zmiany.
- **Preflight (tanio):** `git log -1` + `git status --short` + health daemona. **NIGDY nie odpalaj pełnej matrycy testów na starcie.**
- **TDD celowany:** napisz test odtwarzający KONKRETNY bug, odpalaj TYLKO ten test — nie całą matrycę 1400+.
- **Pełny pytest + smoke TYLKO po dużych taskach** (fundament/współbieżność/głos/migracje). Reszta: celowany test.
- **NIE podbijaj paczek.** Wszystkie deps są najnowsze. `pip install -U` to nie fix.
- **Linie mogły się przesunąć** — zweryfikuj `plik:linia` grepem/Read przed edycją.
- **NIE odpalaj multi-agentowych fan-outów** bez wyraźnej zgody Ozzy'ego (tokeny).
- **Config prod:** daemon czyta `~/.jarvis/jarvis.toml` (NIE `config/*.toml` w repo). DB w UTC, tryb WAL (`PRAGMA wal_checkpoint(FULL)` przy odczycie z zewnątrz).
- **Na koniec taska:** commit z rzeczowym opisem + **DOWÓD DZIAŁANIA w opisie commita/PR** (patrz sekcja „Dowód" każdego taska).
- **Priorytet woli właściciela:** to lokalny single-user tool Ozzy'ego. Ton/treść ustawia ON. Sygnalizuj tylko realnie destrukcyjne/nieodwracalne operacje.

---

# CZĘŚĆ A — FUNDAMENT: JEDEN CONFIG, JEDNO ŹRÓDŁO PRAWDY

## CFG-01 · Fillery działają od ręki (warstwa configu)

- **Problem:** Fillery ustawiane przez Ozzy'ego nie działają.
- **Obserwacja:** „Zmieniłem w pięciu plikach, dodałem — dalej nie działało."
- **Root cause (zweryfikowane):** daemon ładuje `~/.jarvis/jarvis.toml`, a jego `[voice]` (l.73–97) **nie ma klucza `fillers`** → `load_config().voice.fillers` zwraca 57 hardkodów z `jarvis/config.py:31` (`DEFAULT_VOICE_FILLERS`). Edycje w `config/jarvis.toml` / `config/jarvis.example.toml` są ignorowane (to nie jest plik czytany w prod).
- **Co zrobić:** dopisać `fillers = [...]` do `[voice]` w `~/.jarvis/jarvis.toml` (lista Ozzy'ego; jak brak — wziąć obecne 57 z kodu jako bazę i pozwolić edytować). Zero zmian w kodzie.

```
PROMPT DLA CODEXA — CFG-01
Kontekst: jarvis v4.2, ~/Documents/dev/jarvis, branch spike/jarvis-local-runtime-check.
Prod-config to ~/.jarvis/jarvis.toml. Sekcja [voice] NIE ma klucza `fillers`, więc
daemon używa DEFAULT_VOICE_FILLERS z jarvis/config.py:31 (57 pozycji) i ignoruje edycje
z config/*.toml.
Zadanie: dopisz klucz `fillers = [ ... ]` do sekcji [voice] w ~/.jarvis/jarvis.toml.
Jako wartość startową weź obecne DEFAULT_VOICE_FILLERS z jarvis/config.py:31 (przenieś je
1:1 do TOML), żeby Ozzy miał je w jednym miejscu do edycji. NIE zmieniaj kodu.
Ograniczenia: nie ruszaj innych kluczy [voice]; zachowaj komentarze.
```

- **Dowód działania:** `.venv/bin/python -c "from jarvis.config import load_config; print(len(load_config().voice.fillers)); print(load_config().voice.fillers[:3])"` pokazuje listę Z TOML (zmień jeden filler w TOML na „TEST-FILLER-XYZ" i potwierdź, że pojawia się w outputcie — dowód że czytane jest z pliku, nie z kodu). Skasuj testowy wpis po weryfikacji.

---

## CFG-01b · Fix rotacji fillera („zawsze tylko jeden")

- **Problem:** zawsze odtwarzany jest ten sam (pierwszy) filler.
- **Obserwacja:** „Działało, ale był cały czas tylko jeden filler puszczany i chuj wie czemu."
- **Root cause (zweryfikowane):** `SpeechPipeline` tworzony jest CO TURĘ w `jarvis/daemon/app.py:1432` (`_create_turn_orchestrator`, wołane z `:1153` i `:1546`). Licznik rotacji `self._filler_rotation = itertools.count()` żyje na tym per-turowym obiekcie (`jarvis/voice/speech.py:161`). Reset co turę → `next(rotation) % len == 0` → ZAWSZE `fillers[0]` (`speech.py:215-216`).
- **Co zrobić:** trwały stan rotacji między turami. Opcja A: hoist licznika do długożyjącego obiektu (app-level) i wstrzyknięcie do SpeechPipeline. Opcja B: `random.choice(fillers)` (variety bez stanu). **Decyzja Ozzy'ego: rotacja bez powtórek czy losowo** — domyślnie rotacja bez powtórek (deterministyczna, przewidywalna).

```
PROMPT DLA CODEXA — CFG-01b
Kontekst: jw. Bug: SpeechPipeline jest budowany per-turę (jarvis/daemon/app.py:1432,
wołane z :1153 i :1546), a licznik rotacji fillera self._filler_rotation =
itertools.count() (jarvis/voice/speech.py:161) resetuje się razem z nim → arm_filler
(speech.py:208-216) zawsze zwraca fillers[0].
Zadanie (TDD): najpierw test odtwarzający bug — dwie kolejne tury (dwa wywołania przez
_create_turn_orchestrator lub bezpośrednio nowy SpeechPipeline per tura) muszą dać RÓŻNE
fillery; test ma failować na obecnym kodzie. Potem fix: przenieś stan rotacji tak, żeby
przeżywał między turami — zaproponuj hoist licznika do app-level (jeden trwały licznik
wstrzykiwany do SpeechPipeline) LUB rotację opartą o trwałe źródło. Nie zmieniaj zachowania
"co najwyżej jeden filler na turę" ani logiki disarm (speech.py:145-149).
Odpal TYLKO ten nowy test + istniejące testy dotykające speech/filler (celowany zakres).
```

- **Dowód działania:** odpal daemona, zrób **3 tury pod rząd** (tekst lub PTT), potem `grep -i "filler" ~/.jarvis/logs/jarvisd*.log` — pokaż że w kolejnych turach enqueue'owane są **różne teksty fillera**, nie ten sam. Wklej 3 różne linie jako dowód.

---

## CFG-02 · Jeden „efektywny config" z pochodzeniem

- **Problem:** 5 warstw configu (kod-default → repo TOML → home TOML → shared voices → tabela settings) merge'owanych rozproszony i cicho.
- **Obserwacja:** „Nie wiem ile jest plików konfiguracyjnych ani co gdzie jest."
- **Root cause:** brak jednego punktu, który liczy efektywną wartość i mówi z której warstwy przyszła. Merge rozsiany po `jarvis/config.py` (`load_config:466`, `_select_config_path:491`, `_build_section`), `jarvis/voice/shared_voice.py:72`, overlay settings w app.py.
- **Co zrobić:** `resolve_effective_config()` → `(config, provenance)`; `provenance[key] = {value, source_layer, shadowed:[(layer,value)...]}`. Jedna jawna lista warstw w ustalonej kolejności; każdy klucz taguje warstwę-zwycięzcę.

```
PROMPT DLA CODEXA — CFG-02
Kontekst: jw. Config ma 5 warstw: (1) kod-default (dataclass w jarvis/config.py),
(2) config/*.toml w repo [ignorowane w prod], (3) ~/.jarvis/jarvis.toml [prod],
(4) ~/.config/jarvis-voice/voices.toml [shared voices, jarvis/voice/shared_voice.py:72],
(5) tabela settings w DB [overlay z panelu]. Merge jest rozproszony i nietransparentny.
Zadanie (TDD): dodaj jarvis/config_provenance.py z funkcją resolve_effective_config()
zwracającą krotkę (config, provenance), gdzie provenance to mapa klucz ->
{value, source_layer, shadowed:[(layer, value)...]}. Merge idzie przez JEDNĄ jawną,
uporządkowaną listę warstw; każdy klucz dostaje tag warstwy-zwycięzcy i listę przesłoniętych.
Nie zmieniaj publicznego zachowania load_config() (może wołać resolver pod spodem).
Testy (celowane): klucz tylko w warstwie 1 -> source=code-default; ten sam klucz w home-TOML
-> source=home + shadowed zawiera code-default; nadpisany w settings -> source=settings +
shadowed zawiera home. Odpal tylko te testy.
```

- **Dowód działania:** skrypt jednorazowy: ustaw `voice.fillers` w home-TOML na wartość X, w tabeli settings na Y; wywołaj resolver i pokaż że zwraca Y z `source=settings` i `shadowed` z home=X. Wklej output.

---

## CFG-03 · `config explain <klucz>` (CLI + panel)

- **Problem:** nie da się sprawdzić skąd leci wartość bez czytania kodu.
- **Obserwacja:** cała frustracja „zmieniam a nie wiem gdzie to jest".
- **Co zrobić:** subkomenda CLI + pole w panelu oparte o provenance z CFG-02.

```
PROMPT DLA CODEXA — CFG-03 (wymaga CFG-02)
Kontekst: jw., resolve_effective_config() z prowenancją już istnieje (CFG-02).
Zadanie: dodaj subkomendę `python -m jarvis.cli config explain <klucz>` (jarvis/cli.py),
która drukuje: efektywną wartość, warstwę-zwycięzcę + ścieżkę pliku, oraz listę
przesłoniętych warstw z ich wartościami. Dodaj bliźniaczy endpoint diagnostyczny w
jarvis/api/routes_runtime.py i proste pole „config provenance" w panelu
(jarvis/panel/assets/app.js) korzystające z tego endpointu.
Testy celowane: `config explain voice.fillers` na configu z wartością w home i override w
settings pokazuje obie warstwy. Odpal tylko ten test + ewentualny test CLI.
```

- **Dowód działania:** uruchom `python -m jarvis.cli config explain voice.fillers` i wklej output pokazujący wartość + warstwę + przesłonięte. Zrób screenshot pola w panelu (albo curl endpointu) jako dowód, że panel też to widzi.

---

## CFG-04 · Jeden plik configu = jedyne źródło prawdy

- **Problem:** wiele plików configu; niejasne co jest czytane.
- **Obserwacja:** „Chciałbym mieć wszystko w jednym pliku, ewentualnie dwa: persona + główny."
- **Co zrobić:** `~/.jarvis/jarvis.toml` = jedyne źródło prawdy. Repo `config/*.toml` → `*.example` (jawnie nieładowane w prod). Shared voices → wchłonięte albo skasowane. Overlay settings zostaje jako runtime-toggle, jawnie oznaczony w `config explain`. Log przy starcie: `loaded config: <ścieżka>`.

```
PROMPT DLA CODEXA — CFG-04 (miło po CFG-02/03)
Kontekst: jw. Cel: jeden plik = jedyne źródło prawdy (~/.jarvis/jarvis.toml).
Zadanie:
1) W jarvis/config.py: przy starcie loguj realną, rozwiązaną ścieżkę configu
   ("loaded config: <path>") na poziomie INFO.
2) Uczyń warstwę repo (config/jarvis.toml) jawnie NIE-produkcyjną: albo przemianuj na
   config/jarvis.example.toml i usuń ze ścieżki ładowania prod, albo dodaj twardy warning
   przy starcie jeśli prod ładuje plik z repo. Zaktualizuj README.
3) Rozważ wchłonięcie ~/.config/jarvis-voice/voices.toml (shared_voice.py) do głównego pliku
   ALBO zostaw, ale opisz to jawnie w `config explain` jako osobną warstwę.
Nie usuwaj funkcjonalnie działających ustawień — tylko konsoliduj i uczyń widocznym.
Test celowany: start z configiem z repo daje warning/log ścieżki; efektywne wartości bez zmian.
```

- **Dowód działania:** uruchom daemona i pokaż linię logu `loaded config: /Users/n1_ozzy/.jarvis/jarvis.toml`. Pokaż że próba ustawienia czegoś w `config/jarvis.toml` (repo) NIE zmienia runtime (bo nieładowane) — dowód że warstwy zredukowane/oznaczone.

---

## CFG-05 · Audyt wszystkich multi-source ustawień → jeden odczyt

- **Problem:** „drugie ścieżki" odczytu obok configu (np. `jarvis/voice/speech.py:213` ma własny `or DEFAULT_FILLERS`).
- **Co zrobić:** dla każdego ustawienia z >1 źródłem: jeden kanoniczny odczyt przez efektywny config. Obszary: fillery, głosy/mastering (`shared_voice.py`), tool-approval policy (`permissions.py` + `_policy_with_settings_overlay`), model, gate pamięci (`context_builder.py:354`).

```
PROMPT DLA CODEXA — CFG-05 (wymaga CFG-02)
Kontekst: jw. Wiele ustawień ma lokalne fallbacki/merge obok efektywnego configu.
Zadanie: dla każdego z obszarów [fillery: jarvis/voice/speech.py:213; głosy/mastering:
jarvis/voice/shared_voice.py; tool-approval: jarvis/tools/permissions.py + app.py
_policy_with_settings_overlay; wybór modelu; gate pamięci: jarvis/brain/context_builder.py:354]
zredukuj do JEDNEGO kanonicznego odczytu przez efektywny config (CFG-02). Usuń zbędne lokalne
fallbacki (default siedzi już w warstwie kod-default). Dla każdego obszaru dodaj celowany test:
zmiana w home-TOML/settings realnie zmienia zachowanie runtime, nie tylko wartość configu.
Rób obszar po obszarze, osobny commit na obszar. Po tym dużym tasku odpal pełny pytest + smoke.
```

- **Dowód działania:** dla min. 2 obszarów (np. tool-approval i model): zmień ustawienie w jednym miejscu (home-TOML), pokaż runtime-owo że zachowanie się zmieniło (np. narzędzie, które wcześniej wymagało zgody, teraz auto-run — z logu tury). Wklej dowód.

---

# CZĘŚĆ B — DUSZA JARVISA

## JRV-02 · Poziomy chamstwa persony + persona się trzyma

- **Problem:** (a) persona nie trzyma się ustawionej; (b) brak stopniowania chamstwa.
- **Obserwacja:** „Teraz nie trzyma się persony ustawionej."
- **Root cause (hipoteza, do potwierdzenia):** `_resolve_persona_profile` (`context_builder.py:517-541`) **fail-close'uje do `DEFAULT_PERSONA_PROFILE`** gdy (a) setting `persona.profile` nie dotarł z panelu do tabeli settings, albo (b) nie istnieje plik `{profil}.md` obok bazowej persony (`:534`). Fallback jest tylko logowany warningiem — user nigdy go nie widzi → „nie trzyma się". To ta sama klasa co filler: cichy fallback zamiast sygnału.
- **Co zrobić:** (1) zdiagnozować dlaczego wybrana persona nie jest utrzymywana (czy setting się zapisuje? czy plik profilu istnieje?), naprawić tak by wybór persony realnie obowiązywał i był widoczny (żaden cichy fallback bez sygnału w panelu). (2) dodać 4 poziomy chamstwa jako profile person; **kalibracja: obecny najbardziej SAVAGE = poziom LOW (baseline)**, wyżej dopierdolić konkretnie — real savage.

```
PROMPT DLA CODEXA — JRV-02
Kontekst: jw. Persona rozwiązywana w jarvis/brain/context_builder.py:517
(_resolve_persona_profile) — fail-close do DEFAULT_PERSONA_PROFILE gdy setting zły lub brak
pliku {profil}.md (:534); ładowana w _load_persona (:543) z pliku obok self._persona_path.
Panel ustawia persona.profile (jarvis/panel/assets/app.js, klucz "persona.profile").
Zadanie:
1) DIAGNOZA (najpierw): prześledź czy wybór persony w panelu realnie zapisuje się do tabeli
   settings i czy jest czytany per-tura; sprawdź czy pliki profili istnieją. Ustal dlaczego
   "nie trzyma się ustawionej". Udokumentuj root cause z plik:linia.
2) FIX: wybór persony ma realnie obowiązywać między turami; jeśli następuje fallback do bazy,
   ma być WIDOCZNY (status w panelu / log ostrzegawczy zwrócony do UI), nie cichy.
3) FEATURE: dodaj 4 profile person o rosnącym chamstwie. Zbind je z istniejącym mechanizmem
   persona_voices/persona_mastering (jarvis/config.py:311-319), żeby zmiana persony zmieniała
   też brzmienie. Kalibracja tonu wg Ozzy'ego: obecny najbardziej savage ton = poziom LOW;
   kolejne poziomy ostrzejsze (real savage). Ton ustawia właściciel — nie łagodź.
TDD celowany: test że ustawiona persona jest utrzymana przez 2+ tury; test że nieistniejący
profil daje widoczny sygnał, nie cichy fallback.
```

- **Dowód działania:** ustaw personę na najostrzejszy poziom, zrób 3 tury pod rząd i pokaż w logach/DB że `persona_profile` w każdej turze = wybrany (nie base). Wklej 2-3 realne odpowiedzi Jarvisa pokazujące że ton trzyma poziom. Jeśli przełączysz na nieistniejący profil — pokaż że panel/UI to sygnalizuje (dowód braku cichego fallbacku).

---

## JRV-01 · Przenieś poprawny głos/wymowę z DAN do Jarvisa

- **Problem:** niepewność czy dopracowany głos/wymowa z DAN są prawidłowo użyte w Jarvisie/DANusi.
- **Obserwacja:** „Sprawdź czy jest tak fajnie jak w DAN-ie."
- **Root cause (do ustalenia):** wymowa i mastering były „ported from DAN 2026-07-08" (`jarvis/config.py:277,290,304,311`), shared voices w `jarvis/voice/shared_voice.py` czyta `~/.config/jarvis-voice/voices.toml`. Trzeba potwierdzić czy to co realnie ląduje w Jarvisie = to co dopracowane w DAN.

```
PROMPT DLA CODEXA — JRV-01
Kontekst: jw. Głos/wymowa "ported from DAN" (jarvis/config.py:277-319; shared voices w
jarvis/voice/shared_voice.py czyta ~/.config/jarvis-voice/voices.toml). tts_pronunciations i
mastering_profile w ~/.jarvis/jarvis.toml.
PROJEKT DAN (referencja): ~/Documents/dev/dan (branch main). Pliki głosu w DAN:
dan_core/shared_voices.py, dan_core/say.py, dan_core/voice.py, dan_core/config.py,
tools/jarvis/voice_broker.py, config/voice/pronunciations.example.toml,
config/voice/personas.example.toml.
Zadanie:
1) Porównaj konfigurację głosu/wymowy/masteringu w DAN (pliki wyżej) z tym, co realnie wczytuje
   Jarvis (load_config().voice + apply_shared_voices). Wypisz rozjazdy (wymowa PL, mapy, mastering
   per-persona).
2) Jeśli rozjazd: przenieś/popraw tak, żeby Jarvis i DANusia brzmieli jak dopracowany DAN.
   NIE kopiuj na ślepo — mapuj świadomie (kod DAN traktuj jako referencję, nie źródło do 1:1).
Powiązane z JRV-06 (intonacja) — rób w jednym zamachu. Test/dowód poniżej.
```

- **Dowód działania:** wygeneruj tę samą frazę przez Jarvisa i przez DAN, odsłuchaj / porównaj parametry (voice id, mastering profile, mapa wymowy zastosowana). Pokaż że sporne słowa (PL końcówki, nazwy) wymawiane są tak jak w DAN. Wklej efektywne `voice` z prowenancją (`config explain voice`).

---

## JRV-04 · Kontrola gadatliwości Jarvisa

- **Problem:** brak sterowania długością odpowiedzi.
- **Obserwacja:** „Teraz pierdoli i pierdoli aż spać się chce."
- **Root cause:** brak nastawnika verbosity; jedyne co jest to `max_tokens` per-adapter (`groq_adapter.py:65`, `eco_brain_adapter.py:74`) — twardy limit, nie styl. Forma mówiona ma być zwięzła (`speech_text.py`), ale długość czatu nie jest sterowana.
- **Co zrobić:** nastawnik `verbosity` (krótko / normalnie / długo) w jednym configu (spina się z CFG-04), wstrzykiwany jako instrukcja w prompt (context_builder), z osobnym, krótszym trybem dla głosu.

```
PROMPT DLA CODEXA — JRV-04
Kontekst: jw. Brak sterowania długością odpowiedzi. Instrukcje promptu budowane w
jarvis/brain/context_builder.py (_build_core_messages, _VOICE_FORM_INSTRUCTION:35).
Forma mówiona: jarvis/brain/speech_text.py.
Zadanie: dodaj ustawienie `verbosity` (np. short|normal|long) do jednego configu
(~/.jarvis/jarvis.toml) + szybki toggle w panelu. Wstrzyknij odpowiednią instrukcję długości
do promptu w context_builder. Głos domyślnie zwięzły niezależnie od trybu czatu (długie
odpowiedzi mówione tylko na wyraźne żądanie). Nie psuj formy [[GŁOS]]/speech_text.
Test celowany: przy verbosity=short instrukcja krótkości jest w zbudowanym prompt; przy long
— nie. Test że tryb głosu wymusza zwięzłość niezależnie od verbosity czatu.
```

- **Dowód działania:** ustaw `verbosity=short`, zadaj to samo pytanie co przy `long`, pokaż realną różnicę długości dwóch odpowiedzi (liczba zdań/znaków). Dla głosu: pokaż że forma mówiona jest krótka nawet przy `long`. Wklej obie odpowiedzi.

---

## JRV-03 · Aktywacja głosem na komendę + detekcja mowy (VAD)

- **Problem:** brak aktywacji głosowej; trzeba zawsze PTT.
- **Obserwacja:** chce wake-word/komendę + reakcję gdy realnie mówi (czujnik głośności).
- **Referencja:** https://github.com/mp-web3/jarvis-v3 — ma to rozkminione. **NIE kopiować 1:1** (kod niepewny) — chirurgicznie zaimplementować brakujące.
- **Co zrobić:** (a) aktywacja na komendę/wake-word; (b) VAD (voice activity detection) — start nagrywania gdy poziom mowy przekroczy próg. Integracja w `jarvis/voice/` (recorder=sox, `listening.py` leasy, broker). Rozważyć lokalny VAD (webrtcvad / silero), offline.

```
PROMPT DLA CODEXA — JRV-03 (najpierw ANALIZA, potem implementacja; duży task)
Kontekst: jw. Voice pipeline: jarvis/voice/ (recorder sox, listening.py = leasy PTT, broker,
cancellation, speech). Dziś aktywacja tylko przez PTT (ptt_mode="hold", hotkey w
~/.jarvis/jarvis.toml).
Referencja (NIE kopiuj 1:1, kod niepewny): https://github.com/mp-web3/jarvis-v3 — przeanalizuj
jak robią wake-word i VAD.
Zadanie:
1) ANALIZA: opisz jak jarvis-v3 realizuje (a) aktywację na komendę/wake-word, (b) VAD/próg
   głośności. Zmapuj to na nasz pipeline (gdzie wpiąć w listening.py/recorder/broker).
2) IMPLEMENTACJA chirurgiczna: dodaj tryb aktywacji głosowej równolegle do PTT (nie usuwaj PTT).
   VAD lokalnie/offline (rozważ webrtcvad lub silero-vad); wykrycie mowy startuje nagrywanie,
   cisza kończy. Wake-word/komenda startu jako osobny przełącznik. Wszystko sterowane z jednego
   configu.
3) Bezpieczeństwo mikrofonu: uszanuj istniejące leasy i sweeper (żeby nie było "gorącego mikrofonu").
TDD: deterministyczne testy VAD na próbkach (mowa vs cisza) bez sprzętu; potwierdzenie na żywo osobno.
Ten task jest duży — po nim pełny pytest + smoke.
```

- **Dowód działania:** na żywo — powiedz coś bez PTT i pokaż że Jarvis wystartował nagrywanie (log VAD „speech detected" + utworzona tura). Pokaż że cisza nie triggeruje (brak fałszywych startów). Wklej log sekwencji: cisza → mowa → start → cisza → stop.

---

## JRV-06 · Ujednolić intonację TTS (fillery vs streaming)

- **Problem:** prefillery mają źle skonfigurowaną intonację; wypowiedzi w streamingu też wymagają przeglądu i ujednolicenia.
- **Obserwacja:** „Prefilery mają intonację wypowiedzi źle zrobioną... tak samo wypowiedzi w streamingach — trzeba przejrzeć i ujednolicić."
- **Root cause (hipoteza, do potwierdzenia):** parametry prozodii w `jarvis/voice/tts.py` (`supertonic_speed:217`=1.35, `steps:216`=14, `mastering_profile:238`) + **osobne traktowanie krótkich zdań** (`supertonic_short_sentence_chars:219`, `supertonic_short_sentence_speed:222`). Fillery są krótkie (`kind="filler"`, `speech.py:225`) → łapią się na ścieżkę „short sentence speed" i brzmią inaczej niż zdania streamowane (`kind="sentence"`, `speech.py:137/188`). Mastering/EQ może być stosowany niespójnie między `kind`.
- **Co zrobić:** prześledzić jakie parametry realnie trafiają na filler vs sentence przy syntezie; ujednolicić prozodię; wystawić ją do jednego configu (spina się z CFG-04).

```
PROMPT DLA CODEXA — JRV-06
Kontekst: jw. Synteza TTS: jarvis/voice/tts.py (supertonic). Parametry prozodii: speed:217,
steps:216, mastering_profile:238, oraz osobna ścieżka dla krótkich zdań
(supertonic_short_sentence_chars:219, supertonic_short_sentence_speed:222). Fillery to krótkie
teksty enqueue'owane z kind="filler" (jarvis/voice/speech.py:225); zdania streamowane z
kind="sentence" (speech.py:137/188).
Zadanie:
1) DIAGNOZA: prześledź dokładnie, jakie parametry syntezy (speed/steps/short-sentence/mastering)
   są stosowane dla kind="filler" vs kind="sentence". Ustal skąd bierze się rozjazd intonacji
   fillera (prawdopodobnie short-sentence speed). Udokumentuj z plik:linia.
2) FIX: ujednolić prozodię, żeby filler i wypowiedzi streamowane brzmiały spójnie i poprawnie
   intonacyjnie. Jeśli krótkie teksty mają mieć osobne tempo — ma to być świadomy, jeden
   parametr, a nie przypadkowy efekt uboczne progu długości.
3) Wystaw parametry prozodii do jednego configu (~/.jarvis/jarvis.toml), spójnie z CFG-04.
TDD celowany: test że filler i sentence dla tego samego tekstu dostają spójne parametry syntezy
(albo jawnie zamierzone różnice, nie przypadkowe).
```

- **Dowód działania:** zsyntezuj ten sam tekst raz jako filler, raz jako zdanie streamowane; wypisz parametry (speed/steps/mastering) użyte w obu i **odsłuchaj** oba pliki audio — mają brzmieć spójnie intonacyjnie. Wklej parametry obu ścieżek + potwierdzenie odsłuchu.

---

## JRV-03 — AKTUALIZACJA (techniki z jarvis-v3 README)

Do wdrożenia konceptualnie (NIE kopiować kodu): (1) **SmartTurn EOU** — ML przewiduje koniec wypowiedzi zamiast sztywnego timera ciszy; (2) **4-stanowy VAD** QUIET→STARTING→SPEAKING→STOPPING z progiem sustained-frames + bufor sklejający pauzy; (3) **Personalized VAD (pVAD)** — reaguje tylko na głos Ozzy'ego; (4) **pre-buffer** — nagrywa zanim wykryje start (nie ucina 1. słowa); (5) **settling-state barge-in** — stan uspokojenia po przerwaniu TTS. Modele ONNX offline: Silero VAD, SmartTurn v3, FireRedChat pVAD.

## JRV-07 · Intonacja ostatniego słowa + filler (spójność/urywanie/czas kolejki)

- **Problem:** intonacja ostatniego słowa jest ważna; filler brzmi za każdym razem inaczej i czasem się urywa; niejasne czy filler marnuje czas prawdziwego głosu.
- **Obserwacja (Ozzy):** „intonacja ostatniego słowa ma duże znaczenie... filler mówi jedną rzecz ale za każdym razem z inną intonacją... czasami go urywa... czy filler marnuje czas kolejki?"
- **Root cause (zbadane):** filler enqueue'owany do tej samej VoiceQueue (`seq=-1`, `kind="filler"`, interruptible; `speech.py:225`). Broker: 1 slot prefetch — syntezuje następny kawałek gdy bieżący gra (`broker.py:45,121-125`), więc 1. prawdziwe zdanie syntezuje się RÓWNOLEGLE z graniem fillera. ALE filler musi się sam najpierw zsyntezować on-the-fly (własne opóźnienie) i odtwarzanie jest jednokanałowe → real audio czeka na koniec fillera albo go PRZERYWA (→ „urywa" w pół słowa). Różna intonacja = supertonic to model dyfuzyjny bez stałego seeda.
- **Co zrobić:** pre-renderować mały stały zestaw fillerów RAZ do plików audio (cache) → grają natychmiast, zawsze ta sama intonacja, brak ucięcia od wolnej syntezy. Osobno: zadbać o intonację końcówki real-wypowiedzi (ostatnie słowo nie ma opadać/uciąć się).

```
PROMPT DLA CODEXA — JRV-07
Kontekst: jw. Filler: jarvis/voice/speech.py:208-236 (enqueue seq=-1, kind="filler"). Broker:
jarvis/voice/broker.py (1 slot prefetch, drain_all). TTS: jarvis/voice/tts.py (supertonic dyfuzyjny).
Zadanie:
1) DIAGNOZA: potwierdź ścieżkę czasową fillera vs 1. zdania (czy filler dokłada własną latencję
   syntezy; kiedy real audio przerywa filler → mechanizm ucięcia).
2) FIX intonacji/urywania: pre-render stałego zestawu fillerów do plików audio (cache na dysku),
   odtwarzane bez syntezy → identyczna intonacja + zero latencji + brak ucięcia od wolnego synth.
   Zachowaj interruptible (barge-in dalej może przerwać), ale nie ucinaj przez WŁASNĄ powolną syntezę.
3) Intonacja OSTATNIEGO słowa real-wypowiedzi: sprawdź czy końcówka nie jest ucinana/nie opada
   (chunker speech.py + synteza tts.py) i popraw.
TDD: filler z cache gra identycznie 3x (te same bajty/parametry); real audio nie jest już blokowane
przez synthez fillera.
```

- **Dowód działania:** odsłuchaj ten sam filler 3x — ma brzmieć IDENTYCZNIE. Zmierz opóźnienie start-mowy z cache vs on-the-fly (pokaż liczby). Pokaż że ostatnie słowo real-odpowiedzi ma poprawną końcówkę (odsłuch).

## JRV-08 · Transcript polishing STT przed mózgiem (pomysł z jarvis-v3)

- **Problem:** surowy STT (yyy, powtórzenia, drobne błędy) idzie prosto do mózgu → gorsze odpowiedzi.
- **Pomysł (jarvis-v3, NIE kopiować kodu):** mały lokalny model (u nich Qwen 1.5B 4-bit MLX) czyści transkrypt przed brainem — usuwa filler-words, deduplikuje, poprawia gramatykę (+~300-500ms, lepsza jakość).
- **Co zrobić:** opcjonalny etap „polish" (config toggle) między STT (mlx_whisper) a context_builder/brain; hybryda regex + mały model.

```
PROMPT DLA CODEXA — JRV-08 (opcjonalny, dokłada latencję)
Kontekst: jw. STT: mlx_whisper (jarvis/voice/). Referencja jarvis-v3 polisher.py (regex + Qwen 1.5B) —
NIE kopiuj kodu. Zadanie: dodaj opcjonalny etap polish między STT a mózgiem: hybryda regex
(szybkie usuwanie yyy/powtórzeń) + mały lokalny model (do wyboru; offline). Sterowany togglem w
jednym configu (domyślnie off — dokłada latencję). Test: surowy transkrypt z „yyy"/duplikatami →
wyczyszczony; toggle off = passthrough bez zmian.
```

- **Dowód działania:** realna wypowiedź z „yyy" i powtórzeniem — pokaż surowy transkrypt vs wyczyszczony obok siebie. Pokaż że toggle=off nic nie zmienia.

---

# CZĘŚĆ D — PAMIĘĆ, FUNDAMENT, NIEZAWODNOŚĆ

## MEM-01 · Włącz pamięć + trwała pamięć cross-provider (Jarvis + cloud)

- **Problem:** Jarvis ma pamiętać WSZYSTKO między sesjami — własną pamięć ORAZ pamięć z sesji clouda (inny provider, np. OpenClaw/Claude). Teraz pamięć jest WYŁĄCZANA (błąd).
- **Obserwacja (Ozzy):** „musi mieć włączoną pamięć... jest wyłączana, a to błąd... włącz ją na pewno."
- **Root cause:** `memorySearch.enabled=false` w openclaw.json (strona cloud) + compiled-memory gate praktycznie zawsze off w Jarvisie (H-06, `context_builder.py:354`, scope_gate/session_profiles pusty).
- **Co zrobić:** (A) WŁĄCZYĆ pamięć Jarvisa (natychmiast). (B) DESIGN: jedno wspólne, trwałe źródło pamięci, które i Jarvis (lokalny) i agent cloudowy czytają/zapisują — kontekst przechodzi w obie strony. Ustalić store/format/sync + destylację (nie surowy dump — patrz porażka dreamingu w OpenClaw: flat 0.58 confidence, promocja nigdy nie odpala).

```
PROMPT DLA CODEXA — MEM-01 (najpierw DESIGN, potem włączenie)
Kontekst: jw. Pamięć wyłączona: memorySearch.enabled=false (openclaw.json) + gate H-06 w
Jarvisie (jarvis/brain/context_builder.py:354, scope_gate/session_profiles pusty).
Zadanie:
1) DESIGN SPIKE (bez trwałych zmian): zaproponuj architekturę wspólnej, trwałej pamięci
   Jarvis <-> cloud (gdzie store, format, sync, destylacja/promocja, jak nie zaśmiecić).
   Krótki dokument decyzyjny do akceptacji Ozzy'ego.
2) Po akceptacji: włącz pamięć Jarvisa (uprość gate H-06 do memory.enabled ∧ compiled_enabled
   ∧ ¬force_disabled) i wepnij wspólny store.
Dowód: po restarcie Jarvis odwołuje się do faktu z poprzedniej sesji; fakt z sesji cloud jest
widoczny dla Jarvisa i odwrotnie.
```

- **Dowód działania:** restart → Jarvis pamięta fakt z poprzedniej sesji; fakt zapisany w sesji cloud wypływa u Jarvisa (i odwrotnie).

## FND-01 · Siatka bezpieczeństwa: commit + odblokowanie testów + baseline

- **Problem:** brak commitów + testy nie ruszają (C-01) → żaden fix nie jest weryfikowalny.
- **Co zrobić:** (1) commit obecnego stanu; (2) mock-adapter w trybie testowym (BrainManager bez zewnętrznego CLI); (3) pełny pytest raz → spisać realny baseline czerwonych/zielonych.
- **⚠️ Koordynacja:** rusza dopiero gdy druga sesja zakończy edycję.

```
PROMPT DLA CODEXA — FND-01 (po zakończeniu pracy drugiej sesji)
Zadanie: (1) commit obecnego drzewa z rzeczowym opisem. (2) Odblokuj testy: BrainManager.from_config()
wymaga zewnętrznego CLI (jarvis/brain/manager.py, conftest.py mockuje niepełnie) — dodaj mock-adapter/
auto-mock w trybie testowym. (3) Odpal pełny pytest, zapisz listę realnie czerwonych (oddziel
pre-existing od regresji). Dowód: collection przechodzi bez zewnętrznego CLI; baseline zapisany.
```

- **Dowód działania:** `pytest` zbiera i rusza bez zewnętrznego CLI; zapisana lista czerwonych jako punkt odniesienia.

## VOX-REL-01 · Niezawodność głosu (hot mic / niemy broker / race / martwy [[GŁOS]])

- **Problem:** głos się wysypuje (nie jakość — niezawodność).
- **Co zrobić (potwierdź `plik:linia` na aktualnym kodzie):** (1) gorący mic po restarcie (osierocony sox, FIX-04 niepotwierdzony na sprzęcie); (2) broker umiera po cichu na nie-TTS wyjątku → niemy, panel pokazuje OK (C-03/M-05); (3) mic gorący gdy panel crashnie w hold (H-10); (4) race anulowania barge-in → fantomowa mowa (C-04); (5) martwy `[[GŁOS]]` (piece 4 niewpięty) — dokończyć albo wyrwać.

```
PROMPT DLA CODEXA — VOX-REL-01 (rób po jednym; potwierdź linie grepem)
Kontekst: jw. Punkty z audytu: hot mic (FIX-04, jarvis/daemon/app.py stop() + voice_recorder.stop()),
broker cichy zgon (jarvis/voice/broker.py _run/drain), lease hold (jarvis/voice/listening.py sweeper),
race anulowania (jarvis/voice/cancellation.py), martwy speech-form ([[GŁOS]] router niewpięty w
orchestrator). Dla każdego: TDD deterministyczny + potwierdzenie na żywo. Osobny commit na punkt.
```

- **Dowód działania:** restart → mic zimny (brak sox); wyjątek DB w brokerze → broker żyje + log (nie niemy); wygasły lease bez klienta → recorder stop; anulowana tura → zero fantomowej mowy.

---

# CZĘŚĆ C — PRZEGLĄDY (bez edycji kodu, produkują listy/decyzje)

## JRV-05 · Niespójności: czego chce Ozzy vs co narzucają pliki

- **Problem:** pliki (zasady, blokady, instrukcje, reguły, safety) narzucają „bezpieczne/grzeczne" zamiast tego czego chce właściciel.
- **Co zrobić:** przejść config, prompty persony, guardraile, approval policy, instrukcje w docs; wypisać każdy rozjazd „plik X wymusza Y, a Ozzy chce Z" + rekomendację. Zasada: lokalny single-user tool → priorytet ma wola właściciela; sygnalizować tylko realnie destrukcyjne/nieodwracalne operacje.

```
PROMPT DLA CODEXA — JRV-05 (przegląd, nie edycja)
Kontekst: jw. Lokalny single-user tool Ozzy'ego. Zadanie: przejdź prompty persony
(jarvis/brain/context_builder.py + pliki person), guardraile/filtry treści, approval policy
(jarvis/tools/permissions.py), domyślne odmowy, instrukcje w docs/. Wypisz listę niespójności
w formacie: [plik:linia] wymusza <ograniczenie> | Ozzy chce <intencja> | rekomendacja <zmiana>
| ryzyko <jeśli realnie destrukcyjne>. NIE wprowadzaj zmian — tylko raport do decyzji Ozzy'ego.
```

- **Dowód działania:** raport = lista niespójności z konkretnymi `plik:linia`. „Dowód" tu = kompletność: dla każdej reguły ograniczającej ton/treść/działanie wskazane źródło i rekomendacja.

## CFG-06 · Szerszy przegląd sprzężeń (WYMAGA ZGODY NA FAN-OUT)

- **Problem:** „zmiana w 1 miejscu = edycja w 4" poza configiem (wiring narzędzi, event flow, kontrakty panel↔daemon).
- **Co zrobić:** mapa sprzężeń „aby dodać X dotykasz a,b,c,d → docelowo jeden punkt". **Rusza dopiero po zgodzie Ozzy'ego na dokładny przegląd** (ew. mały równoległy zespół audytorów).

---

## ROADMAP MASTER — fazy wg priorytetów Ozzy'ego (2026-07-09, wieczór)

> Kolejność ustalona z Ozzym. Głos/brzmienie idzie pierwsze (jego priorytet), config-SSOT
> wplatany tam gdzie blokuje. Wszystko czeka aż druga sesja skończy edycję + FND-01 (siatka).

**FAZA 0 — Siatka (gdy druga sesja skończy):** FND-01 (commit + odblokowanie testów + baseline).
Bez tego reszta jest niezweryfikowalna.

**FAZA 1 — GŁOS / BRZMIENIE (priorytet Ozzy'ego):**
1. **JRV-01** — mastering + głos/wymowa z DAN (`~/Documents/dev/dan/dan_core/`). Zacząć TU.
2. **JRV-06** — ujednolić intonację (formy wymowy: prefill + głosy; filler vs streaming).
3. **JRV-07** — filler POZA kolejką: pre-render do cache, osobna ścieżka, pokrywa całą ciszę.
   ⚠️ Uwaga: mastering/głos siedzą w wielowarstwowym configu (`shared_voice.py` + `persona_mastering`
   + `mastering_profile`) — prowadź prowenancję ręcznie dopóki CFG-02/03 nie stoją.

**FAZA 2 — CONFIG SSOT (żeby głos i reszta były edytowalne bez zgadywania):**
CFG-01 → CFG-01b → CFG-02 → CFG-03 → CFG-04 → CFG-05.
(CFG-01b = rotacja fillera; ta sama choroba „reset co turę" co persona w JRV-02 — rozważ razem.)

**FAZA 3 — DUSZA / PERSONA:** JRV-02 (chamstwo 4 poziomy + persona się trzyma), JRV-04
(gadatliwość), JRV-05 (niespójności: Twoja wola vs pliki — chamstwo w JEDNYM miejscu, zero cichych
nadpisań).

**FAZA 4 — PAMIĘĆ:** MEM-01 (część A: włącz pamięć Jarvisa; część B: design wspólnej pamięci
Jarvis ⇄ cloud). Włączenie szybkie, architektura wymaga design spike.

**FAZA 5 — GŁOS ZAAWANSOWANY:** JRV-03 (VAD/wake-word + techniki z jarvis-v3: SmartTurn EOU,
4-stanowy VAD, pVAD, pre-buffer, settling barge-in), JRV-08 (transcript polishing).

**FAZA 6 — NIEZAWODNOŚĆ / DŁUG:** VOX-REL-01 (hot mic / niemy broker / race / martwy [[GŁOS]]),
CFG-06 (szerszy przegląd sprzężeń — za zgodą na fan-out).

---

## KOLEJNOŚĆ WYKONANIA (stara lista — referencyjna, zastąpiona przez ROADMAP MASTER wyżej)

1. **CFG-01 + CFG-01b + JRV-02** — najpierw, bo CFG-01b i JRV-02 to prawdopodobnie TA SAMA choroba „stan resetuje się co turę"; efekt natychmiast odczuwalny (różne fillery, persona się trzyma).
2. **CFG-02 → CFG-03 → CFG-04** — fundament jednego configu z widocznością.
3. **CFG-05** — domknięcie klasy multi-source.
4. **JRV-04** (gadatliwość), **JRV-01** (głos z DAN), **JRV-06** (intonacja TTS — spójna z JRV-01, oba dotykają brzmienia).
5. **JRV-03** (VAD/wake-word) — duży, osobno.
6. **JRV-05 / CFG-06** — przeglądy (JRV-05 w każdej chwili; CFG-06 za zgodą).
