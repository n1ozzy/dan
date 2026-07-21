# Jarvis History

> ## ⛔ HISTORICAL — A COMMIT MAP, NOT A DESCRIPTION OF CURRENT BEHAVIOUR
>
> **Classification: historical.** Superseded by the Release 1 cutover
> (2026-07-18) and the 2026-07-21 audit. Every bullet below is the *title of a
> commit that happened*, which is a permanently true statement about history and
> a frequently false statement about the code. A feature named here may since
> have been rewritten, disabled, or deleted.
>
> The clearest example: the commits `4ab52fc docs: design source-sensitive
> permission model`, `077ccbd feat: make permission decisions source-sensitive`
> and `0fd3a49 feat: add approval-gated file_write and whitelisted shell_read
> tools` all really landed — and none of that behaviour survives.
> `ToolPermissionPolicy.decide()` now returns ALLOW unconditionally and
> `ToolRegistry.request_tool()` executes immediately. Containment lives inside
> the individual tools.
>
> Naming: `jarvisd`/`com.ozzy.jarvisd`/`~/.jarvis` here = today's
> `dand`/`com.dan.dand`/`~/.dan`, API `127.0.0.1:41741`. The package is `dan/`,
> not `jarvis/`.
>
> Current truth: `AGENTS.md`, `docs/PROJECT_RULES.md`, `docs/STATUS.md`,
> `docs/CO-JEST-GDZIE.md`, and the code under `dan/`. For real history, use
> `git log` — this file is a map, not a source.

Classification: history summary.
Source: branch `rescue/audt-gpt5.5pro-limit-cdn` git log through HEAD `bd18d3b`.

## Purpose

This document condenses the project history from the first commit through the active Memory OS branch. It is not a replacement for `git log`. It is a map for humans and future model sessions, because apparently archaeology is now part of software maintenance.

## Early foundation

The project starts at `8107911 Initial commit`.

Early commits establish:

- runtime contracts;
- legacy runtime inventory;
- v4.1 runtime scaffold;
- config and runtime paths;
- SQLite schema and migrations;
- event store and bus;
- runtime state machine;
- daemon API;
- runtime supervisor endpoints.

Representative commits:

- `49327c6 docs: freeze Jarvis runtime contracts`
- `64c0ae3 docs: inventory legacy Jarvis runtime`
- `bffc1b6 chore: scaffold Jarvis v4.1 runtime repo`
- `ab13bdd feat: add Jarvis config and runtime paths`
- `3bb5ccd feat: add SQLite schema and migrations`
- `3aff76b feat: add event store and event bus`
- `a18d3c3 feat: add Jarvis runtime state machine`
- `eedd5c8 feat: add Jarvis daemon API`

## Brain and text pipeline

Jarvis then gains stateless brain contracts, context assembly, conversation/turn persistence, and a text turn pipeline.

Representative commits:

- `7a98b27 feat: add brain adapter interface`
- `35e30bb feat: build Jarvis-owned brain context`
- `c5d4dda fix: make brain context metadata deterministic`
- `41b47b1 feat: add conversation and turn repositories`
- `32e0df5 feat: add text turn pipeline`
- `06048a2 fix: harden text turn pipeline`
- `88caded feat: add CLI text input command`
- `706d33c feat: add read-only conversation history`
- `829831f test: add text runtime smoke harness`

## Tools, approvals, and providers

Next came safe CLI adapters, tools, approval gates, approved execution, model tool request capture, and provider tool-call parsing.

Representative commits:

- `b9f3c5e feat: add safe CLI brain adapters`
- `99dc04d feat: add tool registry and approvals`
- `392a295 feat: execute approved tool requests`
- `14c6ece feat: capture model tool requests`
- `ce03856 feat: parse provider tool requests`
- `15c0f20 fix: correlate approval events with turns`

## Memory v0 and cockpit

The first memory path used `memory_blocks`. The API/CLI and memory runtime smoke came before Memory OS.

Representative commits:

- `9c2457e feat: add memory API and CLI`
- `3eef9e4 test: add memory runtime smoke harness`
- `3b5cc24 feat: add static Jarvis cockpit`
- `409b8a8 fix: allow localhost cockpit CORS`

## macOS tools and operator model

The project then added macOS operator contracts, source-sensitive permissions, local transport token, file tools, UI tools, screen tools, and terminal automation.

Representative commits:

- `d8d57d9 docs: define macOS operator contract`
- `4ab52fc docs: design source-sensitive permission model`
- `b3b467b feat: require local transport token on mutating API requests`
- `077ccbd feat: make permission decisions source-sensitive`
- `ac0bf7b feat: add real read-only file tool with fail-closed roots`
- `0fd3a49 feat: add approval-gated file_write and whitelisted shell_read tools`
- `e6a6011 feat: add read-only accessibility adapter with ui_read tools`
- `ba657a0 feat: add approval-gated accessibility action tools (ui_act)`
- `bf9eea1 feat: add narrow screen_read tools with screencapture + Vision OCR bridge`
- `1765c08 feat: add terminal/iTerm operator profile via fixed-script osascript bridge`

## Brain switching, workers, persona, and MVP smoke

The next line added persisted brain switching, worker broker, settings UI, persona profiles, and MVP smoke.

Representative commits:

- `90a402d feat: add brain switch API with settings-persisted adapter choice`
- `619fb59 feat: add WorkerBroker with mock worker and memory-candidate handoff`
- `a9acec4 feat: add settings UI with brain switch to static cockpit`
- `ad41622 feat: add persona profiles with settings-driven selector`
- `0718531 feat: add e2e MVP operator smoke walking acceptance criteria`

## Voice G0-G4

Voice moved through design, audio devices, listening leases, queue/broker, real TTS/STT paths, anti-echo, barge-in, streaming deltas, and live gate docs.

Representative commits:

- `9aaa5d0 docs: design voice sentence-streaming contract with fillers policy`
- `881bc9a feat: add read-only AudioDeviceManager with policy and snapshots`
- `6f5d101 feat: add DB-backed ListeningLease manager with PTT API and mock recorder`
- `193157b feat: add sentence chunker, persisted VoiceQueue and TTS broker with fillers`
- `0130e78 feat: add real Supertonic TTS engine with sox playback`
- `3a2c38c feat: add real sox recorder behind the G2 lease interface`
- `9a92028 feat: add MLX whisper STT with mandatory hallucination filters`
- `269f622 feat: add anti-echo gate and 3-leg barge-in cancellation`
- `27618ee feat: add on_delta streaming in CLI adapters wired to live speech`

## Panel, hotkey, and operator UX

The panel evolved through menu-bar shell, wordmark status item, operator-first UI, PTT button/listening state, several panel UX passes, and global PTT hotkey wiring.

Representative commits:

- `c79edaf feat: H1 menu-bar shell — NSStatusItem popover hosting the cockpit`
- `a909564 feat: status item uses the JARVIS wordmark as a template icon`
- `05b8173 feat: operator-first panel — basic/advanced split, PL labels, Enter sends`
- `d95f304 feat: PTT button + listening state in panel cockpit`
- `c27bd6d feat(panel): global PTT hotkey logic + config field`
- `a4e9fdf feat(panel): wire global PTT hotkey into native menu-bar shell`

## Security and reliability hardening

The FIXME line closed many security/reliability issues, including CORS null origin, git config RCE, per-thread SQLite connections, hot-mic containment, turn/orchestrator consistency, API hardening, token-on-private-GET, redaction, path containment, logging rotation, and barge-in fixes.

Representative commits:

- `884d500 fix(security): FIX-01 — remove "null" from CORS allowlist`
- `78a58b4 fix(security): FIX-02 — harden whitelisted git against repo-local config RCE`
- `b61f537 fix(critical): FIX-03 — per-thread SQLite connections`
- `00a42be fix(voice): FIX-04 — hot-mic containment + broker survivability`
- `8cc2ebd fix(turns): FIX-05 — turn/orchestrator state consistency`
- `9c79ea6 fix(daemon): FIX-06 — API hardening`
- `5406421 fix(daemon): FIX-06 follow-up — require transport token on private-data reads`
- `9fa6840 fix(tools/security): FIX-08 redaction + containment`
- `82cde12 fix(brain/workers): FIX-07 stdin deadlock + atomic claim + context/tool hardening`
- `b1711da fix(voice): FIX-09 barge-in cancel path + anti-echo corpus + DB migration`

## Memory OS branch

The Memory OS branch starts after the v4.2/rescue line and adds a structured, evidence-backed memory lifecycle.

Representative commits:

- `daa4e8c Tu zaczynamy MEMORY dziffko`
- `beab31f docs: define Jarvis Memory OS contract`
- `afff084 test: characterize current memory behavior`
- `6f7000a docs: design Memory OS data model`
- `47b8183 feat: add Memory OS schema foundation`
- `b1ab11c feat: add memory candidate inbox`
- `d298ac3 feat: add memory evidence ledger`
- `78cf5e2 feat: activate approved memory candidates`
- `224faf9 feat: wire memory activation API`
- `9a53985 feat: route memory_save through Memory OS`
- `f3370ab docs: define MemoryCompiler contract`
- `31f7fd9 docs: add MemoryCompiler governance addendum`
- `36f0a9f feat: add deterministic MemoryCompiler`
- `7f25633 test: add MemoryCompiler golden scenarios`
- `35313e8 feat: add MemoryCompiler preview API`
- `bfd34f5 test: harden MemoryCompiler preview API`
- `f3c70fd feat: wire MemoryCompiler into ContextBuilder`
- `cf65e2b chore: wire compiled memory runtime dependencies`
- `7bf8a90 test: snapshot compiled memory context shape`
- `4266fbc test: harden compiled memory context governance`
- `bd18d3b feat: add compiled memory context observability`

## Where this map stops (NOT "current")

`MEMORY-CONTEXT-OBSERVE-01` was committed at `bd18d3b` on branch
`rescue/audt-gpt5.5pro-limit-cdn`. That is where this map ends — it is **not**
the current HEAD and `rescue/audt-gpt5.5pro-limit-cdn` is **not** the current
branch.

As of 2026-07-21 the active branch is `agent/dan-release1-integration`, and
`bd18d3b` is an ancestor of it. Everything after `bd18d3b` — including the whole
Jarvis → DAN rename, the Release 1 cutover, and the removal of the approval
gate — is not covered here. Run `git log --oneline` for the real head.
