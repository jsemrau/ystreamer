# Live Coding Music with Local LLMs: Testing Creativity

*Encyclopedia Autonomica · Agents in creative domains*

**Live coding** is a performance practice in which a musician writes and
rewrites a program on stage while it plays. The audience sees the code
and hears every change. Performers such as Switch Angel build a track
from a single line, then keep revising it (a note here, a filter there, a
wrapper that doubles the hi-hats) until the piece has moved somewhere new.

That makes live coding a good **testbed for machine creativity**. The
task is open-ended: there is no right answer, only better or worse
choices in context. It is sequential, because every edit must follow
from the program as it is now. It is judged by ear, not by a test suite.
And it keeps going: a performer has to keep producing ideas for hours
without repeating itself or falling apart.

This article describes an architecture in which a **local LLM** (for
example a quantized Qwen model served by `llama.cpp`) performs a
live-coded [TidalCycles](https://tidalcycles.org) set that streams
without end. It covers the components, shows code from the reference
implementation ([`jsemrau/ystreamer`](https://github.com/jsemrau/ystreamer)),
and proposes a protocol for treating each performance as a creativity
test.

---

## 1. Why this is a creativity test and not a code-generation test

Asking an LLM to "write a lofi track in Tidal" measures recall: can the
model reproduce plausible syntax it saw during training? Local models
often fail even this. They produce type errors, notes out of key or
patterns that are too dense. More importantly, a one-shot program does
not show what creative behaviour actually looks like:

| One-shot generation | Live performance |
|---|---|
| One output, judged once | Hundreds of small decisions over time |
| Syntax errors are fatal | Invalid edits are rejected, and the music continues |
| Tests recall of examples | Tests *judgement*: what to change next, and why |
| No memory of its own work | The model must read its own program and build on it |
| Novelty is hard to separate from noise | Novelty is measured against the model's own history |

The key design move is to **separate the space of legal moves from the
choice among them**. The system guarantees that every move is
syntactically valid and musically safe (always in key, levels balanced,
values in range). The LLM is left with only the creative problem:
*which* move, *when*, and *why*. Anything the model adds on top of a
random walk through legal moves is its creative contribution, and that
contribution can be measured.

---

## 2. Architecture overview

```
                      ┌─────────────────────────────────────────────┐
                      │                 Performer                    │
                      │                                              │
  program as code ───▶│  phase machine  ─▶  prompt  ─▶  local LLM    │
  (what is playing)   │  build/groove/      (code,      (llama.cpp,  │
                      │  breakdown/...      legal       OpenAI API)  │
                      │                     moves)          │        │
                      │                                     ▼        │
                      │              built-in performer ◀─ JSON edit │
                      │              (fallback)          {op, role,  │
                      │                                  value,      │
                      └──────────────────────┬───────────  reason}   │
                                             │  Edit       ──────────┘
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
                               -- reason) + spoken narration      (YouTube live)
```

The components, from the inside out:

1. **Program model.** The live program as structured data: one line per
   musical role, each rendered to a Tidal statement (`d1 $ ...`).
2. **Block library.** Curated, level-matched starting lines per style
   (drum kits, basses, comping keys, melodies, textures). These are the
   musician's "instruments".
3. **Edit language and validator.** A small set of operations (add,
   mute, change a pattern, ride an effect, wrap in a transformation, nudge
   the tempo). The validator checks them and applies them immutably.
4. **Performer.** A phase machine that shapes each track into an arc,
   plus the LLM prompt and a built-in fallback performer.
5. **Live engine.** A clock that applies one edit per N bars exactly on
   the bar line, sends only the changed statements, and publishes the
   code, reasons and narration.
6. **Audio stack.** GHCi running Tidal, SuperCollider with SuperDirt and
   custom SynthDefs, a master compressor and limiter, and ffmpeg for
   streaming.

---

## 3. Components

### 3.1 The program model

A program is a track (key, tempo, chord progression) plus a set of
`LineSpec`s, one per role. Each role is pinned to a Tidal channel, so the
screen always reads like a hand-written set:

```python
ROLE_CHANNELS = {"beat": 1, "bass": 2, "chords": 3, "keys": 4,
                 "lead": 5, "acid": 6, "texture": 7, "perc": 8}
```

A line is a **template body with named slots**, plus effects, custom
synth controls and wrappers. Keeping these parts separate is what makes
small, meaningful edits possible:

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

    def render(self, harmony: dict[str, str]) -> str:
        body = self.body.format(**harmony, **self.patterns)
        tail = "".join(f" # {name} {value}" for name, value in self.fx)
        tail += "".join(f' # pF "{name}" {value}' for name, value in self.pfs)
        expr = body + tail
        for wrapper in reversed(self.wrappers):
            expr = f"{wrapper} $ {expr}"
        return expr
```

**Harmony is injected, never generated.** The track supplies `{mode}`,
`{root}`, `{chords}` and `{roots}`. Melodic slots hold *scale degrees
relative to the current chord* (0 = root, 2 = third, 4 = fifth), so any
digit string the LLM writes is in key by construction:

```haskell
d5 $ n (scale "minor" ("~ 4 ~ ~ 2 ~ ~ ~" |+ "<0 -2 3 -3>") - 3 + 12) # s "ys_glass" # room 0.45
--                     ^ the LLM edits only this string
```

This one decision removes the most common failure of local models in
music (wrong notes). What is left are real creative decisions: contour,
rhythm and density.

### 3.2 The block library

Blocks are the curated sound palette. Each has a body, variants for each
slot, default effects measured to sit well in the mix, and optionally
starting wrappers:

```python
Block("acid", "acid",
      'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
      {"motif": ["0 4 0 9 7", "0 7 0 3", "0 2 0 4 7", "0 5 0 9 7 12"]},
      fx=[["gain", "0.98"], ["legato", "0.6"]],
      pfs=[["acid", "0.546"]],                        # the squelch knob
      wrappers=['(|* gain "[0.67 0.91 1 1]*4")'])     # 16th accent pattern
```

Curating the palette is a deliberate division of labour. Sound design
and gain staging are engineering problems that local LLMs do badly and
that humans can solve once. What to *do* with the sounds is where the
model's creativity shows. The library is also where taste gets encoded:
in the reference implementation the lofi palette has no sustained pad,
because a pad droning under the whole set made every track sound the
same.

### 3.3 The edit language

Every move the performer can make is one of eleven operations:

| op | Example | Musical meaning |
|---|---|---|
| `add` | `{"op":"add","block":"felt_melody"}` | Bring in a new line |
| `remove`, `mute`, `unmute` | `{"op":"mute","role":"beat"}` | Arrangement: drops, breakdowns |
| `set_pattern` | `{"op":"set_pattern","role":"lead","name":"melody","value":"4 ~ 2 ~ 0 ~ ~ ~"}` | Melody or rhythm variation |
| `set_fx`, `remove_fx` | `{"op":"set_fx","role":"keys","name":"lpf","value":"(slow 8 $ range 800 3000 sine)"}` | Timbre, space, movement |
| `set_pf` | `{"op":"set_pf","role":"acid","name":"acid","value":"0.7"}` | Synth-specific knobs |
| `wrap`, `unwrap` | `{"op":"wrap","role":"keys","name":"jux rev"}` | Structural transformation |
| `tempo` | `{"op":"tempo","value":"86"}` | Energy |

`apply_edit` validates each operation and returns a *new* program, so a
rejected edit cannot corrupt the running state:

```python
def apply_edit(program: Program, edit: Edit, max_lines: int = 7) -> Program:
    new = copy.deepcopy(program)
    ...
    elif op in ("set_fx", "remove_fx"):
        if edit.name not in FX_RANGES or edit.name not in ROLE_FX.get(role, list(FX_RANGES)):
            raise EditError(f"effect {edit.name!r} not allowed on the {role}")
        line.fx = [pair for pair in line.fx if pair[0] != edit.name]
        if op == "set_fx":
            line.fx.append([edit.name, _check_number(edit.value, *FX_RANGES[edit.name])])
    elif op == "set_pattern":
        value = check_pattern(edit.value)          # charset, length, balanced brackets
        if not line.drums and re.search(r"[a-z]", value.replace("~", "")):
            raise EditError("melodic patterns are scale degrees (numbers), not names")
        line.patterns[edit.name] = value
    elif op == "wrap":
        if edit.name not in allowed_wrappers(line):  # e.g. no `jux rev` on the bass
            raise EditError(f"wrapper {edit.name!r} not allowed here")
        if len(line.wrappers) >= 3:
            raise EditError("at most 3 wrappers per line")
        line.wrappers.append(edit.name)
    ...
    return new
```

Values are either plain numbers or a single modulation form, both
checked against per-effect ranges:

```python
FX_RANGES = {"lpf": (200, 9000), "room": (0.0, 0.8), "delay": (0.0, 0.6), ...}
_check_number("(slow 8 $ range 800 3000 sine)", *FX_RANGES["lpf"])   # ok
_check_number("99999", *FX_RANGES["lpf"])                          # EditError
_check_number("1; hush", *FX_RANGES["lpf"])                        # EditError
```

Role rules carry musical common sense: the bass takes no reverb or delay
and no `jux rev`, textures take no rhythmic wrappers, and there is a cap
on how many lines play at once. They do not limit creativity. They
remove moves that are never good.

### 3.4 The performer: phases, prompt, fallback

Unstructured freedom produces aimless music. The performer runs a small
**phase machine** that gives every track a dramatic arc and tells the
model what kind of move fits now:

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

The **prompt** shows the model the program *as code* (what the audience
sees), the current phase with a one-line brief, each line's editable
parts as JSON, the blocks that could still be added, the legal
operations, and the value ranges. The model replies with one JSON object:

```json
{"op": "set_pattern", "role": "lead", "name": "melody",
 "value": "4 ~ 3 ~ 0 ~ ~ ~",
 "reason": "answer the phrase a step lower",
 "narration": "The melody leans down, like it's getting sleepy."}
```

`reason` is shown on screen as a code comment and `narration` is spoken
by a TTS voice. Requiring a reason is cheap and useful. It makes the
model state an intention before committing to a move, and it gives the
evaluator a record of what the model *meant*, not only what it did.

The LLM call itself is plain OpenAI-compatible HTTP, so any local server
works (`llama.cpp`'s `llama-server`, vLLM, LM Studio, Ollama's `/v1`):

```python
def _llm_edit(self, program):
    try:
        reply = llm.complete(self.model_id,
                             "You are a live coder performing TidalCycles. Reply with JSON only.",
                             self._prompt(program), max_tokens=500)
        edit = self._edit_from_json(llm.extract_json(reply), program)
        apply_edit(program, edit, self.settings["max_lines"])   # dry-run validation
        self._llm_failures = 0
        return edit
    except (llm.LLMError, EditError, ValueError, TypeError, KeyError) as e:
        self._llm_failures += 1
        self.log(f"[Performer] LLM edit unusable ({e}); built-in performer takes this one")
        if self._llm_failures >= 3:                    # back off a struggling model
            self._skip_llm, self._llm_failures = 5, 0
        return None
```

The **built-in performer** makes the same kinds of moves at random
within each phase (add the next role in the style's order, mutate one
note, ride a filter, try a wrapper). It keeps the music going when the
model is slow, loading or wrong. It also serves as the **null model** for
the creativity test in section 4.

### 3.5 The live engine

The engine is where the performance becomes real time. It asks the
performer for the next edit *in the background* while the current bars
play, then applies it exactly on a bar boundary, re-evaluating only the
lines that changed:

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
    statements = changed_statements(program, new)          # e.g. ["d5 $ n (scale ...)"]
    if self._type_ok(statements) and self._send(statements):
        program = new
        self.log(f"✎ {edit.describe()}   -- {edit.reason}")
        self._publish(program, highlight=edit.role, comment=edit.reason)
    next_cycle += edit_bars
```

Thinking in the background matters for local models. A 35B model on a
consumer GPU can take 5 to 20 seconds per reply. With edits every 4 bars
at 80 BPM (12 seconds), the engine absorbs that latency instead of
stuttering. An optional GHCi type checker compiles every changed
statement against the real Tidal library before it is sent, which is a
second safety net behind the validator.

What viewers see is the program itself, with the most recent edit marked:

```haskell
setcps (84/60/4)

  d1 $ swingBy (1/8) 4 $ s "[bd ~ [~ bd] ~ ~ ~ bd ~, ~ sn ~ sn, [hh27 hh27:3]*4]" # gain 0.92 # lpf 5000
  d2 $ n (scale "major" ("0 ~ ~ 0 ~ ~ 4 ~" |+ "<0 -2 3 -3>") + 3 - 24) # s "ys_sub" # gain 0.95 # lpf 500
  d4 $ jux rev $ struct "t ~ ~ t ~ ~ ~ ~" $ n (scale "major" "<[0,2,4,6] [-2,0,2,4] ...>" + 3) # s "ys_keys"
▶ d5 $ n (scale "major" ("~ 4 ~ ~ 2 ~ ~ ~" |+ "<0 -2 3 -3>") + 3 + 12) # s "ys_glass" # delay 0.4
  d7 $ slow 4 $ note "0" # s "ys_vinyl" # gain 1.1 # hpf 300 # room 0.35

-- let the melody echo into the space
```

### 3.6 The audio stack

- **GHCi + Tidal** receives the statements through a persistent session
  that starts from `BootTidal.hs`.
- **SuperCollider + SuperDirt** plays them. Custom SynthDefs (electric
  piano, sub, glass bell, acid, gated saw, supersaw with sidechain duck)
  were rendered offline and loudness-measured, so the blocks start
  balanced. A master compressor and limiter keep any edit from clipping
  the stream.
- **ffmpeg** captures the SuperCollider output plus an overlay of the
  code and pushes it over RTMP. A watchdog restarts crashed parts, and
  the engine saves its state after every edit so it can resume where it
  left off.

---

## 4. Testing creativity: a protocol

Because every move is legal, you can compare a local model to the
built-in random performer and to other models on the same footing. We
suggest four families of measures, all computable from the engine's logs
(`runtime/history.jsonl` stores every applied edit as `op`, `role`,
`name` and `value`, with its reason and the full program afterwards; the session log records every rejected or
unusable LLM reply).

### 4.1 Competence: can it play at all?

- **Validity rate**: the share of LLM replies that parse *and* pass
  `apply_edit`. Below about 70%, the fallback performer is doing most of
  the playing and nothing else can be measured.
- **Latency**: the share of replies that arrive before their bar
  boundary.

### 4.2 Divergence: does it produce ideas?

- **Operation entropy**: Shannon entropy over `op` types. A model that
  only rides `lpf` is not performing.
- **Novel material**: the number of distinct pattern strings the model
  writes that come neither from the block library nor from its own
  history.
- **Self-repetition**: how often an edit exactly undoes or repeats one
  from the last *k* edits.

### 4.3 Coherence: do the ideas fit together?

- **Phase fit**: in `breakdown`, does it strip back? In `build`, does it
  add? Score each edit against the phase brief.
- **Motivic development**: do melody edits change one or two degrees of
  the previous pattern (development) or replace it completely (a jump)?
  Edit distance between successive values of the same slot measures
  this.
- **Reason and action consistency**: an LLM judge, or a human, checks
  whether `reason` actually describes the edit ("darken the keys" should
  lower `lpf`, not raise it).

### 4.4 Taste: is it any good?

This measure needs ears. Run blind A/B listening sessions: the same
style, seed and duration, one set played by the built-in performer and
one by the model under test. Listeners pick the one they would keep
listening to. A 24/7 stream adds a naturally occurring signal on top:
how long viewers stay, broken down by which performer is live.

### 4.5 A minimal scorer

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
    return {"edits": total, "op_entropy_bits": round(entropy, 2),
            "repeat_rate": round(repeats / total, 2),
            "reasoned": round(reasons / total, 2)}
```

Run each performer on the same style and seed for the same simulated
time (`--dry-run` uses a simulated clock, so an hour-long set runs in
seconds with the built-in performer, or as fast as the model can reply
with an LLM), score each run, and compare. The built-in performer sets
the **random baseline**. A model is only *creative* in the sense tested
here if it beats that baseline on coherence and taste without losing on
divergence.

```bash
# baseline: the built-in random performer
python orchestrator.py --dry-run --style lofi --llm none --seed 7 --pieces 3
# candidate: a local model served by llama.cpp on :8080
python orchestrator.py --dry-run --style lofi --llm local --model qwen-agentworld-35b --seed 7 --pieces 3
```

---

## 5. Observations and design lessons

- **Constrain the vocabulary, not the imagination.** Scale-degree
  patterns, curated blocks and ranged effects remove whole classes of
  errors while leaving thousands of distinct musical moves open. Local
  models that could not write a valid Tidal track can still perform
  sensibly in this setup.
- **Code is the right artefact.** Earlier versions had the LLM pick
  high-level arrangement plans ("verse, energy 0.5, pad + keys") that an
  engine rendered. The music was correct but lifeless, and the same
  sustained synth dominated every piece. Making the *program itself* the
  thing the model grows and revises, one audible change at a time, made
  both the music and the model's decisions legible.
- **One small edit at a time.** Small edits keep each decision
  attributable, which is what makes evaluation possible, and they sound
  like a human performer instead of a playlist.
- **Always have a fallback.** A stream that stops when the model fails
  is useless for testing a model over long runs. With a fallback, model
  failures become data points instead of outages.
- **Ask for reasons.** A one-line `reason` costs a few tokens, makes the
  model state an intention before it acts, and is the most useful single
  artefact for judging whether a model "meant" what it played.

## 6. Limitations

- The palette is curated, so the test measures *compositional and
  performative* creativity within a sound world, not sound design.
- The phase machine imposes a track arc. Models are tested on how they
  fill that arc, not on whether they would invent one. A variant that
  lets the model choose phase transitions is a natural extension.
- Metrics of divergence can be gamed by randomness, which is why the
  random built-in performer is the required baseline and why listening
  tests stay the final judge.

## See also

- TidalCycles documentation: <https://tidalcycles.org/docs/>
- Reference implementation: `music/livecode.py`, `music/blocks.py`,
  `music/performer.py`, `music/live_engine.py` and `prompts/performer.md`
  in [`jsemrau/ystreamer`](https://github.com/jsemrau/ystreamer)
- The 24/7 stream: YouTube **@sleeplesscomposer**
