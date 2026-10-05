# ystreamer

Endless, AI-directed chill music made with [TidalCycles](https://tidalcycles.org),
built to run 24/7 as a YouTube live stream.

**The code is the artefact.** Like a live coder on stage (think Switch
Angel), a performer builds a short Tidal program up line by line (drums,
then bass, keys, a melody, a texture) and then keeps *revising* it: one
small, audible edit every few bars. It changes a rhythm, rides a filter,
wraps a line in `jux rev` or `off 0.25 (|+ n 12)`, opens the acid knob,
mutes the beat for a breakdown, brings it back, and strips the program
down again for the outro before the next track starts. The program shows
on screen with the line just edited marked `▶` and the performer's reason
as a `--` comment. See [Live coding](#live-coding) below.

The older section-based engine is still there (`--engine arranger`), and
the description below applies to it:

**The LLM directs, the engine plays.** An LLM Director makes musical
decisions (next piece, key, tempo, progression, and which instruments play in
each section) as validated JSON. A deterministic harmony engine turns those
decisions into Tidal code that is in key by construction: shared chord
progressions, voice-led chords, bass on the roots, and melodies with chord
tones on the strong beats. Changes land exactly on phrase boundaries. If the
LLM is slow, down, or talking nonsense, the engine keeps going with its own
plan, so the music never stops.

See [docs/IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) for the reasoning
behind the design.

## Quick start

```bash
./install.sh                                  # ghc + Tidal, SuperCollider + SuperDirt, ffmpeg, venv
./venv/bin/python orchestrator.py --selftest  # type-check every generated pattern with real GHCi
./venv/bin/python orchestrator.py --dry-run --style lofi   # see the code it would play (no audio)
./venv/bin/python -m eval.preview --style lofi --out preview.wav   # rough audio sketch, no SuperCollider

stream/setup_audio.sh --monitor               # create the stream sink and listen to it
./venv/bin/python orchestrator.py             # play forever, directed by the local model (default)
./venv/bin/python orchestrator.py --llm claude --free-layer
```

## Choosing the LLM

`--llm` (or `YS_LLM` in `.env`) picks who directs the music. The default is `local`. `--model` overrides the provider's default model.

| `--llm` | Provider | Setup |
|---|---|---|
| `claude` | Claude via the official Anthropic SDK (default `claude-opus-5`) | `ANTHROPIC_API_KEY` in `.env`, or `ant auth login` |
| `local` (default) | local llama.cpp server (default `qwen-agentworld-35b`) | `LLAMA_SERVER_BIN`, `LLAMA_GGUF_MODEL`; started automatically |
| `jevtypesafe` | [jevtypesafeai.com](https://jevtypesafeai.com/how-to-use) decision API: picks each live edit from a list of legal candidates | `YS_JEV_API_KEY` (live engine only) |
| `none` | deterministic director, no LLM | nothing |

```bash
python orchestrator.py --llm claude --model claude-sonnet-5
python orchestrator.py --llm local
python orchestrator.py --llm jevtypesafe --model my-model
```

Claude requests enable the API's server-side refusal fallback (`fallbacks="default"`), so a declined request is retried on a fallback model instead of failing. Whatever the provider, a failed, slow or invalid answer just means the Director uses its deterministic plan for that decision.

### How the local model is kept on track

- **Constrained decoding:** every request to llama-server carries a JSON
  schema built from the current program (addable blocks, playing roles,
  each line's pattern slots, effects, controls and wrappers). llama.cpp
  decodes under it, so the model cannot produce broken JSON or made-up
  names. Servers that don't support schemas get a plain request.
- **Reason first:** the schema puts `reason` before the operation, so the
  model states its intention before choosing the move.
- **Clamping:** a number slightly out of range (`lpf 12000`, a tempo jump of
  +20) is pulled into range instead of throwing the edit away.
- **One corrected retry:** if an edit still breaks a rule (e.g. a melody
  written as note names), the model is told why and gets one retry, if
  there is time before the edit is due.
- **Deadline:** each call has to finish before its edit is due (8 bars at
  138 BPM = ~14 s, 4 bars at 80 BPM = 12 s). A late reply is dropped and the
  built-in performer plays that edit.
- After each track, the terminal shows how it went, e.g.
  `[Performer] last track: 27/31 edits by the LLM (built-in: 3 late, 1 invalid)`.
  Mostly `late` means the model is too slow at this tempo (try a faster
  model or a lower `LLAMA_CTX_SIZE`); mostly `invalid` means it is struggling
  with the task.

### Cheapest Claude model for the performer

The performer's calls are small (about 1,500 input tokens and 80 output
tokens, roughly 320 calls an hour for lofi), and every edit is validated, so
**Claude Haiku 4.5** is enough and also the fastest:

```bash
./venv/bin/python orchestrator.py --style lofi --llm claude --model claude-haiku-4-5
```

| Model | $ / 1M tokens in / out | Approx. per hour of lofi |
|---|---|---|
| `claude-haiku-4-5` | 1 / 5 | ~$0.60 |
| `claude-sonnet-5` | 2 / 10 | ~$1.20 (+ thinking) |
| `claude-opus-5` (default) | 5 / 25 | ~$3 (+ thinking) |

If many edits show `[built-in]` or `LLM edit unusable`, move up to `claude-sonnet-5`.
Set `ANTHROPIC_MODEL=claude-haiku-4-5` in `.env` to make it the default.

## Choosing the style and mood

There are two options, and you can combine them:

- **Style** (`--style`) is the *genre*: it fixes the tempo range, the keys
  and modes, and the instruments (blocks) the performer can use. It is a
  hard rule the engine enforces.
- **Theme** (`--theme`) is a free-text *brief* for the LLM: mood, imagery,
  titles, and which of the style's moves it prefers. It is a suggestion the
  model interprets. If the theme names a genre ("hardcore techno"), it also
  picks the style (table below); if it doesn't, the stream rotates between
  the chill styles.

**`--style`** fixes the musical style for the whole run:

```bash
./venv/bin/python orchestrator.py --style lofi
```

The choices are `lofi`, `ambient`, `downtempo`, `piano`, `deephouse`, `trance`,
`techno` (138-150 BPM: pounding kick, offbeat rumble bass, 16th hats, acid,
short electric-piano stabs), `hardcore` (160-185 BPM: the same kit with a
distorted gabber kick) and `darkwave` (100-140 BPM coldwave / post-punk /
minimal synth: a drum machine with a gated snare, a chorused bass or a
16th-note sequencer bass, a chorused clean guitar arpeggio, string-machine
chords, a cold square lead lost in delay).
Each sets a tempo range, the modes it uses (e.g. minor or dorian for lofi),
the default instruments and which layers come in first. `deephouse` is
118-124 BPM with a four-on-the-floor kick, off-beat hats, short chord stabs
and a syncopated sub bass, and it opens with the groove rather than a pad.
`trance` is modelled on Switch Angel's live-coded acid trance (Strudel):

```js
n("<0 4 0 9 7>*16").scale("g:minor").trans(-12)
  .s("sawtooth").acidenv(slider(0.546))   // lpf(100).lpenv(x*9).lps(.2).lpd(.12)
s("tbd:2!4").duck("3:4:5:6").duckdepth(.8).duckattack(.16)
```

- **The acid line** (`ys_acid`) is the heart of it: a raw saw through a
  resonant low-pass at 100 Hz, swept up by `acid × 9` octaves on every note
  (decay 0.12 s to a sustain of 0.2). It plays 16ths of a short motif in
  that `0 4 0 9 7` shape, 3, 5 or 7 steps long, so it drifts against the
  bar, relative to each chord's root so it follows the harmony. The `acid`
  knob is automated like a hand on the slider: drifting in the groove,
  opening across builds, turned down in breakdowns (where the line keeps
  going under the pad).
- **Sidechain:** while the kick plays, everything pumps: every layer gets
  the same gain shape on each beat.
- **Around it:** a 136-140 BPM pitch-swept synth kick with off-beat hats and
  claps, a second higher acid line, offbeat M1 piano stabs and a glass lead, pumping
  on the kick. Pieces go groove → breakdown → build (roll,
  filter rise, kick drop) → drop, twice.

**No saw or organ sounds.** The synths built on detuned saws (`ys_supersaw`,
`ys_gatesaw`, `ys_pad`, `ys_strings`, `ys_choir`) are still defined but never
played: not in the live blocks, the styles' defaults or palettes, or the
Director's choices (`UNWANTED_PRESETS` in `music/plan.py`). Chords are
pianos (M1 house piano, DX7/Rhodes electric piano) or bell; a pad is never
the main instrument: the soft pad (`ys_softpad`: triangles and sines) and
air only play as a quiet bed far behind (texture); leads use glass. The
organ (`ys_organ`) is out too. The acid lines (`ys_acid`, `ys_tbass`) stay.
The gated saw's gate chops sustained chords inside the synth. The gate is
remapped to 0…1 so "closed" is silent, and `gatetime` (one bar in seconds)
locks it to the tempo. It's either a
straight 16th chopper (`PulseDPW(freq, mul: 2).range(0, 1)`, or a smoothed
`LFPulse` without sc3-plugins) or a stutter envelope built from a 16-step
pattern, retriggered every bar.

With `--style`, every piece stays in that style, including the LLM's
choices. Without it, the style rotates between the chill styles (deep house
only plays when asked for).

**`--theme`** gives the Director a free-text brief:

```bash
./venv/bin/python orchestrator.py --theme "rainy late-night jazz lounge, mellow and warm"
```

The LLM reads it for every decision (each piece and each section), so it
shapes the mood, keys, tempo, titles and instruments it picks.

If the theme names a style and you didn't pass `--style`, that style is
used for the whole run, even with the built-in planner:

| Theme words | Style |
|---|---|
| hardcore, gabber, speedcore, frenchcore, terrorcore, rotterdam | `hardcore` |
| darkwave, dark wave, coldwave, cold wave, cold electro, minimal wave, minimal synth, post-punk, ebm | `darkwave` |
| techno, hard techno, acid techno, industrial, rave, berghain | `techno` |
| trance, switch angel, uplifting, psytrance, supersaw | `trance` |
| deep house, house, garage, club, dance | `deephouse` |
| lofi, lo-fi, hip hop, study, beats | `lofi` |
| ambient, sleep, drone, meditation, space | `ambient` |
| downtempo, trip hop, chillout | `downtempo` |
| piano | `piano` |

So `--theme "frankfurt deep house"` plays deep house, and the LLM uses the
rest ("frankfurt") for mood and titles. `--style` always wins over the theme.

**The style is the frame, the theme the details.** The style and its
subgenre set the tempo, the drum kit, the bass, which parts play and how a
track is built; the theme picks the sounds, the key, the titles and the mood
inside that. Every sound has a character (`blocks.SOUND_CHARACTER`: the wave
lead is dark, dreamy, melancholic, cinematic; the pluck bright, warm,
dreamy), which the Director reads, and the built-in planner matches the
theme's words against (`plan.THEME_MOODS`: "Vampyre Nights in London" asks
for dark, "Summer sunrise in Ibiza" for bright and warm). The subgenre's own
sounds come first; only when none of them fits the theme does a lead or arp
reach for another style's voice (chords never do). No track repeats one of
the last three tracks' sets of sounds.

Combined, for example a fixed style with a mood brief:

```bash
./venv/bin/python orchestrator.py --style ambient --theme "slow sunrise over the ocean, hopeful"
```

To make them the default (also for the always-on services), set them in
`.env` instead. No quotes are needed:

```bash
YS_STYLE=lofi
YS_THEME=cozy rainy evening, soft and warm
```

Flags on the command line override the `.env` values.

## Live coding

This is the default engine (`--engine live`, or `YS_ENGINE=live`). Each track goes through phases:

| Phase | What happens |
|---|---|
| build | lines are added one at a time, in the style's order (lofi: beat, keys, bass, melody, vinyl, percussion) |
| groove | the running lines are revised: rhythms and melodies, filters (also slow `sine` sweeps), reverb and delay, wrappers, acid knob, tempo nudges |
| breakdown | the beat and bass are muted |
| rebuild / groove2 | they come back, and the revising continues |
| outro | lines are removed one by one, then the next track starts in a new key |

Every edit is validated before it is sent: numbers stay in range,
melodies are scale degrees so they are always in key, bass stays dry, at
most 3 wrappers per line and 4-6 lines at once, depending on the style.
Only the changed line is re-evaluated, just like a live coder pressing
Ctrl-Enter on one line.

The Director conducts: with each track it writes precise instructions for
every part of the arc, naming the part, the block, the sound, the pattern
or the knob value from the style's own lists ("Start the chords: piano
stabs, DX7 / Rhodes electric piano", "The acid's acid from 0.3 to 0.9 over
16 bars", "The chords to the DX7-style bell chords", "Drop the kick"). Every
name is checked against the style (a sound the named block can't play moves
to a block that can), and the Performer plays a precise cue exactly as
given, one per move; what a cue leaves open ("new lead pattern: busier,
16ths") it writes itself. The page shows the instruction, the code and the
Performer's answer. Once a part's instructions are done, the Performer
plays on by itself until the next part.

The Director also acts as art director with a **surprise menu**
(`music/surprises.py`): rule-breaks built only from safe moves, at most one
every `YS_SURPRISE_EVERY` tracks (default 2), never one of the last four, and
only what the track can take: a fake drop (8 bars of peak, then back into a
breakdown), a kickless drop, a bar of silence before the drop, the last peak
a whole step up, a breakdown down to one line, a stripped peak that rebuilds
move by move, a tempo lift through the build-up, a three-note hook. Code
places each at its moment; the page shows it (FAKE DROP, KEY CHANGE, ...)
and throws the live code at the viewer: tokens of the program, a few math
signs and the surprise's name fly out of the screen in 3D, glowing, and
vanish before the glass (`?moment=KEY%20CHANGE` previews it). The session
log records each surprise next to your ratings.

Against repetition, in every style:

- A track is about 5 minutes (`--piece-minutes`), and its shape (where the
  peaks and breakdowns fall) differs from the previous track's.
- At most two knob turns in a row (filter, reverb, acid knob, drive): the
  next move changes the music itself, notes or rhythm, a line in or out, or
  a variation. The model is told so; the built-in performer and the judge's
  options follow it.
- Every part a style has can come in, and the Director's sketched parts come
  in before decoration.
- A progression has at least two different chords, and the chords change
  every one or two bars in every style.

### Trance harmony, after Switch Angel

The club styles' harmony follows Switch Angel's
[strudel-scripts](https://github.com/switchangel/strudel-scripts) and her
live trance demo, translated to Tidal:

- **Voicings** from her `chordshapes`: each track stacks its chords one way
  (triad, add octave, open triad, spread triad, epic stack, anthem), no added
  sevenths (`livecode.TRANCE_VOICINGS`; the Director may choose).
- **Arps** after `trancearp`: 16ths running up, down, up-and-down or around
  the root through the voicing's own notes, so they only play chord tones.
- **Chord gates** after `trancegate`: seeded random 16-step gates, each hit
  held to the next and clipped to 70% (`blocks.TRANCE_GATES`).
- **Echoes** after `dly`: delay feedback stays low (at most 0.5).
- **Straight drums:** the kick alone on d1, every beat; hats and claps on
  d8, about 8 dB quieter and above the low end; the clap only on the
  backbeat (or its fills). Club drum lines are never sped up, shifted,
  swung or stuttered, only filtered or crushed.
- **Few melodies:** at most two melodic lines (acid, arp, lead, keys) above
  the bass and the gated chords; density comes from gates, knobs and rhythm.
- **Clean variations:** pitched lines get no time shifts, speed-ups or
  delayed copies (they drag a chord's notes over the next chord); a lead
  doesn't play backwards (its passing notes would land on the beat); every
  part stays in its register.
- **Melodies, not arpeggios:** bass, acid and arps play chord tones only.
  A lead or keys line puts chord tones on the beats (steps 1/3/5/7 of 8,
  1/5/9/13 of 16, read through `<[bar] [bar]>` phrases too) and may use the
  scale's other notes between them as passing or neighbour notes: a step to
  or from the note beside it, never held into the beat
  (`livecode.harmony_problem`). `_` holds a note, so phrases can end on a
  long one. The Director writes 2-4 bar calls and answers, develops the hook
  at the peak instead of stripping it, and sees the last lead melodies so it
  doesn't repeat them. Seeing them wasn't enough: in a run of hardcore
  tracks Claude wrote one tune five times, an 8-step line between -3 and 4
  falling to the root. The prompt now says what the recent tunes share
  (`plan.melody_brief`), and a hook too close to a recent one
  (`plan.hook_similarity` ≥ 0.6, the order of their notes) is written again
  once, the hook alone (`Director._freshen_hook`). Techno and hardcore also
  have a second lead line, `anthem_lead`: the tune an octave up in long
  notes ringing into the reverb, not pumping with the kick.

Trance comes in **subgenres** (`blocks.SUBGENRES`), each with its own tempo,
drum kit (hat and clap patterns), bass rhythm, sounds and voicings:
progressive (126-132 BPM, offbeat bass, soft hats, e-piano and bells),
uplifting (136-140, rolling bass, supersaw chords and lead, epic voicings),
psy (142-146, galloping bass, busy hats, square-wave leads) and acid
(134-140, the 303 up front, house piano). The Director picks one per track,
a different one from the last.

Darkwave has three, after She Past Away, Twin Tribes, Linea Aspera, Kalte
Nacht, Rue Oberkampf, Kirlian Camera and Deus Ex Lumina. A subgenre also
picks its bass and arp blocks (`"blocks"`):
**post-punk** (128-140: the chorused bass carries the tune in 8ths, a
jangly chorused guitar, a big gated snare), **minimal synth** (112-124: a
16th-note analog sequencer bass, a dry drum machine with claps, a square
lead) and **ethereal** (100-116: wide string machine and bells, a
half-time gated snare, long reverb). Its voices are new SynthDefs:
`ys_chorusbass` (picked bass through a chorus pedal), `ys_coldguitar`
(clean single-coil guitar, chorused), `ys_coldstrings` (a Solina-style
string ensemble built from triangles, not saws), `ys_gatesnare`, and two
lingering wave synths: `ys_wavelead` (swells in, scoops up into the note, a
vibrato that fades in, a long release) for the `wave_lead` line of long held
notes, and `ys_wavesynth` (each note rings for seconds) for the `wave_arp`
whose notes overlap into a wash. Those two blocks keep their own voice, and a
track whose sound for the part is one of them brings in the line written for
it. Hear them with `python -m eval.sound_preview --darkwave` (demos 5-10 in
`recordings/preview/`).

**Sample banks.** Besides Dirt-Samples, SuperDirt loads the project's own
licensed banks from `samples/` (`tidal/superdirt_boot.scd`). Blocks that use
them: `break_loop` (lofi, downtempo: a two-bar funk break stretched to two
cycles with `loopAt`, so it slows and drops in pitch; the loop is locked once
it plays), `foley_bed` (lofi, ambient: steady foley far behind), `electro_kit`
(minimal-synth darkwave: the CPU electro 808, each part at the 808 kit's
level via `amp`) and `guitar_shards` (darkwave: a Blood Sport guitar or noise
hit every few bars). A sample's number is its place among the bank's audio
files in SuperCollider's sort order; `tests/test_samples.py` checks every
numbered sample exists.

The **supersaw** (`ys_hypersaw`): seven band-limited saws with the JP-8000's
detune curve, filtered, stereo, ducked under the kick (a `detune` knob from
0.1 to 0.8). Listen before it goes on air: `python -m eval.sound_preview`
renders four 8-bar demos to `recordings/preview/` (the supersaw on chords,
as a lead, the old buzzy saw and today's house piano for comparison). Two more
saws done the same way: `ys_sawlead` (two band-limited saws 8 cents apart and
one an octave down, a filter that opens on each note and settles, a scoop and
a vibrato that fades in) and `ys_sawpad` (seven band-limited saws, a slowly
breathing low-pass under 2.5 kHz, a slow swell); `--saws` renders them against
today's square lead and soft pad and the old saw pad (demos 11-15). Then
`YS_HYPERSAW=1` in `.env` puts them on air: the supersaw in uplifting trance,
the saw lead in uplifting and psy trance and hardcore, the saw pad as a bed
behind uplifting and progressive trance.

**Lead voices.** Besides the bell, square, pluck and the darkwave voices,
the melody can be sung by a breathy flute (`ys_flute`), a marimba, a sitar
with a buzzing bridge, a vocal "aah" (`ys_vox`), a DX7-style FM lead, and the
1990s signatures: the hoover (The Prodigy; with `YS_HYPERSAW=1`), M1 organ
stabs on the chords, a nylon-string Balearic guitar (Chicane), a dub melodica
(Thievery Corporation), a vibraphone (Nightmares on Wax, Yonderboi), a muted
trumpet and a harpsichord (Massive Attack), and Rhodes or felt piano. Lofi,
downtempo and ambient now have sound choices too, so the theme picks among
these (a dub lounge gets melodica and vibes, a noir night harpsichord and
trumpet). `python -m eval.sound_preview --leads` plays one melody on every
voice (demos 18-34) and the M1 organ stabs (35).

**Classic tracks as subgenres.** Trance's `balearic` (Chicane's
"Saltwater": nylon guitar, supersaw stabs, and `ys_angelchoir`, an ethereal
layered female choir singing long Celtic lines on its own `choir_lead`) and
`lush` (C.J. Bolland's "Lush 3-5": a hypnotic 16th sequence under lush
detuned pads, acid underneath); hardcore's `gabber` and `happy` (Charly
Lownoise & Mental Theo's "Wonderful Days": piano, `ys_chipvox`, the sped-up
chipmunk vocal, in a major key); deep house's `deep` and `piano house`
(Guru Josh's "Infinity": M1 piano and organ, `ys_sax`). A subgenre may
name its own modes (happy hardcore: major/mixolydian); the key moves to the
nearest one (`plan.fit_mode`). A theme can ask for a subgenre
(`plan.THEME_SUBGENRE_KEYWORDS`): "Chicane", "Saltwater", "Balearic",
"Ibiza" or "sunset" make every trance track `balearic` (with either
planner), and its signature sounds, the choir on the lead and as a bed and
the nylon guitar on the arp, play on most tracks; e.g.
`stream/live.sh --style trance --theme "Chicane's Saltwater at sunset"`.
The balearic lead is the Café del Mar pluck (`ys_cafepluck`, Energy 52) or
the nylon guitar, an octave up in a dotted-8th echo (`cafe_lead`); every
line's echo is moved onto the beat at the track's tempo (`livecode.beat_delay`).
**Real instruments.** Seven sounds are recordings, all from
[FreePats](https://freepats.zenvoid.org) under CC0 (public domain):

| SynthDef | instrument | where it plays |
|---|---|---|
| `ys_nylon` | Spanish classical guitar, 48 notes | Balearic lead and arp, lounge |
| `ys_keys` | Upright Piano KW, the soft layer | the felt piano (lofi, piano, downtempo) |
| `ys_sax` | tenor saxophone, the loud layer, looped | deep/piano house, lounge |
| `ys_coldguitar` | FSBS clean electric guitar, through the chorus | darkwave |
| `ys_chorusbass` | picked electric bass, through the chorus | post-punk darkwave |
| `ys_harp` | concert harp (new) | Balearic, ambient, downtempo arps |
| `ys_kalimba` | kalimba (new) | Balearic, ambient, lofi arps |

`./venv/bin/python -m music.instruments` (also in `install.sh`; or name
some: `... piano sax`) downloads them to `instruments/` and keeps one
recording per note from the SFZ map (the velocity layer chosen per
instrument, the first round robin), cut to its attack and levelled (they
were up to 10 dB apart), with its tuning and loop points (`meta.scd`).
SuperDirt loads them at boot, and `~ysSampled` (`tidal/synthdefs.scd`)
makes each SynthDef play the nearest recorded note shifted to the pitch, an
octave nearer for notes far outside the recorded range (the bass covers one
octave), looping the sax for long notes, each at its model's level in the
mix (`mix.SYNTH_LUFS`). Without the recordings the modelled SynthDefs play,
and the harp and kalimba, which have no model, aren't offered. Hear them:
`python -m eval.sound_preview --instruments`, `--balearic` and `--darkwave`
(`YS_PREVIEW_MODELLED=1` for the models). The choir is a voice in the style of that
music, not a copy of any singer; for a real voice with words, see **Vocals**
below.

**Vocals.** Tracks have a vocal part (`vocal`, on d12). It comes in for the
breakdown, as a voice in the space the kick leaves, and sings through the
peak; it never comes in during the build. If the Director's cues don't bring
it in, the track gets a cue that does.

- *Vocal chops*: darkwave, techno, hardcore and downtempo have `vocal_chop`, Blood
  Sport's spoken and half-sung phrases (`samples/bsvocals`: "What is work",
  "I am here", "Hush", ...). It plays one phrase a bar at most, every few
  bars, lost in reverb and delay, at -20 LUFS, just above the lead. The
  phrases have no key to keep, so the vocal wrappers may stutter them (`echo`),
  play them backwards or make them thin like a telephone.
- *Acapellas* (licensed, yours): sung hooks for any style. Put each file in
  `acapellas/` and describe it in `acapellas/acapellas.json`:

  ```json
  [{"name": "Night Calls", "file": "night_calls.wav", "key": "A minor",
    "bpm": 124, "start": 0.48, "bars": 8, "styles": ["deephouse", "trance"],
    "license": "Loopmasters Deep House Vocals, royalty-free licence"}]
  ```

  `start` is the first downbeat in seconds, `bars` the loop's length from
  there, `styles` who may sing it (all styles if left out). `license` is
  required: write where the acapella came from and why the stream may play
  it, because YouTube's Content ID flags vocals lifted from released songs.
  Then run `./venv/bin/python -m music.vocals` (needs sox and ffmpeg).
  It renders each acapella to its own SuperDirt bank (`samples/vox_<name>/`):
  cut to its bars, at -16 LUFS, in seven versions from three semitones down
  to three up. Restart the stream so SuperDirt loads them.

  On `YS_ACAPELLA_CHANCE` of the tracks (0.5 by default) where one fits, the
  track sings an acapella. One fits when its mode is one the style plays and
  its tempo, or double or half of it, is in the style's (or subgenre's) range.
  The track takes that tempo, and the key nearest the Director's choice that
  one of the seven versions reaches. The Director's hook and sketch are in
  scale degrees, so they move with it.
  The acapella plays with `loopAt` and is chopped into beats, so it is in
  step wherever it comes in. While it plays, the tempo stays put: a tempo
  edit, the tempo-lift and key-change surprises would put it out of tune.
  An acapella was sung over another song's chords, so key-matching (what DJs
  do) usually works, but not always. Listen to a few tracks with each one.

The **progressions** come from [NRG-CP](https://doi.org/10.5281/zenodo.15304989)
(WaivOps / Patchbanks, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)):
30,943 eight-bar EDM and trance MIDI files, of which 15,285 are four-chord
cycles in one of the six modes. `eval/mine_nrg.py` reads them as Roman
numerals into `music/data/nrg_progressions.json`; the library is the
progressions found in at least 8 files, weighted by how often (minor:
`i-VI-III-VII` 446 files, `i-VII-VI-VI` 247, `i-VII-v-VI` 211, ...). Only
the progressions are used, not the dataset's rhythms or voicings.

With an LLM (`--llm local|claude|jevtypesafe`), the model sees the whole
program and picks each edit itself (prompt: `prompts/performer.md`), with
a short reason and sometimes a line of narration. If a reply is unusable,
the built-in performer makes that edit, so the music never stops.

Lofi has no sustained pad; its harmony comes from Rhodes and felt-piano
comping. Trance is the Switch Angel acid setup: acid line, ducking kick,
M1 piano stabs and a glass lead (no saws, no organ).

```
./venv/bin/python orchestrator.py --style lofi              # live-coded lofi, local LLM
./venv/bin/python orchestrator.py --style trance --llm none # built-in performer only
./venv/bin/python orchestrator.py --dry-run --style lofi --llm none   # print the edits, no audio
./venv/bin/python orchestrator.py --engine arranger         # the older section engine
```

Without `--style` the stream rotates between lofi, ambient, downtempo and piano, one style per track.

**Mixing.** Every line enters at a level calibrated from the measured
loudness of its synth (`music/mix.py`, measured with `eval/synth_levels.sh`):
melody -21 LUFS, bass -19, keys -25, beds (strings, pads) -29, textures -34.
New lines fade in over 4 bars and removed lines fade out over 2 (Tidal's
`xfadeIn`). Beds step back 2.5 dB when two lead parts play, and melodic
lines give a little more when six or more lines play. Gain edits stay within
about +/-3 dB of a line's level, so no edit can blow up the mix.

Three agents take part in the live engine:

- **Director:** plans each track (key, tempo, progression, mood, title,
  its sections in bars, and a sound per part in the spirit of 1990s trance
  and house: M1 house piano, DX7 electric piano and
  bell, square-wave lead, plucks; `🎹 Sounds: ...`), shown as
  `🧠 Next piece: ...` with its reason.
  Its spoken intro plays when the track starts.
- **Performer:** makes one edit every `--edit-bars` bars (default: 8 bars
  for the slow styles, 16 for the club styles, about 20-30 s; set
  `YS_EDIT_BARS` in `.env` to change it).
- **The arc** is decided before the track starts, from the Director's
  sections in its order (`🧭 Arc plan: build ≤16 · groove 64 · peak 32 ·
  breakdown 32 · rebuild ≤16 · peak 64 · outro 16 bars`): intro -> build,
  verse and bridge -> groove, chorus -> peak, breakdown -> breakdown, a build
  after a breakdown -> rebuild, outro -> outro. So a track can peak early,
  break down twice, or never. Every breakdown is followed by a rebuild, and
  the Director is shown the previous track's shape and told not to repeat
  it (the built-in planner also picks between several shapes). Each step
  lasts its bars, however often the Performer edits; build and rebuild end
  early once their lines are in. When the outro's bars are over, whatever
  still plays fades out and the next track starts.
- **Narrator:** at each turn of the arc (`🧭 Arc: groove → breakdown`),
  writes one warm spoken line about the moment, in the background so the
  music never waits.

With jevtypesafe (a decision API, no text), the built-in planner plans the
tracks and the Narrator uses hand-written lines for each moment.

**Generator and judge** (`--judge jevtypesafe`, or `YS_JUDGE=jevtypesafe`):
the LLM proposes 3 different edits, the built-in performer adds up to 3
more, and jevtypesafe picks one *blind* (shuffled, sources hidden):

The proposals run **from safe to bold**: each is labelled `safe` (a small
step) or `bold` (a move a listener would remember: an answer phrase, a
counter-line, a stripped-back moment, a new colour), at least one bold, two
in a peak. The judge's criterion follows the arc (`performer.BOLDNESS`,
`JUDGE_CRITERIA`): in the groove and the peak it picks the most musically
interesting move that still serves the track (changing notes, rhythm or
lines over turning a knob; never more notes for their own sake); in the
build and rebuild it builds tension; in the breakdown and outro it keeps it
calm. The note budget and harmony rules hold either way. After each track:
`the judge's picks: 9 bold, 4 safe, 2 built-in`.

```bash
./venv/bin/python orchestrator.py --style lofi --llm local --judge jevtypesafe
```

```
💬 Performer ← local/qwen-agentworld-35b (4.1s): {"proposals": [...]}
💬 Judge ← jevtypesafe/decide (0.9s): {"edit": {"choice": "e2"}}
⚖️  Judge chose the LLM idea (from 3 LLM + 3 built-in)
✎ [LLM, judged] d5: melody -> 4 ~ 3 ~ 0 ~ ~ ~   -- answer the phrase a step lower
```

After each track: `the judge preferred the LLM's idea 14/20 times (70%)`,
a direct measure of whether a model's ideas beat random ones. Needs
`YS_JEV_API_KEY` and a text model (local, Claude); if the judge is down,
the LLM's first idea plays.

## How a piece develops (arranger engine)

A piece is about 10-20 minutes long and moves through intro, verse, build,
chorus, breakdown, verse, chorus and outro. It changes as it goes:

- **Harmony:** the chorus and the breakdown each get their own chord
  progression, so the harmony moves between sections. Chords are sevenths,
  or lusher ninths. Sometimes the final chorus lifts a whole step for the
  last peak.
- **Phrases:** the drums add a small turnaround on every 4th bar and a
  proper fill on every 8th bar, counted from the start of the section.
  Hits vary slightly in loudness, and lofi and downtempo drums sit slightly
  behind the beat, like a real player.
- **Builds:** a build that leads into a chorus opens the filters from dark
  to bright across the whole section. The snare or clap starts rolling two
  bars before the chorus, and in the last bar the kick and bass drop out so
  the chorus hits harder.
- **Movement:** pad, keys and arpeggio filters drift slowly ("gentle"), not
  at all ("still"), or more ("lively").
- **Its own sound:** every piece picks its instruments from its style's
  palette. Pads can be soft, airy or piano (no saw or organ pads),
  plus several keys, leads and basses. Each piece also picks how it opens
  (keys first, drums and bass first, an arpeggio, a lone pad...), and it
  never repeats the previous piece's opening or pad.
- **Mix:** every synth was rendered offline and measured. Levels put the
  melody up front, then keys and bass, with pads well in the background.
  Pads are level-matched for chord size and back off 2.5 dB when four or
  more other parts are playing. `eval/synth_levels.sh` re-measures the synths
  after you change `tidal/synthdefs.scd`.
- **Melodies:** short loops get a two-loop phrase (a statement and an
  answer). When a section comes back, its melody keeps its opening but
  answers differently.

With an LLM, the Director chooses the chorus and breakdown progressions,
the chord colour, the key lift, and per section whether to use fills and
how much movement. The built-in planner makes the same kinds of choices
at random.

## Watching the agents talk

Every LLM call is shown in the terminal as it happens: which agent asked
which model, what it asked for, and the model's reply as it came back, with
the time it took. Each edit is tagged with who chose it, the LLM or the
built-in performer, so you can see that the model is really playing:

```
💬 Performer → local/qwen3.8-27b: next edit for 'Silver Waves' (groove, 5 lines playing) (5,321 chars)
💬 Performer ← local/qwen3.8-27b (6.4s): {"op": "wrap", "role": "keys", "name": "jux rev", "reason": "widen the keys"}
✎ [LLM] wrap d4 with `jux rev`   -- widen the keys
✎ [built-in] d2: rhythm -> 0 ~ ~ 0 ~ ~ 4 ~   -- new rhythm pattern on the bass   <- model too slow or unusable
```

- Every LLM edit explains itself twice: `👂 hears:` is what the model
  noticed in the program, and the `-- ...` comment is why it made the move.
  Its own arc decisions appear as `🧭 Arc: groove → breakdown (...)`.
- The prompt teaches the model Tidal as it plays: a mini-notation cheat
  sheet, what each wrapper and effect sounds like, example moves for the
  current phase, and its own last six edits (so it builds on its ideas
  instead of undoing them).
- `--show-llm` also prints the full prompt and the raw reply of every call.
- `runtime/llm_calls.jsonl` keeps the full prompt and reply of every call
  (agent, model, seconds, reply or error), so you can audit a whole session.
- The arranger engine's agents appear the same way, as `Director` (plans
  pieces), `Arranger` (arranges sections) and `Composer` (counter-melodies).

## Asking the performer for something

Open the stream page on the machine that runs it (http://127.0.0.1:8765) and
type into the box at the bottom, e.g. `more bass`, `darker`, `drop the beat`,
`faster`. The Performer takes the request at its next edit (which comes
sooner while a request waits), reviews it and makes the move that best
answers it, within the usual rules (harmony, the kit, +3 dB at most per
line); if it can't do it as asked, it says why and makes the closest good
move. The stream shows a "Host request" card with your words, the move and
the review; the box itself is only on your view (the captured stream loads
the page with `?capture=1`). Without an LLM, simple keyword rules answer
(more / less / drop / bring back a part, darker / brighter, faster / slower).
Requests are logged in the terminal (`📨`) and in the session memory; the
page server only listens on 127.0.0.1.

## Rating what plays (👍 / 👎)

Next to the request box are 👍 and 👎: rate what is playing right now. Each
rating is kept in the session memory with the track, the phase, the code
and the last change before it (`python -m eval.export_edits --ratings`), and
the Performer sees it among its recent moves ("the host 👎 disliked what
played after: ..."), so a model builds on what you like within the track.
Over time this is the taste data to tune and train on.

Each code line has its own 👍 / 👎 too (on your view only: the captured
stream, `?capture=1`, doesn't show them). A thumb on a line is kept with that
line's code and its last change; the Performer reads "the host 👎 disliked
the acid line (...): change it (or take it out) at your next move", the
built-in performer changes a 👎 line at its next tweak and leaves 👍 lines
alone. Line ratings start fresh with each track.

## Transitions (the arranger)

In club styles the arranger adds what a producer does between sections: a
riser and a snare roll that speeds up through the rebuild, one bar without
kick and bass before the drop, an impact when it lands, and a snare fill at
the end of every 16-bar phrase in the groove and the peak. The devices run on
`d10` / `d11`, show in the code panel and the feed ("Arranger"), and are kept
in the session memory.

## Session memory: every change to the artefact

Each live session writes `sessions/<date>_<time>.jsonl` (set
`YS_SESSIONS_DIR` to move it). It is append-only and never truncated, so it
is the memory to learn from:

- `session_start`: theme, style, models, judge, pacing, git commit
- `track_start` / `track_end`: the track, the Director's line, the program it
  starts and ends on, edits by source, groove complexity
- `edit`: every change that played: who chose it (`LLM`, `LLM, judged`,
  `built-in`, ...), what it heard, why, the op, the line before and after,
  the whole code after, the phase, complexity before and after, and with a
  judge every option it weighed and their probabilities
- `rejected`: edits that never played, and why
- `arc`: the track's shape moving on

Read it back:

```bash
python -m eval.export_edits --summary                       # what the memory holds
python -m eval.export_edits --style trance --source llm > trance_llm.jsonl
```

Each exported line is a learning example: the code before, the move, and the
reason (`music/sessionlog.py` has `iter_records()` / `iter_edits()` for
retrieval in code). A 15-minute track takes about 75 KB.

## Watching the Director think

Every decision prints in the terminal with the model's reason:

```
🧠 Next piece: 'Night Ferry', lofi, D minor, 80 BPM, i-VI-III-VII, late-night
   "Relative minor of the last piece keeps the warmth but feels more intimate."  (4.2s)
🧠 Next section (verse 2/8, energy 0.45): pad=warm_pad, texture=vinyl, bass=sub, keys=rhodes, melody sparse
   "Bass and keys come in to ground the verse; the melody stays sparse."  (2.8s)
🎛  Next section (build 3/8, energy 0.6) by the built-in planner: ...   <- LLM was unavailable
```

- `--show-llm` also prints the model's full raw reply.
- `tail -f runtime/thoughts.log` follows just the decisions, in a second terminal.
- Sections are planned one section ahead, so a "Next section" line appears
  while the current section is still playing.

## Spoken narration

The Director's listener-facing lines are read aloud in a warm, low,
intimate late-night radio voice, and shown as a subtitle on the stream:

```
🗣  "Now playing: Velvet Harbor. Stay a little longer. Let the night slow down with you."
🗣  "Here comes a soft little pulse. Just let it carry you."
```

- The LLM writes a separate `narration` sentence for listeners. The
  technical `reason` stays in the terminal. Lines containing numbers,
  preset names or jargon are never spoken.
- Speech runs locally with [Piper](https://github.com/OHF-Voice/piper1-gpl).
  The `warm` style lowers the pitch slightly, adds warmth and a close-mic feel,
  and slows the delivery. The music dips a little while the voice speaks.
- `install.sh` downloads the default voice. To do it by hand:
  ```bash
  ./venv/bin/python -m piper.download_voices en_GB-jenny_dioco-medium --download-dir voices
  ```
  Other voices to try (set `YS_TTS_VOICE=voices/<name>.onnx`):
  `en_US-amy-medium`, `en_US-lessac-medium`, `en_US-ryan-high` (male),
  `en_GB-alan-medium` (male). Browse and listen at
  https://rhasspy.github.io/piper-samples/.
- `--narrate sections|pieces|off` (or `YS_NARRATION`) sets how often it
  speaks. `YS_VOICE_STYLE=calm` gives a cleaner, brighter voice. Speed,
  loudness and the music dip are in `.env` (`YS_TTS_LENGTH_SCALE`,
  `YS_NARRATION_VOLUME_DB`, `YS_NARRATION_DUCK`). The voice is
  loudness-matched to the stream (-14 LUFS) and sits 2 dB above it by
  default, while the music dips about 5.6 dB.
- Only LLM-directed pieces have narration (plus "Now playing: <title>");
  the built-in planner only announces titles.

## Going live on YouTube

### The stream page

The stream shows `stream/ui/deck.html`, after Switch Angel's screen: the live
code fills the left, every pattern with a piano roll of what it plays (asked
of Tidal itself, so it is exact; the notes light up as they sound) and every
knob as a slider (moving ones move). On the right: the channel's name
(`YS_CHANNEL_NAME`), a particle cloud that plays with the music, and the
agent at work where her camera is. The arc's turning points get their moment:
DROP (with a flash and a shake), BREAKDOWN, a new track. The logo glitches on
every bar. In the visual dances a dancer (her clip follows the arc: she
waves hello as a track builds, dances through the groove, sways with her
eyes closed in the breakdown, claps and crouches as it rebuilds, gives it all
at the peak, and in the outro turns on one heel, bows once like a geisha and
then waves goodbye; trance gets its hands-up peak, hardcore hakken, darkwave
a slow gothic sway, and the chill styles no rave jumping or crouching; where
a part has two takes (the hello, the breakdown), each track gets one of them;
each loop lasts a whole number of bars, and she jumps at the drop). The clips in `stream/ui/dancer/`
are cut out of studio footage (Wan 2.2 loops that start and end on one pose,
`dance/`) by `python -m stream.make_dancer <clip> <out.webm> --box 0,0,480,832`
(the light backdrop made transparent, a rim of light added; one `--box` for
clips of one camera keeps her the same size in all of them). `?moment=DROP` previews one. `YS_UI_PAGE=index.html` streams the
earlier look (wave field and the agents' feed), which also renders the
thumbnail, banner and logo (`stream/thumbnail.py`).

1. In YouTube Studio → **Go live** → **Stream**, copy the stream key into
   `.env` as `YT_STREAM_KEY`. The local model and the judge need
   `LLAMA_GGUF_MODEL` and `YS_JEV_API_KEY` there too.
2. Start everything with one command:

```bash
stream/live.sh                      # local model performs, jevtypesafe judges; Ctrl-C stops
stream/live.sh --style darkwave     # extra flags go to orchestrator.py and win over the defaults
stream/live.sh --judge none         # e.g. without the judge
stream/live.sh --hours 3            # stop after 3 hours (any number, e.g. 2.5), once the song playing then ends
```

It runs in that terminal until Ctrl-C. Watch the stream in YouTube Studio
(Go live → Stream), or on your channel once it shows LIVE. If the stream
part stops (no `YT_STREAM_KEY`, no Chromium/Xvfb, ...), the terminal says so
and prints the end of `logs/stream.log`.

### Record a video file instead

To upload a finished set rather than stream it (YouTube won't let a new
channel share its live streams for a while, say), record it to a file:

```bash
stream/record.sh                          # one hour, to recordings/show-<date>.mkv
stream/record.sh --hours 2.5              # any length, in hours
stream/record.sh --out ~/Videos/set.mkv   # another file
stream/record.sh --hours 1 --style darkwave   # other flags go to orchestrator.py
```

The same show as `live.sh`, but captured to the file, starting with the first
track (not the model loading), and everything stops when the time is up. No
stream key needed. `.mkv` stays playable if you stop early with Ctrl-C;
YouTube takes it as is. The default bitrate is 8000k (about 3.6 GB an hour);
set `YS_VIDEO_BITRATE` to change it.

| Part | What it does |
|---|---|
| stream page | served from the start on http://127.0.0.1:8765: open it in a browser to see what the stream shows (video only), even before YouTube is set up (log: `logs/ui.log`) |
| audio sink | `stream/setup_audio.sh` creates the `ystream_sink` the stream captures from |
| engine | `orchestrator.py --llm local --judge jevtypesafe` plus your flags, starting fresh with your theme; restarted with `--resume` if it stops, picking up the track that was playing (never a saved track of another style) |
| stream | `stream/stream.sh`: the stream page (below) captured at 1080p30, limiter → -14 LUFS → RTMP; reconnects by itself (log: `logs/stream.log`) |
| watchdog | restarts the engine on sustained silence or a stale heartbeat, optional push via ntfy (log: `logs/watchdog.log`) |

**Twitch** instead of YouTube: put `YS_STREAM_URL=rtmp://live.twitch.tv/app/<your stream key>`
in `.env` (the key is in the Twitch Creator Dashboard → Settings → Stream).

**YouTube and Twitch at once:** list several targets, separated by spaces;
`youtube` stands for YouTube with `YT_STREAM_KEY`:
`YS_STREAM_URL=youtube rtmp://live.twitch.tv/app/<your stream key>`. The
stream is encoded once and sent (over local UDP, ports from 23480,
`YS_RELAY_PORT`) to one relay per platform, which reconnects on its own: if
Twitch drops, YouTube keeps streaming and Twitch comes back by itself
(`[stream] twitch: the connection ended ...; reconnecting` in the log). Every
platform gets the same resolution and bitrate, and the upload is their sum.

With a Twitch target the stream defaults to 720p at 3 Mbit/s: most channels get no
lower-quality versions, so every viewer receives the stream as sent, and 1080p
at 4.5 Mbit/s buffers for many. If the machine can't encode in real time,
`logs/stream.log` says so (`the encoder runs at 0.85x real time`) with what to
lower (`YS_VIDEO_SIZE`, `YS_FPS`, `YS_VIDEO_BITRATE`). With the browser look,
the frame rate is rounded up to one that divides 60 (24 becomes 30): Chromium
draws at 60 Hz, and 24 fps samples it unevenly, so the waves judder.

To keep it running after you close the terminal, start it inside `tmux`
or `screen`. Test on an **unlisted** stream for a while before going public.

### The stream page

What viewers see is a web page, `stream/ui/`, rendered by Chromium in a
virtual display (Xvfb) and captured by ffmpeg:

- **Top:** LIVE badge, channel name, track title, style / key / BPM / mood,
  and the track's arc as the Director planned it, each step with its bars;
  the current one fills as it plays.
- **Left, the artefact:** the live Tidal program, syntax-highlighted. The
  line just edited types itself in and glows, muted lines are struck
  through, and the reason for the last move is shown as a `--` comment.
- **Right, the agents:** a card for every decision as it happens. The
  Performer shows what it hears, the move and why (tagged AI, AI · judged or
  built-in). The Judge shows all options with its probabilities, revealing
  after the choice which were the AI's and which random. The Director shows
  its track plan and reason, the Narrator its line, the arc its turns.
  "Performer is thinking with qwen…" shows while a model works.
- **Bottom:** the narration as a subtitle, and live stats (AI edits,
  how often the judge prefers the AI).
- **Behind it:** a 3D field of sound waves flying towards the viewer, in
  the style's colours, on the engine's clock: every beat sends a shockwave
  through it, the bass swells it, chords roll it and lead / arp / acid
  ripple it, according to the lines playing; calmer in breakdowns, fullest
  at the peak.

Preview it while the engine runs (or after a `--dry-run`):

```bash
./venv/bin/python -m stream.ui_server      # then open http://127.0.0.1:8765
```

`stream/stream.sh` uses this look automatically when Xvfb and Chromium are
installed (`install.sh` sets them up); `YS_STREAM_UI=overlay` gives the older
ffmpeg-only look. Video is 1920x1080 at 4.5 Mbit/s by default
(`YS_VIDEO_SIZE`, `YS_VIDEO_BITRATE`).

**Thumbnail.** With the page server running:

```bash
./venv/bin/python -m stream.thumbnail                   # -> runtime/thumbnail.png (1280x720)
./venv/bin/python -m stream.thumbnail --style darkwave  # a specific style's look
./venv/bin/python -m stream.thumbnail --banner --style trance  # -> runtime/banner.png: channel banner, 2048x1152
./venv/bin/python -m stream.thumbnail --logo --style trance    # -> runtime/logo.png: channel logo, 800x800
```

It renders the page's poster mode ("AI agents live-code music", the style's
visual, the current track); upload it in YouTube Studio as the stream's
thumbnail.

### Background image or video (overlay look)

Set `YS_STREAM_BG` in `.env`:

```bash
YS_STREAM_BG=/home/you/Pictures/rainy-window.jpg   # still image: png, jpg, webp, bmp
YS_STREAM_BG=/home/you/Videos/ocean-loop.mp4       # video: mp4, mov, webm, mkv (looped, its sound ignored)
YS_STREAM_BG=/home/you/Pictures/lofi-room.gif      # animated gif (looped)
```

It's scaled to fill the frame and centre-cropped, so use the same aspect
ratio as `YS_VIDEO_SIZE` (16:9) to avoid losing the edges. `YS_BG_DIM`
(default 0.35) darkens it so the text stays readable, and `YS_BG_BLUR=8`
gives a soft, out-of-focus look. For videos, a short seamless loop (10-60 s)
works best, and a long, heavily compressed video costs more CPU. Restart
the stream to apply changes (Ctrl+C and `stream/live.sh` again).

## Steering it

```bash
python -m eval.feedback down "melody too busy"   # the Director avoids what's playing now
python -m eval.feedback up
python -m eval.analyze clip.wav --key "C minor"  # objective metrics for any recording
python -m eval.export_dataset > dataset.jsonl    # fine-tuning data from sections that were well received
```

Other useful flags: `--piece-minutes 15` sets the piece length, and
`--no-custom-synths` uses stock SuperDirt sounds (for style and mood, see
[Choosing the style and mood](#choosing-the-style-and-mood)).
Tunables such as the layer cap, timeouts and paths live in `.env` (see
`config.py`).

## Layout

| Path | Purpose |
|---|---|
| `music/livecode.py` | the live program: lines, edits, validation, rendering |
| `music/blocks.py` | per-style building blocks the performer adds |
| `music/instruments.py`, `instruments/` | real instruments (FreePats, CC0): guitar, piano, sax, electric guitar and bass, harp, kalimba |
| `music/vocals.py`, `acapellas/` | licensed acapellas: manifest, key and tempo fit, preparing their banks |
| `music/performer.py`, `prompts/performer.md` | the performer (built-in and LLM) and its phases |
| `music/live_engine.py` | plays the program and applies one edit every few bars |
| `music/theory.py` | keys, modes, progression library, voice leading |
| `music/templates.py`, `music/melody.py` | role templates (pad, keys, bass, melody, arp, drums, texture) that render in-key Tidal code |
| `music/presets.py` | curated, gain-balanced sound palette |
| `music/plan.py`, `music/director.py` | piece/section plans, the LLM Director with validation and fallback |
| `music/engine.py` | cycle clock, phrase-quantised scheduling (`jumpMod`), state/overlay files |
| `tidal/synthdefs.scd`, `tidal/superdirt_boot.scd` | custom SynthDefs and the master compressor/limiter |
| `stream/` | `live.sh` (go live), `record.sh` (record a video file), audio sink, stream page and capture, watchdog, thumbnail |
| `eval/` | analysis, quality monitor, feedback, preview renderer, dataset export |
| `tidal_backend.py`, `prompts/composer_prompt.md` | the old free-form agent loop (`orchestrator.py --legacy "..."`) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Ambiguous module name 'Sound.Tidal.Context': it was found in multiple packages: tidal-X tidal-X` | Tidal was installed twice. Run `python3 tidal/fix_ghc_env.py` (keeps the newest copy, backs up the file). |
| `Could not find module 'Sound.Tidal.Context'` | `cabal update && cabal install --lib tidal` |
| `[llama-server] stopped ... failed to allocate buffer for kv cache` / `cudaMalloc failed: out of memory` | The model fits but its context doesn't. Lower `LLAMA_CTX_SIZE` in `.env` (default 16384; 8192 is enough), add `LLAMA_SERVER_ARGS=-fa on --cache-type-k q8_0 --cache-type-v q8_0`, or lower `LLAMA_GPU_LAYERS`. The error and a likely fix are printed as soon as llama-server stops. |
| `[Director] LLM unavailable (cannot reach http://127.0.0.1:8080/...)` | The local model isn't running. Set `LLAMA_SERVER_BIN` and `LLAMA_GGUF_MODEL` in `.env` (the engine then starts it), start llama-server yourself, or use `--llm none`. The music keeps playing on the built-in planner meanwhile. |
| `apt-get update` fails on a third-party PPA | Harmless for this project; `install.sh` continues. To silence it: `sudo add-apt-repository --remove ppa:<name>`. |
| No sound, but no errors | `pw-link -l \| grep -A2 SuperCollider`; if it isn't connected to `ystream_sink`, run `stream/route_audio.sh`. |

## Tests

```bash
./venv/bin/python -m pytest -q
```

The tests check that every generated note is in key, that all layers share
one harmonic clock, that voicings move smoothly, that LLM output is validated,
that the scheduler only switches on period boundaries and respects the layer
cap, and, using `eval/preview.py`, that whole rendered pieces measure more
than 90% in-key.
