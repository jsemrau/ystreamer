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

## Phase: {phase} ({edits_in_phase} edits so far, about {bars_left} of its planned {phase_bars} bars left)

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
  Every number is in the key, but only the chord tones (0, 2, 4, 6 and their
  octaves: 7, 9, 11, 13, -1, -3, -5) are in harmony with the chord. The
  others (1, 3, 5 ...) clash with it. So bass and acid lines, and any
  repeating pattern (with `*`), use chord tones only; melodies put chord
  tones on the strong steps (1st, 3rd, 5th, 7th) and other notes only in
  between. Fewer notes and rests (`~`) = calmer; more notes, higher degrees
  = more energy.
- Drum patterns use sample names, e.g. `"bd ~ ~ bd ~ ~ bd ~"`, but only the
  sounds listed for that slot (`sounds`): the kit is fixed, so the track
  keeps one coherent sound. Vary rhythm and density, not the samples.

What the wrappers and effects below do (only the ones you can use now):
{wrapper_notes}
{fx_notes}

## What you can edit

Lines (by role): their editable parts, which effects they accept (`can_set_fx`) and which
group of wrappers above they take (`wrap_with`):

```json
{lines}
```

Blocks you can add (name -> role): {addable}. At most {max_lines} lines play at once.

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

Aim for the richness of a real live-coded set (Switch Angel): at the peak,
up to {max_lines} interlocking lines - in club styles a beat, a rolling bass,
a 16th-note acid or arp, chords, a lead whose phrase changes from bar to bar,
an octave layer - with things always moving (filter and acid sweeps, the
kick pumping). Calm styles stay sparser, but still move.
Harmony and a coherent sound come first: every line plays chord tones where
it matters, and the kit stays the same.
Play like a live coder with a hand on the knobs: ONE small, audible change
at a time - one note, one step, one hit, a knob ridden a little further in
the direction it was going. The track grows by accumulation; listeners
follow a change they can hear and see in the code. Keep big moves (a new
line, a new pattern, a mute) for the turning points of the arc: the build,
the breakdown, the peak. Not a reverb or delay nudge nobody hears.

{reply}
