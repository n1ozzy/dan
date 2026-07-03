# Setup Cleanup (screen-control porządnie + drobnica) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jeden kanoniczny skill `screen-control`, który NAPRAWDĘ czyta całą treść okien agentów (transkrypty z logów → Terminal history → OCR+scroll), pilnuje partnerów bez zasypiania — plus drobnica: port launch.json, PTT Claude Code, tts_diag_out.

**Architecture:** Hierarchia źródeł odczytu: (1) transkrypty JSONL z dysku (Claude/Codex — pełna prawda, zero OCR), (2) `history` tabów Terminal.app przez osascript (cały scrollback CLI bez OCR), (3) OCR okna binarką Apple Vision `wos_ocr` + scroll PageUp ze sklejaniem tekstu (desktop appki bez logów). Nowe skrypty: `read-agent.sh` (router hierarchii), `ocr-window.py` (OCR + stitch), `wait-for-agent.sh` (czekanie z timeoutem). Stare skille-klony: backup tar.gz → kasacja.

**Tech Stack:** bash, python3 (stdlib only), osascript/System Events, `screencapture`, binarka `wos_ocr` (Mach-O arm64, Apple Vision, języki pl/en/es), jq.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-03-setup-cleanup-design.md` — wykonujemy 1:1.
- **Zero zależności do instalowania:** python3 stdlib only (brak Quartz/PIL w systemowym python3 — POTWIERDZONE), OCR wyłącznie przez gotową binarkę `wos_ocr`.
- **`~/.claude` NIE jest repo gitowym** — odwracalność przez backup tar.gz, nie commity. Commity gitowe dotyczą wyłącznie repo jarvis (launch.json, .gitignore, spec/plan).
- **NIE odpalać pełnego pytest/smoke'ów jarvisa** — ten plan nie dotyka kodu Jarvisa.
- **Żadnych multi-agent fan-outów**; dozwolony JEDEN subagent `claude-code-guide` w Task 8 (diagnoza PTT).
- **Ścieżka z danymi wos-bota zawiera spację:** `/Users/n1_ozzy/Desktop/dana rzeczy /wos-bot-runs-codex/` — zawsze cytować.
- **Zakaz klikania w composer/input** partnera przy czytaniu; scroll przywracamy na dół po odczycie (key code 119 End).

**Fakty zebrane (nie sprawdzaj ponownie):**
- Skrypty `coxed-claude-hermes/scripts/*` = w 100% identyczne ze `screen-control/scripts/*` (5/5 plików `diff -q` czysto).
- Unikalne w `agent-screen-chat`: `peek_agent.sh` + nagłówek-komentarz i TRAP-notka w `agents.conf`; `send_agent.sh` RÓŻNI SIĘ od kanonu (do porównania w Task 2).
- `~/.codex/sessions/` istnieje (struktura per rok, pliki `*.jsonl`); `~/.claude/projects/<slug>/*.jsonl` = transkrypty Claude.
- `wos_ocr`: `/Users/n1_ozzy/Desktop/dana rzeczy /wos-bot-runs-codex/bin/wos_ocr` (Mach-O 64-bit arm64, działa: czyta PNG ze stdin `-`, `--lang pl-PL,en-US`, zwraca JSON `[{text,confidence,x,y,w,h,cx,cy}]`, origin top-left).
- `~/.claude/settings.json` ma DUBLET: `voice: {enabled: true, mode: "hold"}` ORAZ `voiceEnabled: true`; brak `~/.claude/keybindings.json`.
- Aktualny HEAD repo jarvis: `b1236e4` (spec zacommitowany).

---

### Task 1: Drobnica repo jarvis — launch.json port + tts_diag_out

**Files:**
- Modify: `/Users/n1_ozzy/Documents/dev/jarvis/.claude/launch.json`
- Delete: `/Users/n1_ozzy/Documents/dev/jarvis/tts_diag_out/` (pusty katalog)
- Maybe modify: `/Users/n1_ozzy/Documents/dev/jarvis/.gitignore`

**Interfaces:**
- Consumes: nic.
- Produces: `cockpit-static` na porcie 41801 (nazwa konfiguracji bez zmian).

- [ ] **Step 1: Zmień port w launch.json**

W `.claude/launch.json` podmień obie wartości `41800` → `41801`:

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "cockpit-static",
      "runtimeExecutable": "python3",
      "runtimeArgs": ["-m", "http.server", "41801", "--directory", "jarvis/panel/assets"],
      "port": 41801
    }
  ]
}
```

- [ ] **Step 2: Sprawdź, czy kod tworzy tts_diag_out**

Run: `grep -rn "tts_diag_out" /Users/n1_ozzy/Documents/dev/jarvis/jarvis /Users/n1_ozzy/Documents/dev/jarvis/tests /Users/n1_ozzy/Documents/dev/jarvis/scripts /Users/n1_ozzy/Documents/dev/jarvis/docs 2>/dev/null`
Expected: brak trafień → tylko `rmdir`. Jeśli SĄ trafienia → dodatkowo dopisz linię `tts_diag_out/` na końcu `.gitignore`.

- [ ] **Step 3: Usuń katalog**

Run: `rmdir /Users/n1_ozzy/Documents/dev/jarvis/tts_diag_out && ls /Users/n1_ozzy/Documents/dev/jarvis | grep -c tts_diag_out`
Expected: `0` (katalog zniknął; `rmdir` bezpieczny — działa tylko na pustym).

- [ ] **Step 4: Weryfikacja portu przy żywym daemonie**

Run: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:41800/health 2>/dev/null; echo; python3 -m http.server 41801 --directory /Users/n1_ozzy/Documents/dev/jarvis/jarvis/panel/assets & SPID=$!; sleep 1; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:41801/; kill $SPID`
Expected: druga linia `200` (serwer statyczny wstaje na 41801 niezależnie od daemona).

- [ ] **Step 5: Commit**

```bash
cd /Users/n1_ozzy/Documents/dev/jarvis && git add .claude/launch.json .gitignore 2>/dev/null; git add -u && git commit -m "chore: cockpit-static na 41801 (kolizja z portem API jarvisd) + kasacja tts_diag_out"
```

---

### Task 2: Merge unikalnych kawałków starych skilli do screen-control

**Files:**
- Create: `~/.claude/skills/screen-control/scripts/peek_agent.sh` (przeniesiony z agent-screen-chat)
- Modify: `~/.claude/skills/screen-control/scripts/agents.conf` (komentarz + TRAP + kolumna 6 `transcript`)
- Read-only: `~/.claude/skills/agent-screen-chat/scripts/send_agent.sh` vs kanon (decyzja diffem)

**Interfaces:**
- Consumes: format `agents.conf` 5-kolumnowy (`name|kind|app|title_match|speaker`).
- Produces: `agents.conf` 6-kolumnowy — nowa kolumna `transcript` ∈ {`claude`,`codex`,`-`}; stare skrypty czytające $1..$5 przez `awk -F'|'` działają bez zmian. `peek_agent.sh <agent>` → ścieżka PNG okna.

- [ ] **Step 1: Przenieś peek_agent.sh**

Run: `cp ~/.claude/skills/agent-screen-chat/scripts/peek_agent.sh ~/.claude/skills/screen-control/scripts/peek_agent.sh && chmod +x ~/.claude/skills/screen-control/scripts/peek_agent.sh`
Expected: plik istnieje, wykonywalny. (Skrypt sam resolvuje `CONF` względem własnego katalogu — po przeniesieniu użyje kanonicznego `agents.conf`.)

- [ ] **Step 2: Porównaj send_agent.sh i zdecyduj**

Run: `diff ~/.claude/skills/agent-screen-chat/scripts/send_agent.sh ~/.claude/skills/screen-control/scripts/send_agent.sh`
Decyzja: kanon (screen-control) zostaje. Jeśli diff pokaże w starym coś, czego kanon NIE ma (np. obsługę polskich znaków przez schowek, retry, dodatkowy agent-case) — przenieś TEN fragment do kanonu; w przeciwnym razie nic nie rób. Zapisz w notatce Task 7 (SKILL.md changelog) jedną linię: co przeniesiono albo "nic".

- [ ] **Step 3: Sprawdź użycie kolumny 5 (speaker) w skryptach**

Run: `grep -n '\$5\|speaker' ~/.claude/skills/screen-control/scripts/*.sh`
Expected: ustalisz, czy $5 jest używane jako podpis wiadomości czy tylko opis. Jeśli jako PODPIS — w Step 4 w kolumnie 5 wpisz podpisy (`Klaudiusz`/`Codex`/`Hermes`), nie opisy. Jeśli nieużywane/opis — zostaw opisy jak niżej.

- [ ] **Step 4: Nowy agents.conf (komentarz + TRAP ze starego + kolumna 6)**

Zapisz do `~/.claude/skills/screen-control/scripts/agents.conf` (całość; kolumnę 5 skoryguj wg Step 3):

```
# Rejestr agentów screen-control — KTO jest GDZIE. Jedna linia = jeden agent:
#   name | kind | app | title_match | speaker | transcript
#   kind        = app      -> desktop app z własnym oknem (aktywacja procesu)
#               = terminal -> działa w tabie Terminal.app (namierzanie po tytule okna)
#   app         = nazwa procesu macOS dla System Events
#   title_match = substring TYTUŁU OKNA (kind=terminal; puste dla app)
#   speaker     = etykieta/podpis nadawcy dla wychodzących wiadomości
#   transcript  = skąd read-agent.sh czyta PEŁNĄ treść bez OCR:
#                 claude -> ~/.claude/projects/*/*.jsonl
#                 codex  -> ~/.codex/sessions/**/*.jsonl (+ archived_sessions)
#                 -      -> brak logów (Terminal history albo OCR okna)
#
# TRAP złapany na żywo: Hermes ma ZARÓWNO Hermes.app (Electron — otwiera widok
# BlueStacks/Ranking, NIE czat!) jak i okno w Terminalu. Prawdziwy CZAT Hermesa
# = Terminal z tytułem *hermes*. Dlatego kind=terminal.
codex-desktop|app|Codex||Codex desktop app|codex
codex-cli|terminal|Terminal|codex|Codex CLI in Terminal|codex
codex-primary|app|Codex||Codex primary|codex
codex-main|app|Codex||Codex main|codex
claude-desktop|app|Claude||Claude desktop app|claude
claude-cli|terminal|Terminal|claude|Claude CLI in Terminal|claude
claude-primary|terminal|Terminal|claude|Claude primary|claude
claude-main|terminal|Terminal|claude|Claude main|claude
hermes|terminal|Terminal|hermes|Hermes terminal|-
chatgpt|app|ChatGPT||ChatGPT desktop app|-
```

- [ ] **Step 5: Test — stare skrypty nie pękają na 6. kolumnie, peek działa**

Run: `~/.claude/skills/screen-control/scripts/discover-agents.sh; echo "exit=$?"; ~/.claude/skills/screen-control/scripts/peek_agent.sh claude-cli || true`
Expected: discover kończy się `exit=0` (wykrywa agentów albo czysto raportuje brak); peek wypisuje ścieżkę PNG albo czytelny `ERR:` (gdy brak okna) — bez syntax errorów.

- [ ] **Step 6: Przejrzyj SKILL.md skilla coxed pod kątem unikalnych zasad**

Run: `cat ~/.claude/skills/coxed-claude-hermes/SKILL.md`
Wynotuj sekcje, których kanon nie ma (kandydaci: procedury supervise-with-evidence, audyt logów/JSON/screenshotów, recovery stale processes). Wklej wynotowane fragmenty do pliku tymczasowego `/tmp/screen-control/merge-notes.md` (`mkdir -p /tmp/screen-control`) — użyjesz ich w Task 7 przy przepisywaniu SKILL.md. Analogicznie rzuć okiem na `~/.claude/skills/agent-screen-chat/SKILL.md` (73 linie).

---

### Task 3: Backup i kasacja skilli-klonów

**Files:**
- Create: `~/.claude/backups/skills-consolidation-2026-07-03.tar.gz`
- Delete: `~/.claude/skills/agent-screen-chat/`, `~/.claude/skills/coxed-claude-hermes/`

**Interfaces:**
- Consumes: Task 2 zakończony (unikalne kawałki już przeniesione).
- Produces: dokładnie JEDEN skill screen-* w `~/.claude/skills/`.

- [ ] **Step 1: Backup**

Run: `tar -czf ~/.claude/backups/skills-consolidation-2026-07-03.tar.gz -C ~/.claude/skills agent-screen-chat coxed-claude-hermes && tar -tzf ~/.claude/backups/skills-consolidation-2026-07-03.tar.gz | head -5`
Expected: listing zawartości tar (min. oba katalogi). Przywrócenie w razie czego: `tar -xzf ~/.claude/backups/skills-consolidation-2026-07-03.tar.gz -C ~/.claude/skills`.

- [ ] **Step 2: Kasacja**

Run: `rm -rf ~/.claude/skills/agent-screen-chat ~/.claude/skills/coxed-claude-hermes && ls ~/.claude/skills/`
Expected: zostają `claude-loud-thinking` (celowo, zarchiwizowany `.disabled`) i `screen-control`.

---

### Task 4: OCR okna — bin/wos_ocr + ocr-window.py

**Files:**
- Create: `~/.claude/skills/screen-control/bin/wos_ocr` (kopia binarki)
- Create: `~/.claude/skills/screen-control/scripts/ocr-window.py`

**Interfaces:**
- Consumes: binarka wos_ocr (stdin PNG, `--lang`, stdout JSON obserwacji `{text,confidence,x,y,w,h,cx,cy}`).
- Produces: `ocr-window.py --app "<App>" [--stitch] [--png <plik>] [--lang pl-PL,en-US] [--max-pages 30]` → linie tekstu okna top→bottom na stdout; dowody PNG w `/tmp/screen-control/evidence/<epoch>/`; exit 0 OK / 3 brak okna / 4 błąd OCR.

- [ ] **Step 1: Skopiuj binarkę**

Run: `mkdir -p ~/.claude/skills/screen-control/bin && cp "/Users/n1_ozzy/Desktop/dana rzeczy /wos-bot-runs-codex/bin/wos_ocr" ~/.claude/skills/screen-control/bin/wos_ocr && chmod +x ~/.claude/skills/screen-control/bin/wos_ocr && file ~/.claude/skills/screen-control/bin/wos_ocr`
Expected: `Mach-O 64-bit executable arm64`.

- [ ] **Step 2: Smoke binarki na zrzucie ekranu**

Run: `screencapture -x /tmp/screen-control-ocr-test.png && ~/.claude/skills/screen-control/bin/wos_ocr - --lang pl-PL,en-US < /tmp/screen-control-ocr-test.png | head -c 300`
Expected: JSON-array z obiektami `{"text":...}`. (Jeśli Gatekeeper zablokuje: `xattr -d com.apple.quarantine ~/.claude/skills/screen-control/bin/wos_ocr` i powtórz.)

- [ ] **Step 3: Napisz ocr-window.py**

Zapisz do `~/.claude/skills/screen-control/scripts/ocr-window.py` i `chmod +x`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr-window.py — przeczytaj okno aplikacji przez Apple Vision (bin/wos_ocr).

Stdlib only. Wzorce z wos-bota (screencap_when_stable, koniec-scrolla po
niezmienionej klatce, dedup overlapu) przepisane na macOS/tekst — bez PIL:
stabilność i overlap liczone na TEKŚCIE z OCR, nie na pikselach.

Tryby:
  ocr-window.py --app "Codex"             # bieżący ekran okna -> tekst
  ocr-window.py --app "Codex" --stitch    # PageUp az do poczatku, sklej calosc
  ocr-window.py --png /tmp/shot.png       # OCR gotowego pliku PNG
Opcje: --lang pl-PL,en-US   --max-pages 30
Stdout: linie tekstu top->bottom. Dowody: /tmp/screen-control/evidence/<epoch>/
Exit: 0 OK; 3 brak okna/appki; 4 blad OCR.
Po --stitch przywraca widok na dol (key code 119 = End).
"""
import argparse, json, re, subprocess, sys, time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
OCR_BIN = SKILL_DIR / "bin" / "wos_ocr"


def osa(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True, timeout=15)


def window_rect(app: str):
    r = osa(f'tell application "System Events" to tell (first process whose '
            f'name is "{app}") to get {{position, size}} of front window')
    if r.returncode != 0:
        return None
    nums = [int(s) for s in re.findall(r"-?\d+", r.stdout)]
    return nums if len(nums) == 4 else None


def focus_app(app: str) -> None:
    osa(f'tell application "System Events" to set frontmost of '
        f'(first process whose name is "{app}") to true')
    time.sleep(0.4)


def send_key(app: str, key_code: int) -> None:
    osa(f'tell application "System Events" to tell (first process whose '
        f'name is "{app}") to key code {key_code}')


def screencap(rect, out_path: Path) -> bytes:
    x, y, w, h = rect
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}",
                    str(out_path)], check=True)
    return out_path.read_bytes()


def ocr(png: bytes, langs: str):
    p = subprocess.run([str(OCR_BIN), "-", "--lang", langs],
                       input=png, capture_output=True, timeout=20)
    if p.returncode != 0:
        sys.stderr.write("OCR error: " +
                         p.stderr.decode("utf-8", "replace")[:300] + "\n")
        sys.exit(4)
    return json.loads(p.stdout.decode("utf-8", "replace") or "[]")


def to_lines(obs):
    """Grupuje obserwacje OCR w linie po wspolrzednej pionowej cy."""
    rows = []
    for o in sorted(obs, key=lambda o: (o["cy"], o["cx"])):
        h = max(int(o.get("h", 14)), 8)
        if rows and abs(o["cy"] - rows[-1][0]) <= h * 0.6:
            rows[-1][1].append(o)
        else:
            rows.append((o["cy"], [o]))
    out = []
    for _, items in rows:
        line = " ".join(i["text"] for i in
                        sorted(items, key=lambda i: i["cx"])).strip()
        if line:
            out.append(line)
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def merge_overlap(older, newer):
    """older = klatka wyzej w historii; newer = akumulator. Sklej bez dubli:
    najdluzszy sufiks older rowny prefiksowi newer wypada raz."""
    no, nn = [norm(l) for l in older], [norm(l) for l in newer]
    for k in range(min(len(no), len(nn)), 0, -1):
        if no[-k:] == nn[:k]:
            return older + newer[k:]
    return older + newer


def capture_stable(rect, langs: str, evidence: Path, tag: str):
    """Czytaj dopiero, gdy ekran przestal sie zmieniac (2x ten sam tekst)."""
    prev = None
    for attempt in range(4):
        png = screencap(rect, evidence / f"{tag}-{attempt}.png")
        lines = to_lines(ocr(png, langs))
        if prev is not None and lines == prev:
            return lines
        prev = lines
        time.sleep(0.3)
    return prev or []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app")
    ap.add_argument("--png")
    ap.add_argument("--stitch", action="store_true")
    ap.add_argument("--lang", default="pl-PL,en-US")
    ap.add_argument("--max-pages", type=int, default=30)
    args = ap.parse_args()

    if args.png:
        print("\n".join(to_lines(ocr(Path(args.png).read_bytes(), args.lang))))
        return
    if not args.app:
        ap.error("--app albo --png wymagane")

    evidence = Path("/tmp/screen-control/evidence") / str(int(time.time()))
    evidence.mkdir(parents=True, exist_ok=True)

    focus_app(args.app)
    rect = window_rect(args.app)
    if not rect:
        sys.stderr.write(f"Brak okna procesu '{args.app}'\n")
        sys.exit(3)

    if not args.stitch:
        print("\n".join(capture_stable(rect, args.lang, evidence, "page-00")))
        sys.stderr.write(f"dowody: {evidence}\n")
        return

    acc, prev_frame = None, None
    for page in range(args.max_pages):
        lines = capture_stable(rect, args.lang, evidence, f"page-{page:02d}")
        if prev_frame is not None and lines == prev_frame:
            break                       # PageUp nic nie zmienil = poczatek tresci
        acc = lines if acc is None else merge_overlap(lines, acc)
        prev_frame = lines
        send_key(args.app, 116)         # PageUp
        time.sleep(0.35)
    send_key(args.app, 119)             # End -> wracamy na dol
    print("\n".join(acc or []))
    sys.stderr.write(f"dowody: {evidence}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Test na pliku (bez okna)**

Run: `~/.claude/skills/screen-control/scripts/ocr-window.py --png /tmp/screen-control-ocr-test.png | head -10`
Expected: sensowne linie tekstu z Twojego ekranu (top→bottom), bez tracebacka.

- [ ] **Step 5: Test na żywym oknie Terminala**

Run: `~/.claude/skills/screen-control/scripts/ocr-window.py --app "Terminal" | tail -5; echo "exit=$?"`
Expected: ostatnie linie widocznej zawartości Terminala, `exit=0`, na stderr ścieżka `dowody: /tmp/screen-control/evidence/...`.

---

### Task 5: read-agent.sh — router hierarchii źródeł

**Files:**
- Create: `~/.claude/skills/screen-control/scripts/read-agent.sh`

**Interfaces:**
- Consumes: `agents.conf` 6-kolumnowy (Task 2); `ocr-window.py` (Task 4).
- Produces: `read-agent.sh <agent> [--since|--full|--peek]` → stdout: nagłówek `=== <agent> (źródło: transcript-claude|transcript-codex|terminal-history|ocr) ===` + treść; stan pozycji per agent w `/tmp/screen-control/state/<agent>.pos`; exit 0 (jest treść/nowa treść), 1 (brak nowej treści przy --since), 3 (agent nieznany/brak źródła).

- [ ] **Step 1: Napisz read-agent.sh**

Zapisz do `~/.claude/skills/screen-control/scripts/read-agent.sh` i `chmod +x`:

```bash
#!/bin/bash
# read-agent.sh <agent> [--since|--full|--peek]
# Czyta, co NAPRAWDE powiedzial agent — hierarchia zrodel (najpewniejsze pierwsze):
#   1) transcript z logow  (kolumna 6 agents.conf: claude/codex) — pelna prawda, zero OCR
#   2) history tabu Terminal.app (kind=terminal)                 — caly scrollback, zero OCR
#   3) OCR okna (kind=app, transcript=-)                          — ocr-window.py [--stitch przy --full]
# --since (domyslnie): tylko NOWE od ostatniego odczytu (stan: /tmp/screen-control/state/<agent>.pos)
# --full: calosc; --peek: biezacy stan bez zapisu pozycji.
# Exit: 0 = wypisano tresc; 1 = brak nowej tresci (--since); 3 = agent/zrodlo nieosiagalne.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$SCRIPT_DIR/agents.conf"
STATE_DIR=/tmp/screen-control/state
mkdir -p "$STATE_DIR"

AGENT="${1:-}"; MODE="${2:---since}"
[ -z "$AGENT" ] && { echo "ERR: usage: read-agent.sh <agent> [--since|--full|--peek]"; exit 3; }

resolve_agent() {  # jak w peek_agent.sh: nazwa wprost, potem -desktop/-cli/goła baza
  local name="$1" line="" base candidate
  base="${name%-primary}"; base="${base%-main}"
  for candidate in "$name" "${base}-desktop" "${base}-cli" "$base"; do
    line=$(awk -F'|' -v n="$candidate" 'BEGIN{IGNORECASE=1} $1 ~ "^"n"$" {print; exit}' "$CONF")
    [ -n "$line" ] && { echo "$line"; return 0; }
  done
  return 1
}

LINE=$(resolve_agent "$AGENT") || { echo "ERR: unknown agent '$AGENT' (agents.conf)"; exit 3; }
KIND=$(echo "$LINE" | awk -F'|' '{print $2}')
APP=$(echo "$LINE" | awk -F'|' '{print $3}')
TITLE=$(echo "$LINE" | awk -F'|' '{print $4}')
HINT=$(echo "$LINE" | awk -F'|' '{print $6}')
POS="$STATE_DIR/${AGENT}.pos"

emit() {  # emit <zrodlo> <plik-z-trescia>
  local src="$1" body="$2"
  if [ ! -s "$body" ]; then [ "$MODE" = "--since" ] && exit 1 || { echo "(pusto)"; exit 0; }; fi
  echo "=== ${AGENT} (źródło: ${src}) ==="
  cat "$body"
}

tmp=$(mktemp /tmp/screen-control/read-XXXXXX)
trap 'rm -f "$tmp"' EXIT

case "$HINT" in
  claude)
    F=$(ls -t "$HOME"/.claude/projects/*/*.jsonl 2>/dev/null | head -1)
    [ -z "$F" ] && { echo "ERR: brak transkryptow claude"; exit 3; }
    # swiezosc: aktywna sesja = plik modyfikowany < 15 min; inaczej ostrzez na stderr
    [ -z "$(find "$F" -mmin -15 2>/dev/null)" ] && \
      echo "UWAGA: najnowszy transcript starszy niz 15 min ($F)" >&2
    TOTAL=$(wc -l < "$F" | tr -d ' ')
    LAST=0; [ "$MODE" != "--full" ] && [ -f "$POS" ] && LAST=$(cat "$POS")
    [ "$LAST" -gt "$TOTAL" ] && LAST=0   # inny/krotszy plik -> czytaj od zera
    tail -n +"$((LAST+1))" "$F" | jq -r '
      select(.type=="assistant") | .message.content[]? |
      select(.type=="text") | .text' 2>/dev/null > "$tmp"
    [ "$MODE" != "--peek" ] && echo "$TOTAL" > "$POS"
    emit "transcript-claude ($F)" "$tmp"
    ;;
  codex)
    F=$(find "$HOME/.codex/sessions" "$HOME/.codex/archived_sessions" \
        -name '*.jsonl' -mmin -240 -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1)
    [ -z "$F" ] && F=$(find "$HOME/.codex" -name '*.jsonl' -path '*session*' 2>/dev/null | head -1)
    [ -z "$F" ] && { echo "ERR: brak transkryptow codex"; exit 3; }
    TOTAL=$(wc -l < "$F" | tr -d ' ')
    LAST=0; [ "$MODE" != "--full" ] && [ -f "$POS" ] && LAST=$(cat "$POS")
    [ "$LAST" -gt "$TOTAL" ] && LAST=0
    # uniwersalny ekstraktor tekstu (schemat rollout Codexa bywa zagniezdzony)
    tail -n +"$((LAST+1))" "$F" | jq -r '.. | .text? // empty' 2>/dev/null | \
      grep -v '^[[:space:]]*$' > "$tmp"
    [ "$MODE" != "--peek" ] && echo "$TOTAL" > "$POS"
    emit "transcript-codex ($F)" "$tmp"
    ;;
  *)
    if [ "$KIND" = "terminal" ]; then
      # caly scrollback tabu Terminala — bez OCR, bez scrollowania
      osascript -e "
        tell application \"Terminal\"
          repeat with w in windows
            if (name of w as text) contains \"$TITLE\" then
              return history of selected tab of w
            end if
          end repeat
          return \"\"
        end tell" > "$tmp.hist" 2>/dev/null
      if [ ! -s "$tmp.hist" ]; then rm -f "$tmp.hist"; echo "ERR: brak okna Terminal '$TITLE'"; exit 3; fi
      BYTES=$(wc -c < "$tmp.hist" | tr -d ' ')
      LAST=0; [ "$MODE" != "--full" ] && [ -f "$POS" ] && LAST=$(cat "$POS")
      [ "$LAST" -gt "$BYTES" ] && LAST=0
      if [ "$MODE" = "--full" ]; then cp "$tmp.hist" "$tmp"; else tail -c +"$((LAST+1))" "$tmp.hist" > "$tmp"; fi
      [ "$MODE" != "--peek" ] && echo "$BYTES" > "$POS"
      rm -f "$tmp.hist"
      emit "terminal-history" "$tmp"
    else
      # desktop app bez logow -> OCR; --full = doczytaj calosc scrollem (stitch)
      STITCH=""; [ "$MODE" = "--full" ] && STITCH="--stitch"
      "$SCRIPT_DIR/ocr-window.py" --app "$APP" $STITCH > "$tmp" || exit 3
      NEWHASH=$(md5 -q "$tmp" 2>/dev/null || md5sum "$tmp" | cut -d' ' -f1)
      OLDHASH=""; [ -f "$POS" ] && OLDHASH=$(cat "$POS")
      if [ "$MODE" = "--since" ] && [ "$NEWHASH" = "$OLDHASH" ]; then exit 1; fi
      [ "$MODE" != "--peek" ] && echo "$NEWHASH" > "$POS"
      emit "ocr" "$tmp"
    fi
    ;;
esac
```

- [ ] **Step 2: Test na własnym transkrypcie (claude)**

Run: `~/.claude/skills/screen-control/scripts/read-agent.sh claude-cli --full | head -8; echo "exit=$?"`
Expected: nagłówek `=== claude-cli (źródło: transcript-claude (...))` + tekst ostatnich odpowiedzi Claude (ta sesja pisze do transkryptu — więc zobaczysz własne odpowiedzi), `exit=0`.

- [ ] **Step 3: Test --since (pozycja działa)**

Run: `~/.claude/skills/screen-control/scripts/read-agent.sh claude-cli --since >/dev/null; ~/.claude/skills/screen-control/scripts/read-agent.sh claude-cli --since; echo "exit=$?"`
Expected: drugie wywołanie bez nowej treści → `exit=1` (pozycja zapamiętana w `/tmp/screen-control/state/claude-cli.pos`).

- [ ] **Step 4: Test codex (jeśli Codex ma świeże sesje)**

Run: `~/.claude/skills/screen-control/scripts/read-agent.sh codex-cli --full 2>&1 | head -8`
Expected: transcript-codex + treść, ALBO czytelny `ERR: brak transkryptow codex` (gdy Codex dawno nie działał) — bez tracebacka. Jeśli ekstraktor `.. | .text?` wypisuje śmieci (np. same ID) — obejrzyj `head -3 <plik.jsonl>` i zawęź filtr jq do pola z treścią wiadomości, np. `select(.type=="message") | .content[]?.text? // empty`.

---

### Task 6: wait-for-agent.sh + podpięcie w coop-loop.sh

**Files:**
- Create: `~/.claude/skills/screen-control/scripts/wait-for-agent.sh`
- Modify: `~/.claude/skills/screen-control/scripts/coop-loop.sh` (dopięcie read-agent po odczycie ekranu)

**Interfaces:**
- Consumes: `read-agent.sh <agent> --since` (exit 0 = nowa treść, 1 = brak).
- Produces: `wait-for-agent.sh <agent> [--timeout 300] [--interval 10]` → czeka na NOWĄ treść; wypisuje ją i exit 0; po timeoucie exit 75 + raport. coop-loop loguje pełną nową treść partnera do swojego logu.

- [ ] **Step 1: Napisz wait-for-agent.sh**

Zapisz do `~/.claude/skills/screen-control/scripts/wait-for-agent.sh` i `chmod +x`:

```bash
#!/bin/bash
# wait-for-agent.sh <agent> [--timeout SEC] [--interval SEC]
# Czeka, az agent napisze COS NOWEGO (read-agent.sh --since). Nowa tresc -> stdout, exit 0.
# Timeout -> exit 75 + raport. NIGDY nie wisi w nieskonczonosc — zamiast udawac
# obserwacje, po timeoucie glosno raportuje (zasada: zero gluchego czekania).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT="${1:-}"; shift || true
TIMEOUT=300; INTERVAL=10
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout)  TIMEOUT="$2";  shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -z "$AGENT" ] && { echo "ERR: usage: wait-for-agent.sh <agent> [--timeout S] [--interval S]"; exit 2; }

START=$(date +%s)
while :; do
  OUT=$("$SCRIPT_DIR/read-agent.sh" "$AGENT" --since 2>/dev/null); RC=$?
  if [ $RC -eq 0 ] && [ -n "$OUT" ]; then echo "$OUT"; exit 0; fi
  NOW=$(date +%s)
  if [ $((NOW - START)) -ge "$TIMEOUT" ]; then
    echo "TIMEOUT: $AGENT bez nowej tresci przez ${TIMEOUT}s — raportuj Ozzy'emu zamiast wisiec." >&2
    exit 75
  fi
  sleep "$INTERVAL"
done
```

- [ ] **Step 2: Test timeoutu (szybki)**

Run: `~/.claude/skills/screen-control/scripts/read-agent.sh claude-cli --since >/dev/null 2>&1; ~/.claude/skills/screen-control/scripts/wait-for-agent.sh claude-cli --timeout 5 --interval 2; echo "exit=$?"`
Expected: po ~5 s `TIMEOUT: ...` na stderr i `exit=75`. (Pozycja claude-cli została przed chwilą skonsumowana, więc nic nowego nie ma.)

- [ ] **Step 3: Podepnij read-agent w coop-loop.sh**

Run: `grep -n "capture-agent-reply\|peek\|screencap" ~/.claude/skills/screen-control/scripts/coop-loop.sh | head -10`
Znajdź miejsce, gdzie pętla robi odczyt ekranu partnera (linia z `capture-agent-reply.sh`). BEZPOŚREDNIO PO tej linii wstaw blok (dostosuj nazwę zmiennej partnera — w coop-loop to prawdopodobnie `$WITH` lub `$PARTNER`, sprawdź nagłówek skryptu):

```bash
# pelna tresc nowej wypowiedzi partnera (transcript/history/ocr), nie tylko zrzut:
"$SCRIPT_DIR/read-agent.sh" "$WITH" --since >> "$COOP_LOG" 2>/dev/null || true
```

Jeśli coop-loop nie definiuje `$COOP_LOG`, użyj ścieżki logu, którą skrypt już ma (SKILL.md: `/tmp/screen-control/coop.log.jsonl`); jeśli używa innej zmiennej — podstaw ją.

- [ ] **Step 4: Test coop-loop na sucho (1 minuta)**

Run: `~/.claude/skills/screen-control/scripts/coop-loop.sh --with claude --me codex --minutes 1 --interval 20; tail -5 /tmp/screen-control/coop.log.jsonl 2>/dev/null`
Expected: pętla kończy się po ~1 min bez błędów składni; log zawiera wpisy cyklu (i ewentualnie sekcję `=== claude ... ===`, jeśli była nowa treść).

---

### Task 7: SKILL.md — hierarchia odczytu + twarde zasady

**Files:**
- Modify: `~/.claude/skills/screen-control/SKILL.md`

**Interfaces:**
- Consumes: merge-notes z Task 2 Step 6 (`/tmp/screen-control/merge-notes.md`).
- Produces: SKILL.md dokumentujący nowe narzędzia i zasady.

- [ ] **Step 1: Zaktualizuj tabelę narzędzi**

W sekcji `## Narzędzia` DOPISZ do tabeli wiersze (przed wierszem o coop-loop):

```markdown
| **Przeczytaj CO NAPRAWDĘ napisał (transcript/history/OCR)** | `scripts/read-agent.sh <agent> [--since\|--full\|--peek]` |
| **Czekaj na nową treść (z timeoutem, bez wiszenia)** | `scripts/wait-for-agent.sh <agent> --timeout 300 --interval 10` |
| Podejrzyj okno agenta (crop PNG → Read) | `scripts/peek_agent.sh <agent>` |
| OCR okna / doczytanie całości scrollem | `scripts/ocr-window.py --app <App> [--stitch]` |
```

- [ ] **Step 2: Dodaj sekcję hierarchii odczytu (po sekcji "NIE ZASYPIAJ")**

```markdown
## CZYTANIE = WIEDZA, NIE ZGADYWANIE (hierarchia źródeł)
Zanim zareagujesz, przeczytaj CAŁĄ nową treść partnera — `read-agent.sh` wybiera
najpewniejsze źródło automatycznie:
1. **Transkrypty z logów** (Claude: `~/.claude/projects/*/*.jsonl`, Codex: `~/.codex/sessions/`)
   — pełna prawda z dysku, zero OCR, zero scrollowania. Kolumna 6 w `agents.conf`.
2. **History tabu Terminal.app** (osascript `history of tab`) — cały scrollback CLI bez OCR.
3. **OCR okna** (`ocr-window.py`, Apple Vision przez `bin/wos_ocr`) — desktop appki bez logów;
   `--stitch` scrolluje PageUp aż do początku treści i skleja całość (dedup overlapu po tekście),
   po czym wraca na dół (End). Dowody (PNG klatek) w `/tmp/screen-control/evidence/<ts>/`.
Zrzut ekranu (`peek`/`capture`) służy do oceny STANU UI (spinner? idle? popup?), NIE do
czytania długiej treści. Ekran pokazuje ostatni ekran — treść czytaj przez read-agent.

## Twarde zasady interakcji (z krwi i frustracji Ozzy'ego)
- Zanim scrollujesz/klikasz: ustal, co ma focus. Scroll nad środkiem obszaru TREŚCI okna,
  NIGDY przy focusie w composerze. ZAKAZ klikania w input "żeby scrollować".
- Scroll myszą nie działa → PageUp/PageDown (key code 116/121), End (119) wraca na dół.
- Czekasz na partnera → `wait-for-agent.sh` z timeoutem i raport po jego upływie.
  Zakaz głuchego wiszenia i zakaz deklaracji "przeczytałem" bez dowodu
  (transcript/history/stitch — coś, co można pokazać).
- Po --stitch przywróć widok na dół okna (robi to ocr-window.py; sprawdź, nie zostawiaj
  partnera odscrollowanego w kosmos).
```

- [ ] **Step 3: Wciągnij merge-notes**

Przejrzyj `/tmp/screen-control/merge-notes.md` (z Task 2 Step 6). Unikalne procedury z coxed/agent-screen-chat (supervise-with-evidence, recovery stale processes itp.) wstaw jako podsekcje w odpowiednich miejscach SKILL.md — bez dublowania tego, co już jest. Dopisz na końcu sekcji "Pochodzenie": `2026-07-03: dokasowane klony agent-screen-chat + coxed-claude-hermes (backup: ~/.claude/backups/skills-consolidation-2026-07-03.tar.gz); doszły read-agent/wait-for-agent/ocr-window (klocki wos-bota, OCR=Apple Vision).`

- [ ] **Step 4: Smoke listy skilli**

Run: `ls ~/.claude/skills/ && head -12 ~/.claude/skills/screen-control/SKILL.md`
Expected: jeden skill screen-* na liście; frontmatter SKILL.md nienaruszony (name/description bez zmian — triggery działają).

---

### Task 8: PTT/dyktowanie Claude Code — diagnoza i naprawa

**Files:**
- Modify: `~/.claude/settings.json` (po backupie)
- Create: `~/.claude/settings.json.bak-przed-ptt-fix` (backup)

**Interfaces:**
- Consumes: wiedza z subagenta `claude-code-guide` (JEDEN agent, dozwolony).
- Produces: settings.json bez martwego klucza; PTT działa ALBO raport z przyczyną.

- [ ] **Step 1: Backup**

Run: `cp ~/.claude/settings.json ~/.claude/settings.json.bak-przed-ptt-fix && ls -la ~/.claude/settings.json.bak-przed-ptt-fix`
Expected: backup istnieje.

- [ ] **Step 2: Zapytaj claude-code-guide (jeden subagent)**

Dispatch agenta `claude-code-guide` z promptem:
"Claude Code desktop na macOS: jak działa dyktowanie głosowe / push-to-talk? (1) Które klucze settings.json są kanoniczne: `voice: {enabled: bool, mode: 'hold'}` czy `voiceEnabled: bool` — czy któryś jest legacy/martwy? (2) Czego wymaga tryb `hold` — jaki klawisz się trzyma, czy trzeba go skonfigurować (keybindings.json?), jakie uprawnienia systemowe (mikrofon)? (3) Najczęstsze przyczyny 'PTT nie działa' i jak je zdiagnozować."

- [ ] **Step 3: Zastosuj wynik do settings.json**

Na bazie odpowiedzi: usuń martwy klucz (przewidywanie ze speca: legacy `voiceEnabled` wypada, zostaje `voice: {...}`) i uzupełnij brakującą konfigurację trybu hold, jeśli agent wskaże (np. wpis w keybindings). Edytuj `~/.claude/settings.json` zachowując WSZYSTKIE pozostałe klucze. Jeśli agent wskaże odwrotnie (kanoniczny `voiceEnabled`) — usuń blok `voice` i zostaw `voiceEnabled`.

- [ ] **Step 4: Uprawnienia mikrofonu — instrukcja dla Ozzy'ego**

Wypisz Ozzy'emu dokładnie: Ustawienia systemowe → Prywatność i ochrona → Mikrofon → sprawdź, czy aplikacja Claude jest na liście i WŁĄCZONA. (Nie da się tego pewnie sprawdzić skryptem bez pełnego dostępu do TCC.db — nie kombinuj z sqlite na TCC.)

- [ ] **Step 5: Test ręczny z Ozzym (gate)**

Poproś Ozzy'ego: zrestartuj aplikację Claude Code, przytrzymaj klawisz dyktowania (wg wyniku Step 2) i powiedz zdanie. Działa → task zamknięty. Nie działa → zbierz objaw (brak ikonki? nagrywa ale nie transkrybuje?) i wypisz raport: przyczyna + czy to konfiguracja, uprawnienie, czy bug aplikacji (wtedy obejście: `/config` → voice albo zgłoszenie).

---

### Task 9: Finalny smoke wg speca + zapis statusu

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-setup-cleanup-design.md` (dopisek statusu)

**Interfaces:**
- Consumes: wszystkie poprzednie taski.
- Produces: spec z adnotacją DONE + commit w repo jarvis.

- [ ] **Step 1: Przejdź checklistę weryfikacji ze speca**

Run (kolejno, wyniki wklej do raportu):
```bash
ls ~/.claude/skills/                                                   # 1 skill screen-*
~/.claude/skills/screen-control/scripts/discover-agents.sh; echo $?    # exit 0
~/.claude/skills/screen-control/scripts/read-agent.sh claude-cli --full | head -3   # transcript działa
~/.claude/skills/screen-control/scripts/ocr-window.py --png /tmp/screen-control-ocr-test.png | head -3  # OCR działa
grep -c 41801 /Users/n1_ozzy/Documents/dev/jarvis/.claude/launch.json  # 2
ls /Users/n1_ozzy/Documents/dev/jarvis | grep -c tts_diag_out          # 0
python3 -c "import json;d=json.load(open('/Users/n1_ozzy/.claude/settings.json'));print('voiceEnabled' in d, 'voice' in d)"  # zgodnie z Task 8
```
Test "całości" (spec 2a): otwórz okno Terminala z długim outputem (`seq 1 200` w nowym tabie), potem `read-agent.sh hermes --full` na tym oknie (tymczasowo zmień title_match) ALBO prościej: `ocr-window.py --app "Terminal" --stitch | wc -l` → wynik zawiera >1 ekran linii (początek I koniec).

- [ ] **Step 2: Dopisz status do speca i commituj**

Na końcu speca dopisz sekcję:

```markdown
## Status wykonania (2026-07-03)
Zrealizowane wg planu docs/superpowers/plans/2026-07-03-setup-cleanup.md.
Wyniki weryfikacji: [wklej skrótowo wyniki Step 1]. PTT: [działa / raport-przyczyna].
Backup starych skilli: ~/.claude/backups/skills-consolidation-2026-07-03.tar.gz.
```

```bash
cd /Users/n1_ozzy/Documents/dev/jarvis && git add docs/superpowers/specs/2026-07-03-setup-cleanup-design.md && git commit -m "docs: status wykonania sprzątania setupu (screen-control skonsolidowany)"
```

- [ ] **Step 3: Raport końcowy dla Ozzy'ego**

Po ludzku, bez żargonu: co teraz ma (jeden skill, czytanie całości z logów/history/OCR, czekanie z timeoutem), co się zmieniło w plikach, gdzie backup, status PTT (i JAKI klawisz trzyma — wprost!), co zostało odłożone (Tier 1, effort, retencja — czekają na osobne "dawaj").
