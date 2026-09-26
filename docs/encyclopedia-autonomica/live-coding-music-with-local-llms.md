# Jamming with Jef and Tidal

*Encyclopedia Autonomica · Agents in creative domains · Technical deep dive*

> **Summary.** This article describes an architecture in which a local large
> language model performs a live-coded [TidalCycles](https://tidalcycles.org)
> set that streams without end. The model does not generate a song. It
> *performs* one: it grows a short program line by line and keeps revising
> it, one small, audible edit every few bars, and explains each edit:
> what it heard and why it made the move. Every move is guaranteed to be
> legal and musically safe, and every line is mixed in at a measured
> level, so the only thing left to the model is judgement: what to
> change, when, and why. Four agents share the work (a Director plans each
> track, a Performer live-codes it, a Narrator speaks at its turning
> points, and optionally a Judge chooses blind between the Performer's
> ideas and random ones), and every exchange between them is visible.
> That makes each performance a controlled, explainable experiment in
> machine creativity, scored against a random baseline performer. The reference
> implementation is [`jsemrau/ystreamer`](https://github.com/jsemrau/ystreamer),
> which streams 24/7 on YouTube as **@sleeplesscomposer**.

---

## 1. Background: live coding as a stage for machine creativity

**Live coding** is a performance practice in which the musician writes
and rewrites a program while it plays, with the code projected for the
audience. In TidalCycles, a Haskell-embedded pattern language, a set
starts with a single line:

```haskell
d1 $ s "bd ~ ~ bd ~ ~ bd ~"
```

and grows into a small program of up to nine lines, one per channel
(`d1`...`d9`). Each line is a pattern: rhythms and melodies in compact
*mini-notation* (`"0 ~ 2 4"`, `"<a b>"`, `"hh*8"`), piped through
effects (`# lpf 800`, `# room 0.4`) and transformations (`jux rev`,
`every 4 (fast 2)`, `off 0.25 (|+ n 12)`). Re-evaluating a line replaces
that channel's pattern at the next cycle. The music never stops; it is
steered.

Performers such as Switch Angel have made the style popular. What the
audience enjoys is not the final program but its *trajectory*: the
build-up, the moment the acid filter opens, the breakdown when the kick
drops out, the small twist that makes a loop feel new.

This makes live coding an unusually good **test of creative behaviour in
language models**, for four reasons:

1. **It is open-ended.** No edit is correct; edits are only better or
   worse in context.
2. **It is sequential and self-referential.** Every decision must be
   read against the program as it is now, which is the model's own
   earlier work.
3. **It is long-horizon.** A stream runs for hours. A model that has
   three good ideas and then loops on them fails visibly.
4. **It is judged by ear.** Listeners, not unit tests, decide what is
   good. That keeps the test honest about what creativity means.

Asking a model to *write a track* in one shot tests something different:
recall of syntax seen during training. Local models (for example
quantized 27B to 35B Qwen models served by `llama.cpp`) do poorly at
this. They produce type errors, wrong notes, clashing layers and walls
of sound. None of that tells us whether the model has musical judgement.
The architecture below separates the two questions.

---

## 2. Design principles

Six principles shape every component.

**P1. Separate the space of legal moves from the choice among them.**
The system, not the model, guarantees that a move is syntactically valid,
in key, within sensible ranges and appropriate for the instrument. The
model only chooses among legal moves. Anything its choices add beyond a
random walk through the same moves is its creative contribution, and
that contribution is measurable.

**P2. The artefact is the code.** The program is not an internal
representation hidden behind the music. It is the product. It is shown
on screen, it is what the model reads, and it is what gets revised.

**P3. One small edit at a time.** An edit touches one line. Small edits
sound like a human performer, keep every decision attributable (so it
can be evaluated), and limit the damage a bad decision can do.

**P4. The music never stops.** Every LLM call can time out or return
garbage. A built-in performer takes over any edit the model fails to
provide, so model failures become data points, not outages.

**P5. Engineering belongs to engineers, judgement to the model.** Sound
design, gain staging, timing, syntax and streaming are solved once, by
hand, and kept out of the model's reach. The model gets the part that
can't be solved once and for all: what to play next.

**P6. Every decision explains itself.** Understanding *why* each agent
decided is as important as the music. Every LLM exchange is shown as it
happens (who asked which model what, and what came back), every edit is
tagged with who chose it, the model states what it hears before it
states its move, and the system's own structural decisions (the track's
arc) are logged with their reasons.

---

## 3. Architecture overview

```
   ┌──────────────┐  plans each track (key, tempo, progression, mood, title, intro line)
   │   Director   │──────────────────────────────────────────────┐
   └──────────────┘                                              ▼
                      ┌─────────────────────────────────────────────┐
                      │                 Performer                    │
                      │                                              │
  program as code ───▶│  phase machine  ─▶  prompt  ─▶  local LLM    │
  (what is playing)   │  build/groove/      (code,      (llama.cpp,  │
                      │  breakdown/...      legal       OpenAI API)  │
                      │                     moves)          │        │
                      │                                     ▼        │
                      │              built-in performer ◀─ JSON edit │
                      │              (fallback)       {listening,    │
                      │                                reason, op,   │
                      └──────────────────────┬──────── role, value}  │
                                             │  Edit       ──────────┘
                     optional:  LLM proposes 3 ─┐
                                built-in adds 3 ─┼─▶ Judge (jevtypesafe) picks one, blind
                                             ▼
┌──────────────┐    ┌─────────────────────────────────────┐
│    Mixer     │───▶│  measured levels, fades, room for    │
│ (music/mix)  │    │  the front, gain window              │
└──────────────┘    └──────────────────┬──────────────────┘
                                       ▼
┌──────────────┐    ┌─────────────────────────────────────┐
│ Block library│───▶│        Program model + validator     │
│ (per style)  │    │  LineSpec per role, apply_edit():    │
└──────────────┘    │  ranges, key-safety, role rules      │
                    └──────────────────┬──────────────────┘
                                       │ changed lines only
                                       ▼
┌──────────────┐    ┌─────────────────────────────────────┐    ┌────────────────┐
│ Clock (cps)  │───▶│            Live engine               │───▶│ GHCi + Tidal   │
│ bar-aligned  │    │  one edit every N bars, on the bar   │    │ SuperDirt / SC │
└──────────────┘    │  overlay, narration, state, history  │    └───────┬────────┘
                    └──────────────────┬──────────────────┘            │ audio
                                       │                               ▼
                               on-screen code (▶ edited line,     ffmpeg → RTMP
                               -- reason) + Narrator at the       (YouTube live)
                               arc's turning points
```

The system has seven layers:

| Layer | Module(s) | Responsibility |
|---|---|---|
| Program model | `music/livecode.py` | The live program as data; rendering to Tidal; the edit language and its validator |
| Block library | `music/blocks.py`, `tidal/synthdefs.scd` | Curated instruments and starting lines per style (nine styles) |
| Mixer | `music/mix.py` | Levels calibrated from measured loudness, fades, density ducking, the gain window |
| Agents | `music/performer.py`, `music/director.py`, `music/live_engine.py`, `prompts/` | Director, Performer (phase machine, prompt, schema, built-in fallback), Judge, Narrator |
| Live engine | `music/live_engine.py`, `music/engine.py` (`Clock`) | Timing, applying edits on the bar, sending changed lines, publishing state |
| LLM gateway | `music/llm.py`, `audio_stack.py` | Provider routing (local, Claude, jevtypesafe), schema-constrained decoding, local server lifecycle, timeouts, logging every exchange |
| Audio and stream | `audio_stack.py`, `tidal/`, `stream/`, `music/narrator.py` | GHCi session, SuperCollider boot, master bus, TTS narration, ffmpeg, watchdog |

**One edit cycle** looks like this. While the current bars play (8 bars
in slow styles, 16 in club styles, about 20 to 30 seconds), a worker
thread builds a prompt from the current program and asks the model for
its next move, under a JSON schema of the moves that are legal right
now. In judge mode, the model proposes three moves, the built-in
performer adds three, and the Judge picks one without knowing who
proposed what. On the bar line the engine validates the chosen edit and
sends only the changed Tidal statements: a new line fades in, a removed
one fades out, and lines whose level changes because the mix got fuller
are re-sent. It then updates the on-screen code, logs what the model
heard, what it did and why, saves state, and, if the track's arc moved
on, asks the Narrator for a line. If the reply is late, invalid or
missing, the built-in performer's edit is used instead, tagged as such.
The loop then starts over.

**Threads.** There are four concurrent actors: the engine thread (the
clock and sending), one performer worker (LLM calls, so thinking never
blocks the beat), one worker that writes Narrator lines, and one that
speaks them (text-to-speech). Tidal itself
runs in a child GHCi process and SuperCollider in a child `sclang`
process.

---

## 4. The roles of the LLM

"The LLM makes music" is too vague to design or evaluate, so the
architecture assigns explicit **roles**. Each role has a defined input,
an output contract, a validator and a fallback. The live engine uses the
first five; the last two belong to the older arranger engine and are
described because contrasting them is instructive.

| Role | Engine | Called | Input | Output | Validated by | Fallback |
|---|---|---|---|---|---|---|
| **Performer** | live | every 8 to 16 bars | program as code, phase, legal moves, its recent edits | one edit (or three proposals) as JSON, under a schema | schema, `apply_edit` (+ GHCi type check) | built-in performer |
| **Commentator** | live | with every edit | same call | `listening`, `reason` (on screen), `narration` (spoken) | length and "no technical words" filter | template reason |
| **Director** | live and arranger | per track (arranger: also per section) | theme, style, previous track | title, key, tempo, progression, mood, intro line | schema, style's tempo and key range | built-in planner |
| **Narrator** | live | at each turn of the arc | track, mood, the moment and why, last move | one spoken sentence | "no numbers or jargon" filter, one-minute gap | hand-written line per moment |
| **Judge** | live, opt-in | every edit | situation, 2-6 shuffled unlabelled options | a choice with probabilities | option id must exist | the Performer's first idea |
| **Composer** (free layer) | arranger | per section, opt-in | key, progression, mood | counter-melody as pentatonic degrees | token grammar, 8 steps per bar | layer omitted |
| **Free-form composer** (legacy) | legacy | continuously | natural-language goal | raw Tidal code | GHCi only | none |

### 4.1 Performer

The Performer is the central role, and the only one that touches the program. The model sees the program exactly as
the audience does, plus a machine-readable view of what it may change,
and returns **exactly one** operation (three alternative proposals in
judge mode, section 8.5). It never writes a Tidal statement
itself. It writes *edits*, which the system turns into statements. This
is what lets a 35B local model play for hours without a single syntax
error reaching the audio engine.

What the Performer decides:
- **Arrangement**: which instrument enters next, when to drop the beat,
  when to strip back.
- **Composition**: melodic contour and rhythm, as scale-degree and
  mini-notation strings.
- **Timbre and space**: filters, reverb, delay, the acid knob, including
  slow sine modulation.
- **Transformation**: which Tidal higher-order functions to wrap around
  a line.
- **Energy**: small tempo changes.

What the Performer does *not* decide: pitch spelling (the key is fixed
per track), sound design, levels, timing and the overall arc of a track
(the phase machine suggests it; see section 8).

### 4.2 Commentator

Every Performer reply carries, in this order, `listening` (what it hears
in the program that matters now), `reason` (why this move, and what the
listener should hear change), the operation, and optionally a
`narration` for big moments. The schema enforces the order, so the model
describes before it decides:

```
👂 hears: Kick, hats, bass and pad all lock to a perfectly straight grid; the groove is accurate but stiff.
✎ [LLM, judged] wrap d1 with `swingBy (1/12) 4`   -- A light swing on the beat loosens the 16ths ...
```

This role costs almost nothing because it shares the Performer's call.
It does three jobs:

1. **It makes the model state an observation and an intention before
   committing to a move**, a light form of chain-of-thought constrained
   to two sentences.
2. **It makes the performance legible to viewers**, who can read *why*
   the hi-hats just doubled.
3. **It gives evaluators ground truth about intent.** "Darken the keys"
   followed by a *raised* low-pass cutoff is a measurable
   inconsistency.

Narration is filtered before it is spoken: anything with digits,
underscores or engineering words ("layer", "energy", "orbit", "cps") is
dropped. Listeners should hear "the melody leans down, like it's getting
sleepy", not "lpf to 1800".

### 4.3 Director

In the live engine, the Director plans each track before the Performer
starts: title, key, tempo, chord progression, mood, and a spoken intro,
with its reasoning shown (`🧠 Next piece: 'Neon Rain', lofi, D minor,
84 BPM, i-VI-III-VII, late-night`). Its plan is checked against the
style that will actually play: a tempo or key outside the style's range
is replaced (a darkwave track once started at 76 BPM because the model
had planned it as lofi).

Before the live engine existed, the Director was the main role. Per
piece it chose a title, key, tempo, chord progression, mood, extra
progressions for the chorus and breakdown, chord colour (sevenths or
ninths) and an optional key lift. Per section it chose which preset
played in each role (pad, keys, bass, melody, arpeggio, drums, texture)
and how much movement and fill to use. A deterministic harmony engine
rendered those plans into Tidal code.

The Director role is reliable because it is almost pure selection from
catalogues. It turned out to be a poor test of creativity. Decisions
came every one to three minutes and were coarse, and the result was
correct but static music. The same sustained pad sat under every
section, and listeners called it boring. That experience motivated
principle P2: the model's creative surface must be *the code*, and
changes must be frequent and small. The Director now does what it is good
at, a coarse plan per track, and the Performer does the rest.

### 4.4 Narrator

When the track's arc moves on (the groove settles, the breakdown, the
rebuild, the second peak, the outro), the Narrator writes one warm
sentence about the moment for the text-to-speech voice. It runs on a
separate worker, so the music never waits for it, and it sees why the
arc moved and what the last edit was. Lines with numbers or technical
words are not spoken, lines closer than a minute apart are skipped (the
track intro always plays), and each skipped line says why in the log.
Without a text model the Narrator uses a hand-written line per moment.

### 4.5 Judge

With `--judge jevtypesafe`, the Performer becomes a **generator**: it
proposes three different moves in one call, the built-in performer adds
up to three of its random-but-legal moves, and a decision model
(jevtypesafe's typed decision API) picks one from the shuffled,
unlabelled pool. The Judge returns a probability per option, which the
log shows:

```
💬 Judge ← jevtypesafe/decide (0.9s): {"edit": {"choice": "e2", "confidence": 0.26,
                                        "probabilities": {"e2": 0.38, "e6": 0.34, "e5": 0.21, ...}}}
⚖️  Judge chose the LLM idea (p 0.38, confidence 0.26, from 3 LLM + 3 built-in)
```

Because the Judge is blind, how often it prefers the model's ideas over
random ones, and how much probability it gives them, is a direct
measure of the model's creative contribution (section 12). A decision
API cannot write text, so it cannot perform alone in any interesting
way: given only built-in candidates it can only pick the best of random
ideas. The pairing is the point: a generator for ideas, a judge for
taste.

### 4.6 Composer (free layer)

The arranger engine could optionally ask the model for one
**counter-melody** per section, written as degrees of a pentatonic scale
(`"0 _ ~ 2 ~ ~ 4 _"`, exactly 8 steps per bar, at most 4 onsets). This
was the first use of the technique that the live engine generalises:
*let the model write material only in a representation where every
string is in key*. The same idea reappears as scale-degree pattern slots
(section 5.2).

### 4.7 Free-form composer (legacy)

The project's original design asked the model to write raw Tidal code
from a natural-language goal. It is kept (`--legacy`) as a negative
result. With local models, most outputs failed to type-check, and those
that did were rarely harmonic. Unconstrained generation mixes three
separate skills (syntax, music theory, taste) in one sample, so it
measures none of them cleanly.

### 4.8 Division of labour, summarised

| Concern | Owner |
|---|---|
| Syntax and types | system (templates + validator + GHCi) |
| Harmony (key, chords) | system (track plan; degrees relative to chord roots) |
| Sound design | human (curated SynthDefs and blocks) |
| Mix levels, fades | system (measured loudness per synth, `music/mix.py`) |
| Timing and synchronisation | system (bar-aligned clock) |
| Track plan (key, tempo, progression, mood) | model (Director), checked against the style |
| Track arc (phases) | system proposes, model fills in |
| Choosing between ideas | model (Performer) or a separate Judge |
| **What changes next, and why** | **model** |
| **Melodic and rhythmic material** | **model** (within the degree representation) |
| **Commentary, narration** | **model** |

---

## 5. The program model

### 5.1 Lines and roles

A program is a **track** (key, tempo, progression) plus up to nine
lines, one per musical role. Roles are pinned to Tidal channels so the
screen always reads like a hand-written set:

```python
ROLE_CHANNELS = {"beat": 1, "bass": 2, "chords": 3, "keys": 4,
                 "lead": 5, "acid": 6, "texture": 7, "perc": 8, "arp": 9}
```

The `arp` role came last (section 13.1): a second 16th-note layer, such
as an acid line an octave up or a pluck sequence, that gives club styles
the interlocking density of a real set.

Each line is a `LineSpec`: a **template body with named slots**, plus
effects, custom synth controls and wrappers. Keeping the parts separate
is what makes small, meaningful edits possible. "Change the melody" is a
slot write, "darken" is one effect value, "reverse in stereo" is one
wrapper.

```python
@dataclass
class LineSpec:
    role: str
    body: str                  # 'n (scale "{mode}" ("{melody}" |+ "<{roots}>") {root}) # s "ys_glass"'
    patterns: dict[str, str]   # named mini-notation slots in body
    fx: list[list[str]]        # [["lpf", "3000"], ["room", "0.4"]]
    pfs: list[list[str]]       # custom SynthDef controls, e.g. [["acid", "0.55"]]
    wrappers: list[str]        # ["jux rev", "off 0.25 (|+ n 12)"]
    muted: bool = False
    drums: bool = False
    block: str = ""            # which library block it came from

    def render(self, harmony: dict[str, str]) -> str:
        body = self.body.format(**harmony, **self.patterns)
        tail = "".join(f" # {name} {value}" for name, value in self.fx)
        tail += "".join(f' # pF "{name}" {value}' for name, value in self.pfs)
        expr = body + tail
        for wrapper in reversed(self.wrappers):
            expr = f"{wrapper} $ {expr}"
        return expr
```

A `Program` renders each line as `dN $ <expr>` (or `dN $ silence` when
muted), plus a tempo statement `setcps (bpm/60/4)` (one Tidal cycle is
one 4-beat bar). For display, muted lines are shown commented out
(`-- d1 $ ...`) and the most recently edited line gets a `▶` marker.

### 5.2 Harmony is injected, never generated

The track supplies harmony as template placeholders:

```python
def harmony(self) -> dict[str, str]:
    key = Key.parse(self.key)                       # e.g. "A minor"
    rep = f"!{self.bars_per_chord}" if self.bars_per_chord > 1 else ""
    stacks = []
    for d in self.degrees:                          # progression as scale degrees
        base = d - 7 if d > 3 else d                # keep chords near the tonic
        stacks.append("[" + ",".join(str(base + i) for i in (0, 2, 4, 6)) + "]" + rep)
    roots = " ".join(f"{d - 7 if d > 3 else d}{rep}" for d in self.degrees)
    root = key.root if key.root <= 5 else key.root - 12
    return {"mode": key.mode,
            "root": f" + {root}" if root > 0 else (f" - {-root}" if root < 0 else ""),
            "chords": " ".join(stacks), "roots": roots,
            "bar": f"{1 / self.cps:.4f}", "beat": f"{1 / self.cps / 4:.4f}"}
```

Melodic lines are built from this pattern:

```haskell
n (scale "minor" ("~ 4 ~ ~ 2 ~ ~ ~" |+ "<0 -2 3 -3>") - 3 + 12) # s "ys_glass"
--                 └── melody slot ─┘   └ chord roots ┘ └key┘ └octave
```

The melody slot holds **scale degrees relative to the current chord
root**. `|+` adds the root of whichever chord is playing, `scale` maps
degrees to semitones in the track's mode, and the key offset transposes.
So 0 is always the chord root, 2 its third and 4 its fifth. Any string
of digits the model writes lands in key and follows the chord changes.
This single decision removes the most common failure of local models in
music (wrong notes). What remains is the creative part: contour, rhythm,
density and register.

Chord lines use four-note stacks (`[0,2,4,6]` is a seventh chord in
scale steps) kept near the tonic so voicings don't drift. Rhythm is
added with `struct "t ~ ~ t ~ ~ ~ ~"`, which is itself an editable slot.

### 5.3 Immutability and diffs

Edits never mutate the running program. `apply_edit` deep-copies it,
changes the copy and returns it. `changed_statements(before, after)`
computes the minimal set of Tidal statements needed to move from one
program to the next:

```python
def changed_statements(before: Program, after: Program) -> list[str]:
    out = []
    if before.track.bpm != after.track.bpm:
        out.append(after.tempo_statement())
    harmony_changed = before.track.harmony() != after.track.harmony()
    for role in after.lines:
        if harmony_changed or role not in before.lines or \
                before.statement(role) != after.statement(role):
            out.append(after.statement(role))
    for role in before.lines:
        if role not in after.lines:
            out.append(f"d{ROLE_CHANNELS[role]} $ silence")
    return out
```

A tempo change also changes `bar` and `beat` (used by tempo-synced
synths), so every line is re-sent. Otherwise usually exactly one line
goes over the wire, just as a human re-evaluates one line with
Ctrl-Enter.

---

## 6. The block library and the sound palette

### 6.1 Blocks

A **block** is a curated starting line: body, a few variants for each
slot, default effects and optional starting wrappers. `Block.make(rng)`
picks slot variants, sets the line's calibrated level (section 6.3) and
returns a fresh `LineSpec`. Nine styles each have their own library:
lofi, ambient, downtempo and piano (the chill rotation), deep house,
trance (after Switch Angel's acid setup), techno (138-150 BPM), hardcore
(160-185 BPM, a distorted gabber kick) and darkwave (110-128 BPM cold
electro: a stiff 808 drum machine, a 16th-note sequencer bass, a cold
string-machine bed, a pluck arpeggio in long delay). A theme like
"hardcore techno" or "cold electro dark wave" selects the style by
keyword; without one, the stream rotates between the chill styles.

```python
Block("acid", "acid",
      'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
      {"motif": ["0 4 0 9 7", "0 7 0 3", "0 2 0 4 7", "0 5 0 9 7 12"]},
      fx=[["gain", "0.98"], ["legato", "0.6"]],
      pfs=[["acid", "0.546"]],                        # the squelch knob
      wrappers=['(|* gain "[0.67 0.91 1 1]*4")'])     # 16th accent pattern
```

`<a b c>*16` plays one element of the motif per sixteenth note and
cycles through it. With a five-note motif against sixteen steps, the
line phases against the bar, which is the rolling acid figure of
Switch Angel-style trance.

Each style also has settings: how often to edit, how many lines may play
at once, and which roles may open a track:

```python
STYLE_SETTINGS = {
    "lofi":     {"edit_bars": 8,  "max_lines": 6, "openers": [["beat"], ["keys"], ["bass", "beat"]]},
    "trance":   {"edit_bars": 16, "max_lines": 7, "openers": [["acid"], ["beat"], ["acid", "beat"]]},
    "darkwave": {"edit_bars": 12, "max_lines": 7, "openers": [["bass"], ["beat"], ["bass", "beat"]]},
    ...
}
```

Edits every 4 bars (about 11 seconds) turned out to be too busy to
listen to; 8 to 16 bars (20 to 30 seconds) gives each change time to be
heard. `--edit-bars` overrides it.

The library is where **taste is encoded by humans**. For example, the
lofi palette has no sustained pad at all. Its harmony comes from Rhodes
and felt-piano comping, because an earlier version with a pad under
every track was the single most common listener complaint.

### 6.2 SynthDefs

The sounds are custom SuperCollider SynthDefs (`tidal/synthdefs.scd`):
an electric piano, a felt piano, a sub bass, a glass bell, a pluck, air
and vinyl textures, strings, organ, choir, a supersaw with sidechain
duck, a kick, a rolling bass, a gated saw and a 303-style acid voice.
The acid voice shows how one model-facing control (`acid`, 0.1 to 0.95)
can drive a musically rich parameter space:

```supercollider
SynthDef(\ys_acid, { |out, sustain = 0.1, pan, freq = 110, speed = 1, acid = 0.5, acidres = 0.18|
    var env    = EnvGen.ar(Env([0, 1, 0.9, 0], [0.002, sustain.max(0.04), 0.03]), doneAction: 2);
    var fenv   = EnvGen.ar(Env([0, 1, 0.2], [0.001, 0.12], -4));
    var cutoff = (100 * (2 ** (acid.clip(0, 1) * 9 * fenv))).clip(40, 16000);
    var sig    = RLPF.ar(Saw.ar(freq * speed), cutoff, acidres.clip(0.05, 1));
    sig = (sig * 1.8).tanh * 0.3;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
```

The filter envelope sweeps from 100 Hz up to `100 * 2^(9 * acid)`, a
range of nine octaves. So `acid 0.3` is a muffled pulse and `acid 0.8`
screams. A single number the model can ride, even as
`(slow 8 $ range 0.4 0.75 sine)`, carries the whole tension arc of an
acid track.

### 6.3 Mixing: measured levels, fades and room for the front

The first live version had no mixer: every line entered at a hand-picked
gain, instantly, however full the mix already was. When strings came in
noticeably too loud, measuring showed why. Rendering every SynthDef
offline (sclang plus scsynth in non-realtime mode, EBU R128 integrated
loudness, `eval/synth_levels.sh`) found the sustained beds 4 to 8 dB too
loud: ambient strings +6.4 dB, the trance gated saw +7.8 dB. One block
had used a single gain for three alternative synths whose loudness
differed by 8 dB. The mixer (`music/mix.py`) now works in four layers:

1. **Calibrated levels.** Each sustained block's gain is computed from its
   synth's measured loudness so that its role lands at a target level:
   melody -21 LUFS, bass -19, keys -25, beds -29, textures -34. SuperDirt's
   gain curve is amplitude ∝ gain⁴, so a gain `g` changes the level by
   `80·log10(g)` dB, and four-note chords add about 5.6 dB. Drums, short
   chord hits and fast 16th-note streams keep hand-tuned gains, because a
   single test note says little about their loudness.

   ```python
   def calibrated_gain(synth, role, chord):
       level = level_db(synth, chord, gain=1.0)          # measured LUFS (+5.6 dB for chords)
       return clamp(10 ** ((ROLE_TARGET_LUFS[role] - level) / 80), 0.3, 1.2)
   ```

2. **Fades.** New and unmuted lines fade in over 4 bars, removed and
   muted lines fade out over 2, using Tidal's transitions:
   `xfadeIn 3 4 $ (<pattern>) |< orbit 2` instead of `d3 $ <pattern>`.
3. **Room for the front.** Beds step back 2.5 dB when two of lead, keys
   and acid are playing, and melodic lines give 1.4 dB when six or more
   lines play. This is rendered as `(<pattern>) |* gain 0.93`, and lines
   whose level changes are re-sent with the edit that caused it.
4. **A gain window.** The model may balance a line by about ±3 dB around
   the level it entered at (gain ×0.92 to ×1.09), no more. Before, a
   single `gain 1.0` on a calibrated string bed would have been +13 dB.

A master chain (a gentle 2:1 compressor, make-up gain and a limiter at
-1 dBFS) runs after all channels, so no combination of edits can clip
the stream.

---

## 7. The edit language and its validator

### 7.1 Operations

Every move is one of eleven operations:

| op | Fields | Example | Musical meaning |
|---|---|---|---|
| `add` | `block` | `{"op":"add","block":"felt_melody"}` | Bring in a new line |
| `remove` | `role` | `{"op":"remove","role":"perc"}` | Take a line out for good |
| `mute` / `unmute` | `role` | `{"op":"mute","role":"beat"}` | Breakdowns and drops |
| `set_pattern` | `role`, `name`, `value` | `{"op":"set_pattern","role":"lead","name":"melody","value":"4 ~ 2 ~ 0 ~ ~ ~"}` | Melody or rhythm variation |
| `set_fx` | `role`, `name`, `value` | `{"op":"set_fx","role":"keys","name":"lpf","value":"(slow 8 $ range 800 3000 sine)"}` | Timbre, space, movement |
| `remove_fx` | `role`, `name` | `{"op":"remove_fx","role":"beat","name":"crush"}` | Clean up |
| `set_pf` | `role`, `name`, `value` | `{"op":"set_pf","role":"acid","name":"acid","value":"0.7"}` | Synth-specific knobs |
| `wrap` / `unwrap` | `role`, `name` | `{"op":"wrap","role":"keys","name":"jux rev"}` | Structural transformation |
| `tempo` | `value` | `{"op":"tempo","value":"86"}` | Energy |

The language is deliberately **small and closed**. A model can learn
everything it needs from the prompt, and each operation corresponds to
something a human live coder actually does.

### 7.2 Validation rules

`apply_edit(program, edit, max_lines)` enforces:

- **Value ranges.** Numbers must fall within per-effect ranges
  (`lpf` 200 to 9000, `room` 0 to 0.8, `delay` 0 to 0.6, `acid` 0.1 to
  0.95, ...), and `gain` within about ±3 dB of the line's entry level. The only non-numeric value allowed is
  one modulation form, `(slow N $ range a b sine|saw|tri)`, with
  N from 1 to 64 and both bounds in range.
- **Pattern hygiene.** Mini-notation must match a restricted character
  set (lowercase letters, digits, whitespace and the mini-notation
  symbols `~ _ [ ] < > * ! . , : - | / ? @`; no quotes, parentheses, `$`,
  `#` or `;`), be at most 120 characters long, and have balanced `[]`
  and `<>`. Melodic slots may not contain letters, only numbers, rests
  and symbols, so the model cannot write note names that escape the key.
- **Role rules.** Each role has its own allowed effects (`ROLE_FX`): the
  bass gets no reverb or delay (it would muddy the low end), textures get
  only space and tone. Wrappers come from three whitelists (melodic,
  drum, bass), and textures take none.
- **Structural limits.** At most three wrappers per line, a per-style
  cap on simultaneous lines, no adding a role that is already playing,
  and tempo moves of at most ±12 BPM within 50 to 200.
- **Locked and choice slots.** Some slots are locked (the synth name).
  Others take a value from a fixed list, for example the trance gate
  pattern `gate` ∈ {0, 1, 2, 3}.

```python
def _check_number(value: str, lo: float, hi: float) -> str:
    value = str(value).strip()
    if _NUMBER_RE.match(value):
        v = float(value)
        if not lo <= v <= hi:
            raise EditError(f"{value} outside {lo}..{hi}")
        return f"{round(v, 4):g}"
    m = _SINE_RE.match(value)
    if m:
        rate, a, b = int(m.group(1)), float(m.group(2)), float(m.group(3))
        if not (1 <= rate <= 64 and lo <= a <= hi and lo <= b <= hi):
            raise EditError(f"{value} outside {lo}..{hi}")
        return f"(slow {rate} $ range {a:g} {b:g} {m.group(4)})"
    raise EditError(f"{value!r} is not a number or (slow N $ range a b sine)")
```

### 7.3 Why the whitelists matter beyond music

The Tidal session is a live Haskell interpreter. A free-text value such
as `1; hush` or a crafted string containing `"` could end a statement
early and inject arbitrary code. Because every value is either parsed as
a number, matched against one modulation template, restricted to a
mini-notation character set or picked from a list, **no model output
ever reaches GHCi as free text**. The validator is a musical safety net
and a security boundary at the same time. This matters because prompts
can carry untrusted content (for example a viewer-supplied theme).

### 7.4 Wrappers

The wrapper whitelists are small vocabularies of idiomatic Tidal
transformations, all pitch-safe (they shift only by octaves):

```python
MELODIC_WRAPPERS = ["every 4 (fast 2)", "every 3 (0.25 <~)", "every 4 (0.125 ~>)",
                    "sometimesBy 0.2 (degradeBy 0.5)", "off 0.125 (|+ n 12)",
                    "off 0.25 (|+ n 12)", "jux rev", "every 4 rev", "degradeBy 0.1",
                    "whenmod 8 6 (# lpf 800)", "every 2 (# room 0.5)"]
DRUM_WRAPPERS    = ["every 4 (fast 2)", "sometimesBy 0.15 (# speed 2)", "degradeBy 0.1",
                    "every 8 (0.25 <~)", "whenmod 8 7 (# crush 6)", "swingBy (1/8) 4",
                    "swingBy (1/12) 4", "every 3 (# lpf 1500)",
                    "sometimesBy 0.1 (echo 3 0.125 0.5)"]
```

Wrappers compose. `jux rev $ off 0.25 (|+ n 12) $ n (...)` plays the
line, adds an octave echo a sixteenth later, and plays a reversed copy
in the other stereo channel. Choosing *which* transformation suits a
line, and when to take it off again, is one of the most expressive
decisions available to the model.

---

## 8. The performer

### 8.1 The phase machine

Unstructured freedom produces aimless music. Every track follows a
**phase machine** that gives it a dramatic arc and tells the model what
kind of move fits now:

```
build ──(target lines reached)──▶ groove ──(6-10 edits)──▶ breakdown ──(4 edits)──▶
rebuild ──(muted lines back)──▶ groove2 ──(6-10 edits)──▶ outro ──(≤1 line left)──▶ next track
```

```python
def _advance_phase(self, program):
    st, active = self.state, program.active_roles()
    if st.phase == "build" and len(active) >= st.target_lines:
        st.phase, st.edits_in_phase = "groove", 0
    elif st.phase == "groove" and st.edits_in_phase >= st.groove_edits:
        st.phase, st.edits_in_phase = "breakdown", 0
    elif st.phase == "breakdown" and st.edits_in_phase >= 4:
        st.phase, st.edits_in_phase = "rebuild", 0
    elif st.phase == "rebuild" and not any_breakdown_line_still_muted(program):
        st.phase, st.edits_in_phase = "groove2", 0
    elif st.phase == "groove2" and st.edits_in_phase >= st.groove_edits:
        st.phase, st.edits_in_phase = "outro", 0
```

Each phase comes with a one-sentence brief in the prompt ("Breakdown:
mute the beat and/or bass, and make what's left more spacious."). Each
change of phase is logged with its reason, because the arc is a
decision too:

```
🧭 Arc: groove → breakdown (9 edits of groove, time for contrast)
```

The
target number of lines and the length of the groove phases are drawn at
random per track, so tracks differ in shape. When the outro leaves at
most one line, the track ends. The Director plans a new track (new
key, tempo and progression, related to the previous one), the old lines
are silenced, and the build starts again. With no fixed style, the
stream rotates between lofi, ambient, downtempo and piano from track to
track.

### 8.2 The prompt: teaching Tidal while playing

The prompt (`prompts/performer.md`) is built fresh for every edit. Early
versions gave the model names and ranges only: it saw
`off 0.25 (|+ n 12)` and `lpf 200-9000` but not what they sound like,
and its edits felt random. The prompt now teaches as it asks:

1. **Framing**: "You are performing a live-coding set in TidalCycles,
   like Switch Angel ... The audience sees the code and hears every
   change, and they read your notes to understand why," plus the stream
   theme and style.
2. **The program as code**: exactly what viewers see.
3. **Phase, brief and example moves for this phase**: local models copy
   good examples far more faithfully than they follow rules ("in a
   breakdown: mute the beat first; the space it leaves is the point").
4. **Its own recent edits**, the last six that actually played, with
   reasons: "build on these; don't undo a move you just made, and don't
   make the same kind of move three times in a row". Without this memory
   the model only sees the present and tends to loop or undo itself.
5. **A Tidal cheat sheet**: mini-notation (`~ [ ] < > * ! ? ,`), what
   scale degrees mean, and one line per wrapper and effect *on what it
   sounds like* (`jux rev`: a reversed copy in the other ear, wide and
   dreamy; `lpf`: low is dark and muffled, high is bright and open).
   Only the wrappers and effects that apply to the lines playing now are
   explained, which keeps the prompt small.
6. **Editable parts as compact JSON**: per line its slots and values, its
   effects, which effects it accepts and which wrapper group it takes.
7. **Addable blocks, operations, taste guidance**: aim for the richness
   of a real live-coded set (up to `max_lines` interlocking lines, things
   always moving), harmony first, and make each edit something a
   listener will notice (new notes, a rhythm, a layer, a sweep) rather
   than a reverb nudge. An earlier version said "prefer revising over
   adding"; section 13.1 explains why that went.
8. **Reply contract**: `listening`, `reason`, the operation, optional
   `narration`; or, in judge mode, three genuinely different proposals.

The system prompt is one sentence: *"You are a live coder performing
TidalCycles. Reply with JSON only."* For Qwen-family models, the local
provider turns off "thinking" mode in the chat template; otherwise the
model can spend thousands of tokens reasoning and miss its bar.

**Small context windows.** llama-server shares its context between
parallel slots, so `--ctx-size 8192` with two slots gives each request
4,096 tokens, and the full prompt with a busy program is about 4,000. If
the server reports an overflow, the performer switches to a compact
prompt for the rest of the run (wrapper names and ranges only, no
example moves; about 25% smaller) and retries at once. The style's
character is the last thing to be cut; the glossary is the first.

### 8.3 From reply to edit: keeping a local model on track

A 35B local model produced unusable edits often enough that the
built-in performer played much of the set. Four mechanisms fixed the
causes rather than the symptoms:

1. **Constrained decoding.** Every request to llama-server carries a
   JSON schema of the edits that are legal *right now*: the addable
   blocks, playing and muted roles, each line's pattern slots, the
   effects allowed on each role, controls, and wrappers not yet applied.
   llama.cpp compiles it into a grammar and decodes under it, so the
   model cannot produce broken JSON or a made-up name.

   ```python
   def variant(op, **props):     # one alternative of the schema's anyOf
       properties = {"listening": note, "reason": note, "op": {"const": op}, **props,
                     "narration": {"type": "string", "maxLength": 160}}
       return {"type": "object", "properties": properties,
               "required": ["listening", "reason", "op", *props], "additionalProperties": False}
   # e.g. variant("set_fx", role={"const": "keys"}, name={"enum": ["lpf", "room", "delay", ...]}, value=text)
   ```

   Each schema was checked with llama.cpp's own `json-schema-to-grammar`
   converter for 66 program states across all styles.
2. **Clamping instead of rejecting.** A number slightly out of range
   (`lpf 12000`, a tempo jump of +20) is pulled into range.
3. **One corrected retry.** If an edit still breaks a rule, the model is
   shown its reply and the reason it was rejected and answers again, if
   there is time before the bar.
4. **A deadline.** Each call should finish within 85% of the edit
   window at the current tempo. A local model gets at least 60 seconds
   (`YS_LOCAL_PERFORMER_WAIT`); if it needs longer than the window, the
   music keeps playing and its edit lands on the bar after the reply
   (section 9.2). A reply past the deadline counts as `late`, not as an
   error.

After each track the Performer reports how it went:

```
[Performer] last track: 27/31 edits by the LLM (built-in: 3 late, 1 invalid)
```

Mostly `late` means the model is too slow at this tempo; mostly
`invalid` means it struggles with the task. After three failures in a
row the built-in performer plays the next five edits, so a model that is
still loading never delays every bar.

For Claude and other hosted models (no grammar), `extract_json` finds
the first balanced JSON object in the reply, fenced or not, and the same
validation, clamping and retry apply.

### 8.4 The built-in performer

When the model is unavailable, or deliberately as a **baseline**, the
built-in performer chooses edits with simple, phase-aware randomness:

- **build**: start with one of the style's openers, then add missing
  roles in the style's order (lofi: beat, keys, bass, lead, texture,
  percussion): always until three lines play, then 85% of the time, so
  a club track reaches its full groove in two to four minutes.
- **breakdown**: mute beat, then bass, then make calm tweaks (darker
  filters, lower acid).
- **rebuild**: unmute bass, then beat.
- **outro**: remove lines one by one, keeping the texture for last.
- **groove**: sometimes add a missing role (25%) or nudge the tempo
  (5%); otherwise *tweak*.

A tweak picks a random line and a kind of change, weighted toward moves a
listener hears (three in seven are pattern edits, two in seven wrappers): a pattern edit
(most often changing one number to another from the block's degree
pool, a small motivic mutation), an effect edit (filter scaled by a
random factor, sometimes turned into a slow sine sweep, bitcrush toggled
on the beat), a control ride (the acid knob drifting up or down, which
direction depending on phase), or toggling a wrapper. Every candidate
goes through `apply_edit`, and the performer retries up to twelve times
until one is valid.

The result sounds acceptable, structured and in key, but it has no
intention behind it. That is exactly what a null model should be.

### 8.5 Generator and judge

In judge mode, one edit takes two calls. The Performer proposes up to
three different legal edits in one reply (the schema allows an array of
one to three), the built-in performer contributes up to three candidates
of its own, duplicates are dropped, the pool is shuffled, and every
option is described the same way, with no hint of its source:

```
e1: d2: rhythm -> 0 ~ 4 ~ 7 ~ 4 ~: walk the bass
e2: wrap d1 with `swingBy (1/12) 4`: loosen the drums (light swing: loosens a stiff groove)
e3: d4: # lpf -> 1800: darken the keys (low-pass cutoff in Hz)
...
```

The Judge, jevtypesafe's decision API, gets the situation (style, theme,
track, phase, the program, the recent edits) and a single `choice`
question with these options. It returns the choice, a confidence and a
probability per option. The chosen edit keeps the Performer's
`listening` and `reason` if it was the Performer's idea. If the Performer
fails, the Judge chooses among built-in ideas and that counts as the
Performer's failure, not as a judgement; if the Judge fails, the
Performer's first idea plays. A choice without a built-in alternative
(at the start of a track all ideas may be the same three `add` moves) is
marked "not a comparison" and left out of the statistics.



---

## 9. The live engine

### 9.1 The clock

Tidal keeps its own time in *cycles*. The engine mirrors it with a
`Clock` that converts wall-clock time into a cycle position and handles
tempo changes without drift. It does this by re-anchoring on every cps
change:

```python
class Clock:
    def cycle(self) -> float:
        return self.ref_cycle + (self._now() - self.ref_time) * self.cps

    def set_cps(self, cps: float) -> None:
        self.ref_cycle = self.cycle()          # re-anchor at the current position
        self.ref_time = self._now()
        self.cps = cps

    def seconds_until(self, cycle: float) -> float:
        return (cycle - self.cycle()) / self.cps
```

At the start of a set the engine sends `hush`, `setcps` and
`resetCycles` and resets its clock at the same moment, so both agree on
where cycle 0 is.

### 9.2 Thinking while the music plays

```python
while not self.stop_event.is_set():
    future = self._pool.submit(self.performer.next_edit, program)   # think while it plays
    self._wait_until(next_cycle)                                    # land on the bar
    edit = future.result()
    try:
        new = apply_edit(program, edit, self.performer.settings["max_lines"])
    except EditError as e:
        self.log(f"[Live] skipped invalid edit ({edit.describe()}): {e}")
        next_cycle += edit_bars
        continue
    statements = changed_statements(program, new)          # the edited line + any re-levelled beds
    if self._type_ok(statements) and self._send(self._with_fades(statements, new, edit)):
        program = new
        self.log(f"✎ [{edit.source}] {edit.describe()}   -- {edit.reason}")
        self._publish(program, highlight=edit.role, comment=edit.reason)
    next_cycle += edit_bars
```

The next edit is requested *as soon as* the previous one has been
applied, so the model has the full `edit_bars` window to answer: eight
bars at 80 BPM is 24 seconds, sixteen bars at 138 BPM about 28 seconds.
That is enough for a 35B mixture-of-experts model on a single consumer
GPU when thinking mode is off, and for the two calls of judge mode. A
slower local model may take longer: then the engine logs "the performer
is still deciding; the music plays on", waits for the reply, and applies
the edit on the next bar. Edits always land on a bar boundary (Tidal
re-evaluates at the next cycle anyway), so even a late edit sounds
intentional. While waiting, the engine writes a heartbeat file
every few seconds for the watchdog.

### 9.3 Defence in depth

An edit passes up to four gates before it is heard:

1. **Decode**: for a local model, the JSON schema makes illegal names
   impossible; for others, JSON extraction and op and block lookup.
2. **Validate**: `apply_edit` ranges and rules, after clamping numbers
   into range (in the performer, and again in the engine against the
   program at apply time).
3. **Type check (optional)**: each changed statement is compiled in a
   separate GHCi process running Tidal, as
   `:t ((<expr>) # orbit N :: ControlPattern)`, before it is sent. This
   is on whenever an LLM is performing and GHCi with Tidal is installed.
4. **Interpreter**: the live GHCi session's output is scanned for errors
   after every send. A rejected statement leaves the previous pattern
   playing, and the engine keeps the old program.

At every gate a failure costs one edit slot and nothing else.

### 9.4 Publishing and explaining

Every decision is visible in the terminal as it happens, and recorded:

```
🧠 Next piece: 'Neon Rain', darkwave, D minor, 118 BPM, i-VI-III-VII, late-night
   "Minor and unhurried suits a rainy city theme."
🗣  "Now playing: Neon Rain. Pull up a chair. The rain has a rhythm tonight."
💬 Performer → local/qwen-agentworld-35b: 3 ideas for 'Neon Rain' (groove) (7,962 chars)
💬 Performer ← local/qwen-agentworld-35b (4.1s): {"proposals": [...]}
💬 Judge → jevtypesafe/decide: choose the next edit for 'Neon Rain' (groove) from 6 options
💬 Judge ← jevtypesafe/decide (0.9s): {"edit": {"choice": "e2", "confidence": 0.26, "probabilities": {...}}}
⚖️  Judge chose the LLM idea (p 0.38, confidence 0.26, from 3 LLM + 3 built-in)
👂 hears: Kick, hats, bass and pad all lock to a perfectly straight grid; the groove is accurate but stiff.
✎ [LLM, judged] wrap d1 with `swingBy (1/12) 4`   -- A light swing loosens the 16ths under the hats ...
🧭 Arc: groove → breakdown (9 edits of groove, time for contrast)
💬 Narrator → local/qwen-agentworld-35b: a line for the breakdown
🗣  "Let the floor fall away for a moment and just breathe."
```

The `→` line shows which agent asked which model for what; the `←` line
shows the reply as it came back and how long it took; `✎` shows the edit
with who chose it (`[LLM]`, `[built-in]`, `[LLM, judged]`,
`[jevtypesafe]`). `--show-llm` also prints every full prompt and raw
reply, and `runtime/llm_calls.jsonl` keeps the full prompt and reply of
every call (agent, model, seconds, reply or error) for auditing. A line
the Narrator does not speak says why (`(skipped: last line 42s ago)`,
`(not spoken: numbers or technical words)`).

After each successful edit the engine also:

- writes the on-screen **overlay**: a now-playing line (title, key,
  tempo, phase) and the program's code with the `▶` marker and the
  reason as a trailing comment. Both files are written atomically, and
  ffmpeg re-reads them every frame.
- appends the edit to `runtime/thoughts.log` (human-readable) and
  `runtime/history.jsonl` (structured: `source`, `listening`, `op`,
  `role`, `name`, `value`, `reason` and the full program afterwards).
- saves `runtime/state.json` (program, performer state, style), so a
  restarted engine resumes mid-track with `--resume` instead of starting
  over.
- hands any `narration` to the narrator, and, if the arc moved on, asks
  the Narrator agent for a line about the moment.

What viewers see:

```haskell
setcps (84/60/4)

  d1 $ swingBy (1/8) 4 $ s "[bd ~ [~ bd] ~ ~ ~ bd ~, ~ sn ~ sn, [hh27 hh27:3]*4]" # gain 0.92 # lpf 5000
  d2 $ n (scale "major" ("0 ~ ~ 0 ~ ~ 4 ~" |+ "<0 -2 3 -3>") + 3 - 24) # s "ys_sub" # gain 0.95 # lpf 500
  d4 $ jux rev $ struct "t ~ ~ t ~ ~ ~ ~" $ n (scale "major" "<[0,2,4,6] [-2,0,2,4] ...>" + 3) # s "ys_keys"
▶ d5 $ n (scale "major" ("~ 4 ~ ~ 2 ~ ~ ~" |+ "<0 -2 3 -3>") + 3 + 12) # s "ys_glass" # delay 0.4
  d7 $ slow 4 $ note "0" # s "ys_vinyl" # gain 1.1 # hpf 300 # room 0.35

-- let the melody echo into the space
```

---

## 10. The LLM gateway

### 10.1 Providers

All text-model calls go through `llm.converse(agent, model_id, system,
user, schema=...)`, which logs the exchange and records it, around
`llm.complete`. Model ids are `<provider>/<model>`:

| Id | Provider | Transport |
|---|---|---|
| `local` or `local/<model>` | llama.cpp `llama-server` (default) | OpenAI-compatible `/chat/completions` over plain HTTP |
| `claude/<model>` | Anthropic API | official SDK |
| `jevtypesafe` | jevtypesafeai.com decision API | `POST /api/v1/decide` with a `state` and typed questions (`choice`, `score`, `noul`); used as Performer (choosing among legal candidates) or Judge, via `llm.decide()` |
| anything else | LiteLLM passthrough | e.g. `ollama/...`, other hosted APIs |

The local path needs no extra packages:

```python
body = {"model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},     # Qwen: no hidden reasoning
        "response_format": {"type": "json_schema",               # decode under the legal-moves schema
                            "json_schema": {"name": "reply", "schema": schema}}}
resp = requests.post(f"{LOCAL_LLM_API_BASE}/chat/completions", json=body, timeout=timeout)
```

If a server rejects schemas, the request is repeated without one and
later calls skip it; a context-window overflow (also HTTP 400) is not
mistaken for that and gets its own explanation instead.

Because the interface is the common denominator, any local server works:
`llama-server`, vLLM, LM Studio, or Ollama's `/v1` endpoint. Swapping
models for an experiment is a command-line flag.

### 10.2 Failure containment

`complete` runs every call in a worker thread with a hard timeout (60
seconds by default, 180 for the local model, which can take minutes on a
long planning prompt). It turns *every* failure (connection refused, HTTP
503 while a model loads, bad JSON envelope, authentication, even crashes
inside native extensions) into a single `LLMError`. Callers only need to
handle one exception type, and nothing an LLM backend does can take
down the music thread.

### 10.3 Local server lifecycle

If the configured model is local and nothing answers on the port, the
orchestrator launches `llama-server` itself: the GGUF file from `.env`,
all layers on the GPU, a 16k context shared by two parallel slots
(`LLAMA_CTX_SIZE`, `LLAMA_GPU_LAYERS` and `LLAMA_SERVER_ARGS` adjust it;
8-bit KV cache and flash attention roughly halve the cache's memory). It starts
the server *before* booting SuperCollider and Tidal, so the model loads
while the audio stack starts. It then polls `/health` (503 while
loading, 200 when ready) for up to five minutes, showing how much of the
model file has been read and which loading stage the server is in. If
the server dies while loading (typically the GPU running out of memory
for the KV cache), the end of its log and a likely fix are printed at
once. If the model still isn't ready, the built-in performer plays until
it is. The stream never waits on the model.

---

## 11. The audio and streaming stack

- **Tidal session.** A persistent GHCi process loads `BootTidal.hs`,
  which connects to SuperDirt on port 57120 and defines the usual
  helpers (`d1`..`d12`, `hush`, `setcps`, `all`, `resetCycles`). One
  lock serialises writes, so the engine, the narrator and a watchdog
  replay never interleave. Output is scanned for GHCi error signatures
  after each send.
- **SuperCollider.** `sclang superdirt_boot.scd` boots the server, starts
  SuperDirt with 12 orbits, loads the custom SynthDefs and appends the
  master chain at the tail of the node tree:

  ```supercollider
  SynthDef(\ys_master, {
      var sig = In.ar(0, 2);
      sig = Compander.ar(sig, sig, thresh: 0.25, slopeBelow: 1, slopeAbove: 0.5,
                         clampTime: 0.02, relaxTime: 0.25);
      sig = Limiter.ar(sig * 1.4, 0.89, 0.01);
      ReplaceOut.ar(0, sig);
  }).add;
  ```
- **Narration.** A Piper TTS voice speaks the Director's and Narrator's lines through the
  stream's audio sink, loudness-normalised to the stream target plus a
  small lift. At most one line is queued, lines are at least a minute
  apart, and while the voice speaks the whole Tidal mix is ducked with
  `all (|* gain 0.85)` and restored with `all id`.
- **Streaming.** `stream/stream.sh` runs ffmpeg with a background image
  or video, an audio waveform and three `drawtext` layers (now playing,
  code, narration) that reload their text files every frame. The
  captured audio is encoded and pushed over RTMP to YouTube. ffmpeg is
  restarted with backoff if it exits.
- **Watchdog.** A separate process watches for sustained silence on the
  stream sink and for a stale engine heartbeat. When either happens, it
  stops the engine, and `stream/live.sh` starts it again, resuming from
  `state.json`. Restarts
  are rate-limited and can send a phone notification.

---

## 12. Testing creativity: the protocol

Because every move is legal and the built-in performer draws from the
same move space, performers can be compared on equal terms. We propose
four groups of measures, all computable from the engine's logs.

### 12.1 Competence: can it play at all?

- **Validity rate**: the share of edits the model actually played. The
  per-track report gives it directly (`27/31 edits by the LLM (built-in:
  3 late, 1 invalid)`), and `history.jsonl` records the `source` of every
  edit. Below about 70%, the fallback does most of the playing and the
  other measures describe the baseline, not the model.
- **Punctuality**: the share of replies that arrive before their bar:
  the `late` count in the same report; every `←` line shows the reply
  time.

### 12.2 Divergence: does it produce ideas?

- **Operation entropy**: Shannon entropy over `op`. A performer that
  only rides `lpf` scores near 0; one that uses all eleven operations
  evenly scores about 3.46 bits.
- **Novel material**: distinct pattern values the model writes that
  appear neither in the block library nor earlier in its own history.
- **Self-repetition**: the share of edits that repeat an identical
  `(op, role, name, value)` from the previous *k* edits, or undo one
  (A→B→A).

### 12.3 Coherence: do the ideas fit together?

- **Phase fit**: does it strip back in the breakdown and add during the
  build? Score each edit against its phase brief with a simple rule
  table.
- **Motivic development**: the edit distance between successive values
  of the same melodic slot. Small distances mean development, large ones
  mean replacement. A musical performer shows both, and mostly the first.
- **Reason consistency**: does `reason` match the edit? "Darken" should
  lower `lpf`, "open the acid" should raise `acid`. Check with rules for
  the common verbs, and with an LLM judge or a human for the rest.
- **Breadth of attention**: the distribution of edits over roles. A
  performer that edits only the lead is ignoring the ensemble.

### 12.4 Preference: does a blind judge prefer its ideas?

Judge mode gives a measure that needs no listening panel. For every
edit, the model's ideas and random-but-legal ideas compete in front of a
judge that cannot see where they came from. Two numbers per track:

- **Preference rate**: how often the judge chose the model's idea.
  Chance is roughly the share of model ideas in the pool (about 40-50%).
- **Probability mass**: the average share of the judge's probability
  given to the model's ideas, which also captures near misses.

```
[Performer] the judge preferred the LLM's idea 14/20 times (70%) over the built-in
            candidates; on average it gave them 61% of its probability
```

The judge has its own taste and biases, so this measures agreement with
one judge, not absolute quality; comparing several models under the same
judge, and the same model under several judges, separates the two.

### 12.5 Taste: is it any good?

Taste needs ears. Run **blind A/B listening sessions**: the same style,
seed and duration, one set played by the built-in performer and one by
the model under test (or two models against each other). Listeners pick
the one they would keep listening to. A 24/7 stream adds a naturally
occurring signal on top: how long viewers stay, broken down by which
performer was live.

### 12.6 A minimal scorer

```python
import json, math
from collections import Counter

def score(history_path="runtime/history.jsonl", k=6):
    edits = [json.loads(line) for line in open(history_path)]
    edits = [e for e in edits if "op" in e]
    ops = Counter(e["op"] for e in edits)
    total = sum(ops.values())
    entropy = -sum(n / total * math.log2(n / total) for n in ops.values())
    key = lambda e: (e["op"], e["role"], e["name"], e["value"])
    repeats = sum(key(e) in {key(p) for p in edits[max(0, i - k):i]}
                  for i, e in enumerate(edits))
    reasons = sum(bool(e.get("reason")) for e in edits)
    by_llm = sum(e.get("source", "").startswith("LLM") for e in edits)
    return {"edits": total, "op_entropy_bits": round(entropy, 2),
            "repeat_rate": round(repeats / total, 2),
            "reasoned": round(reasons / total, 2),
            "by_llm": round(by_llm / total, 2)}
```

### 12.7 Running an experiment

`--dry-run` replaces the audio stack with a printer and the clock with a
simulated one. With the built-in performer, an hour-long set runs in
seconds. With an LLM, it runs as fast as the model can reply. Fix the
style and seed, run each performer for the same number of tracks, score
each run, and listen to the interesting ones live:

```bash
# baseline: the built-in random performer
python orchestrator.py --dry-run --style lofi --llm none --seed 7 --pieces 3
# candidate: a local model served by llama.cpp on :8080
python orchestrator.py --dry-run --style lofi --llm local --model qwen-agentworld-35b --seed 7 --pieces 3
# the same model as generator, with a blind judge
python orchestrator.py --dry-run --style lofi --llm local --judge jevtypesafe --seed 7 --pieces 3
# the same model, live, with its raw replies printed
python orchestrator.py --style lofi --llm local --show-llm
```

A model counts as **creative in the sense tested here** when it beats the
built-in baseline on coherence and taste without falling behind on
divergence. Beating the baseline on divergence alone is easy (random
choices are diverse) and proves nothing.

---

## 13. What went wrong, and how it was fixed

The architecture above was not designed in one go. Most of it is the
residue of problems that surfaced while the system was playing, usually
first noticed by ear. They are worth recording because they share one
pattern, which is the main result of the project so far:

> **Every step that handed more of the music to the model made the music
> worse, and most fixes restored quality by moving a decision back into
> code.**

That is a tension with both goals of the project: music that is not "all
precoded", and decisions whose reasons can be seen. The fixes below keep
the model in charge of *judgement* (what to change, when, and why) while
code keeps the *vocabulary* musical. Whether that is the right split is
exactly what the protocol in section 12 measures.

### 13.1 The artefact made the music simpler

**Symptom.** Before the live engine, a Director planned whole
arrangements and the engine rendered them at once. When the program
itself became the artefact, grown and revised one edit at a time, the
music got noticeably thinner. Set against a reference like Switch
Angel's layered acid trance (two 16th-note acid lines, a sidechained
kick, a filter knob that never stops moving), it sounded like a sketch.

**Diagnosis.** A simulated trance set on the old code showed why:

- It took about 18 edits (roughly eight minutes) to reach five lines, and
  the full mix barely lasted before the outro.
- Most edits were nudges nobody hears: `room 0.1 → 0 → 0.1`,
  `delayfeedback 0.35 → 0.2`.
- Patterns stood still. The lead played `0 ~ ~ 2 ~ ~ 0 ~`, the same three
  notes every bar; the acid knob sat at `0.546` for the whole track.
- There was one line per role, so no layering.
- The prompt itself asked for caution: "prefer revising over adding",
  "never make the track busier".

**Fix.**

- **A ninth role, `arp` (`d9`)**, for club styles: a second acid an
  octave up, or a 16th-note pluck or glass sequence, pumping with the
  kick.
- **Lines that move by themselves.** The acid knob and arp filters ride
  slow sines by default (`pF "acid" (slow 16 $ range 0.35 0.75 sine)`),
  and leads get phrases that change from bar to bar
  (`"<[0 ~ 4 ~ 7 ~ 4 ~] [9 ~ 7 ~ 4 ~ 2 ~] ...>"`). Two new wrappers,
  `every 4 (|+ n 12)` and `chunk 4 (fast 2)`, add phrase-level
  variation.
- **A faster build to a fuller peak.** Club styles peak at seven lines;
  during the build a line is added almost every edit; built-in tweaks
  favour notes, rhythms and wrappers over effect nudges.
- **A prompt that asks for density**, per style, instead of caution.

**Result.** In simulation, club styles reach their groove in two to four
minutes, with seven interlocking lines including a 16th-note layer and
at least one sweep. A test pins this down. The stream page had to follow:
seven long lines no longer fit the code panel, so the code now shrinks
until every line shows.

### 13.2 Harmony

**Symptom.** "The agents introduced a completely unmatched clap sound.
It's extremely annoying." The first diagnosis blamed the sample and
removed the clap. The listener corrected it: *the problem is not the
clap, but that it's not in harmony.*

**Diagnosis.** Melodic patterns are scale degrees, so every note was in
the key, but in key is not in harmony. Degrees 1, 3 and 5 (the second,
fourth and sixth of the chord) clash with the chord under them when they
repeat or sit on a strong beat. One bug made it worse: the techno acid
motifs had been written as semitones, not degrees.

**Fix.** A rule, enforced by the validator (`harmony_problem`) and
explained in the prompt:

- bass, acid and arp lines, and any repeating pattern (`*`, `<>`,
  `[]`), use chord tones only (0, 2, 4, 6 and their octaves);
- melodies put chord tones on the strong steps and other notes only in
  between;
- drum lines may only use the samples of their kit, so a track keeps one
  coherent sound.

Each block's degree pool follows the same rule, so the built-in
performer cannot break it either. The clap came back.

### 13.3 The mix

**Symptom.** "Strings were added but the volume was too loud."

**Fix.** Levels are measured, not guessed (section 6.3): every synth's
loudness was measured offline, each role has a target level, a line
enters at the gain that meets its target, edits may only move gain by
±3 dB, lines fade in over four bars, and beds step back when the front is
busy.

### 13.4 Local models on one GPU

Most operational trouble came from running a 27–35B model on a single
consumer GPU next to SuperCollider.

| Problem | What it looked like | Fix |
|---|---|---|
| Out of memory at load | The model loaded to 100%, then the server died with `cudaMalloc failed` | The context default went down to 16,384 tokens; the orchestrator reads llama-server's log when it stops and prints the likely fix (smaller context, quantised KV cache, fewer GPU layers) |
| Context overflow | `4105 tokens > 4096` | The prompt explains only the wrappers and effects that apply now; if it still doesn't fit, the performer switches to a compact prompt for the rest of the run |
| Hidden "thinking" | Replies that took ages and began with `<think>` | Thinking is turned off in the chat template; if a model thinks anyway, the reply is cleaned and the log warns, with the fix (`--reasoning-budget 0`) |
| Slow replies | `Director ← local (60.1s): ✗ Read timed out`; the Performer gave up after 11 s | Every reply logs its speed (`210 tokens at 22 tok/s`); a timeout says the model was too slow and what to check; the local model alone gets longer (180 s per call, 60 s per edit), and a late edit lands on the next bar instead of being dropped |
| Unusable edits | Wrong names, numbers slightly out of range | Output forced into a JSON schema of the legal moves, numbers clamped into range, one corrected retry (section 8.3) |

The slow-reply fix has a cost the logs make visible: a slow model still
plays, but makes fewer edits. The faster fix was a model choice: a
mixture-of-experts model runs only part of its weights per token and
answered comfortably inside the edit window where a dense 27B model did
not.

### 13.5 Style

**Symptoms.** "I asked for hardcore techno but it started slow." A
darkwave track was planned at 76 BPM. A model opened a darkwave track
with "cold sustained pads" when the genre lives on the sequencer bass and
drum machine.

**Fixes.** Techno, hardcore and darkwave became styles with their own
blocks and tempo ranges. The theme's keywords choose the style, and the
Director's tempo and key are checked again after the style is fixed. The
deeper problem, that the models have little feel for what makes a style
itself, is still open (section 15).

### 13.6 Keeping the agents in the room

**Symptoms.** Moving to the live engine silently dropped the Director
and the Narrator. With a judge, the performer was consulted so often
that the music never settled. Later the Narrator was too loud, and then
lost altogether.

**Fixes.** The Director plans each live track again, and the Narrator
speaks at arc moments (build to groove, the breakdown, the second
peak). Edits come every 8 to 16 bars instead of every 4. The narrator's
voice is normalised to the stream's loudness (-14 LUFS) with an
adjustable offset (`YS_NARRATION_VOLUME_DB`), the music ducks while it
speaks, and a skipped line is logged with its reason.

### 13.7 Trust: "so I know this is not all precoded"

**Symptom.** The first runs printed only the result of each decision.
The listener could not tell the model's choices from the code's.

**Fix.** Every exchange with every agent is logged as it happens
(`💬 Performer → local/...` and the reply, with its time and speed), and
also written to `runtime/llm_calls.jsonl`. Every edit is tagged with who
chose it (`LLM`, `LLM, judged`, `built-in`, `built-in, judged`), and the
stream page shows what each agent heard, did and why. That turned an
objection into the project's second goal: understanding *why* each agent
decided matters as much as pleasant music.

### 13.8 Running it 24/7

Smaller problems, each of which stopped a stream:

- An API key in `.env` was not picked up: an empty leftover entry from
  `.env.example` overrode it. The loader now skips empty entries and
  warns on lines it cannot read, instead of failing silently.
- A leftover SuperCollider process from a crashed run held UDP port
  57120, so SuperDirt could not start; it is now found and stopped
  automatically.
- A set of systemd units was more than one person needed; `stream/live.sh`
  now starts the audio sink, engine, stream and watchdog with one
  command, and restarts the engine, which resumes the saved track.

### 13.9 What the problems add up to

Read together, the fixes draw the boundary of the model's creativity in
this system. Code decides what is *possible*: the blocks, the harmony
rule, the kit, the level window, the fallbacks. The model decides what
*happens*: which line, which notes, when, and why. Each problem above
moved that boundary, almost always toward code. The honest measure of
the result is the share of edits the model actually makes and the share
of blind comparisons it wins against random ideas (section 12.4), which
the Performer prints after every track. If that share holds up as the
vocabulary grows richer, the model is doing more of the music; if it
falls, the richness is coming from the code.

## 14. Design lessons

- **Constrain the vocabulary, not the imagination.** Scale-degree slots,
  curated blocks and ranged effects remove whole classes of errors while
  leaving thousands of distinct musical moves open. Models that could
  not write a valid Tidal track on their own can perform sensibly inside
  this frame.
- **Make the code the artefact.** Coarse planning roles (the Director)
  produced correct but lifeless music. Making the program itself the
  thing the model grows and revises made both the music and the model's
  decisions legible.
- **Frequent, small, attributable decisions.** They sound like
  performance and make evaluation possible.
- **Fallbacks turn failures into data.** Continuity is what makes
  long-horizon testing possible at all.
- **Ask for reasons.** One line of intent costs a few tokens and is the
  most useful single artefact for judging whether a model "meant" what
  it played.
- **The validator is also the security boundary.** When model output
  drives a live interpreter, whitelists are not optional.
- **Fix failures at the source.** A local model's unusable replies were
  mostly malformed names and slightly-off numbers. Constraining decoding
  with a schema of the legal moves, clamping numbers and allowing one
  corrected retry did more than any prompt wording.
- **Teach the language in the prompt.** A model that only sees
  `off 0.25 (|+ n 12)` has to guess what it sounds like. One line per
  wrapper and effect on its *sound*, example moves per phase, and the
  model's own recent edits made the edits noticeably more purposeful.
- **Explanations are a product, not a debug aid.** Showing what each
  agent heard, did and why, and tagging every edit with who chose it, is
  what lets a human trust (or distrust) the music, and it is the data a
  creativity evaluation needs.
- **Measure, don't guess, the mix.** Hand-set levels were 4-8 dB off for
  sustained beds; measuring every synth offline and deriving gains from
  targets fixed what listeners complained about. Fades and ducking make
  an addition sound like part of the song rather than a switch.
- **A judge needs a generator.** A decision model alone can only pick
  the best of random ideas; paired with a generator it becomes both a
  source of taste and a blind measure of the generator's ideas.
- **Listen, then measure.** Every major fix here started with a listener
  saying what was wrong ("too loud", "not in harmony", "much simpler"),
  and every one ended with a number that pins it down (a level target, a
  chord-tone rule, lines at the peak). The first diagnosis was often
  wrong; the ear was not.
- **Scaffolding is a trade.** Each guard-rail that made the music better
  took a choice away from the model. Track the model's share of the
  decisions alongside the quality of the music, or the code quietly
  becomes the composer.
- **Budget the context.** A prompt that grows with every useful addition
  eventually overflows a small local context window; describing only what
  applies now, and degrading gracefully to a compact prompt, keeps the
  performer playing.

## 15. Limitations and extensions

- **Curated palette.** The test measures compositional and performative
  creativity within a given sound world, not sound design. An extension
  could expose a few SynthDef parameters as additional controls.
- **Imposed arc.** The phase machine proposes each track's shape. Letting
  the model decide phase transitions ("I think it's time for a
  breakdown") would test structural creativity too.
- **Single-move replies.** One edit per call keeps things attributable
  but prevents coordinated moves (mute the beat *and* open the filter).
  A small "combo" operation is a natural next step.
- **Style knowledge.** The model has little feel for what makes darkwave
  darkwave or techno techno: in one run it opened a darkwave track with
  "cold sustained pads" when the genre is carried by the sequencer bass
  and drum machine. The planned fix (`docs/STYLE_KNOWLEDGE_PLAN.md`) is a
  short style guide per style in the prompt, a role description per
  block, style-specific example moves, and a judge that scores style fit.
- **Memory.** The model sees its last six edits, not the whole set.
  Longer-range development (a motif introduced early and recalled at
  the end) would need a summary of the track so far.
- **Short-sound calibration.** The mixer calibrates sustained sounds from
  measurements; drums, short chord hits and fast 16th-note streams still
  use tuned gains, because a single test note does not measure them well.
- **Gameable metrics.** Divergence measures reward randomness, which is
  why the random baseline is required and why listening remains the
  final judge.

## Glossary

- **Cycle**: Tidal's unit of musical time; here one 4-beat bar. `cps` is
  cycles per second (`bpm / 60 / 4`).
- **Mini-notation**: Tidal's compact pattern strings, e.g. `"bd ~ [sn sn]"`,
  `"<0 2 4>"`, `"hh*8"`.
- **Orbit**: a SuperDirt effect bus; each channel `dN` uses its own.
- **Wrapper**: a higher-order function applied to a whole line, e.g.
  `jux rev`.
- **Scale degree**: a step within the current scale; with `scale "minor"`,
  degree 2 is the minor third.
- **LUFS**: loudness units relative to full scale (EBU R128), the
  loudness measure the mixer calibrates against.
- **`xfadeIn`**: a Tidal transition that crossfades a channel from its
  current pattern to a new one over a number of cycles; used for fades.
- **Constrained decoding**: generating only tokens that keep the output
  valid under a grammar, here compiled by llama.cpp from a JSON schema.
- **Blind judge**: a model that chooses among options without knowing
  which agent proposed which.

## See also

- TidalCycles documentation: <https://tidalcycles.org/docs/>
- Reference implementation, [`jsemrau/ystreamer`](https://github.com/jsemrau/ystreamer):
  `music/livecode.py`, `music/blocks.py`, `music/mix.py`,
  `music/performer.py`, `music/director.py`, `music/live_engine.py`,
  `music/llm.py`, `prompts/performer.md`, `tidal/synthdefs.scd`,
  `eval/synth_levels.sh`, `docs/STYLE_KNOWLEDGE_PLAN.md`
- The 24/7 stream: YouTube **@sleeplesscomposer**
