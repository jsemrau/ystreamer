# Composer: write a counter-melody

Write a short counter-melody for an endless chill-music stream. You do not
choose pitches directly: you write **scale degrees** of a safe pentatonic
scale, and the engine maps them to notes that always fit the chords.

- Degree 0 is the tonic, 1-4 are the next pentatonic notes, 5 is the tonic
  an octave up, up to 9. Negative degrees down to -3 go below the tonic.
- Each cycle (bar) is 8 eighth-note steps. Use `~` for a rest and `_` to hold
  the previous note. Every cycle must have exactly 8 tokens.
- Leave space: this plays *under* the main melody, so prefer long notes,
  rests, and simple repeated shapes. At most 4 note onsets per cycle.

## Context

Key: {key}. Progression: {progression} ({cycles} cycles). Mood: {mood}.
Section: {section} (energy {energy}).

Reply with ONE JSON object and nothing else:

```json
{{"cycles": ["0 _ _ ~ 2 _ ~ ~", "4 _ _ _ 3 _ ~ ~", ...],
  "reason": "A slow falling answer to the main melody, leaving space on the downbeats."}}
```

The list must contain exactly {cycles} strings.
