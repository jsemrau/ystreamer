# Improvement Plan: harmonic, listenable, 24/7 YouTube stream

Goal: AI agents keep composing TidalCycles music that sounds good to people,
and it runs forever as a YouTube live stream.

> **Status: implemented (phases 0-5).** See the README for usage. Where each
> phase lives:
>
> | Phase | Implementation |
> |---|---|
> | 0: quick wins | `music/normalize.py` (scale-root rewrite, key check, synth swaps), fixed prompts, `tidal_backend.py` (`--legacy`): crossfades, layer cap, timeouts, bounded histories; `config.py` + `.env.example` for paths |
> | 1: harmony engine | `music/theory.py`, `music/templates.py`, `music/melody.py`, `music/presets.py` |
> | 2: director redesign | `music/plan.py`, `music/director.py`, `music/engine.py`, `prompts/director_*.md`, `prompts/free_layer.md`, `music/validator.py` (GHCi `:t` checks) |
> | 3: sound quality | `tidal/synthdefs.scd` (8 SynthDefs), `tidal/superdirt_boot.scd` (compressor + limiter) |
> | 4: streaming and ops | `stream/stream.sh`, `stream/setup_audio.sh`, `stream/watchdog.py`, `stream/systemd/*`, `stream/install_services.sh`, `--resume` |
> | 5: quality measurement | `eval/analyze.py`, `eval/monitor.py`, `eval/feedback.py`, `eval/preview.py`, `eval/export_dataset.py` |
>
> Not yet verified on real hardware: the generated Tidal code passed Python
> tests but has not been type-checked against real Tidal in this environment
> (run `orchestrator.py --selftest`). The SynthDefs and the PipeWire/JACK
> routing have not been heard yet either.

---

## 1. Diagnosis: why it sounds bad today

### 1.1 A real bug: the key/scale syntax the prompts teach is wrong
The composer prompt (`prompts/composer_prompt.md` lines 54, 81, 88, 110) and the
director prompt (line 23) teach `n (scale "c minor" "0 3 5 7")`.

Tidal's `scale` takes a **scale name only** (`"minor"`, `"major"`,
`"dorian"`...). It does not take a root. In Tidal's source, a name it doesn't
recognise falls back to the scale `[0]`, so every scale degree becomes a whole
octave:

| degree | expected in C minor | what `scale "c minor"` plays |
|---|---|---|
| 0 | C | C |
| 3 | F | C, **3 octaves up** |
| 5 | Ab | C, **5 octaves up** |
| 7 | C (octave) | C, **7 octaves up** (inaudible or shrill) |

So each "in key" layer really plays one pitch class spread over huge octave
jumps, and layers set to "g minor", "c minor" and so on all fall back to the
same broken result. **This alone explains most of the "not harmonic" feeling.**

The correct form is the scale name plus a root offset:
```haskell
d1 $ n (scale "minor" "0 2 4 7" + "c") # s "superpiano"      -- C minor
d1 $ n (scale "minor" "0 2 4 7") |+ n 7 # s "superpiano"     -- G minor
```

### 1.2 Nothing shares the harmony between layers
- Each channel is written separately by an LLM that sees only the other
  channels' code as text. There is no shared chord progression or harmonic
  clock, so the pad, bass and melody change chords at different times
  (`slow 4` on one, `<...>` on another, plain lists on a third).
- Code without `scale` (for example `n "0 3 5 7"`) is chromatic and ignores
  `SONG_STATE["key"]` entirely.
- "Chord pads" play single root notes (`n (scale ... "<0 5 2 6>")`), not chords.

### 1.3 The LLM is writing raw code, and that is the wrong level
A 35B-A3B local model producing free-form Haskell is asked to be the composer,
arranger, sound designer and mix engineer at once. It gets syntax right (with
effort) but has no ears. Every decision (pitch, register, gain, synth, filter)
is a fresh guess, so the result is random.

`validate_tidal_syntax` is regex-only. It can't catch type errors or
out-of-key notes. The fine-tuning data (`tidal_10k_dataset.csv`,
`tidal_llm_train.jsonl`) comes from templates: 5 styles, mostly single-note
patterns (`n (scale "lydian" "2")`), and it uses the same broken idioms. It
would teach a model very little about harmony.

### 1.4 The piece changes too often and never settles
- A new change arrives every `director_interval` (8 s) plus LLM time, which is
  roughly one change every 10 to 12 bars at 0.8 cps. For ambient or lofi music
  a layer should stay put for 1 to 4 minutes.
- `d1 $ ...` replaces the pattern immediately, mid-phrase, with no crossfade.
- The director sees only the last 4 tasks, so it has no long-term arc. The
  prompts favour "add", so layers pile up (up to 16 channels).

### 1.5 No mix or mastering
- Gains are guessed per layer. There is no master limiter, compressor or
  loudness target, so the output clips when layers stack.
- The synth palette includes harsh defaults (`supersaw`, `superzow`,
  `superchip`, `hoover`) with no tuned presets.

### 1.6 Not built to run forever
- The Tidal boot file path and the recording, model and HF paths are hardcoded
  to `/home/moebius/...`. The boot path even points at a differently named
  repo.
- `tasks`, `song_history`, `executed_tasks_log`, `accumulated_logs` and
  `agent_conversation_log` grow without bound, so memory leaks over days.
- `agent.run()` has no timeout. A hung llama-server stalls the loop. That
  alone is harmless, because Tidal keeps playing, but nothing detects it.
- No watchdog: if `sclang`, `scsynth` or `ghci` crashes, the stream goes silent
  and stays silent.
- Recording writes one endless MP3. There is no streaming output yet.

---

## 2. Target architecture: "the LLM directs, the engine plays"

Let the LLM make **musical decisions** in a small, structured vocabulary. Let
deterministic Python code turn those decisions into Tidal code from
**hand-tuned, tested templates** that are harmonic by construction.

```
 ┌────────────────────┐   JSON plan    ┌───────────────────────┐  Tidal code  ┌───────┐
 │ Director (LLM)     │ ─────────────▶ │ Music Engine (Python) │ ───────────▶ │ GHCi  │─▶ SuperDirt ─▶ master bus ─▶ ffmpeg ─▶ YouTube
 │ piece / section /  │                │ harmony + templates + │  quantized,  └───────┘        (limiter)
 │ energy / layers    │ ◀───────────── │ presets + scheduler   │  crossfaded
 └────────────────────┘  song state    └───────────────────────┘
          ▲                                     ▲
          │ optional                            │ validated via real ghci :t
 ┌────────────────────┐                         │
 │ Composer (LLM)     │ ── motif / rhythm ──────┘  (only ever emits scale degrees
 │ one "free" layer   │                             and rhythms, never raw pitches)
 └────────────────────┘
```

### 2.1 Director output (JSON, not prose)
```json
{
  "section": "build",
  "energy": 0.55,
  "progression": "i-VI-III-VII",
  "layers": {
    "drums":   {"preset": "lofi_brush", "density": 0.4},
    "bass":    {"preset": "sub_round",  "rhythm": "roots_half"},
    "pad":     {"preset": "warm_pad",   "voicing": "open"},
    "keys":    {"preset": "rhodes",     "rhythm": "offbeat_stabs"},
    "melody":  null,
    "texture": {"preset": "vinyl_air"}
  },
  "hold_bars": 32
}
```
The engine validates this with a schema. Invalid output means "keep playing
what's there". No regex scraping of prose.

### 2.2 Harmony engine (the core fix)
A small Python module, `harmony.py`, that owns:
- **Key and mode** (for example C minor), with a Tidal root offset and a
  Tidal scale name.
- **A progression library**: roman numerals mapped to chord symbols in the
  current key, with voice leading. Choose the inversion that moves each voice
  the least, and keep the pad in about C3 to C5.
- **One shared harmonic clock**: every layer uses the same chord length
  (for example 1 chord = 2 or 4 cycles) and the same `<...>` sequence, so
  everything changes chords together.
- **Role-specific rendering** from the same progression:

```haskell
-- pad: full voiced chords, 4 cycles each
d2 $ slow 4 $ n "<c'min7'ii af'maj7 ef'maj7'ii bf'dom7>" # s "superpiano" # legato 1.1 # gain 0.55 # room 0.4 # size 0.8 # lpf 2400
-- bass: chord roots, same clock, low register
d3 $ slow 4 $ n "<c2 af1 ef2 bf1>" # s "superfm" # gain 0.7 # lpf 400
-- melody: scale degrees snapped to the key, strong beats on chord tones
d4 $ n (scale "minorPentatonic" "<[0 ~ 2 4] [~ 4 3 ~] [2 ~ 0 ~] [~ ~ -1 0]>" + "c5") # s "superpiano" # gain 0.45 # delay 0.3 # delaytime 0.375 # delayfeedback 0.3
```
- **Melody generation** in Python rather than by the LLM: motifs built from
  chord tones on strong beats, passing tones from the scale on weak beats,
  stepwise motion preferred, phrases that end on stable degrees, and
  call-and-response repetition with variation. The LLM may *choose* motif
  character ("rising", "sparse", "answer phrase"). The engine picks the notes.
- **Modulation between pieces**: move to related keys only (relative
  major/minor, ±1 step on the circle of fifths) at piece boundaries.

### 2.3 Curated sound palette
A preset library with roles, for example `presets.yaml`:
`role → preset → {synth or sample, register range, gain, lpf, room, size,
legato, attack/release, orbit}`, each tuned **by ear** once.
- Remove harsh defaults (`supersaw`, `superchip`, `hoover`, `superzow`)
  unless a preset tames them.
- Add a few custom SynthDefs to `SuperDirt` (warm pad, Rhodes-style electric
  piano, soft sub bass, felt piano). SuperDirt's defaults are not
  mix-ready.
- Optionally add good-quality CC0 multisampled instruments (piano, strings,
  guitar) and lofi drum kits, and check the license of every sample pack used
  on a public stream.

### 2.4 Scheduler: musical time instead of wall-clock time
- Apply changes only on phrase boundaries (every 8, 16 or 32 bars). Use
  Tidal transitions such as `xfadeIn 2 8 $ ...` for layers and `clutchIn` for
  drums instead of hard `d2 $`.
- Keep a `hold_bars` minimum per section (ambient: 32 to 64 bars).
- Allow at most 4 to 6 active layers. The energy level decides how many.

### 2.5 Mixing and mastering
- **SuperCollider master chain** (added after `~dirt.start`): a gentle bus
  compressor plus a `Limiter.ar(..., 0.9)` at the tail of bus 0.
- **Gain staging**: each preset has a fixed gain. Energy scales all gains
  within a ±3 dB window, never beyond it.
- **Low end**: an automatic `hpf` on everything except bass and kick (this
  generalises the existing `add_hpf` normalisation).
- **Loudness**: -14 LUFS integrated for YouTube, using ffmpeg `loudnorm` or
  `alimiter` in the stream chain (see 2.7).

### 2.6 Long-form structure for infinite play
Organise the stream into **pieces** of about 8 to 20 minutes:
1. At piece start, the Director picks a title, key, tempo (cps), mood and
   section plan (intro → A → build → B → breakdown → A' → outro).
2. The engine plays the plan, and the Director adjusts only at section
   boundaries.
3. Between pieces there is a 16 to 32 bar crossfade into a related key or
   tempo, and the title overlay updates.
4. The piece history (title, key, progression) is kept, so the director
   avoids repeating the same key or progression several times in a row.

### 2.7 24/7 YouTube infrastructure
- **Audio routing**: SuperCollider → dedicated PipeWire null sink
  (`ystream_sink`) → ffmpeg. This separates it from desktop audio and works
  headless.
- **Video**: ffmpeg composes a background (a loop or generated visuals), a
  waveform or spectrum, and "Now playing: <title> · <key> · <section>" plus
  the live Tidal code, read from text files that the engine rewrites:
  ```bash
  ffmpeg -f pulse -i ystream_sink.monitor -stream_loop -1 -i bg.mp4 \
    -filter_complex "[0:a]alimiter=limit=0.9,loudnorm=I=-14:TP=-1.5:LRA=11[a];\
      [0:a]showwaves=s=1280x160:mode=cline:colors=white@0.6[w];\
      [1:v][w]overlay=0:H-160,drawtext=textfile=now_playing.txt:reload=1:x=40:y=40:fontsize=32:fontcolor=white[v]" \
    -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -b:v 3500k -g 60 -pix_fmt yuv420p \
    -c:a aac -b:a 160k -ar 44100 -f flv "rtmp://a.rtmp.youtube.com/live2/$YT_STREAM_KEY"
  ```
- **Process supervision**: run llama-server, sclang, ghci/engine and ffmpeg
  as separate `systemd` units (or supervisord) with `Restart=always`.
- **Watchdogs**:
  - Silence detection (ffmpeg `silencedetect` or an RMS probe). After more
    than 10 s of silence, restart the audio stack and **replay the saved
    `current_patterns` snapshot**, which is kept in `state.json`.
  - A timeout on every LLM call (for example 60 s). On timeout or error, keep
    playing, and after N failures fall back to the deterministic engine only.
    The music must never depend on the LLM being up.
  - A heartbeat log and optional alerting (a Telegram or ntfy push on
    restarts).
- **Resource hygiene**: ring buffers (`collections.deque(maxlen=…)`) for all
  histories and logs, rotating log files, and recordings rotated hourly
  (or turned off: YouTube archives the stream).
- **Config**: move every `/home/moebius/...` path into `.env`, and make the
  boot file path relative to the repo.
- **Stream restarts**: YouTube ends very long streams (about 12 h without a
  reconnect can be archived or split). Plan a scheduled ffmpeg reconnect
  every 6 to 12 h, and use a persistent stream key.

---

## 3. Phased roadmap

### Phase 0: Quick wins (1 to 2 days, big audible improvement)
1. Fix the `scale "c minor"` idiom in both prompts, using the scale name plus
   a root offset. Add a normaliser in `normalize_tidal_code` that rewrites
   `scale "<root> <mode>"` to `scale "<mode>" ... + "<root>"`, so old habits
   are corrected automatically.
2. Make the key mandatory: the composer must use `scale` and the current key,
   and the normaliser rejects code that uses plain chromatic `n "..."` on
   melodic synths.
3. Change cadence: raise the default `director_interval` to 60 to 120 s and
   apply changes with `xfadeIn i 8`. Cap active layers at 5.
4. Restrict instruments to a short whitelist of pleasant ones with fixed
   gains, for example `superpiano`, `superfm`, `supermandolin`, `bd`, `sn`,
   `hh`, `cp`, `pad`.
5. Add a master limiter in the `sclang` boot code.
6. Remove hardcoded paths and bound all history lists.

### Phase 1: Harmony engine (about 1 week)
- `harmony.py`: keys, modes, progression library, voice leading, and chord
  symbols to Tidal mini-notation.
- `templates/`: role templates (pad, bass, keys, arp, melody, drums, texture)
  that take `(key, progression, energy, preset)` and return Tidal code.
- A melody generator constrained to chord tones and the scale.
- Unit tests: every generated note is in key; every layer has the same chord
  clock; register ranges are respected.

### Phase 2: Director redesign (about 1 week)
- JSON-schema director output (2.1), with the piece and section plan (2.6).
- The scheduler (2.4) applies the plan on bar boundaries.
- The composer LLM becomes optional: at most one "free" layer, wrapped by the
  engine in the current `scale` and root, and validated with a real
  `ghci :t` check in a second, sandboxed GHCi before it is sent.

### Phase 3: Sound quality (ongoing)
- Custom SynthDefs and a sample library; tune the presets by ear.
- Per-style preset packs (lofi, ambient, downtempo, piano) so the stream can
  rotate styles.

### Phase 4: Streaming and operations (3 to 5 days)
- PipeWire null sink, the ffmpeg RTMP pipeline, overlays, systemd units,
  watchdogs and state snapshot/replay.
- A 72-hour soak test on an unlisted stream before going public.

### Phase 5: Quality measurement
- **Automatic**: record a 60 s clip per section and check integrated LUFS,
  true-peak, clipping count, and a key-conformance score (the share of
  chroma energy inside the key's pitch classes, using `librosa`). Flag or
  roll back changes that score badly.
- **Human**: a periodic A/B listening session and a "thumbs down" hotkey that
  logs the current state, so bad presets and progressions can be pruned.
- If fine-tuning is still wanted later, build the dataset from **approved
  engine outputs plus ratings**, not from the current random templates.

---

## 4. What to keep
- The orchestrator shell, logging tee and signal handling.
- GHCi error detection plus the retry loop. It becomes the validator for the
  optional free layer.
- The local llama-server integration. The Director's JSON task is much easier
  for a small model than writing Haskell, so local models will do better.
- The `SONG_STATE` concept. It grows into the engine's state object and is
  persisted to disk.
