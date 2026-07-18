# Głos i kolejka

## Broker

Broker głosu działa **wewnątrz `dand`** i jest jedynym właścicielem syntezy
i odtwarzania. Każdy producent (CLI, panel, hook, skill, inne agenty) mówi
przez API/CLI — nikt nie odtwarza WAV-ów bezpośrednio i nikt nie uruchamia
własnego brokera. Silnikiem live jest Supertonic; brak silnika, głosu lub
assetu kończy request jawnym błędem — nie ma cichego fallbacku.

## Statusy kolejki

Kolejka jest trwała w SQLite (`~/.dan/dan.db`, tabela `voice_queue`). Request
przechodzi przez:

| Status | Znaczenie |
|---|---|
| `queued` | przyjęty, snapshot renderu kompletny, czeka na syntezę |
| `synthesizing` | broker generuje audio |
| `speaking` | audio faktycznie leci z głośnika |
| `done` | odtworzone i potwierdzone (`playback_confirmed`) |
| `cancelled` | anulowany (pojedynczo lub flushem sesji) |
| `failed` | jawny błąd syntezy/odtwarzania, z opisem w polu `error` |

Broker bierze dokładnie jeden element do playbacku naraz.

## Snapshot renderu

Intencja (tekst + persona) i rekord kolejki to dwa kontrakty. Zanim request
dostanie `queued`, `dand` rozwiązuje **niezmienny snapshot renderu**: silnik
i jego wersję, głos/styl, tempo, mastering, DSP, wymowę, gain oraz SHA-256
użytych assetów. Niekompletny snapshot = błąd przed zapisem, nie częściowy
rekord. Dzięki temu wiadomo dokładnie, czym została wyrenderowana każda
wypowiedź, nawet po zmianie konfiguracji.

## Stary feeder vs zachowanie w Wydaniu 1

Stary tor: feeder-bash pilnował rosnącego pliku playlisty i każda dopisana
linia natychmiast zaczynała grać; DSP jechało przemyconym polem `profile`.
W Wydaniu 1 tego toru nie ma: playlista jest importowana transakcyjnie jako
segmenty sesji, dopisanie czegoś do starego pliku po imporcie **niczego nie
uruchamia**, postęp siedzi w bazie (restart nie dubluje i nie gubi), a treść
live wchodzi tym samym API jako kolejny segment — nie ma drugiej kolejki.

## Render offline

Przygotowane kwestie (pipeline Chatterbox V3 dla Żanety) to jawny pipeline
**offline** — renderuje pliki poza żywą kolejką i nigdy nie jest automatycznym
silnikiem live. Wejście przez katalog głosów (`dan/voice/pipelines/`),
trasa `offline_pipeline` w katalogu person.

## Przykłady CLI (copy/paste)

```bash
# Podstawowa wypowiedź z polskimi znakami:
dan speak --as dan "Zażółć gęślą jaźń — to jest test dykcji, chłopie."

# Druga persona:
dan speak --as danusia "Dobra, moja kolej. Posłuchaj uważnie."

# JSON przez stdin (wynik też w JSON):
dan speak --as danusia --json --stdin <<< "Święta prawda, mówię to z pliku."

# Co siedzi w kolejce:
dan queue list --json --limit 10

# Anuluj jeden request (id z queue list):
dan queue cancel 42

# Wyczyść całą sesję (np. radiową):
dan queue flush --session radio

# Skąd wzięła się aktualna konfiguracja (plik, env, default):
dan config explain --json

# Przełącznik hooka głosowego:
dan voice hook off
dan voice hook on
dan voice hook status
```
