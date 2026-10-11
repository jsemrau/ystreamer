# Skill: song design, the basics

Every track gets these right. They're what makes a listener stay for the
second minute and hum the tune in the tenth. Each section names the fields
of your reply that carry it out.

## 1. One idea

A track is one idea, said in one sentence (`recommendation.concept`): an
image, a feeling, a situation. Every choice below serves it: the voice of
the hook, the mode, the tempo, the form, what drops out and when. A choice
that serves no idea is decoration; leave it out. Two ideas in one track is
two half-tracks.

## 2. The hook is the song

The hook is the one tune a listener takes home (`hook`). Everything else
accompanies it.

- **Give it a voice with character** (`sounds`): the hook's sound is the
  track's signature, the first thing a listener recognises. A real
  recording (a grand piano, a woman's voice, an oboe, an mbira) beats a
  synth preset.
- **State it early, repeat it, never wear it down.** The listener must hear
  it within the first minute (have the hook's part start in the `build` or
  the first `groove`), hear it again and again, and hear it come back after
  it rested. Repetition is how a tune becomes a hook; variety comes from
  what changes around it.
- **Make it singable**: mostly steps, one high point, a rhythm of its own
  (a held note, a rest, an off-beat note), a call that ends open and an
  answer that ends home. The vocal part sings it at the breakdown and the
  peak, so keep it within a singer's range: about an octave and a third
  (degrees -1 to 9 above the tonic) and no fast runs.

## 3. Verse and chorus: contrast makes the chorus

A chorus only lands if what comes before it is different. Between
neighbouring sections change at least two of these (`sections`, `cues`,
`section_progressions`; a chorus section is played as the performer's
peak, so its instructions go in `cues.peak`):

| | verse / groove | chorus / peak |
|---|---|---|
| density | 3-4 lines | every line, the hook on top |
| register | middle | the hook an octave up, the bass deeper |
| harmony | `progression` | `section_progressions.chorus`: other chords |
| energy | 0.4-0.6 | 0.8-1.0 |
| the hook | teased, or on one voice | whole, sung, doubled |

The chorus (`chorus`, the performer's peak) is the hook at its fullest. The
`breakdown` is the opposite: the kick and bass gone, the hook alone in a
voice (the vocal sings it there), space. Each peak should come after a
dip, never straight after another peak.

## 4. The energy arc: tension, then release

Draw the track's energy as a line (`sections[].energy`, `recommendation.arc`):
up in steps, a dip, a bigger rise, the highest point late (about three
quarters through), then down. Tension is built by withholding: a filter held
shut (`motion`), the kick or bass left out, the hook teased but not given,
a chord that doesn't resolve. Release is giving it back all at once.

**Save something for the last peak**: the hook an octave up, the vocal, a
part held back all track, the brightest chords. If the last peak sounds
like the first, the track has nowhere to go.

## 5. Arrangement: every part has its place

- **Frequency**: kick and bass own the low end; chords the middle; the hook
  above them. Two parts in the same register and rhythm fight; move one.
- **One melody in front.** The hook leads; any other melodic part answers in
  its rests (call and response) or holds still under it. Never two busy
  tunes at once (`max_melodic`).
- **Interlock rhythms** (`parts`): the bass on the offbeats against a
  four-on-the-floor kick; the chords stabbing where the bass rests; an odd
  cycle (5, 7) rolling against the bar for motion.
- **Less is more.** A part earns its place by doing something the others
  don't. A full peak is six or seven parts that leave each other room, not
  more parts.

## 6. The groove: kick and bass are one instrument

The kick and the bass decide whether people move. Pick the bass pattern
against the kick (`parts.bass`): offbeat for lift, rolling 16ths for drive,
long notes for space. The hats push forward (offbeat open hat), the
clap or snare marks 2 and 4. Keep one groove for the track and vary it
with dropouts, not rewrites.

## 7. Transitions: announce every change

A listener should feel a new section coming. In the last bars before a
change: a riser, a fill, a filter opening, a part dropping out for a bar
(`cues` at the end of a section, the `lift` section, the arranger's risers
and fills). A change with no warning sounds like a mistake; a warned one
like an event.

## 8. Contrast across the set

The track follows another (`previous`, `recent_*` below). Change at least
the mode or the tempo band, the hook's voice and the form. A set breathes:
darker after brighter, slower after faster, a sparse track after a dense one.

## Before you reply, check

1. Can you say the concept in one sentence, and does every choice serve it?
2. Is the hook heard within the first minute, sung at the breakdown and
   peak, and within a singer's range?
3. Does every chorus differ from the section before it in two ways?
4. Is the highest energy late, after a dip, with something saved for it?
5. Does each part have its own register and rhythm, one melody in front?
6. Is every section change announced?
7. Is it different from the last track in mode or tempo, voice and form?
