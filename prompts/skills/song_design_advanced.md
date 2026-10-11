# Skill: song design, advanced techniques

The basics make a track work. These make it memorable. Each is a device
real records use, with the fields that carry it out here. Choose one or two
per track that serve its idea; never all of them, never the same ones as
the last tracks (`techniques_recent` below). Name the ones you use by their
number in `recommendation.techniques`, e.g. ["A3", "A11"], and say in
`recommendation.bold` how the track uses them.

## The hook

**A1 · Tease, then give.** The build plays only the hook's first bar or its
rhythm on one note; the full hook arrives at the first peak. Levers: the
hook's part starts late in `cues.build`; a `pattern` cue with the hook's
first notes; the whole hook comes in a `groove` or `peak` cue.

**A2 · Call and response.** The hook's rests are answered by another voice:
the arp, the keys, the vocal chops. Levers: `parts` written into the hook's
gaps (rests where the hook plays, notes where it rests); a cue bringing the
answering part in at the first peak.

**A3 · Sequence.** The hook's first phrase repeated a step or a third
higher over the next chords (the call climbs, the answer comes home). Lever:
`hook`, bars 3-4 the shape of bars 1-2 moved up.

**A4 · Rhythmic hook.** The hook is a rhythm more than a melody: two or
three notes in a pattern people can clap (Darude's "Sandstorm"). Levers:
`hook` with few pitches and a striking rhythm; the `three_note_hook`
surprise; a `cycle` hook with rests.

**A5 · The sung refrain as payoff.** The hook plays on an instrument all
track; at the breakdown the voice sings it alone over the pads, and at the
peak voice and instrument sing it together. Levers: hold the vocal back
until `breakdown` (it comes in there by default); `cues.breakdown` stopping
the hook's instrument so the voice is alone; `cues.peak` bringing the
instrument back under it.

## Harmony

**A6 · The common tone.** Choose chords that share a note (I and vi share
two, IV and ii share two, I and IV share the tonic), and let the hook hold
that note across the change: the harmony moves under a voice that stays,
the most inevitable-sounding thing a melody can do. Levers: `progression`
with neighbours that share notes; `hook` with a held note (`_ _ _`) over
the bar line into the next chord.

**A7 · Home only at the end.** The progression starts away from the tonic
(on IV, vi or ii) and reaches the tonic only on its last chord, so every
loop wants to go round again. Lever: `progression`, e.g. "IV-V-vi-I",
"vi-IV-V-I", "ii-IV-V-I".

**A8 · A darker breakdown, a brighter chorus.** The breakdown borrows a
minor or modal chord (iv in a major key, bVI or bVII), the chorus answers
with the brightest chords. Levers: `section_progressions.breakdown` and
`.chorus`; `chromatic` for the rare track that wants it all the way.

**A9 · The deceptive return.** The last chord before the peak isn't the
expected one (vi where I was due), so the peak's first chord lands as a
surprise. Lever: `section_progressions.breakdown` ending on V, the chorus
progression starting on vi or IV.

**A10 · Key lift for the last chorus.** Everything a whole step up for the
final peak: the oldest trick, and it still works once. Lever: the
`key_lift` surprise (a tempo lift, `tempo_lift`, is its rhythmic cousin).

## Rhythm and groove

**A11 · Polymeter.** A cycle of 3, 5 or 7 notes rolling against the 4/4, so
the pattern lands differently every bar (Orbital, Switch Angel). Levers: a
`cycle` hook or `parts` arp of odd length; a second odd cycle in another
part for a peak that never repeats.

**A12 · Half-time breakdown.** The breakdown feels half as fast: long
notes, the hats and claps gone, then the peak snaps back to full time. Levers: `cues.breakdown` stopping hats and
claps, slow `motion` sweeps; the `break` section for a short version.

**A13 · The drop that holds back.** The peak arrives without the kick, or
with only kick, bass and hook, and the rest returns move by move. Levers:
the `kickless_drop`, `stripped_peak`, `fake_drop` or `silent_bar`
surprises; or `cues.peak` starting parts one by one.

## Arrangement and sound

**A14 · Accumulation.** No drop at all: one layer joins every few bars over
an unchanging loop until the whole thing glows (Orbital's "Halcyon"). Levers:
long `groove` sections with rising `energy`; `cues` each adding one part;
`motion` opening filters over 32-64 bars.

**A15 · Subtraction.** The peak is made by taking away: everything stops
but the hook and one other part, then the full band slams back. Levers: a
`tag` section (the hook alone over beat and bass); the `solo_breakdown`
surprise; `cues` stopping parts in the bar before the return.

**A16 · Register climb.** The track rises in pitch as it goes: the hook an
octave up at the last peak, the chords voiced higher, the bass dropping an
octave for weight. Levers: the hook comes back an octave up at the peak
(automatic); `voicing` "epic stack" or "add octave"; `cues.peak` wrapping
the lead with `(|+ n 12)`.

**A17 · The sound turn.** At the peak one part changes voice: the arp from
a pluck to a real harp, the chords from a piano to a string section. The
same notes, a new colour, and the peak feels new. Levers: a `sound` cue
in `cues.peak` (the sounds must be in that part's `sounds`).

**A18 · Intimate, then communal.** A small, close verse (one voice, few
parts, dry) opening into a huge chorus (choir, strings, everything)
(Jamie xx's "Loud Places"). Levers: low-`energy` verses with 2-3 parts;
`cues.peak` bringing in the crowd choir, the string section or the voice.

## Choosing

- An **exploit** track (build on what worked): the basics done perfectly,
  one technique at most, the one that fits the touchstone or the host's
  liked sound.
- An **explore** track (go somewhere new): two techniques you haven't used
  lately, one about the hook or harmony, one about the arrangement. That's
  the risk that makes the set interesting.
- A touchstone from the host's favourites often names its own technique:
  "Halcyon" A14, "Children" A1 and A5 (the piano bare first), "Sandstorm"
  A4, "Loud Places" A18, "Angels" A15, "Lush 3-5" A11, "Sonic Empire" A16.
