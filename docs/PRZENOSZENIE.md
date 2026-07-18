# Przenoszenie DAN-a na inny komputer

## Co jest w Git (jedzie z repo)

- cały kod (`dan/`, `scripts/`, `launchd/`, `integrations/`);
- kanon persony: `config/persona/DAN.md`;
- konfiguracja głosu: `config/voice/` — `personas.toml`, `pronunciations.toml`,
  `gains.json`, pipeline'y i 20 wersjonowanych custom stylów Supertonica
  z deterministycznymi przepisami i SHA-256;
- przykład konfiguracji: `config/dan.example.toml`;
- dokumentacja i testy.

## Co jest lokalne/prywatne (NIGDY nie wysyłać)

- `~/.dan/dan.db` — rozmowa, pamięć, kolejka, eventy; to jest prywatna
  historia właściciela;
- `~/.dan/config.toml` — lokalna konfiguracja (porty, ścieżki, urządzenia);
- `~/.dan/logs/`, `~/.dan/backups/`, `~/.dan/migration/` — logi i backupy;
- bazowe modele TTS/STT — instalator dociąga je w pinowanej rewizji, nie
  siedzą w repo;
- referencyjny WAV Lily i wygenerowane WAV-y Żanety — **local-only**: brak
  wystarczającej proweniencji do redystrybucji; w repo jest tylko oczekiwany
  SHA-256 jako metadana;
- klucze API i sekrety (audyt: `scripts/dan-release-audit`).

## Licencje assetów

Custom style głosu (`config/voice/custom_styles/`) są derywatami modelu
Supertonic (rewizja `724fb5ab…`) i jadą z licencją **OpenRAIL-M**
(`LICENSE-OpenRAIL-M.txt` + `NOTICE.txt` obok plików). Pipeline offline
Chatterbox V3 weryfikuje pinowany commit źródła i snapshot modelu, z
wyłączonym network-fallbackiem. Szczegóły: `docs/migration/VOICE-DECISIONS.md`.

## Czysta instalacja (np. nowy Mac z M5)

```bash
git clone <repo> DAN && cd DAN
bash scripts/install.sh --no-launchd
dan doctor --json
```

1. `install.sh` tworzy `~/.dan/venv`, wrappery `~/.dan/bin/{dan,dand}`,
   przechodzi preflight (każdy asset local-only jest jawnie wyjaśniony,
   nie ukryty za fallbackiem) i zapisuje manifest instalacji;
2. skopiuj `config/dan.example.toml` → `~/.dan/config.toml`, przejrzyj;
3. świadomy autostart: `bash scripts/install-launchd.sh --yes`;
4. weryfikacja: `dan doctor --json` musi być czyste na pustym `$HOME` —
   produkt nie może zależeć od cache'ów ani repozytoriów starego komputera.

Odinstalowanie jest manifest-scoped: `scripts/uninstall.sh` (zostawia
`dan.db` i backupy).
