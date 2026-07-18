# Jarvis Fix Tasks Handoff

Cel: robic kazdy task w osobnej sesji. Nie mieszac zmian.

## Aktualny stan po sesji Task 5-7 + DAN fillery

Commit tej sesji powinien zawierac:
- Task 5 DONE: formatter CLI dopisuje guard, zeby model nie powtarzal persona/System context i odpowiadal tylko finalna trescia.
- Task 5 regresja: prompt zawiera jasny zakaz echo persona/System context.
- Task 6 DONE: regresje pilnuja, ze przecinek nie jest globalnym terminatorem zdania i `filler_after_ms` jest liczony w milisekundach (`/ 1000.0`). Produkcyjny kod byl juz zgodny z tym kontraktem.
- Task 7 DONE: `DaemonApp.close()` sprzata wystartowany voice runtime przez `stop(reason="close")` przed zamknieciem DB.
- Task 7 regresja: `app.start(); app.close()` zatrzymuje recorder/broker/STT/gateway/sweeper.
- Voice filler cleanup: domyslne fillery sa wspolna pula `DEFAULT_VOICE_FILLERS`, bez robotycznego `Już sprawdzam.`, z wybranymi polskimi/memicznymi/DANowymi tekstami.

Weryfikacja wykonana w trakcie sesji przed finalnym wyborem fillerow:
- `.venv/bin/python -m pytest -q tests/test_brain_cli_adapters.py`
- `.venv/bin/python -m pytest -q tests/test_config.py::test_default_voice_fillers_have_enough_variation`
- `.venv/bin/python -m pytest -q tests/test_voice_broker.py::test_filler_fires_once_when_generation_is_slow tests/test_voice_broker.py::test_filler_delay_uses_milliseconds`
- `.venv/bin/python -m pytest -q tests/test_sentence_chunker.py tests/test_voice_broker.py`
- `.venv/bin/python -m pytest -q tests/test_voice_fix04.py tests/test_daemon_sigterm.py`
- `git diff --check`

Uwaga: po ostatnim finalnym wyborze fillerow testy celowo NIE byly uruchamiane, bo user powiedzial: "nie odpalaj testow" i ze odpali je nowa sesja po tasku.

## Aktualny stan po sesji Task 1-2

Commit tej sesji powinien zawierac:
- Task 1 DONE: `POST /voice/ptt/down` natychmiast anuluje aktywna mowe przez `voice_cancellation.cancel_active_speech(reason="ptt_down")` przed zalozeniem listening lease.
- Task 1 regresja: aktywny `voice_queue` po `POST /voice/ptt/down` przechodzi na `cancelled`, pojawia sie `voice.speak.cancelled`, a event cancel jest przed `listening.lease.created`.
- Task 2 DONE: `SpeechStreamSession.feed()` rozbraja filler przy pierwszej niepustej delcie, zanim chunker domknie pelne zdanie.
- Task 2 regresja: `session.feed("Pierwsza delta bez kropki")` rozbraja timer i nie wrzuca jeszcze nic do `voice_queue`; kolejne delty nie rozbrajaja drugi raz.

Weryfikacja wykonana przed commitem:
- `.venv/bin/python -m pytest -q tests/test_listening_leases.py tests/test_voice_turn_gateway.py`
- `.venv/bin/python -m pytest -q tests/test_speech_stream_session.py tests/test_streaming_turn_speech.py`
- `git diff --check`

## Prompt dla nastepnej sesji

```text
Pracujesz w $HOME/Documents/dev/jarvis.

Najpierw przeczytaj docs/JARVIS_FIX_TASKS_HANDOFF.md i sprawdz git status.

Poprzednia sesja dodala Task 5, Task 6 regresje, Task 7 oraz finalna pule DANowych voice fillerow, ale na prosbe usera NIE odpalala testow po ostatniej edycji fillerow.

Twoje zadanie: tylko zweryfikuj ostatni commit i raportuj wynik. Nie zmieniaj kodu, chyba ze testy realnie failuja i user wyraznie kaze naprawiac.

Zasady:
- sprawdz git status przed zmianami
- nie cofaj cudzych zmian
- nie ruszaj panelu ani nowych zadan przy okazji
- uruchom tylko focused testy zwiazane z ostatnim commitem:
  - `.venv/bin/python -m pytest -q tests/test_brain_cli_adapters.py`
  - `.venv/bin/python -m pytest -q tests/test_config.py::test_default_voice_fillers_have_enough_variation`
  - `.venv/bin/python -m pytest -q tests/test_sentence_chunker.py tests/test_voice_broker.py`
  - `.venv/bin/python -m pytest -q tests/test_voice_fix04.py tests/test_daemon_sigterm.py`
  - `git diff --check`
- na koncu daj krotki raport: commit hash, testy, git status

Jesli wszystko przejdzie, raportuj tylko wynik weryfikacji. Jesli cos failuje, pokaz konkretny failure i czekaj na decyzje usera.
```

## Task 1 - PTT Instant Cancel [DONE]

Problem: `POST /voice/ptt/down` tylko startuje listening lease. Jarvis przestaje mowic dopiero po capture -> VAD -> STT -> anti-echo -> transcript.

Poprawka: `ptt/down` oraz ewentualnie `listen/lock` maja od razu wolac `voice_cancellation.cancel_active_speech(reason="ptt_down")`, zanim recorder/STT zacznie robote.

Pliki startowe:
- `jarvis/api/routes_voice.py`
- `jarvis/daemon/app.py`
- `jarvis/voice/cancellation.py`
- `tests/test_listening_leases.py` albo nowy test voice API

Weryfikacja:
- test endpointu: aktywna kolejka/glos -> `POST /voice/ptt/down` -> `voice.speak.cancelled`
- `.venv/bin/python -m pytest -q tests/test_listening_leases.py tests/test_voice_turn_gateway.py`

## Task 2 - Filler Off On First Delta [DONE]

Problem: filler gasnie dopiero po pierwszym pelnym zdaniu w streamie. Jesli model juz streamuje, ale nie domknal zdania, filler moze wejsc przed wlasciwa odpowiedzia.

Poprawka: rozbroic filler przy pierwszej sensownej delcie albo po progu kilku/kilkunastu znakow, nie dopiero po chunku zdania.

Pliki startowe:
- `jarvis/voice/speech.py`
- `jarvis/turns/orchestrator.py`
- `tests/test_speech_stream_session.py`
- `tests/test_streaming_turn_speech.py`

Weryfikacja:
- nowy test: `session.feed("pierwsza delta bez kropki")` rozbraja timer
- `.venv/bin/python -m pytest -q tests/test_speech_stream_session.py tests/test_streaming_turn_speech.py`

## Task 3 - Implement Interruptible Filler

Problem: filler zapisuje `interrupt_policy="interruptible"`, ale broker tego nie uzywa. Kontrakt mowi, ze realna odpowiedz moze uciac filler, runtime tego nie robi.

Poprawka: dodac `interrupt_policy` do `VoiceRequest`, czytac go z `voice_queue`, a broker ma przerwac aktualny filler, gdy pojawi sie sentence dla tego samego turnu.

Pliki startowe:
- `jarvis/voice/models.py`
- `jarvis/voice/queue.py`
- `jarvis/voice/broker.py`
- `jarvis/voice/tts.py`
- `tests/test_voice_broker.py`

Weryfikacja:
- test: speaking filler + queued sentence -> filler cancelled/interrupted -> sentence gra dalej
- `.venv/bin/python -m pytest -q tests/test_voice_broker.py tests/test_voice_tts_supertonic.py`

## Task 4 - Persona Boundaries

Problem: failuja testy persony. `config/persona/jarvis.md` i `gangus-1.md` nie maja wymaganych markerow: `Granice`, `approval`, `registry`.

Poprawka: przywrocic twarde granice w bazowej personie i `gangus-1`, bez mieszania persony z uprawnieniami tooli.

Pliki startowe:
- `config/persona/jarvis.md`
- `config/persona/gangus-1.md`
- `tests/test_persona_assets.py`

Weryfikacja:
- `.venv/bin/python -m pytest -q tests/test_persona_assets.py`

## Task 5 - Prompt/Profile Echo Guard

Problem: persona idzie do `System context` i adapter CLI splaszcza ja do promptu. Jesli model echo-uje profil/system context, TTS moze to wypowiedziec jako normalna odpowiedz.

Poprawka: dopisac w formatterze jasna zasade: nie powtarzaj persona/system context, odpowiedz tylko finalna trescia. Dodatkowo test, ze prompt zawiera guard.

Pliki startowe:
- `jarvis/brain/claude_cli_adapter.py`
- `tests/test_brain_cli_adapters.py`

Weryfikacja:
- `.venv/bin/python -m pytest -q tests/test_brain_cli_adapters.py`

## Task 6 - Chunker And Filler Timing Cleanup

Problem: dirty zmiany wskazuja na ryzykowne zachowanie:
- `jarvis/voice/chunker.py` traktuje przecinek jak terminator zdania
- `jarvis/voice/speech.py` ma `delay_ms / 1500.0`, co przyspiesza filler zamiast go opoznic

Poprawka: nie ciac globalnie po przecinku. Przywrocic przelicznik fillera do `/ 1000.0` albo ustawic swiadomie wiekszy `filler_after_ms`.

Pliki startowe:
- `jarvis/voice/chunker.py`
- `jarvis/voice/speech.py`
- `tests/test_sentence_chunker.py`
- `tests/test_voice_broker.py`

Weryfikacja:
- test chunkera: przecinek nie emituje osobnego chunku
- `.venv/bin/python -m pytest -q tests/test_sentence_chunker.py tests/test_voice_broker.py`

## Task 7 - Close Cleans Voice Runtime

Problem: normalny CLI robi `stop()` przed `close()`, ale samo `DaemonApp.close()` tylko zamyka DB i zeruje referencje. Skroty/testy po `start()` moga zostawic broker/recorder/STT/sweeper.

Poprawka: zrobic `close()` idempotentnym cleanupem albo jasno wymusic `stop()` w helperach testowych. Preferowane: `close()` bezpiecznie sprzata voice runtime, jesli jeszcze jest started.

Pliki startowe:
- `jarvis/daemon/app.py`
- `tests/test_voice_fix04.py` albo nowy test daemon cleanup

Weryfikacja:
- test: `app.start(); app.close()` zatrzymuje broker/recorder/sweeper bez wiszacych watkow
- `.venv/bin/python -m pytest -q tests/test_voice_fix04.py tests/test_daemon_sigterm.py`

## Kolejnosc

1. Task 1 - PTT Instant Cancel
2. Task 2 - Filler Off On First Delta
3. Task 3 - Implement Interruptible Filler
4. Task 6 - Chunker And Filler Timing Cleanup
5. Task 4 - Persona Boundaries
6. Task 5 - Prompt/Profile Echo Guard
7. Task 7 - Close Cleans Voice Runtime
