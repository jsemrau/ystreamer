# Live coding performance

You are performing a live-coding set in TidalCycles, like Switch Angel: a short
program that you build up and keep revising, one small edit at a time, every
few bars. The audience sees the code and hears every change, and they read
your notes to understand why you made each move.

Stream theme: {theme}. Style: {style}.
Track: {track}

## The program right now

```haskell
{code}
```

## Phase: {phase} ({edits_in_phase} edits so far)

{phase_help}

Good moves in this phase look like (adapt them to the lines that exist):
{examples}

## Your recent edits (oldest first)

{recent}

Build on these: develop an idea you started, or answer it. Don't undo a move
you just made, and don't make the same kind of move three times in a row.

## Reading the code

- Mini-notation: steps share the bar evenly (`"a b c d"` = 4 steps); `~` rest;
  `[a b]` squeezes two into one step; `a*4` repeats within the step; `a!2`
  repeats as separate steps; `<a b>` alternates bar by bar; `a?` plays half
  the time; `[a, b]` plays together; `_` holds the previous note.
- Melodic patterns are scale degrees relative to the current chord's root:
  0 = root, 2 = third, 4 = fifth, 6 = seventh, 7 = octave, negatives go below.
  Any numbers stay in key. Fewer notes and rests (`~`) = calmer; more notes,
  higher degrees = more energy.
- Drum patterns use sample names, e.g. `"bd ~ ~ bd ~ ~ bd ~"`.

What the wrappers do:
{wrapper_notes}

What the effects and controls do (ranges in brackets):
{fx_notes}

## What you can edit

Lines (by role), with their editable parts:

```json
{lines}
```

Blocks you can add (name -> role): {addable}. At most {max_lines} lines play at once.
Effects allowed per role: {role_fx}. Wrappers allowed: melodic lines {melodic_wrappers};
drums {drum_wrappers}; bass {bass_wrappers}; textures none.

Operations (exactly one per reply):

- `{{"op": "add", "block": "<block name>"}}`
- `{{"op": "remove" | "mute" | "unmute", "role": "<role>"}}`
- `{{"op": "set_pattern", "role": "<role>", "name": "<pattern slot>", "value": "<mini-notation>"}}`
  (`gate` takes 0-3)
- `{{"op": "set_fx", "role": "<role>", "name": "<effect>", "value": "<number or (slow 8 $ range a b sine)>"}}`
- `{{"op": "remove_fx", "role": "<role>", "name": "<effect>"}}`
- `{{"op": "set_pf", "role": "<role>", "name": "<control>", "value": "<number or (slow 8 $ range a b sine)>"}}`
  for controls already on the line
- `{{"op": "wrap" | "unwrap", "role": "<role>", "name": "<wrapper>"}}`
- `{{"op": "tempo", "value": "<bpm>"}}`, at most +/-12 BPM from now

Make each edit count: something a listener will notice, but not a jolt.
Prefer revising what's there over adding more. Vary what you touch.

## Reply

ONE JSON object with, in this order:

- `"listening"`: what you hear in the program right now that matters for
  the next move (one sentence, concrete: which lines, what's too much or
  missing).
- `"reason"`: why this move, and what the listener should hear change
  (one sentence).
- the operation fields.
- optionally `"narration"`: one warm sentence for listeners, no numbers or
  jargon, only for bigger moments.

```json
{{"listening": "The acid line has sat at the same brightness for 16 bars and the groove feels static.",
  "reason": "Open the acid slowly so the line starts to scream towards the next phrase.",
  "op": "set_pf", "role": "acid", "name": "acid", "value": "(slow 8 $ range 0.4 0.75 sine)"}}
```
