# Co jest gdzie

Jedna tabela własności. Zasada: jedna wartość — jeden właściciel; wszystko,
co dotyczy audio, hotkeya i kolejki głosu, należy do `dand`
(`docs/adr/001-dand-single-owner.md`).

| Element | Właściciel | Ścieżka |
|---|---|---|
| Daemon `dand` (audio, hotkey, kolejka, mózg) | launchd (`KeepAlive`) | `~/.dan/bin/dand` (wrapper na `python -m dan.cli daemon run`) |
| Baza produktu (rozmowa, pamięć, kolejka głosu, eventy) | `dand` (jedyny writer) | `~/.dan/dan.db` |
| Konfiguracja runtime | operator (edycja), `dand` (odczyt) | `~/.dan/config.toml` (wzór: `config/dan.example.toml` w repo) |
| Logi daemona | `dand` (rotacja własna) | `~/.dan/logs/` |
| Plist launchd | instalator (`scripts/install-launchd.sh`) | `~/Library/LaunchAgents/com.dan.dand.plist` (wzór: `launchd/com.dan.dand.plist.example`) |
| Panel (pasek menu) | operator; tylko klient HTTP daemona | start: `scripts/dan-panel`; kod: `dan/panel/` |
| CLI `dan` | operator | `~/.dan/bin/dan` (wrapper na `python -m dan.cli`) |
| Venv produktu | instalator (`scripts/install.sh`) | `~/.dan/venv/` |
| Kanon persony DAN | repo (wersjonowany) | `config/persona/DAN.md` |
| Assety głosu (persony, wymowa, style) | repo (wersjonowane) | `config/voice/` |
| Katalog runtime (pid, locki, np. `hotkey.lock`) | `dand` | `~/.dan/runtime/` |
| Backupy instalatora i manifest | instalator | `~/.dan/backups/`, `~/.dan/install-manifest.json` |
| Journal cutovera/rollbacku | `scripts/dan-cutover` / `scripts/dan-rollback` | `~/.dan/migration/` |

Czego tu **nie ma** (celowo): osobnego brokera głosu poza `dand`, plików
requestów w katalogach tymczasowych, drugiego odtwarzacza audio, feedera
czytającego pliki playlist. Stare tory zostały zamknięte w Wydaniu 1.
