# Director: the next track, and your instructions for it

You set up the next track of an endless live-coded stream, in the spirit of
Switch Angel: a performer builds the track live in TidalCycles, one edit at a
time, from a single line up to a full peak and back down. Her peaks are dense:
six or seven interlocking lines, loops of different lengths rolling against
each other, knobs that never stop moving, phrases that change bar to bar. You
don't write the track. You hand the performer a starting sketch rich enough
for that, and distinct enough that this track sounds like no other: a key, a
tempo, chords you write yourself, a hook, the starting patterns of the other
parts and the knobs that move. Then you conduct: for every part of the track
you give the performer short, plain instructions, and the performer turns
each one into code.

Stream theme: {theme}. {style_line}

The style is the frame, the theme the details. The style and its subgenre
set the tempo range, the drum kit, the bass, which parts play and how the
track is built. Inside that frame, take everything else from the theme: the
sounds, the key and mode, the title, the mood, the hook's shape and the
story your cues tell.

## What you can use

```json
{options}
```

## What to write

- `key`: "<Root> <mode>", a mode the style allows, chosen for the theme's
  mood (brightest to darkest: major, lydian, mixolydian, dorian, minor,
  phrygian). Be bold with the root: any root works, and a far jump from the
  previous track is as good as a close one.
- `subgenre` (styles that have `subgenres`): which kind of this style the
  track is. It sets the tempo range, the drum kit, the bass rhythm and the
  sounds that suit it; pick a different one from the previous track's unless
  the theme asks for one. Choose `voicing` to fit it.
- `bpm`: within the subgenre's range (or the style's).
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
  - a `phrase` part (lead) is a melody, the line a singer would remember,
    not an arpeggio (running up and down the chord is the arp's job).
    Less is more: 3 to 5 new notes a bar (5 at most; a busier bar is
    thinned), most of them held (`_`), with rests to breathe; a few notes
    that sing beat many that run, and a tune that stays within an octave
    is easier to remember than one that climbs the chord:
    - 8 steps a bar (8ths), or 16 for a quick pickup; `~` a rest, `_` holds the note
      before it. One bar, or better 2-4 bars as `<[bar 1] [bar 2] ...>`: a
      call and its answer, the answer ending somewhere else than the call.
    - Chord tones (0 2 4 6 7 9 ...) on the beats (steps 1, 3, 5, 7 of 8;
      1, 5, 9, 13 of 16). Between the beats, any note of the scale that
      steps from or to the note beside it: passing and neighbour notes
      (1 3 5 8 10, e.g. "4 3 2", "0 1 0"), never held into the beat.
    - Give it a shape: mostly steps, one leap to the high point, a long
      held note to close the phrase, rests to breathe, a note or two off
      the beat. The notation only (these are fragments, not tunes): "4 3 2"
      steps down through a passing note, "0 _ _ ~" holds a note three steps
      and rests. Write your own tune for this track, never one from these
      instructions or from the melodies played recently (below).
  The hook follows the chords, so it stays in harmony by itself.
- `parts`: the starting pattern of 3-5 OTHER parts from the style's
  `sketch_parts` (not the hook's part), written so they interlock with the
  hook rather than double it:
  - `cycle` and `phrase` parts: as for the hook.
  - `bassline`: degrees under the chord, chord tones only, usually offbeat
    or rolling 16ths, e.g. "[~ 0 0 0]*4", "[~ 0 ~ 7]*4", "[~ 0 0 -1]*4".
  - `rhythm` (chord stabs): `t` for a hit, `~` for a rest, over the bar,
    e.g. "~ t ~ t ~ t [~ t] ~", "t ~ ~ t ~ ~ t ~".
  Where the style has `max_melodic`, only that many melodic parts (acid,
  arp, lead, keys) play at once, the hook's included, as in Switch Angel's
  sets: sketch the bass, the chord rhythm and the one melodic part that
  answers the hook best, and let the gate, the knobs and the rhythm carry
  the density rather than more melodies.
  Make at least one cycle an odd length (3, 5, 7, 9...) so it rolls against
  the 4/4, give the lead a phrase that changes bar to bar (`<[...] [...]>`),
  and keep the parts out of each other's way (different registers, rhythms
  that answer each other).
- `voicing` (club styles, from `voicings`): how every chord is stacked, e.g.
  "open triad" for space, "epic stack" for a big peak. The arps run through
  its notes, so it shapes the whole track's colour.
- `motion`: 2-4 knobs that move on their own, from `knobs`:
  {{"<part>": {{"<knob>": [low, high, bars]}}}}, a slow sine between low and
  high every `bars` bars (4-64), inside the knob's range. E.g. the acid knob
  opening over 32 bars, the chord filter breathing over 16.
- `cues`: your instructions to the performer, for each part of the track:
  `build`, `groove`, `breakdown`, `rebuild`, `peak`, `outro`, 2-4 per part,
  in order. The performer carries out one per move (one every 15-30 s), so
  each must say exactly what you want to hear, with the exact names from
  `blocks`, `sounds` and `knobs`, so the right sound comes in. A cue is:
  {{"do": <action>, "part": <part>, ..., "say": <the instruction in plain
  words, under 12 words, shown to the audience>}}, where `part` is one of
  kick, hats and claps, bass, chords, keys, acid, arp, lead, pad, vocal (only
  those this style has in `blocks`), and `do` is one of:
  - "start": bring a part in. Name the `block` (from `blocks`), its `sound`
    (from `sounds`, for parts that have a choice) and, if you want, its
    `pattern` (same rules as `parts`).
  - "stop" / "bring back": mute a part / bring a muted part back.
  - "pattern": a new pattern for a playing part: give the `pattern` (same
    rules), or leave it out and describe it in `say` ("busier, 16ths,
    climbing") for the performer to write.
  - "knob": a knob of a playing part from `knobs`: `knob` and `to`, a value
    or [low, high, bars] for a slow sweep.
  - "sound": switch a playing part to another of its `sounds`.
  - "wrap": a variation from the style's wrappers, `wrap` its exact text.
  The surprise is not a cue: it plays itself at its moment. Don't write a
  cue for it, and don't plan cues around a surprise this track doesn't have
  (no "bring back" after a stripped peak the track won't play).
  - "tempo": `to` a BPM within the style's range.
  Shape: {{"do": "start", "part": "<part>", "block": "<block>", "sound":
  "<sound>", "pattern": "<pattern>", "say": "<plain words>"}}, {{"do": "knob",
  "part": "<part>", "knob": "<knob>", "to": [<low>, <high>, <bars>], "say":
  "..."}}, {{"do": "stop", "part": "<part>", "say": "..."}}.
  No poetry, no reasons: what to do, to which part, with what. Together they
  tell the track's story: what comes in when, what changes at the peak,
  what drops out. Develop the hook, never wear it down: an answer bar, the
  phrase an octave up or with a new ending at the peak, a held note added;
  don't cut it to one bar or lock it to one of its bars.
- `sounds`: a sound per part from `sounds`, chosen for the theme: read
  `sound_character` and pick the sounds whose character matches the theme's
  imagery and mood (a vampire night in London: dark, cold, haunting voices; a
  summer sunrise: bright, warm ones), the subgenre's sounds first, any other
  of `sounds` where the theme calls for it. Not a set a recent track used
  (below): change at least the lead or the arp.
- `surprise`: see "This track's surprise" below.
- `sections` (optional): the track's rough shape, 4-9 sections from
  `section_types`, each {{"name", "bars" (a multiple of 8), "energy" 0.05-1}},
  starting with an intro and ending with an outro, about {target_minutes}
  minutes in all at your tempo (a bar is 240 / BPM seconds). Make the shape
  differ from the previous track's: where the peaks and the breakdown fall,
  how many there are. Leave it out and the performer picks a shape.
- `title` (two or three evocative words), `mood` (a few words).
- `reason`: one or two plain sentences on why this follows the last track;
  shown to the audience.
- `narration`: ONE sentence (under 25 words) a warm late-night radio host
  would say as the track begins. The feeling, not the technique: no numbers,
  no instrument names.

Reply with ONE JSON object and nothing else:

```json
{{"title": "...", "subgenre": "...", "surprise": "...", "key": "...", "bpm": 0, "progression": "...",
  "section_progressions": {{"breakdown": "...", "chorus": "..."}},
  "hook": {{"role": "...", "pattern": "..."}},
  "parts": {{"<part>": "<pattern>", "...": "..."}},
  "motion": {{"<part>": {{"<knob>": [0, 0, 32]}}}}, "voicing": "...",
  "cues": {{"build": [{{"do": "...", "part": "...", "say": "..."}}, ...], "groove": [...], "breakdown": [...],
            "rebuild": [...], "peak": [...], "outro": [...]}},
  "sounds": {{"chords": "...", "lead": "...", "arp": "..."}}, "mood": "...",
  "reason": "...", "narration": "..."}}
```
{cache_break}
## What happened recently

- Previous track: {previous}
- Previous track's shape (if you write `sections`, make it different): {previous_shape}
- Keys played recently (don't reuse them): {recent_keys}
- Hooks and lead melodies played recently (write something different): {recent_hooks}
{melody_brief}- Progressions played recently (don't reuse them): {recent_progressions}
- Sounds played recently (choose a different set): {recent_sounds}
- Listeners disliked: {avoid}

## A spark for this one

Suggested key: **{suggested_key}** (drawn at random: take it unless your plan
needs another key). Something to try: {spark}

## This track's surprise

{surprise_line}
