# Głos na żywo — instrukcja operatorska

To jest krótka instrukcja obsługi, nie książka presetów.

## Kto może mówić

Tylko:

- `--as dan`;
- `--as danusia`.

Nazwa hosta albo sesji pozostaje w `--source` i `--session`. Nie jest personą.

## Jak wysłać wypowiedź

```bash
printf '%s' 'Jedna kompletna myśl do powiedzenia.' |
  dan speak --json --as dan --session gadanie --source codex --stdin
```

Danusia:

```bash
printf '%s' 'Jedna kompletna odpowiedź.' |
  dan speak --json --as danusia --session danusia-live --source codex --stdin
```

Nie kopiuj flag z dawnych instrukcji. Przed użyciem dodatkowej kontrolki:

```bash
dan speak --help
```

To potwierdza jedynie, że flaga istnieje. Nie potwierdza, że brzmi naturalnie.

## Jak pisać

- Pisz tekst przeznaczony do słuchania, nie log ani markdown.
- Jedno zgłoszenie ma zawierać jedną pełną myśl.
- Zapisuj cyfry, skróty, ścieżki i anglicyzmy w formie wygodnej do wymówienia.
- Używaj interpunkcji jako hipotezy wykonania, nie jako pewnego presetu.
- Nie wpisuj didaskaliów, których silnik może przeczytać.
- Nie ustawiaj tempa, pauzy ani profilu z tabelki. Kontekst ma pierwszeństwo.
- Jeśli aktywny limit techniczny wymusza podział, tnij na granicy semantycznej.

## Jak potwierdzić odtworzenie

```bash
dan queue list --json
dan doctor --json
```

`queued` nie oznacza, że Ozzy coś usłyszał. Dopiero wpis tej wypowiedzi z
`status=done` i `playback_confirmed=true` jest technicznym potwierdzeniem.

## Czego nie uruchamiać

- drugiego playera;
- dawnego brokera lub feedera;
- skryptu kierującego surowe identyfikatory głosu do `--as`;
- instalatora podmieniającego `dan/voice/prosody`;
- zapamiętanej receptury z archiwalnego handoffu.

Pełna granica i aktywne fakty kodu:
[`MUST-READ-GLOS-PROZODIA.md`](../MUST-READ-GLOS-PROZODIA.md).
