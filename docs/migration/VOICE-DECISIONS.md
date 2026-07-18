# Voice migration decisions

Task 6 freezes the active shared catalog and the legal, reproducible asset set. The
versioned files under `config/voice/` are the migration source of truth. Task 5's
`VoiceResolver` remains the only runtime resolver; Task 7 will own persistence and
playback wiring.

## Audited sources

- Active `~/.config/voice/personas.toml`, six dated backups, `gains.json`, and
  `pronunciations.toml`.
- `state/overrides.json`, `dan_core/say.py`, both identical `voice_turn.sh` copies
  (SHA-256 `fb59145949d881c4003f2a5867e8c118e62d5aee5acb67814dd3491e38fab89e`),
  the radio feeder, schedules/scenarios, panel voice data, and `~/.jarvis/jarvis.toml`.
- The 466-sample Voice Lab verdicts, accepted casting samples, the custom-style
  cache, Chatterbox V3 generators, local references, and accepted Zaneta outputs.

The old example comment saying DSP was feeder-owned was false. DSP is resolved into
the immutable render snapshot and must pass unchanged to playback. Missing measured
gain keeps the existing loudness fallback
`loudnorm=I=-14:TP=-2.0:LRA=7,aresample=44100`; no calibration value is invented.

## Final matrix

| Key | Sources | Reader | Effective old value | Final route | Asset | Audio evidence | Decision |
|---|---|---|---|---|---|---|---|
| `persona:jarvis` | active personas, Jarvis TOML, overrides, panel | VoiceResolver | conflicting M3 clean 1.35 and override M2 1.4 | supertonic M1 clean 1.35 DSP none (casting 2026-07-18; wcześniej M3 — kolidował z DAN-em) | pinned base M1 | Ozzy: casting konsoli odsłuchowej 2026-07-18, kandydaci M2/M4/M1, wybrany M1; akceptacja słowna po odsłuchu det-takes | versioned-final |
| `persona:dan` | active personas plus six backups, say.py, voice turns | VoiceResolver | M3 raw 1.28 active; older 1.25 | supertonic M3 raw 1.28 DSP none | pinned base M3 | active accepted route; raw won mastering audit | versioned-final |
| `persona:danusia` | active personas plus backups, radio scenarios | VoiceResolver | F4 clean 1.28 active; older 1.25 | supertonic F4 clean 1.28 DSP none | pinned base F4 | active accepted route | versioned-final |
| `persona:zaneta` | active personas, PERSONA-ZANETA, V3 generators | VoiceResolver plus offline pipeline | live F2 raw 1.15; offline Lily V3 | offline Chatterbox V3 explicit; live fallback supertonic F2 raw 1.15 | local-only reference hash 06f54e0f; no WAV versioned | V3 accepted 0.95 to 1.00; better than V2 | versioned-final-local-only |
| `persona:zdzicho` | active personas, casting and radio scenarios | VoiceResolver | M5 raw 0.95 with pitch DSP | supertonic M5 raw 0.95 with versioned DSP | pinned base M5 | accepted casting persona | versioned-final |
| `persona:krysia` | active personas, casting and radio scenarios | VoiceResolver | F1 raw 0.95 | supertonic F1 raw 0.95 DSP none | pinned base F1 | accepted casting persona | versioned-final |
| `persona:komentator` | active personas, casting and panel | VoiceResolver | M2M1 raw 1.45 | supertonic M2M1 raw 1.45 DSP none | versioned M2M1 JSON | casting 10 of 10 | versioned-final |
| `persona:spiker` | active personas, casting and panel | VoiceResolver | ROBOT raw 1.2 | supertonic ROBOT raw 1.2 DSP none | versioned ROBOT JSON | casting 10 of 10; system voice role | versioned-final |
| `persona:ksiadz` | active personas, casting and scenarios | VoiceResolver | M1 raw 1.05; scenario M4 existed | supertonic M1 raw 1.05 DSP none | pinned base M1 | casting 10 of 10 | versioned-final; M4 remains line override |
| `persona:typ_z_telefonu` | active personas, casting and scenarios | VoiceResolver | M1M3 raw 1.1 telephone EQ | supertonic M1M3 raw 1.1 with versioned DSP | versioned M1M3 JSON | casting 10 of 10 | versioned-final |
| `persona:blondyna` | active personas, casting and scenarios | VoiceResolver | F2 raw 1.15 pitch DSP | supertonic F2 raw 1.15 with versioned DSP | pinned base F2 | accepted casting; do not pair with Danusia | versioned-final |
| `persona:zagadka` | active personas, casting and scenarios | VoiceResolver | F1M1 raw 1.1 | supertonic F1M1 raw 1.1 DSP none | versioned F1M1 JSON | casting 9 of 10 | versioned-final |
| `persona:radiowiec` | active personas, casting and scenarios | VoiceResolver | M1M3 raw 1.15 radio EQ | supertonic M1M3 raw 1.15 with versioned DSP | versioned M1M3 JSON | accepted casting persona | versioned-final |
| `persona:M1` | active personas, Voice Lab battery | VoiceResolver | M1 raport 1.18 | supertonic M1 raport 1.18 DSP none | pinned base M1 | base voice accepted; measured gain retained | versioned-final |
| `persona:M2` | active personas, Voice Lab battery | VoiceResolver | M2 raw 1.2 | supertonic M2 raw 1.2 DSP none | pinned base M2 | base voice accepted | versioned-final |
| `persona:M3` | omitted active row, backups and audit decision | VoiceResolver | inherited or captured by legacy override | supertonic M3 raw 1.25 DSP none | pinned base M3 | explicit raw-code route required by audit | versioned-final |
| `persona:M4` | active personas, Voice Lab battery | VoiceResolver | M4 raw 1.15 | supertonic M4 raw 1.15 DSP none | pinned base M4 | retained explicit raw code despite low casting rank | versioned-final |
| `persona:M5` | active personas, Voice Lab battery | VoiceResolver | M5 raw 1.1 | supertonic M5 raw 1.1 DSP none | pinned base M5 | explicit raw-code route | versioned-final |
| `persona:F1` | active personas, Voice Lab battery | VoiceResolver | F1 clean 1.15 | supertonic F1 clean 1.15 DSP none | pinned base F1 | accepted base voice | versioned-final |
| `persona:F2` | active personas, Voice Lab battery | VoiceResolver | F2 raw 1.25 | supertonic F2 raw 1.25 DSP none | pinned base F2 | ranked best female base timbre | versioned-final |
| `persona:F3` | active personas, Voice Lab battery | VoiceResolver | F3 raw 1.25 | supertonic F3 raw 1.25 DSP none | pinned base F3 | retained explicit raw code | versioned-final |
| `persona:F4` | omitted active row, backups and audit decision | VoiceResolver | inherited or captured by legacy override | supertonic F4 clean 1.25 DSP none | pinned base F4 | explicit clean-code route required by audit | versioned-final |
| `persona:F5` | active personas, Voice Lab battery | VoiceResolver | F5 raw 1.25 | supertonic F5 raw 1.25 DSP none | pinned base F5 | retained explicit raw code | versioned-final |
| `state/overrides.json:voice.enabled` | overrides | legacy panel | true | installation-owned enablement, not catalog | none | not an audio choice | rejected-from-catalog |
| `state/overrides.json:voice.backend` | overrides | legacy broker and panel | supertonic | resolver engine per persona | pinned engine revision | active engine decision | superseded |
| `state/overrides.json:voice.report_persona` | overrides | say.py | dan | speech intent persona | none | routing only | superseded |
| `state/overrides.json:voice.supertonic_voice` | overrides | legacy panel | M1 | no global catalog override | none | previously captured bare codes | rejected |
| `state/overrides.json:voice.dan_supertonic_voice` | overrides | say.py | M3 | persona dan route | pinned base M3 | agrees on voice only | superseded |
| `state/overrides.json:voice.danusia_supertonic_voice` | overrides | say.py | F4 | persona danusia route | pinned base F4 | agrees on voice only | superseded |
| `state/overrides.json:voice.dan_drift` | overrides | legacy say.py | false | no resolver field | none | rejected experiment | rejected |
| `state/overrides.json:voice.dan_profile` | overrides | legacy say.py | bastard | persona dan raw | none | raw beat bastard 7.4 to 2.6 | rejected |
| `state/overrides.json:voice.supertonic_speed` | overrides | legacy panel | 1.2 | per-persona speed only | none | global value caused drift | rejected |
| `state/overrides.json:voice.dan_speed` | overrides | say.py | 1.25 | persona dan 1.28 | none | active catalog is newer | superseded |
| `state/overrides.json:voice.dan_voice` | overrides | legacy panel | F2 | persona dan M3 | pinned base M3 | contradicted accepted DAN voice | rejected |
| `state/overrides.json:voice.jarvis_supertonic_voice` | overrides | legacy panel | M2 | persona jarvis M3 | pinned base M3 | active shared catalog wins | rejected |
| `state/overrides.json:voice.jarvis_speed` | overrides | legacy panel | 1.4 | persona jarvis 1.35 | none | active shared catalog wins | rejected |
| `state/overrides.json:voice.zaneta_supertonic_voice` | overrides | legacy panel | F2 | explicit live fallback F2; offline V3 canonical | local-only reference metadata | agrees only with live fallback | narrowed |
| `state/overrides.json:voice_v2.max_speech_s` | overrides | legacy input | 180 | installation voice-input config | none | outside TTS catalog | retained-outside-task |
| `state/overrides.json:voice_v2.stop_silence_s` | overrides | legacy input | 0.2 | installation voice-input config | none | outside TTS catalog | retained-outside-task |
| `state/overrides.json:voice_v2.input_device` | overrides | legacy input | MacBook microphone | installation device config | none | local hardware only | retained-outside-task |
| `state/overrides.json:voice_v2.start_silence_s` | overrides | legacy input | 0.15 | installation voice-input config | none | outside TTS catalog | retained-outside-task |
| `~/.jarvis/jarvis.toml:voice.supertonic_voice` | Jarvis TOML | legacy Jarvis runtime | M3 | persona jarvis M3 | pinned base M3 | voice agrees | superseded-by-versioned |
| `~/.jarvis/jarvis.toml:voice.mastering_profile` | Jarvis TOML | legacy Jarvis runtime | bastard despite clean comments | persona jarvis clean | none | contradictory file; active shared route wins | rejected |
| `~/.jarvis/jarvis.toml:voice.tts_pronunciations` | Jarvis TOML | legacy Jarvis runtime | local differences such as bug to bag | Jarvis-local override only | none | must not alter shared dictionary | retained-local-only |

## Asset and licensing outcome

Exactly 20 custom Supertonic style JSON files are versioned with deterministic
recipes and SHA-256 values. They are derivatives of model revision
`724fb5abbf5502583fb520898d45929e62f02c0b` and carry the included OpenRAIL-M
license and notice. Base model assets remain installer-fetched at the pinned
revision.

The Lily reference lacks sufficient provenance for redistribution or cloning as
a shipped product asset. Its expected SHA-256 is versioned as metadata, while the
WAV and all generated Zaneta WAV files remain local-only and untracked. The offline
pipeline requires explicit local paths, verifies the Chatterbox source commit
`65b18437192794391a0308a8f705b1e33e633948` and model snapshot
`5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`, disables network fallback, and only
publishes mono PCM16 candidates scoring at least `0.9`.

## Korekta 2026-07-18: casting głosu Jarvisa

Podczas odsłuchu akceptacyjnego (Task 14) Ozzy odrzucił trasę `persona:jarvis`
na M3 („Jarvis musi mieć inny głos niż M3" — kolizja barwy z DAN-em, clean vs
raw nie wystarcza do rozróżnienia). Casting na konsoli odsłuchowej
(deterministyczne take'y, mastering i tempo produkcyjne, różny tylko kod
głosu): kandydaci M2 (Jarvis sprzed 2026-07-09), M4 (wolny kod), M1 (barwa
Maksa/Codexa). **Wybrany: M1** — świadoma kolizja z Maksem ([M1] raport 1.18);
rozróżnia ich mastering (clean vs raport) i tempo (1.35 vs 1.18). Wpisane do
`~/.config/voice/personas.toml` (backup `personas.toml.bak-2026-07-18-jarvis-m1`),
regresja voice stacku po zmianie czysta. Dowody: verdicts.jsonl i
det-takes/chosen.json w katalogu konsoli odsłuchowej.
