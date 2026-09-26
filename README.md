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
short supersaw stabs), `hardcore` (160-185 BPM: the same kit with a
distorted gabber kick) and `darkwave` (110-128 BPM cold electro / coldwave:
a stiff 808 drum machine, a 16th-note sequencer bass, a cold string-machine,
choir or organ bed, pluck arpeggios lost in delay and a sparse glass line).
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
- **Sidechain:** while the kick plays, everything pumps. Sustained supersaws
  duck inside the synth on every beat (depth 0.8, recovering over 0.16 s),
  and 16th-note layers get the same shape per step.
- **Around it:** a 136-140 BPM pitch-swept synth kick with off-beat hats and
  claps, a second higher acid line or a gated arp, and supersaw chords
  through a real trance gate. Pieces go groove → breakdown → build (roll,
  filter rise, kick drop) → drop, twice.

The supersaw pad's trance gate chops sustained chords inside the synth
(`ys_gatesaw`). The gate is remapped to 0…1 so "closed" is silent, and
`gatetime` (one bar in seconds) locks it to the tempo. It's either a
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

With an LLM (`--llm local|claude|jevtypesafe`), the model sees the whole
program and picks each edit itself (prompt: `prompts/performer.md`), with
a short reason and sometimes a line of narration. If a reply is unusable,
the built-in performer makes that edit, so the music never stops.

Lofi has no sustained pad; its harmony comes from Rhodes and felt-piano
comping. Trance is the Switch Angel acid setup: acid line, ducking kick,
gated saw and supersaw lead.

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

- **Director:** plans each track (key, tempo, progression, mood, title),
  shown as `🧠 Next piece: ...` with its reason. Its spoken intro plays when
  the track starts.
- **Performer:** makes one edit every `--edit-bars` bars (default: 8 bars
  for the slow styles, 16 for the club styles, about 20-30 s; set
  `YS_EDIT_BARS` in `.env` to change it).
- **Narrator:** at each turn of the arc (`🧭 Arc: groove → breakdown`),
  writes one warm spoken line about the moment, in the background so the
  music never waits.

With jevtypesafe (a decision API, no text), the built-in planner plans the
tracks and the Narrator uses hand-written lines for each moment.

**Generator and judge** (`--judge jevtypesafe`, or `YS_JUDGE=jevtypesafe`):
the LLM proposes 3 different edits, the built-in performer adds up to 3
more, and jevtypesafe picks one *blind* (shuffled, sources hidden):

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
  palette. Pads can be warm analog, strings, organ, choir, airy or piano,
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

1. In YouTube Studio → **Go live** → **Stream**, copy the stream key into
   `.env` as `YT_STREAM_KEY`. The local model and the judge need
   `LLAMA_GGUF_MODEL` and `YS_JEV_API_KEY` there too.
2. Start everything with one command:

```bash
stream/live.sh                      # local model performs, jevtypesafe judges; Ctrl-C stops
stream/live.sh --style darkwave     # extra flags go to orchestrator.py and win over the defaults
stream/live.sh --judge none         # e.g. without the judge
```

It runs in that terminal until Ctrl-C. Watch the stream in YouTube Studio
(Go live → Stream), or on your channel once it shows LIVE. If the stream
part stops (no `YT_STREAM_KEY`, no Chromium/Xvfb, ...), the terminal says so
and prints the end of `logs/stream.log`.

| Part | What it does |
|---|---|
| stream page | served from the start on http://127.0.0.1:8765: open it in a browser to see what the stream shows (video only), even before YouTube is set up (log: `logs/ui.log`) |
| audio sink | `stream/setup_audio.sh` creates the `ystream_sink` the stream captures from |
| engine | `orchestrator.py --llm local --judge jevtypesafe` plus your flags, starting fresh with your theme; restarted with `--resume` if it stops, picking up the track that was playing (never a saved track of another style) |
| stream | `stream/stream.sh`: the stream page (below) captured at 1080p30, limiter → -14 LUFS → RTMP; reconnects by itself (log: `logs/stream.log`) |
| watchdog | restarts the engine on sustained silence or a stale heartbeat, optional push via ntfy (log: `logs/watchdog.log`) |

To keep it running after you close the terminal, start it inside `tmux`
or `screen`. Test on an **unlisted** stream for a while before going public.

### The stream page

What viewers see is a web page, `stream/ui/`, rendered by Chromium in a
virtual display (Xvfb) and captured by ffmpeg:

- **Top:** LIVE badge, channel name, track title, style / key / BPM / mood,
  and the track's arc (build → groove → breakdown → rebuild → peak → outro).
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
| `music/performer.py`, `prompts/performer.md` | the performer (built-in and LLM) and its phases |
| `music/live_engine.py` | plays the program and applies one edit every few bars |
| `music/theory.py` | keys, modes, progression library, voice leading |
| `music/templates.py`, `music/melody.py` | role templates (pad, keys, bass, melody, arp, drums, texture) that render in-key Tidal code |
| `music/presets.py` | curated, gain-balanced sound palette |
| `music/plan.py`, `music/director.py` | piece/section plans, the LLM Director with validation and fallback |
| `music/engine.py` | cycle clock, phrase-quantised scheduling (`jumpMod`), state/overlay files |
| `tidal/synthdefs.scd`, `tidal/superdirt_boot.scd` | custom SynthDefs and the master compressor/limiter |
| `stream/` | `live.sh` (go live), audio sink, stream page and capture, watchdog, thumbnail |
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
