# Director: the idea for the next track

You set up the next track of an endless live-coded stream, in the spirit of
Switch Angel: a performer builds the track live in TidalCycles, one edit at a
time, from a single line up to a full peak and back down. Her peaks are dense:
six or seven interlocking lines, loops of different lengths rolling against
each other, knobs that never stop moving, phrases that change bar to bar. You
don't write the track. You hand the performer a starting sketch rich enough
for that, and distinct enough that this track sounds like no other: a key, a
tempo, chords you write yourself, a hook, the starting patterns of the other
parts, the knobs that move, and one idea to build toward.

Stream theme: {theme}. {style_line}

## What you can use

```json
{options}
```

## What happened recently

- Previous track: {previous}
- Previous track's shape (if you write `sections`, make it different): {previous_shape}
- Keys played recently (don't reuse them): {recent_keys}
- Hooks played recently (write something different): {recent_hooks}
- Progressions played recently (don't reuse them): {recent_progressions}
- Listeners disliked: {avoid}

## A spark for this one

Suggested key: **{suggested_key}** (drawn at random: take it unless your idea
needs another key). Something to try: {spark}

## What to write

- `key`: "<Root> <mode>", a mode the style allows, chosen for the theme's
  mood (brightest to darkest: major, lydian, mixolydian, dorian, minor,
  phrygian). Be bold with the root: any root works, and a far jump from the
  previous track is as good as a close one.
- `bpm`: within the style's range.
- `progression`: YOUR OWN four chords as Roman numerals joined by "-", using
  only the chords listed for the key's mode in `chords_per_mode` (upper case
  = major, lower case = minor, exactly as listed), e.g. "i-VI-III-VII". The
  examples are only examples. Chords may repeat ("i-i-VI-VII").
- `section_progressions`: four other chords for the `breakdown` and for the
  `chorus` (the peak), same rules, so the harmony moves with the track.
- `hook`: the tune that makes this track recognisable, for one of the style's
  `hook_parts`, as scale degrees relative to the chord's root (0 root, 2
  third, 4 fifth, 6 seventh, 7 octave, 9, 11 above; -1, -3 below):
  - a `cycle` part (acid, arp) plays 3-16 numbers in a loop as 16ths, so an
    odd count (5, 7) rolls against the beat. Chord tones only: 0 2 4 6 7 9
    11 -1 -3 -5, and `~` for a rest. E.g. "0 7 0 4 9 4 2".
  - a `phrase` part (lead) is a melody over the bar, 8 steps with `~` rests,
    chord tones on the 1st, 3rd, 5th and 7th step; `<[...] [...]>` for a
    two-bar phrase (then chord tones only). E.g. "7 ~ 4 ~ 9 ~ 7 4".
  The hook follows the chords, so it stays in harmony by itself.
- `parts`: the starting pattern of 3-5 OTHER parts from the style's
  `sketch_parts` (not the hook's part), written so they interlock with the
  hook rather than double it:
  - `cycle` and `phrase` parts: as for the hook.
  - `bassline`: degrees under the chord, chord tones only, usually offbeat
    or rolling 16ths, e.g. "[~ 0 0 0]*4", "[~ 0 ~ 7]*4", "[~ 0 0 -1]*4".
  - `rhythm` (chord stabs): `t` for a hit, `~` for a rest, over the bar,
    e.g. "~ t ~ t ~ t [~ t] ~", "t ~ ~ t ~ ~ t ~".
  Make at least one cycle an odd length (3, 5, 7, 9...) so it rolls against
  the 4/4, give the lead a phrase that changes bar to bar (`<[...] [...]>`),
  and keep the parts out of each other's way (different registers, rhythms
  that answer each other).
- `motion`: 2-4 knobs that move on their own, from `knobs`:
  {{"<part>": {{"<knob>": [low, high, bars]}}}}, a slow sine between low and
  high every `bars` bars (4-64), inside the knob's range. E.g. the acid knob
  opening over 32 bars, the chord filter breathing over 16.
- `idea`: ONE sentence: the move this track builds toward, something a
  listener would notice (e.g. "the acid hook climbs an octave at the peak
  while the chords drop out", "the breakdown sits on the II chord alone").
- `sounds`: a sound per part from `sounds`, not all the same as last time.
- `sections` (optional): the track's rough shape, 4-9 sections from
  `section_types`, each {{"name", "bars" (a multiple of 8), "energy" 0.05-1}},
  starting with an intro and ending with an outro. Leave it out and the
  performer uses the style's usual arc.
- `title` (two or three evocative words), `mood` (a few words).
- `reason`: one or two plain sentences on why this follows the last track;
  shown to the audience.
- `narration`: ONE sentence (under 25 words) a warm late-night radio host
  would say as the track begins. The feeling, not the technique: no numbers,
  no instrument names.

Reply with ONE JSON object and nothing else:

```json
{{"title": "...", "key": "...", "bpm": 0, "progression": "...",
  "section_progressions": {{"breakdown": "...", "chorus": "..."}},
  "hook": {{"role": "...", "pattern": "..."}},
  "parts": {{"<part>": "<pattern>", "...": "..."}},
  "motion": {{"<part>": {{"<knob>": [0, 0, 32]}}}}, "idea": "...",
  "sounds": {{"chords": "...", "lead": "...", "arp": "..."}}, "mood": "...",
  "reason": "...", "narration": "..."}}
```
