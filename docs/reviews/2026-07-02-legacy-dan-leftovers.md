# Legacy DAN leftovers — snapshot do decyzji (H2, diagnose-only)

> **Status:** snapshot z 2026-07-02, wygenerowany przez `scripts/jarvis-dan-report`
> (moduł `jarvis/diagnostics/legacy_dan.py`, testy: `tests/test_legacy_dan_report.py`).
> Odśwież przed dniem "Jarvis w 100%": `scripts/jarvis-dan-report` (albo `--json`).
>
> Dekret §7.6: narzędzie NICZEGO nie kasuje — sprzątanie wykonuje wyłącznie
> Ozzy, ręcznie. Autorytatywny obraz runtime'u legacy: `docs/LEGACY_RUNTIME_FINDINGS.md`.
> Rejestr znanych nazw legacy współdzielony z `jarvis/runtime/supervisor.py`.

```
JARVIS — raport pozostałości po legacy DAN (H2, diagnose-only)
==============================================================
To narzędzie NICZEGO nie kasuje i nie zatrzymuje — wyłącznie
raportuje. Sprzątanie wykonuje wyłącznie Ozzy, ręcznie, w dniu
"Jarvis w 100%" (dekret §7.6: DAN działa osobno do odwołania).

[BRAK  ] Procesy DAN-lineage: - (-)
         ↳ Brak pasujących procesów w chwili raportu.
[OBECNE] LaunchAgent DAN: $HOME/Library/LaunchAgents/com.dan.voice-broker.plist (2.6 KiB)
         ↳ Definicja usługi; sam plik nie znaczy, że usługa jest załadowana.
[OBECNE] Repo legacy DAN: $HOME/Documents/dev/dan (3.0 GiB)
[OBECNE] Venv w repo DAN: $HOME/Documents/dev/dan/.venv (1.5 GiB)
         ↳ Wliczony też w rozmiar repo powyżej.
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-autojarvis.out (7.6 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-jarvis.out (9.0 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-listen (67.4 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel-audit-crop.png (44.8 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel-audit-panel.png (118.9 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel-audit.png (1.5 MiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel-shot.png (605.7 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel-shot2.png (35.7 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel.log (3.7 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-panel.out (0 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-primary (5 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-say.lock (0 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-session-names.json (65 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-shot.png (2.0 MiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-tcctest.log (125 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-voice (141.4 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-voice-broker.err (5.8 KiB)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-voice-broker.out (0 B)
[OBECNE] Pozostałość tymczasowa DAN: /tmp/dan-voice-queue (0 B)
[OBECNE] Model chatterbox w HF cache: $HOME/.cache/huggingface/hub/models--ResembleAI--chatterbox (10.9 GiB)
         ↳ PyTorch chatterbox po DANie.
[OBECNE] Model chatterbox w HF cache: $HOME/.cache/huggingface/hub/models--litmudoc--Chatterbox-Multilingual-MLX-v2-fp16 (2.4 GiB)
         ↳ Uwaga: wariant MLX to najprawdopodobniej zasób Jarvisa (M1, dekret §7.8 — zostaje). NIE KASOWAĆ bez osobnej decyzji.
[BRAK  ] XTTS venv: $HOME/xtts-venv | $HOME/Documents/dev/xtts-venv | $HOME/Documents/dev/dan/xtts-venv (-)
         ↳ Nie znaleziono w żadnej z historycznych lokalizacji.
[OBECNE] Modele TTS w Application Support: $HOME/Library/Application Support/tts (1.7 GiB)

Łącznie na dysku: 18.0 GiB
Z tego zasoby Jarvisa (nie kasować): 2.4 GiB
Kandydat do zwolnienia decyzją Ozzy'ego: 15.6 GiB
```
