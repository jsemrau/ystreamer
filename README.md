# ystreamer

Endless, AI-directed chill music made with [TidalCycles](https://tidalcycles.org),
built to run 24/7 as a YouTube live stream.

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
| `jevtypesafe` | [jevtypesafeai.com](https://jevtypesafeai.com/how-to-use) | `YS_JEV_API_BASE`, `YS_JEV_API_KEY`, `YS_JEV_API_STYLE` (`openai` or `json`) |
| `none` | deterministic director, no LLM | nothing |

```bash
python orchestrator.py --llm claude --model claude-sonnet-5
python orchestrator.py --llm local
python orchestrator.py --llm jevtypesafe --model my-model
```

Claude requests enable the API's server-side refusal fallback (`fallbacks="default"`), so a declined request is retried on a fallback model instead of failing. Whatever the provider, a failed, slow or invalid answer just means the Director uses its deterministic plan for that decision.

## Choosing the style and mood

There are two options, and you can combine them.

**`--style`** fixes the musical style for the whole run:

```bash
./venv/bin/python orchestrator.py --style lofi
```

The choices are `lofi`, `ambient`, `downtempo`, `piano`, `deephouse` and `trance`.
Each sets a tempo range, the modes it uses (e.g. minor or dorian for lofi),
the default instruments and which layers come in first. `deephouse` is
118-124 BPM with a four-on-the-floor kick, off-beat hats, short chord stabs
and a syncopated sub bass, and it opens with the groove rather than a pad.
`trance`, after Switch Angel's live-coded SuperCollider trance, is
134-140 BPM with a pitch-swept synth kick, off-beat open hats, a rolling
16th bass between the kicks, and a supersaw arpeggio through the classic
trance gate (`1 0 1 1 0 1 0 1`) with a sidechain-style pump and filter
sweeps. Pieces go groove → breakdown (supersaw pad and lead) → build (roll,
filter rise, kick drop) → drop, twice.
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

Flags on the command line override the `.env` values. Use these two
settings rather than putting `--theme` into `YS_ENGINE_ARGS`: systemd splits
that at spaces and ignores quotes, which would break a multi-word theme.

## How a piece develops

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
   `.env` as `YT_STREAM_KEY` (see `.env.example`). Optionally set
   `YS_STREAM_BG` to a background image or looping video.
2. `stream/install_services.sh` installs and starts these systemd user units:

| Unit | What it does |
|---|---|
| `ystreamer-audio` | creates the `ystream_sink` PipeWire sink the stream captures from |
| `ystreamer-llm` | local llama.cpp server for the Director (skip with `--no-llm`) |
| `ystreamer-engine` | SuperDirt + Tidal + engine, `--resume`s the saved piece after any restart |
| `ystreamer-stream` | ffmpeg: limiter → -14 LUFS → RTMP, waveform + now-playing + live-code overlay, reconnects every 11 h |
| `ystreamer-watchdog` | restarts the engine on sustained silence or a stale heartbeat, sends a push via ntfy |
| `ystreamer-quality` | every 10 min: loudness, clipping and key-conformance check on a clip; bad harmony → automatic thumbs-down |

Run `loginctl enable-linger $USER` so the units keep running after you log out.
Test the whole thing on an **unlisted** stream for a few days before going public.

### Background image or video

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
the stream to apply changes (`systemctl --user restart ystreamer-stream`,
or Ctrl+C and `stream/stream.sh`).

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
| `music/theory.py` | keys, modes, progression library, voice leading |
| `music/templates.py`, `music/melody.py` | role templates (pad, keys, bass, melody, arp, drums, texture) that render in-key Tidal code |
| `music/presets.py` | curated, gain-balanced sound palette |
| `music/plan.py`, `music/director.py` | piece/section plans, the LLM Director with validation and fallback |
| `music/engine.py` | cycle clock, phrase-quantised scheduling (`jumpMod`), state/overlay files |
| `tidal/synthdefs.scd`, `tidal/superdirt_boot.scd` | custom SynthDefs and the master compressor/limiter |
| `stream/` | audio sink, ffmpeg streaming, watchdog, systemd units |
| `eval/` | analysis, quality monitor, feedback, preview renderer, dataset export |
| `tidal_backend.py`, `prompts/composer_prompt.md` | the old free-form agent loop (`orchestrator.py --legacy "..."`) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Ambiguous module name 'Sound.Tidal.Context': it was found in multiple packages: tidal-X tidal-X` | Tidal was installed twice. Run `python3 tidal/fix_ghc_env.py` (keeps the newest copy, backs up the file). |
| `Could not find module 'Sound.Tidal.Context'` | `cabal update && cabal install --lib tidal` |
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
