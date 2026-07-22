# DAN voice recovery — two full summaries

**Date:** 2026-07-23
**Purpose:** durable archive of the two long summaries readied for Ozzy's review.
**Status:** documentation only. This file does not replace the active voice catalog,
the persona canon, or runtime configuration.

---

## Message one — what the historical natural voice actually was

The notes exist. There is no single magic file. The useful material is spread across
the repository, Git history, and the old repository. The picture below combines those
layers into one readable account.

The most important fact is that the best state confirmed by ear was the natural-v3
version from 16 July. After several iterations Ozzy said: “Teraz jest dobrze”. This
was not a finished savage-roast mode. It was the best confirmed pattern for a natural
Polish DAN voice used for radio and bedtime content.

The goal was not a synthetic effect or an actor preset. The goal was a Polish voice
that sounds like a person. Character was supposed to come from the text itself: the
reaction, the point of view, punctuation, length of the thought, and pauses. Audio
processing could help, but it could not act in place of the writing.

The accepted casting was concrete:

- DAN used M3 with the raw profile.
- Danusia used F4 with the clean profile.
- Danusia kept her own natural pace instead of being mechanically forced to match
  DAN.
- DAN could use a moderate stage tempo, but the base had to remain natural.

An ordinary line was one complete thought, usually around 180–300 characters. A tense
line could reach roughly 250–340 characters. A quick riposte could be around 60–140
characters. Complete spoken thoughts mattered because they sound like speech instead
of a report chopped into sentences.

The text itself was part of the prosody. Emotion came from reaction, position,
punctuation, and a complete thought. DAN had to react and take a position, not narrate
a document with a swear word glued on. Danusia had to be a real counterpoint,
provocateur, or separate point of view, not a second narrator.

Pauses were not all the same:

- ordinary pause: about 0.18 seconds;
- question: about 0.26 seconds;
- tension or a format change: about 0.32–0.34 seconds;
- closing a thought: about 0.40–0.48 seconds;
- final contrast: about 0.68 seconds.

A 0.90-second pause was avoided because it sounded unnaturally long. A stage pause
belonged after the final internal fragment of the whole utterance, not after every
technical chunk created by the broker. Otherwise the voice became a sequence of
separate announcements.

The bedtime preparation used a generator that read a script and produced a playlist.
Each line carried a persona, tempo, profile, pause, and then the spoken text. The
feeder received only the prepared playlist lines, not human-facing headers. The
generator also produced a prosody description so the text and settings could be
checked before listening.

The intended flow was:

1. write a complete thought with its prosody;
2. resolve the persona and render parameters;
3. send it through Supertonic;
4. apply the final audio treatment;
5. play it through the single audio owner.

Supertonic was kept warm so the first line did not begin with a long startup gap. The
broker was the single playback owner. Parallel `afplay`, `say`, or a second broker
were not part of the accepted flow.

Processing was separate from casting. Raw meant DAN's natural, unpolished route;
clean was Danusia's route. Stronger profiles such as gritty and krzyk existed, but
they were removed from the accepted natural-v3 demo because they sounded theatrical.
Szept was kept only as a rare contrast, preferably for a DAN ending.

Endings had to survive intact. The last syllables could not be cut merely because the
text was split technically. Artificially long tails could be trimmed, but the thought
had to finish with a full breath rather than a swallowed ending or abrupt cut.

There was also a pronunciation layer. Text passed through a pronunciation dictionary
before synthesis to fix Polish names, abbreviations, and nicknames. This is where the
pronunciation of Ozzy's nickname belongs: “Ozi”, not “Ożi” and not an English reading.

Later, deterministic Supertonic seeding was added. The same text, voice, and settings
with the same seed produce the same file. Three identical live A/A/A renders produced
the same hash. Seed 91 produced a different result. The `best_take` tool can render
controlled variants and retain the machine-selected candidate, but a human listening
verdict remains more important than a machine score.

There was an older `segment_take` path that retained selected segments and allowed a
better take to be assembled from them. That is historical material, not something to
turn on blindly. There were also historical wcinki: small breaths, stutters,
corrections, laughs, sighs, short gasps, and interruptions. Those human breaks were a
major part of the old natural feel. They are material to recover, not random filler
to attach without control.

The strongest quality evidence came from listening, not theory. There were hundreds
of human verdicts, blind trials, accepted variants, and an accepted laugh sample.
Naturalness and disfluencies performed well; prosody and punctuation were weaker.
That evidence tells us what worked historically, but it does not prove that every
current live path still uses every one of those decisions.

The old `dan_voice` file was an attempt to control voice from one place, but it was
not a trustworthy new centre. It mixed casting, tempo, profile, and processing, and
some settings sounded impressive only on paper. It should not simply be restored as
another source of truth.

The present conflict is this: the deterministic Supertonic route, voice catalog,
pronunciation, render snapshot, and candidate selection have been recovered, while
ordinary live gadanie still splits an answer into sentence requests. The best natural
pattern was built out of complete thoughts and deliberately written prosody. The
technical pieces exist, but they are not yet assembled into the same artistic route.

The missing layer is mainly live conversation direction: natural transitions, a
variable reaction delay, entering another speaker's sentence, follow-up questions,
short reactions, and a safe way to use wcinki. The connection between text prosody and
live gadanie is also not yet proven end to end.

The safe restoration order is therefore:

1. preserve the catalog and deterministic rendering;
2. restore complete-thought writing with text prosody;
3. add tempo and pause choices based on the role of the line;
4. add dialogue direction and human wcinki;
5. listen to the result as a human;
6. call it done only after a concrete sample is actually played and judged.

---

## Message two — what was actually done today and what is still missing

Start/end pads are deliberately omitted here because Ozzy said they are not important
for now. Bluetooth headphone details are also out of scope.

The first correction is important: the work was not done in the wrong repository for
two weeks. That was a false alarm caused by similar path names. The active project is
the lowercase `dan` repository, and the current daemon and voice path are connected
to that project. The earlier path confusion did not erase two weeks of work.

What exists now is a working, repeatable Polish DAN voice on M3. If the same text is
rendered three times with the same settings, the result is identical. That was checked
with a concrete hash. Seed 1 is currently active, but it is honestly only a provisional
machine-best, not a final human listening verdict.

Seed 91 produces a different result, which proves the seed is real and useful. It lets
us compare candidates without chaos and return to exactly the same sample. This is
recovered control, not proof that the current sound is perfect.

The active casting is explicit:

- DAN: M3, raw;
- Danusia: F4, clean;
- Jarvis: M1, separate from DAN so their timbres do not collide.

Those choices are stored in the project's versioned catalog. The resolver is intended
to read one source instead of guessing among old files.

There is an immutable render snapshot for a request. Before synthesis, the request can
retain the persona, voice, tempo, profile, seed, and processing information that were
actually used. This makes it possible to prove what was rendered instead of trusting
someone's description of what they meant to configure.

The pronunciation path is also recovered. Text can pass through a pronunciation
dictionary before synthesis for Polish names, abbreviations, and the nickname Ozi.
The mechanism and its insertion point are known, although the mere existence of the
dictionary does not prove that every live response currently passes through it.

The `best_take` path can generate several deterministic samples, retain their results,
and select a machine-best candidate. It does not replace Ozzy's listening verdict. Its
job is to stop losing candidates and make the selected material reproducible.

The historical whole-thought workflow is recovered as well. Text was written as
complete thoughts instead of isolated live sentence requests. Each thought could get
its own tempo, emotion, tone, and pause. Punctuation was not decoration; it controlled
breath, emphasis, and the moment of entry.

The strongest natural pattern used M3 raw for DAN and F4 clean for Danusia. Writing
was spoken, reactive, and personal, with short confrontations. It did not require
every line to shout. It required waves: calm, pressure, amusement, contempt, a quick
cut, and then a normal breath.

The bedtime generator understands a playlist containing persona, tempo, profile, and
pause. The preparation layer can prepare the text before sending it to voice. The
problem is not that these elements vanished. The problem is that ordinary live speech
does not yet use them in the same order with the same hand-written direction.

The Supertonic service can be kept warm, and the daemon remains the one playback owner.
That distinction matters because accepting a request into a queue is not the same as
proving that anyone heard it. The proof is a completed playback and a real listening
check.

Audio processing exists, but it is not an actor. Raw and clean are attached to the
casting, while additional processing can change colour, loudness, or the ending. A
filter cannot create real anger, breathlessness, hesitation, or human pressure when
the writing and direction do not carry them.

The historical `segment_take` mechanism and wcinki are found as material. Segment take
kept selected fragments. Wcinki supplied stutters, corrections, laughs, sighs,
breaths, and interruptions. This is precisely the layer that used to make the voice
feel human. It is found historically, but it is not yet safely connected to ordinary
conversation.

There is real listening evidence from earlier work: hundreds of verdicts, blind tests,
accepted variants, and the confirmed natural-v3 reference. That tells us what once
worked. It must not be exaggerated into a claim that the current daemon automatically
replays every old decision.

What was actually done today was narrower and concrete:

- the deterministic Supertonic implementation was preserved at a checkpoint;
- the active seed was identified and compared against another seed;
- the snapshot, resolver, TTS, daemon, and `best_take` path were documented together;
- the historical natural-v3 recipe was recovered as the reference;
- the distinction between active code and historical material was made explicit;
- the missing live-dialogue layer was identified instead of pretending it already
  worked.

What was not delivered today:

- the full natural live path was not restored;
- ordinary gadanie still splits responses into sentence requests;
- transitions between DAN and Danusia can still contain a few seconds of silence;
- there is no finished controlled random reaction delay;
- there is no finished entering-the-sentence, follow-up, or interruption behaviour;
- there is no end-to-end proven flow that takes a live answer, assigns roles and
  emotion, adds tempo, pauses, wcinki, and mastering, then plays it as one human
  exchange.

That is why the current voice can be technically correct but feel bare: the old tempo,
pressure, emotion, and human breaks are not all present in the live route.

The honest verdict is neither “we have nothing” nor “everything is finished”. We have
the recovered, reproducible foundation and much of the historical recipe. We do not
yet have the assembled live layer that made it sound human. That layer is the
difference between DAN and correctly generated sentences.

The next order is straightforward: compare a concrete current sample with the old
natural reference; restore whole-thought writing and text prosody; add controlled
tempo, pauses, and dialogue roles; reconnect wcinki and natural transitions; then
listen and rate the result. Only after that is it honest to say the voice is fixed.

---

## Source-of-truth warning

This archive preserves the two summaries in one visible place. It is not a replacement
for the live authorities:

- persona: `config/persona/DAN.md`;
- voice catalog: `config/voice/personas.toml` and `config/voice/pronunciations.toml`;
- runtime implementation: `dan/voice/` and the `dand` daemon;
- historical artistic reference: the natural-v3 handoff and its accepted playlist.

When this archive disagrees with current code, current code and the active catalog win.
The archive exists so the reasoning, recovered recipe, and missing pieces do not vanish
from the next session.
