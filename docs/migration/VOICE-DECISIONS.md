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
| `persona:jarvis` | active personas, Jarvis TOML, overrides, panel | VoiceResolver | conflicting M3 clean 1.35 and override M2 1.4 | supertonic M1 clean 1.35 DSP none (casting 2026-07-18; previously M3 — it collided with DAN) | pinned base M1 | Ozzy: audition-console casting 2026-07-18, candidates M2/M4/M1, M1 picked; verbal acceptance after the det-takes listening | versioned-final |
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

## Correction 2026-07-18: Jarvis voice casting

During the acceptance listening session (Task 14) Ozzy rejected the
`persona:jarvis` route on M3 („Jarvis musi mieć inny głos niż M3" — "Jarvis
must have a different voice than M3": a timbre collision with DAN; clean vs
raw is not enough to tell them apart). Casting on the audition console
(deterministic takes, production mastering and tempo, only the voice code
differing): candidates M2 (the Jarvis from before 2026-07-09), M4 (an
unclaimed code), M1 (the Maks/Codex timbre). **Picked: M1** — a deliberate
collision with Maks ([M1] raport 1.18); they are told apart by mastering
(clean vs raport) and tempo (1.35 vs 1.18). Written into
`~/.config/voice/personas.toml` (backup `personas.toml.bak-2026-07-18-jarvis-m1`),
the voice-stack regression after the change was clean. Evidence: verdicts.jsonl
and det-takes/chosen.json in the audition-console directory.

## Live voice gates 2026-07-18 (Task 14 Step 3)

An isolated `dand` (port 41999, separate HOME/DB/venv, code from the release
branch, voice catalog from the versioned `config/voice/`) played LIVE with the
operator present; the old broker/supertonic untouched (serve :7788 used solely
as a warm engine client). Report: `~/.dan/migration/release1-voice-acceptance.json`
(mode live-audio, ok=true, dan/danusia/jarvis all `done` with
`playback_confirmed=1`).

Queue evidence (RenderSnapshot per request): `dan → M3/raw/1.28`,
`danusia → F4/clean/1.28`, `jarvis → M1/clean/1.35` (the first live proof of
the M1 casting in the new path), `zaneta (live fallback) → F2/raw/1.15 +
dsp asetrate=44100*0.93,aresample=44100,atempo=1.075` — everything 1:1 with
the accepted routes of this matrix. Żaneta offline V3 remains a pre-render
outside the daemon (as documented above — it does not go through `dan speak`).

Queue properties proven live: two producers submitting <1 s apart played
strictly sequentially (disjoint playback windows); a cancel during synthesis
stopped the request before audio started; a cancel mid-speech cut the sound
in 0.17 s with no tail. Short phrases and a long report-style one with no
swallowed endings; diacritics („zażółć gęślą jaźń", „źdźbło i łąka",
„pchnąć w tę łódź jeża") correct in the operator's listening check.

A real bug from the first live listening was found and fixed on the release
branch: `CoreAudioPlayer` connected node→mixer in the default format (the
device's stereo) and scheduled Int16 mono buffers — CoreAudio kept aborting
playback (`_outputFormat.channelCount == buffer.format.channelCount`). Fix:
a lazy connection in the buffer's format (Float32, mono; the mixer mixes down
to the device) with a reconnect on format change. The first failed request of
that run in the live-gates queue is precisely this bug (kept as evidence).

**Operator verdict (2026-07-18, after the matrix re-run):** Ozzy listened to
the full live matrix from the new dand twice (first run + a re-run on demand:
dan, danusia, jarvis/M1, zaneta — 4/4 done, playback confirmed) and approved
all voice routes („Klepnięte" — approved) — zero routes rejected, the cutover
has a green light. The earlier message „jarvis nieakceptuje tego testu"
("jarvis does not accept this test") came from a different window and was
explicitly retracted by Ozzy.
