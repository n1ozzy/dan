# JAK STEROWAĆ GŁOSEM NA ŻYWO — INSTRUKCJA DLA LUDZI

Wersja prosta i ostateczna. Bez teorii. Same przepisy: chcesz efekt → robisz
dokładnie to, co napisane. Każdy przepis ma oznaczenie, czy już działa:

- **[DZIAŁA JUŻ]** — działa teraz, od ręki.
- **[PO RESTARCIE]** — kod już jest, zadziała po restarcie demona dand.
- **[JESZCZE NIE MA]** — dopiero będzie, nie próbuj, bo nie zadziała.

---

## 1. O co w ogóle chodzi (trzy zdania)

Głos DAN-a sam robi melodię zdania — ty mu tylko mówisz JAK, przez sposób
napisania tekstu. Znaki na końcu zdania (`.` `?` `!` `…`) to twoje główne
pokrętła i działają ZAWSZE. Osobne flagi (tempo, pauzy, szept) to dodatki —
dochodzą po kolei i każda jest niżej opisana osobno.

---

## 2. Jedna komenda, którą mówisz

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Tu wpisz, co ma powiedzieć."
```

Co jest czym — po ludzku:

- `--as dan` → KTO mówi. Wpisz `dan`, `danusia`, `jarvis`, `zdzicho`,
  `krysia`, `komentator`, `spiker`, `ksiadz`, `typ_z_telefonu`, `blondyna`,
  `zagadka`, `radiowiec` albo `zaneta`.
- `"Tu wpisz..."` → CO ma powiedzieć. W cudzysłowie. Nie wstawiaj do środka
  drugiego cudzysłowu `"`, bo komenda się wysypie.
- `--session gadanie` → nazwa wątku. Zostaw `gadanie`, nie kombinuj.
- Reszty (`--json`, `--source claude`) nie ruszasz nigdy.

Wysyłasz kilka komend pod rząd → powie je PO KOLEI, w kolejności wysłania.
Nic się nie nakłada, nic się nie gubi.

**Limit: jedna komenda = maks 2–4 zdania (do ~400 znaków).** Dłuższy tekst
potnij na kilka komend. Za długi jeden kawałek = ryzyko, że zamiast głosu
będzie cisza.

---

## 3. PRZEPISY — „chcę, żeby brzmiał…"

### 3.1 …normalnie **[DZIAŁA JUŻ]**

Piszesz zwykłe zdania z kropkami. Nic więcej.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Melduję wykonanie. Kolejka czysta, demon stoi, testy zielone."
```

### 3.2 …groźnie / poważnie **[DZIAŁA JUŻ]**

Krótkie zdania. Dużo kropek. Zero wykrzykników. Najlepiej na końcu jedno
bardzo krótkie zdanie.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Radzę ci to naprawić. Dzisiaj. Bo jutro nie będę taki miły."
```

### 3.3 …podekscytowany / z kopem **[DZIAŁA JUŻ]**

Wykrzyknik na końcu zdania. JEDEN. Nie trzy (trzy nic nie dodają).

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Działa, kurwa, za pierwszym strzałem!"
```

### 3.4 …złośliwie / ironicznie **[DZIAŁA JUŻ]**

Pytanie, na które nie czeka się na odpowiedź. Po nim krótka sucha kropka.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Naprawdę myślałeś, że tego nie zauważę? No proszę."
```

### 3.5 …tajemniczo / z napięciem **[DZIAŁA JUŻ]**

Wielokropek `…` tam, gdzie głos ma zawisnąć. Może być w środku i na końcu.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Coś tu jest… Log urywa się w połowie linii. I to nie jest przypadek."
```

### 3.6 …dobitnie, jak młotkiem **[DZIAŁA JUŻ]**

Bardzo krótkie zdania. Każde słowo osobno z kropką, jak chcesz walić.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude "Głośność. Naprawiona. Koniec tematu."
```

### 3.7 …wolniej (poważna scena, puenta) **[PO RESTARCIE]**

Dokładasz `--tempo 0.94`. Zakres na chłopski rozum:

- `0.94` = lekko wolniej — powaga, groźba.
- `0.90` = wyraźnie wolniej — finał sceny.
- `0.85` = bardzo wolno — tylko wyjątkowo, jedna kwestia. Niżej NIE schodź,
  bo brzmi jak inny człowiek.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude --tempo 0.94 "Słuchaj mnie teraz bardzo uważnie."
```

### 3.8 …szybciej (akcja, ekscytacja) **[PO RESTARCIE]**

Dokładasz `--tempo 1.05` (lekko szybciej) albo `--tempo 1.10` (wyraźnie).
Wyżej nie ma po co.

```bash
/Users/n1_ozzy/.dan/bin/dan speak --json --as dan --session gadanie --source claude --tempo 1.08 "I leci, i mija drugiego, i jest na prostej!"
```

**Zasada do tempa (obie strony):** nie skacz. Jak jedna kwestia miała
`0.94`, to następna może mieć `0.90` albo `1.0`, ale nie `1.10`. I po scenie
WRACASZ do normalnego (czyli bez flagi). Tempo to przyprawa, nie obiad.

### 3.9 …z pauzami / oddechem **[PO RESTARCIE — automat]**

Nic nie ustawiasz. Po restarcie demon SAM robi oddech po każdym zdaniu,
zależnie od znaku na końcu:

- po kropce → krótki oddech (0.4 sekundy),
- po wykrzykniku → najkrótszy (0.3 — żeby nie zabić energii),
- po pytajniku → trochę dłuższy (0.45),
- po wielokropku → najdłuższy (0.55 — zawieszenie).

Czyli: chcesz dłuższą ciszę po zdaniu → zakończ je wielokropkiem. Chcesz
krótką → wykrzyknikiem. To wszystko.

Osobna flaga na ekstra-długą pauzę PO całej kwestii (np. 0.68 sekundy przed
finałem) — **[JESZCZE NIE MA]**, dojdzie później.

### 3.10 …szeptem **[JESZCZE NIE MA]**

Będzie osobny profil szeptu. Jak wejdzie, zasada będzie jedna: szept
najwyżej RAZ na całą scenę, najlepiej jako ostatnie zdanie DAN-a. Na razie
„cicho i groźnie" robisz przepisem 3.2 (krótkie zdania, kropki).

### 3.11 …krzycząc **[JESZCZE NIE MA]**

Będzie profil krzyku, ale UWAGA: on prawie nigdy nie jest potrzebny.
Wykrzyknik z 3.3 załatwia 95 procent przypadków. Krzyk-profil to teatr —
zostaw go na wyjątki.

---

## 4. Persony — co wolno komu

- **DAN** → wszystkie przepisy z tej instrukcji.
- **Jarvis** → to ten sam głos co DAN (alias). Te same zasady.
- **Danusia** → TYLKO tekst (przepisy 3.1–3.6). ŻADNYCH flag tempa ani
  profili — jej głos ma zostać taki, jaki jest. To nie propozycja, to zasada.
- **Reszta ferajny** (zdzicho, krysia, komentator itd.) → jak Danusia:
  sam tekst, bez flag, dopóki nie ustalimy inaczej.

---

## 5. CZEGO NIE ROBIĆ (bo już raz przez to płakaliśmy)

1. **Nie pisz didaskaliów** typu „[groźnie]" albo „*szepcze*" — on to
   PRZECZYTA na głos jak debil.
2. **Nie pisz WIELKIMI LITERAMI** — to nie krzyk, to literówki w głosie.
   Od krzyku jest wykrzyknik.
3. **Nie dawaj trzech wykrzykników!!!** — jeden robi robotę.
4. **Nie wciskaj cyfr, ścieżek i linków** — pisz „dwanaście sekund", nie
   „12s". On czyta dosłownie wszystko, co dostanie.
5. **Nie wysyłaj jednego wielkiego bloba tekstu** — tnij na kwestie po 2–4
   zdania. Za długie = cisza zamiast głosu.
6. **Nie kręć wszystkim naraz** (tempo + napięcie + krzyk w jednej kwestii).
   Jedna kwestia = jeden efekt. Kilka efektów naraz = bełkot. Dokładnie za
   to wyleciał stary silnik. Dwa razy.
7. **Nie odpalaj żadnego innego grania** (afplay, say, drugi program) —
   głośnik należy do demona. Zawsze.
8. **Nie podbijaj głośności systemowej w nocy** — cisza nocna to nie awaria.

---

## 6. Jak sprawdzić, co się dzieje

Co siedzi w kolejce (co zaraz powie):

```bash
/Users/n1_ozzy/.dan/bin/dan queue list --json --limit 10
```

Czy tor głosu w ogóle żyje:

```bash
/Users/n1_ozzy/.dan/bin/dan doctor --json
```

Skasować z kolejki wszystko, czego jeszcze nie powiedział (z wątku gadanie):

```bash
/Users/n1_ozzy/.dan/bin/dan queue flush --session gadanie
```

---

## 7. ŚCIĄGA — całość w jednej tabelce

| CHCĘ | ROBIĘ | DZIAŁA? |
|---|---|---|
| normalnie | zwykłe zdania z kropkami | JUŻ |
| groźnie | krótkie zdania, same kropki, krótki finał | JUŻ |
| z kopem | `!` na końcu (jeden) | JUŻ |
| złośliwie | pytanie retoryczne `?` + sucha kropka po nim | JUŻ |
| napięcie | `…` w środku albo na końcu | JUŻ |
| jak młotkiem | słowa osobno z kropkami: „Głośność. Naprawiona." | JUŻ |
| dłuższa cisza po zdaniu | zakończ zdanie `…` | PO RESTARCIE |
| krótsza cisza po zdaniu | zakończ zdanie `!` | PO RESTARCIE |
| wolniej | `--tempo 0.94` (powaga) / `0.90` (finał) | PO RESTARCIE |
| szybciej | `--tempo 1.05` / `1.10` | PO RESTARCIE |
| szeptem | profil szeptu | JESZCZE NIE MA |
| krzykiem | profil krzyku (prawie nigdy niepotrzebny) | JESZCZE NIE MA |
| ekstra pauza po całej kwestii | flaga pauzy | JESZCZE NIE MA |
| Danusia z emocją | TYLKO tekstem (3.1–3.6), zero flag | JUŻ |

Jedna zasada nad wszystkimi: **piszesz znakami, on gra głosem.** Jak nie
wiesz, którego przepisu użyć — nie używaj żadnego, sam tekst z dobrą
interpunkcją załatwia większość roboty.
