# Ochrona przed utratą roboty — design

Data: 2026-07-21
Status: projekt zatwierdzony w brainstormingu z Ozzym

## Problem

Raport /insights (35 sesji, 2026-06-27 → 2026-07-21) pokazuje, że największe realne
straty w sesjach to nadpisane albo zgubione pliki — sztandarowy przykład: `tts.py`
nadpisany 11-bajtowym placeholderem, fixy bezpowrotnie w piach. Brakowało dwóch rzeczy:
nawyku snapshotu PRZED edycją krytycznych plików i twardej procedury odzyskiwania
PRZED pisaniem czegokolwiek od zera.

## Rozwiązanie

### 1. Snapshot przed edycją krytycznych plików

Przed pierwszą w danej sesji edycją pliku z krytycznych ścieżek:

    git stash store -m "snapshot: <co i po co>" $(git stash create)

- `git stash create` tworzy commit-obiekt ze stanu working tree **bez modyfikowania
  czegokolwiek** (working tree, index i branch zostają nietknięte),
- `git stash store` wpina ten obiekt na listę stash,
- odzysk: `git stash list` → `git stash apply stash@{n}`,
- zero commitów na branchu, zero dodatkowych plików na dysku.

Uwagi techniczne:
- przy czystym working tree `git stash create` zwraca pusty string — wtedy snapshot
  jest zbędny (nie ma niezapisanych zmian do stracenia); nie wołać `store` z pustym
  argumentem, bo się wywali,
- `git stash create` nie obejmuje plików untracked — świeżo tworzone pliki nie
  potrzebują snapshotu, bo nie ma tam nic do stracenia.

### 2. Krytyczne ścieżki (snapshot obowiązkowy)

- `dan_core/`
- `config/persona/`
- `config/voice/`
- `dan/brain/`
- `voice_broker*`
- `tools/jarvis/`
- wszystko, co dotyka żywego demona `dand`

Reszta plików — bez ceremonii. W razie wątpliwości, czy plik jest krytyczny:
snapshot (domyślnie fail-safe).

### 3. Procedura odzyskiwania

Gdy jakikolwiek fix albo plik „zniknął", sprawdzać w kolejności:

1. `git reflog`
2. `git stash list`
3. `~/.claude/file-history/`

Dopiero po wyczerpaniu wszystkich trzech — pisanie od zera. Nigdy odwrotnie.

## Wdrożenie

Nowa krótka sekcja „Ochrona roboty" w globalnym `~/.claude/CLAUDE.md` (zasada dotyczy
obu repo — dan i DAN — oraz każdej sesji, więc nie wchodzi do projektowego CLAUDE.md).
Trzy–cztery linijki, bez elaboratu.

## Poza zakresem (świadomie)

- Żadnych hooków — twardy zakaz w CLAUDE.md, chyba że Ozzy wprost poprosi słowami.
- Żadnych auto-commitów — „commit tylko na komendę" obowiązuje bez zmian.
- Żadnych worktree.
