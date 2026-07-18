# ADR 001: `dand` jest jedynym właścicielem audio, hotkeya i kolejki głosu

Status: przyjęty (Wydanie 1, konsolidacja produktu)

## Kontekst

Stary układ miał wielu właścicieli tej samej wartości: osobny broker głosu,
feeder-bash pilnujący pliku playlisty, panel wołający bezpośrednio `launchctl`
i `pkill`, hotkey w osobnym procesie i skrypty odtwarzające WAV-y wprost.
Skutki: dwa odtwarzacze naraz, requesty ginące między procesami, „naprawy"
przez zabijanie procesów na ślepo i stan, którego nikt nie umiał odtworzyć.

## Decyzja

Jedna wartość — jeden właściciel. Właścicielem **audio, globalnego hotkeya
PTT i trwałej kolejki głosu** jest wyłącznie daemon `dand`:

- broker głosu działa w procesie `dand`; synteza i playback nie istnieją
  poza nim, a broker bierze dokładnie jeden element do playbacku naraz;
- każdy producent mowy (CLI, panel, hooki, skille, inne agenty) przechodzi
  przez API/CLI (`dan speak`), nigdy bezpośrednio do silnika czy głośnika;
- kolejka jest trwała w SQLite w `~/.dan/dan.db`, którego jedynym writerem
  jest `dand`.

### Restart: exit 86 + launchd `KeepAlive`

Bezpieczny restart (`POST /runtime/restart`) domyka intake, drenuje głos,
zatrzymuje dzieci i **kończy proces kodem `RESTART_EXIT_CODE = 86`**
(`dan/daemon/restart.py`). Wskrzeszenie to robota platformy: plist
`com.dan.dand` ma `KeepAlive = true`, więc launchd wstawia daemona z
powrotem. Nikt — ani daemon, ani panel — nie woła `launchctl` czy `pkill`.
Kod 86 odróżnia w logach „poproszono o restart" od crashu i czystego stopu.

### Hotkey: wyłączność przez `flock` na `hotkey.lock`

Globalny monitor PTT bierze wyłączny, nieblokujący `flock` na
`~/.dan/runtime/hotkey.lock` (`dan/input/macos_event_tap.py`). Lock jest na
open-file-description, więc nawet drugi monitor w tym samym procesie go nie
obejdzie. Brak locka lub brak uprawnień Accessibility to widoczny błąd,
nie cicha degradacja.

### Porty: `ForeignPortOwnerError`

Supervisor dzieci (`dan/daemon/supervisor.py`) przed startem usługi sprawdza
właściciela portu. Port zajęty przez proces spoza rodziny `dand` podnosi
`ForeignPortOwnerError` — daemon **odmawia** startu usługi zamiast zabijać
cudzy proces albo po cichu zmieniać port.

## Konsekwencje

- Panel jest czystym klientem HTTP: pauza/wznów/pomiń/restart to wywołania
  API; gdy daemon leży, panel pokazuje „offline" i nie wskrzesza niczego.
- Nie istnieje żaden legalny drugi tor mowy (bezpośredni player, osobny
  broker, feeder plikowy). Testy kontraktowe pilnują pojedynczego
  odtwarzacza, pojedynczej instancji daemona i braku `launchctl`/`pkill`
  w kodzie runtime.
- Awaria daemona zatrzymuje głos w całości — to celowe: lepszy jeden
  widoczny brak właściciela niż dwóch właścicieli naraz.
