# DAN

Jeden lokalny runtime głosowo-tekstowy: daemon `dand` (jedyny właściciel
audio, hotkeya i kolejki głosu), CLI `dan` i panel w pasku menu.

Zasady runtime'u:

- Jedna rozmowa DAN. Mózg to jeden trwały proces `claude_cli` (stream-json).
- Tożsamość DAN-a pochodzi wyłącznie z `config/persona/DAN.md` (kanon w tym
  repo), ładowanego świeżo i fail-loud. Historia rozmowy i pamięć to dane
  kontekstowe, nigdy instrukcje persony.
- TTS to Supertonic; testy zawsze mockują warstwę TTS.
- Szczegóły własności: `docs/adr/001-dand-single-owner.md` i
  `docs/CO-JEST-GDZIE.md`.

## Instalacja

```bash
git clone <repo> DAN && cd DAN
bash scripts/install.sh --no-launchd
```

Instalator jest backup-first: tworzy `~/.dan/venv`, wrappery `~/.dan/bin/dan`
i `~/.dan/bin/dand`, a każdą podmienioną ścieżkę odkłada do
`~/.dan/backups/`. Nie dotyka `~/.dan/dan.db` ani archiwów. Autostart przez
launchd to osobny, świadomy krok:

```bash
bash scripts/install-launchd.sh --yes
```

## Pierwszy start

1. Skopiuj `config/dan.example.toml` do `~/.dan/config.toml` i przejrzyj
   (ścieżki, port, mózg, głos).
2. Uruchom daemona: przez launchd (po `install-launchd.sh`) albo ręcznie
   `~/.dan/bin/dand`.
3. Sprawdź zdrowie:

```bash
dan doctor --json
```

## Panel

Panel w pasku menu pokazuje stan daemona, brokera głosu, kolejki i aktualnej
wypowiedzi; daje pauzę, wznowienie, pominięcie i bezpieczny restart. Panel
niczego nie wskrzesza — gdy `dand` leży, panel pokazuje „offline" i czeka.
Start: `scripts/dan-panel`. Szczegóły: `docs/PANEL.md`.

## Trzy pierwsze komendy

```bash
dan config explain
dan speak --as dan "Cześć, żyję i mówię po polsku."
dan queue list --json
```

## Dokumentacja operatora

- `docs/CO-JEST-GDZIE.md` — co gdzie leży i kto jest właścicielem;
- `docs/GLOS-I-KOLEJKA.md` — głos, kolejka, statusy i przykłady CLI;
- `docs/PANEL.md` — stany i przyciski panelu;
- `docs/RADIO-DAN.md` — status Radia (Wydanie 2);
- `docs/PRZENOSZENIE.md` — przenosiny na inny komputer, Git vs prywatne;
- `docs/ODZYSKIWANIE.md` — diagnostyka i rollback.

Runbooki smoke (dla deweloperów):
`docs/runbooks/TEXT_RUNTIME_SMOKE.md`, `docs/runbooks/PROVIDER_SMOKE.md`,
`docs/runbooks/TOOLS_AND_APPROVALS.md`, `docs/runbooks/MEMORY_API.md`.
