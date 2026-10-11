# Live: develop '{title}' into its next section

The track is playing. You planned it as it began; now you develop it live,
section by section, the way a live coder does: hear what is actually
playing (it is never quite what was planned), keep what works, change what
doesn't, and steer toward the moments you planned.

## The track

- Concept: {concept}
- Your reasoning as it began: {reasoning}
- Arc: {arc}
- Techniques you chose: {techniques}
- Frame: {frame}
- Hook ({hook_role}): {hook}
- Sounds: {sounds}

## Where it is

{form}

## What is playing now (the live code)

```
{code}
```

## What the performer did lately (oldest first)

{edits}

## The host while it plays

{reactions}

## What you planned for the next section ({next_phase}, {bars} bars)

{planned}

## What you can use

```json
{options}
```

## Now

Decide how the song develops into the next section, as yourself, with your
song-design skills: keep the concept and the hook (it stays as written; it
may come back an octave up, sung, doubled, answered), make the next section
differ from this one in at least two ways, keep the arc rising toward the
last peak with something saved for it, and if the host reacted, answer
them. Then either keep your plan for the next section, if it still fits
what is playing, or replace it with 2-4 new cues, exactly as before:
{{"do": "start" | "stop" | "bring back" | "pattern" | "knob" | "sound" |
"wrap" | "tempo", "part": ..., "block"/"sound"/"pattern"/"knob"/"to"/"wrap"
as each needs, "say": "<under 12 words>"}}, with the exact names above,
parts that exist in this style.

Reply with ONE JSON object:

```json
{{"listening": "what you hear now that matters, one sentence",
  "decision": "how the song develops into the next section and why: one or two sentences, first person, shown on screen",
  "keep": false,
  "cues": [{{"do": "...", "part": "...", "say": "..."}}]}}
```
