# Panel v4 — spec UX dla nowej sesji (feedback Ozzy'ego 2026-07-03 wieczór)

> Stan wyjściowy: cockpit v3 (aplikacja jednowidokowa z tabbarem) na
> `origin/main` — commity `2c010ed` (v3), `8a438ee` (tryb głosu w kompozytorze,
> ring z DOM usunięty), `efadf61`+`8134e40` (natywna ramka stanu na warstwie
> WKWebView). Pliki: `jarvis/panel/assets/{index.html,app.js,styles.css}`,
> powłoka `jarvis/panel/menubar_app.py`, kontrakty
> `tests/test_panel_assets.py` + `tests/test_panel_menubar.py`, runbooki
> `docs/runbooks/PANEL_COCKPIT.md` + `docs/runbooks/PANEL_MENUBAR.md`.

## Zasady twarde (bez zmian względem poprzednich sesji)

- Vanilla HTML/CSS/JS, **bez frameworków, bundlera i nowych paczek** (dotyczy
  też Pythona — np. brak Quartz; PyObjC tylko to, co już jest).
- TDD kontraktowe na źródłach (string-kontrakty w `test_panel_assets.py` /
  `test_panel_menubar.py`); zakazane w app.js: `innerHTML`, `eval(` —
  patrz `FORBIDDEN_APP_SNIPPETS`. **Celowane testy panelu wolno odpalać**
  (sekundy); pełnego pytesta NIE odpalać bez prośby Ozzy'ego.
- Git współdzielony: `git fetch` przed commitem, `git add` TYLKO swoje pliki,
  push wyłącznie fast-forward, nigdy force. W repo wisi nietracked
  `docs/JARVIS_FIX_TASKS_HANDOFF.md` — **nie zagarniać**.
- Weryfikacja wizualna: preview `cockpit-static` (port 41800,
  `.claude/launch.json`) + `preview_eval` na sztucznych danych (funkcje app.js
  są globalne). Realny popover: `jarvis restart` (restartuje daemona i panel;
  WKWebView ładuje assety przy starcie panelu, więc po zmianach RESTART jest
  konieczny). Token: panel dostaje go automatycznie (bootstrap z
  `~/.jarvis/runtime/api-token`) — nie drukować do transkryptu.
- Ruch w UI WYŁĄCZNIE dla rzeczy, które trwają (nagrywanie, pending);
  żadnych dekoracyjnych animacji. Wszystkie teksty **po polsku, dla
  człowieka** — identyfikatory/wartości systemowe najwyżej jako meta w mono.

## Zadania (kolejność = priorytet)

### 1. Wybór trybu głosu (PTT | Nasłuch) → do Ustawień

Dziś: segmenty `PTT | NASŁUCH` siedzą w kompozytorze czatu (`voice-mode`,
`pttModeButton`/`listenToggle`, logika `setVoiceMode()` w app.js).

Docelowo:
- Przenieść segmenty do zakładki **SYSTEM**, sekcja „Głos" (patrz zad. 8) —
  z ludzkim opisem: „PTT: mówisz, trzymając globalny skrót (⌘L+⇧L).
  Nasłuch: mikrofon słucha cały czas, dopóki go nie wyłączysz."
- W kompozytorze zostaje TYLKO status mikrofonu (fala + tekst, np.
  „cisza — przytrzymaj hotkey PTT" / „słucha (nasłuch)"). Fala animowana
  wyłącznie gdy `listening=true` (już tak jest — nie zepsuć).
- Kontrakty: `test_composer_has_voice_mode_switch` przepisać (voice-mode
  przenosi się DO details/System; status w composer zostaje przed pierwszym
  `<details>`). Routes `/voice/listen/lock|unlock` zostają w REQUIRED_ROUTES.

DoD: przełączenie trybu działa z Systemu (lock/unlock na daemonie), composer
pokazuje sam status, testy panelu (celowane) zielone.

### 2. Kompozytor: „Wyślij" po prawej, obok pola

Dziś: textarea na całą szerokość, pod spodem rząd [tryb głosu | status |
Wyślij].

Docelowo (jak w komunikatorach):
- Jeden rząd: `[textarea (flex)] [Wyślij]` — przycisk po prawej, wyrównany
  do dołu pola; textarea niska (2 rzędy), rośnie do max ~5 rzędów.
- Status mikrofonu (fala + tekst) jako dyskretna linijka POD polem, po lewej.
- Enter wysyła / Shift+Enter nowa linia (jest — nie ruszać `requestSubmit`).

DoD: layout wąski (420px) i szeroki bez łamania; kontrakt: `composer` grid z
przyciskiem w tym samym rzędzie co textarea (string-kontrakt na CSS, np.
`composer-row`).

### 3. Ramka stanu — wykonać PORZĄDNIE (największy pojedynczy task)

Feedback: ramka na warstwie WKWebView jest „dalej źle wykonana" — ring jest
wpisany W bąbel popovera (podwójna ramka: systemowa krawędź bąbla + nasz
ring, mismatch promieni, strzałka bez ramki, widoczna szczelina).

Rekomendacja (opcja A, właściwa): **porzucić NSPopover na rzecz własnego
NSPanel** — wtedy „karta widżetu" jest naprawdę nasza:
- `NSPanel` borderless (`NSWindowStyleMaskBorderless | NonactivatingPanel`),
  `level=NSStatusWindowLevel`, `hasShadow=True`, tło przezroczyste,
  contentView z `wantsLayer`, `cornerRadius=12`, `masksToBounds`, i **2pt
  border stanu na TEJ warstwie** — jedna geometria, zero podwójnych ramek,
  zero strzałki.
- Pozycjonowanie pod ikoną: z `statusItem.button().window().frame()` wyliczyć
  x centrowane pod ikoną, y = pod paskiem menu; pokazywać/chować w
  `togglePanel:`; chować na kliknięcie poza (globalny monitor
  `NSEventMaskLeftMouseDown` + `resignKeyWindow`).
- Poller stanu (`fetch_daemon_status`/`classify_daemon_state` w
  `menubar_app.py`) ZOSTAJE — maluje border NSPanel zamiast warstwy webview.
  Kolory jak dziś: teal `#2dd4bf` / bursztyn `#fbbf24` / czerwień `#f87171`.
- Opcja B (mniejsza, jeśli A okaże się za duża): zostawić NSPopover, ale
  usunąć ring w ogóle i przenieść stan na IKONĘ menubara (kolorowa kropka
  przy wordmarku: template image + mały `NSView` z kółkiem, albo
  `attributedTitle` z „●" w kolorze stanu). Wtedy karta jest czysta, a stan
  widać nawet bez otwierania panelu. **Decyzję A vs B podjąć na starcie
  sesji; preferencja Ozzy'ego: border na karcie (A).**
- Kontrakty w `test_panel_menubar.py` (`TestStateBorder`) zaktualizować do
  wybranej opcji; utrzymać czysty DOM (bez `statusline`/`conic-gradient`).

DoD: jedna, równa ramka na krawędzi karty (bez podwójnych obrysów i szczelin),
kolor zmienia się z daemonem (stop → czerwień w ≤3 s), popover/panel dalej
otwiera się i chowa poprawnie, hotkey PTT działa.

### 4. Widok ZGODY — redesign

Problemy: pusty stan wygląda jak wyszarzony input; karta zgody to surowe
linie „shell - destructive - czeka na zgodę / id ap-... - brain".

Docelowo:
- **Pusty stan**: wycentrowany, spokojny — ikona/znak ✓ w kółku (CSS, bez
  obrazków), „Nic nie czeka", pod spodem jedno zdanie: „Gdy Jarvis poprosi
  o użycie narzędzia, decyzja pojawi się tutaj." Bez ramki-inputa
  (`empty-row` w tym widoku zastąpić klasą `empty-state`).
- **Karta zgody**: nagłówek = ludzka nazwa narzędzia (mapa PL — patrz zad. 6)
  + chip ryzyka (kolor: szarość dla odczytów, bursztyn dla zapisu, czerwień
  dla destructive, z polską etykietą); argumenty jako tabelka klucz→wartość
  (mono, wartości skracane jak dziś `argumentPreview`); meta (id, kto prosi,
  kiedy — czas względny) jedną linijką mono na dole; przyciski Zatwierdź
  (teal, pełny akcent) / Odrzuć (czerwony outline) w prawym dolnym rogu;
  „Wykonaj zatwierdzone" analogicznie dla trybu approved.
- Opis sekcji skrócić do jednego zdania (reszta żyje w pustym stanie).

DoD: pusty stan nie wygląda jak formularz; karta zgody czytelna „na rzut
oka": CO, JAK ryzykowne, JAKIE argumenty, przyciski jednoznaczne.

### 5. Widok PAMIĘĆ — ma być oczywisty na wejściu

Problemy: formularz bez labeli (gołe placeholdery „fact", „Tytuł", „0",
„Treść") — nie wiadomo co wpisać ani jak to działa.

Docelowo:
- Opis sekcji od zera: „Pamięć to notatki, które Jarvis czyta na starcie
  każdej rozmowy. Wyższy priorytet = notatka ważniejsza (wchodzi do
  kontekstu przed innymi). Wyłączona notatka zostaje w bazie, ale Jarvis
  jej nie widzi."
- **Formularz schowany**: domyślnie widać listę bloków + przycisk
  „+ Nowa notatka"; klik rozwija formularz (details albo klasa na sekcji).
- Pola Z LABELAMI nad inputami: „Rodzaj" (select z wartościami znanymi
  z API — sprawdzić dozwolone `kind` w `jarvis/api/routes_memory.py` /
  repozytorium pamięci; jeśli dowolny string, select z podpowiedziami
  fact/preference/instruction + opcja własnego), „Tytuł" („krótka nazwa,
  po której poznasz notatkę"), „Treść" („to dokładnie przeczyta model"),
  „Priorytet" (number, hint: „0 = zwykła; wyżej = ważniejsza").
- Bloki na liście: tytuł, treść, chipy `rodzaj` + `priorytet N`, meta
  „zaproponował: model · zatwierdził: approvals" zamiast surowego
  `proposed_by: model · promoted_by: approval`; akcje: pole priorytetu +
  „Zapisz" (PATCH) i „Wyłącz" (DELETE) — jak dziś, tylko opisane.

DoD: człowiek, który pierwszy raz widzi zakładkę, wie co to jest, co wpisać
i co się stanie po „Utwórz".

### 6. Narzędzia + Zdarzenia → zakładka LOGI; treści dla człowieka

- **Tabbar: CZAT / ZGODY / PAMIĘĆ / LOGI / SYSTEM** (5 zakładek; szerokość
  420px mieści krótkie etykiety).
- **LOGI** = strumień zdarzeń (dzisiejsze „Zdarzenia"): wiersze po ludzku —
  mapa typów zdarzeń na polskie etykiety (np. `listening.lease.created` →
  „Nasłuch: początek", `voice.speak.finished` → „Wypowiedź zakończona",
  `memory.updated` → „Pamięć zaktualizowana", `turn.*` → „Tura: …",
  `approval.*` → „Zgoda: …", `tool.*` → „Narzędzie: …"; fallback = surowy
  typ w mono). Meta: `#id · źródło · czas względny`. Filtr prosty (select:
  wszystko / tury / głos / zgody / narzędzia). Limit jak dziś (50 + live).
- **Narzędzia** przenieść do SYSTEM jako sekcję „Możliwości Jarvisa"
  (to rejestr capability, nie log): każda pozycja = ludzka nazwa + opis
  (jest w `tool.description`) + chip polityki zgód po polsku. Mapa nazw
  narzędzi PL w app.js (jedna stała, np. `TOOL_LABELS`): `file_read` →
  „Odczyt pliku", `file_write` → „Zapis pliku", `shell` → „Polecenie
  w terminalu", `screen_ocr` → „Odczyt ekranu (OCR)", `ui_*` → „Sterowanie
  UI: …", `memory_save` → „Zapis do pamięci", `echo` → „Echo (test)",
  `approval_probe` → „Sonda zgód (demo)"; fallback = surowa nazwa.
  Risk po polsku: `safe_read` → „bezpieczny odczyt", `file_read` → „czyta
  pliki", `file_write` → „pisze pliki", `shell_read` → „czyta przez shell",
  `destructive` → „destrukcyjne — zawsze pyta". NIGDY `file_read -
  file_read`.
- Usunąć `<details>` dla tych dwóch list (LOGI to własna zakładka, sekcja
  narzędzi w System może zostać płaska).
- Kontrakty: zaktualizować `test_index_splits_basic_and_collapsible_views`
  (zdarzenia/narzędzia wypadają z details), dodać kontrakt na `TOOL_LABELS`
  i mapę zdarzeń.

DoD: zakładka LOGI czyta się jak dziennik po polsku; w SYSTEM widać po
ludzku, co Jarvis umie i o co pyta.

### 7. Usunąć wskaźnik „live" przy Zdarzeniach

`streamStatus` („stream off"/„live" + zielony kolor) wyrzucić z UI —
o życiu łącza mówi ramka stanu na karcie (zad. 3). W app.js zostawić
`setStreamStatus` jako no-op/log wewnętrzny albo usunąć całkiem wraz
z elementem; reconnect/backoff streamu NIE ruszać. Jeśli stream padnie
a daemon żyje, jedyny skutek dla UX: brak live-append (heartbeat dalej
łata zgody) — świadomie akceptowane. Kontrakty: usunąć asercje o
`streamStatus` (test_app_stream_client_is_read_only zostaje — dotyczy
`.send(`).

### 8. SYSTEM (dawne „Zaawansowane") — ma być zrozumiały

Rozbić na płaskie, opisane sekcje (bez zagnieżdżonych details; scroll
wewnętrzny widoku już jest):

1. **Mózg** — „Model, który odpowiada za myślenie Jarvisa." Select adapterów
   + „Przełącz" (jest); pokazać `current/default` po polsku („aktywny: …,
   domyślny: …").
2. **Głos** — segmenty PTT | Nasłuch (z zad. 1) + zdanie o hotkeyu.
3. **Połączenie** — API base (opis: „Adres daemona. Zmieniaj tylko, jeśli
   wiesz po co."); stan daemona jako 3–4 ludzkie pozycje (działa od…,
   wersja schematu, głos wł/wył) zamiast surowej kv-listy; pełna kv-lista
   i Runtime → pod jednym details „Diagnostyka (surowe)" na samym dole.
4. **Ustawienia surowe** — key→JSON edytor (jest) z ostrzeżeniem: „Pary
   klucz→wartość zapisywane prosto do bazy daemona. Dla zaawansowanych."
5. Sekcja „Możliwości Jarvisa" (narzędzia, z zad. 6).

DoD: każda sekcja ma nagłówek + jedno zdanie po polsku mówiące, co tu można
zrobić i po co; surowizna (kv/runtime) schowana w „Diagnostyka (surowe)".

### 9. Pass typograficzno-odstępowy (całość)

- Jedna skala: 13.5px treść czatu / 13px kontrolki / 12px opisy / 11px meta
  mono / 10.5px etykiety zakładek; sprawdzić KAŻDY widok na 420×620 ORAZ
  420×760 (config `panel.height`).
- Paddingi kart/sekcji ujednolicić (12px), gap list 6–8px, brak podwójnych
  ramek (karta w karcie tylko tam, gdzie to wiersz listy).
- Kontrast drobnego tekstu ≥ ~4.5:1 (obecny `--text-faint: #7b8aa0` na
  ciemnych tłach jest na granicy — sprawdzić po zmianach).
- Focus-visible, prefers-reduced-motion — utrzymać (kontrakty są).

## Kolejność robienia

3 (ramka, decyzja A/B) → 2 (kompozytor) → 4 (zgody) → 5 (pamięć) →
6+7 (logi/narzędzia/live) → 1+8 (głos w System + System po ludzku) → 9
(pass końcowy). Po każdym większym kroku: celowane testy panelu + preview +
`jarvis restart` + screenshot; commit per krok (fetch → add tylko swoje →
push FF).

## Pułapki znane z poprzednich sesji

- Równolegle mogą lecieć INNE sesje na tym samym branchu — patrz zasady git.
- `.claude/launch.json` cockpit-static MUSI zostać na porcie 41800 (to
  CORS-origin dev-podglądu; API daemona = 41741).
- Heartbeat panelu nadpisuje sztuczne dane w preview co ~3 s — do smoke'ów
  wizualnych wyłączać przez `preview_eval` (podmiana `refreshHealthAndState`
  itd.) albo celować apiBase w martwy port dla ścieżki offline.
- `setText()` toleruje null-e; wszystkie renderery używają
  `textContent`/`createElement` — utrzymać (FORBIDDEN_APP_SNIPPETS).
- Statyczny serwer potrafi podać CSS bez charsetu — znaki specjalne w CSS
  `content:` wyłącznie jako escape (np. `"\203A"`).
- PyObjC: bez top-level importów AppKit/WebKit w `menubar_app.py`
  (kontrakt `test_module_imports_without_pyobjc`); malowanie UI tylko z
  głównego wątku (`NSOperationQueue.mainQueue`).
- Stary błąd sekcji nie może wisieć po przejściu offline —
  `clearDynamicSections` czyści error-boxy; nie zgubić przy refaktorze.
