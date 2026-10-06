"""
Live coding: the music is a short Tidal program that is built up and revised
one edit at a time, the way a live coder performs (e.g. Switch Angel):

    setcps (136/60/4)

    d1 $ s "[ys_kick*4, [~ 808oh]*4]" # gain 1
    d2 $ n (scale "minor" ("<0 4 0 9 7>*16" |+ "<0!2 5!2 2!2 6!2>") + 7 - 24)
       # s "ys_acid" # pF "acid" 0.546 # legato 0.6

Each line is a LineSpec: a body template, ordered effects (`# room 0.3`) and
wrappers (`every 4 (fast 2) $`). Edits change one thing (a number, a pattern,
an effect, a wrapper; add, mute or remove a line; the tempo), and only
whitelisted functions, effects and value ranges are allowed. Pitches always go
through Tidal's `scale` with the track's mode, and harmony placeholders
({mode}, {root}, {roots}, {chords}) are filled from the track's key and
progression, so an edit can't break the key or the syntax.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field

from .theory import Key

# Channel per role (d1..d8): fixed so the code reads the same way every time.
ROLE_CHANNELS = {
    "beat": 1, "bass": 2, "chords": 3, "keys": 4, "lead": 5, "acid": 6, "texture": 7, "perc": 8, "arp": 9,
    "vocal": 12,                  # (d10 and d11 are the arranger's riser and fills)
}
ROLES = list(ROLE_CHANNELS)

# Effects an edit may set, with value ranges (value may also be a slow sine
# between two in-range numbers, e.g. "(slow 8 $ range 400 2400 sine)").
FX_RANGES: dict[str, tuple[float, float]] = {
    "gain": (0.5, 1.15), "room": (0.0, 0.8), "size": (0.0, 0.95), "lpf": (200, 9000), "hpf": (20, 2000),
    "resonance": (0.0, 0.5), "delay": (0.0, 0.6), "delaytime": (0.05, 1.0), "delayfeedback": (0.0, 0.5),
    "pan": (0.0, 1.0), "legato": (0.1, 2.0), "crush": (4.0, 16.0), "shape": (0.0, 0.5), "speed": (0.5, 2.0),
}
# Custom SynthDef controls (sent with pF).
PF_RANGES: dict[str, tuple[float, float]] = {
    "acid": (0.1, 0.95), "acidres": (0.08, 0.6), "detune": (0.05, 0.8), "accent": (0.0, 1.0), "drive": (0.5, 3.0), "gatedepth": (0.0, 1.0), "duckdepth": (0.0, 0.9),
}

# Wrappers an edit may add, per kind of line. Pitch-safe: only octave shifts.
# Pitched lines only get wrappers that keep their notes on the chord that is
# playing: no time shifts, speed-ups or delayed copies (they carry the old
# chord's notes over a chord change, or stack a moving line on itself), so
# the harmony stays clean the way Switch Angel's does. Chord-tone lines (arps,
# acid, chords) may also play backwards or mirrored; a lead, whose passing
# notes sit between the beats, may not (backwards puts them on the beats).
MELODIC_WRAPPERS = [
    "sometimesBy 0.2 (degradeBy 0.5)", "jux rev", "every 4 rev", "degradeBy 0.1",
    "whenmod 8 6 (# lpf 800)", "every 2 (# room 0.5)", "every 4 (|+ n 12)", "(|+ n 12)",
]
LEAD_WRAPPERS = ["sometimesBy 0.2 (degradeBy 0.5)", "degradeBy 0.1", "whenmod 8 6 (# lpf 800)",
                 "every 2 (# room 0.5)", "every 4 (|+ n 12)", "(|+ n 12)"]
# Earlier versions' melodic wrappers: still understood (e.g. to unwrap them
# from a resumed track), never offered.
RETIRED_WRAPPERS = ["every 4 (fast 2)", "every 3 (0.25 <~)", "every 4 (0.125 ~>)", "off 0.125 (|+ n 12)",
                    "off 0.25 (|+ n 12)", "chunk 4 (fast 2)"]
DRUM_WRAPPERS = [
    "every 4 (fast 2)", "degradeBy 0.1", "every 8 (0.25 <~)",
    "whenmod 8 7 (# crush 6)", "swingBy (1/8) 4", "swingBy (1/12) 4", "every 3 (# lpf 1500)",
    "sometimesBy 0.1 (echo 3 0.125 0.5)",
]
# Club styles keep the drums straight, as Switch Angel does: the kick on
# every beat and the backbeat where it belongs, never sped up, shifted,
# swung, stuttered or thinned out by chance (that put the drums out of step
# with the bass and acid). Variation is in the tone; fills come from the
# arranger's fill line and the clap's fill choices.
CLUB_DRUM_WRAPPERS = ["whenmod 8 7 (# crush 6)", "every 3 (# lpf 1500)", "whenmod 16 15 (# hpf 1200)"]
CLUB_STYLES = ("trance", "techno", "hardcore", "deephouse", "darkwave")
BASS_WRAPPERS = ["degradeBy 0.1", "sometimesBy 0.2 (degradeBy 0.5)", "whenmod 8 6 (# lpf 300)"]
# Vocal chops (spoken phrases, no pitch to keep) may stutter, play backwards
# or go thin; a sung acapella (a "loop" in key and on the beat) only changes
# its tone and space, so it stays in time and in tune.
ACAPELLA_WRAPPERS = ["whenmod 8 6 (# hpf 1500)", "every 2 (# room 0.5)"]
VOCAL_WRAPPERS = ["sometimesBy 0.25 (echo 3 0.125 0.6)", "every 4 (# speed (-1))"] + ACAPELLA_WRAPPERS
ALL_WRAPPERS = (set(MELODIC_WRAPPERS) | set(DRUM_WRAPPERS) | set(CLUB_DRUM_WRAPPERS) | set(BASS_WRAPPERS)
                | set(VOCAL_WRAPPERS) | set(RETIRED_WRAPPERS))

# What each wrapper does to the sound, for the performer's prompt (a model
# that only sees "off 0.25 (|+ n 12)" has to guess).
WRAPPER_NOTES = {
    "every 4 (fast 2)": "every 4th bar plays twice as fast: a fill or roll at the phrase end",
    "every 3 (0.25 <~)": "every 3rd bar shifts a quarter bar earlier: syncopated, off-balance",
    "every 4 (0.125 ~>)": "every 4th bar lands an 8th late: lazy, behind the beat",
    "sometimesBy 0.2 (degradeBy 0.5)": "now and then drops half the notes: the line breathes",
    "off 0.125 (|+ n 12)": "adds an octave-up echo an 8th later: brighter and busier",
    "off 0.25 (|+ n 12)": "adds an octave-up echo a quarter later: call and answer",
    "jux rev": "a reversed copy in the other ear: wide, dreamy, a little blurred",
    "every 4 rev": "every 4th bar plays backwards: a twist at the phrase end",
    "degradeBy 0.1": "randomly drops 1 in 10 notes: human, less mechanical",
    "every 4 (|+ n 12)": "every 4th bar jumps an octave up: a lift at the end of the phrase",
    "(|+ n 12)": "the whole line an octave up: the hook returning higher, for the peak",
    "chunk 4 (fast 2)": "each bar a different quarter plays double time: a roll that walks through the bar",
    "whenmod 8 6 (# lpf 800)": "last 2 bars of every 8 go muffled: a dip before the next phrase",
    "every 2 (# room 0.5)": "every other bar gets more reverb: the space pulses",
    "every 8 (0.25 <~)": "every 8th bar shifts early: a turnaround",
    "whenmod 8 7 (# crush 6)": "last bar of every 8 is bitcrushed: a gritty fill",
    "whenmod 16 15 (# hpf 1200)": "last bar of every 16 goes thin (the low end filtered out): a lift into the next phrase",
    "swingBy (1/8) 4": "strong swing: laid-back hip-hop feel",
    "swingBy (1/12) 4": "light swing: loosens a stiff groove",
    "every 3 (# lpf 1500)": "every 3rd bar darker: subtle variation",
    "sometimesBy 0.1 (echo 3 0.125 0.5)": "occasional stuttered echo: a glitchy fill",
    "whenmod 8 6 (# lpf 300)": "last 2 bars of every 8 the bass goes subby: tension before the turn",
    "sometimesBy 0.25 (echo 3 0.125 0.6)": "now and then the vocal stutters (a chopped echo): a DJ's vocal chop",
    "every 4 (# speed (-1))": "every 4th bar the vocal plays backwards: an eerie reversed phrase",
    "whenmod 8 6 (# hpf 1500)": "last 2 bars of every 8 the vocal goes thin, like a telephone: a lift before the turn",
}

# What each effect and control sounds like.
FX_NOTES = {
    "gain": "loudness: only for balancing, within about +/-3 dB of where the line entered",
    "room": "reverb amount: 0 dry and close, 0.8 huge and distant",
    "size": "reverb room size: small booth to cathedral",
    "lpf": "low-pass cutoff in Hz: low (400-1200) dark and muffled, high (5000+) bright and open",
    "hpf": "high-pass cutoff in Hz: removes low end, thinner and airier",
    "resonance": "filter peak: 0 smooth, 0.4 squelchy and vocal",
    "delay": "echo amount: 0 none, 0.5 strong echoes",
    "delaytime": "echo spacing in seconds",
    "delayfeedback": "how long the echoes keep repeating",
    "pan": "stereo position: 0 left, 0.5 centre, 1 right",
    "legato": "note length: 1 full, 0.3 short and plucked, 1.5 notes overlap",
    "crush": "bit depth: 16 clean, 4 very gritty and lo-fi",
    "shape": "distortion/saturation: 0 clean, 0.5 heavily driven",
    "speed": "sample playback speed: 2 = an octave up and shorter",
    "acid": "the acid squelch knob: 0.2 closed and muffled, 0.9 screaming open",
    "acidres": "the acid filter's damping: lower is more resonant and whistling (0.08 screams, 0.4 smooth)",
    "accent": "the 303 accent: accented steps (1) are louder, brighter, more resonant; a pattern like \"[0 0 1 0]*4\"",
    "drive": "grit on the acid: 1 clean, 2 dirty, 3 screaming",
    "gatedepth": "how hard the trance gate chops the chords",
    "detune": "how wide the supersaw spreads: 0.1 nearly one saw, 0.3 the classic trance sound, 0.6 huge and seasick",
    "duckdepth": "sidechain pump from the kick: 0 none, 0.8 strong pumping",
}

# The custom controls each of our SynthDefs takes (tidal/synthdefs.scd), so an
# edit can set one the line doesn't carry yet (it plays at the synth's default).
SYNTH_CONTROLS: dict[str, tuple[str, ...]] = {
    "ys_acid": ("acid", "acidres", "accent", "drive"),
    "ys_gatesaw": ("gatedepth", "duckdepth"),
    "ys_supersaw": ("duckdepth",),
    "ys_hypersaw": ("detune", "duckdepth"),
}
_SYNTH_RE = re.compile(r'# s "(\w+)"')


def line_synth(line: "LineSpec") -> str:
    """The synth a line plays (its `synth` slot, or the `# s "..."` in its body)."""
    m = _SYNTH_RE.search(line.body)
    if not m or m.group(1) == "{synth}" or "synth" in line.patterns:
        return line.patterns.get("synth", "")
    return m.group(1)


def line_controls(line: "LineSpec") -> list[str]:
    """The pF controls a line can set: those it carries, then the others its
    synth takes."""
    have = [n for n, _ in line.pfs]
    m = _SYNTH_RE.search(line.body)
    synth = m.group(1) if m else ""
    if synth == "{synth}" or not m:
        synth = line.patterns.get("synth", "")
    return have + [c for c in SYNTH_CONTROLS.get(synth, ()) if c not in have and c in PF_RANGES]


# Effects that make sense per role (bass stays dry and dark; textures only
# get space and tone).
ROLE_FX = {
    "beat": ["lpf", "room", "crush", "shape", "gain"], "perc": ["room", "pan", "gain", "lpf", "hpf"],
    "bass": ["lpf", "gain", "legato"], "texture": ["room", "lpf", "gain"],
    "chords": ["lpf", "room", "size", "gain"], "keys": ["lpf", "room", "delay", "legato", "gain"],
    "lead": ["lpf", "room", "delay", "delayfeedback", "legato", "gain"],
    "acid": ["lpf", "room", "delay", "legato", "gain", "resonance"],
    "arp": ["lpf", "room", "delay", "delayfeedback", "legato", "gain", "pan"],
    "vocal": ["room", "size", "delay", "delaytime", "delayfeedback", "hpf", "lpf", "pan", "gain"],
}


def allowed_wrappers(line: "LineSpec", style: str | None = None) -> list[str]:
    if line.role == "texture":
        return []
    if line.role == "vocal":
        return ACAPELLA_WRAPPERS if "loop" in line.patterns else VOCAL_WRAPPERS
    if line.drums:
        return CLUB_DRUM_WRAPPERS if style in CLUB_STYLES else DRUM_WRAPPERS
    if line.role == "bass":
        return BASS_WRAPPERS
    if line.role == "lead":
        return LEAD_WRAPPERS
    return MELODIC_WRAPPERS

# Pattern slots that aren't free mini-notation.
CHOICE_SLOTS = {
    "gate": ["0", "1", "2", "3"],                 # trance gate: chopper, classic, two stutters
    # The clap is the backbeat (beats 2 and 4, as in Switch Angel's sets):
    # it may drop out, go half-time, or fill the end of a phrase, but never
    # wander onto the offbeats, which a free pattern let it do ("~ cp ~ cp ~ ~
    # cp ~" is 8 steps: claps after beats 1 and 2).
    "clap": ["~", "~ cp ~ cp", "~ ~ ~ cp", "~ cp ~ [cp cp]", "~ cp ~ <cp cp cp [cp cp]>",
             "~ cp ~ <cp cp cp [~ cp cp cp]>"],
}
# Chosen when the line is added: the synth, and a break loop (loopAt fits the
# loop to its bar count; another hit or sample would put it out of time).
LOCKED_SLOTS = {"synth", "loop"}

# What a mini-notation pattern value may contain.
_PATTERN_RE = re.compile(r"^[0-9a-z_~\s\[\]<>*!.,:\-|/?@]+$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SINE_RE = re.compile(r"^\(slow (\d+) \$ range (-?[\d.]+) (-?[\d.]+) (sine|saw|tri)\)$")


# Less is more: the most new notes a melodic line starts in a bar (its
# busiest bar). A tune sings in a few notes, held (_) and with rests (~); the
# arps and acid of trance are streams by design and have no budget.
NOTE_BUDGET = {"lead": 5, "keys": 4}
_NOTE_TOKEN_RE = re.compile(r"^-?\d+$")


def _bars(pattern: str) -> list[list[str]]:
    """A tune's bars as their steps: each top-level [..] of a <[..] [..]>, or
    the whole pattern."""
    p = pattern.strip()
    if p.startswith("<[") or p.startswith("["):
        groups = re.findall(r"\[([^\[\]]*)\]", p)
        if groups:
            return [g.split() for g in groups]
    return [p.strip("<>").split()]


def notes_per_bar(body: str, slot: str, value: str) -> float:
    """How many new notes the busiest bar of a line's tune starts: a phrase
    ("{melody}") counts its notes per bar, a cycle streamed N a bar
    ("<{arp}>*N") plays N steps a bar, its notes among them."""
    m = re.search(r'"<\{' + re.escape(slot) + r'\}>\*(\d+)"', body)
    if m:
        steps = [t for t in value.replace("<", " ").replace(">", " ").replace("[", " ").replace("]", " ").split()]
        notes = sum(bool(_NOTE_TOKEN_RE.match(t)) for t in steps)
        return int(m.group(1)) * notes / max(1, len(steps))
    if f'"{{{slot}}}"' not in body:
        return 0.0
    repeat = re.fullmatch(r"\s*\[([^\[\]]*)\]\*(\d+)\s*", value)        # "[0 4 7 4]*2": the bar twice
    if repeat:
        return float(sum(bool(_NOTE_TOKEN_RE.match(t)) for t in repeat.group(1).split()) * int(repeat.group(2)))
    return float(max((sum(bool(_NOTE_TOKEN_RE.match(t)) for t in bar) for bar in _bars(value)), default=0))


def hold_between(pattern: str, every: int, pickups: bool = True) -> str:
    """In each bar, a note off the grid of `every` steps becomes a hold of the
    note before it: the line keeps its notes on the beats, so its harmony and
    shape, with fewer notes. A note right after a rest (a pickup) stays,
    unless `pickups` is False: then it joins the rest."""
    def bar(tokens: list[str]) -> str:
        out = []
        for i, t in enumerate(tokens):
            if i % every and _NOTE_TOKEN_RE.match(t) and out:
                if out[-1] != "~":
                    t = "_"
                elif not pickups:
                    t = "~"
            out.append(t)
        return " ".join(out)
    p = pattern.strip()
    if (p.startswith("<[") or p.startswith("[")) and re.search(r"\[[^\[\]]*\]", p):
        return re.sub(r"\[([^\[\]]*)\]", lambda m: "[" + bar(m.group(1).split()) + "]", pattern)
    wrapped = p.startswith("<") and p.endswith(">")
    text = bar(p.strip("<>").split())
    return f"<{text}>" if wrapped else text


def space_cycle(pattern: str) -> str:
    """A streamed cycle with a rest after each note: half as many notes a bar."""
    p = pattern.strip()
    wrapped = p.startswith("<") and p.endswith(">")
    text = " ".join(f"{t} ~" if _NOTE_TOKEN_RE.match(t) else t for t in p.strip("<>").split())
    return f"<{text}>" if wrapped else text


def within_budget(body: str, slot: str, value: str, budget: int) -> str:
    """The tune, thinned until its busiest bar starts at most `budget` notes.
    A phrase: notes between the 8ths held, then those between the beats,
    then the pickups after rests too. A streamed cycle: a rest after each note."""
    if not budget:
        return value
    if re.search(r'"<\{' + re.escape(slot) + r'\}>\*\d+"', body):
        for _ in range(3):
            if notes_per_bar(body, slot, value) <= budget:
                break
            value = space_cycle(value)
        return value
    steps = max((len(b) for b in _bars(value)), default=8)
    beat = max(1, steps // 4)
    for every, pickups in ([(2, True)] if steps >= 16 else []) + [(beat, True), (beat, False)]:
        if notes_per_bar(body, slot, value) <= budget:
            break
        value = hold_between(value, every, pickups)
    return value


@dataclass
class LineSpec:
    role: str
    body: str                       # e.g. 'n (scale "{mode}" "<{chords}>" + {root}) # s "ys_epiano"'
    patterns: dict[str, str] = field(default_factory=dict)   # named mini-notation slots in body
    fx: list[list[str]] = field(default_factory=list)         # [[name, value], ...]
    pfs: list[list[str]] = field(default_factory=list)        # [[name, value], ...] custom controls
    wrappers: list[str] = field(default_factory=list)
    muted: bool = False
    drums: bool = False
    block: str = ""                 # which library block it came from
    base_gain: str = ""             # the level the line entered at; gain edits stay within +/-3 dB of it
    max_notes: int = 0              # its note budget a bar (0: the role's, NOTE_BUDGET)
    # The track's hook, when this line carries it: the tune as given, its
    # slot, and whether it's written against the key across the progression
    # (music/hooks.py) rather than on each chord's root.
    hook: str = ""
    hook_slot: str = ""
    key_tune: bool = False

    @property
    def channel(self) -> int:
        return ROLE_CHANNELS[self.role]

    def render(self, harmony: dict[str, str]) -> str:
        values = {**harmony, **self.patterns}
        body = self.body.format(**values)
        tail = "".join(f" # {name} {value}" for name, value in self.fx)
        tail += "".join(f' # pF "{name}" {value}' for name, value in self.pfs)
        expr = body + tail
        for wrapper in reversed(self.wrappers):
            expr = f"{wrapper} $ {expr}"
        return expr

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LineSpec":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# Chord voicings, as scale steps above the chord's root. The default is the
# plain seventh chord the chill styles were made with; the club styles use
# Switch Angel's consonant trance shapes (her chordshapes in strudel-scripts,
# https://github.com/switchangel/strudel-scripts): triads, octaves and fifths,
# no added sevenths or ninths to rub against the lines above them.
DEFAULT_VOICING = "0,2,4,6"
TRANCE_VOICINGS = {
    "triad": "0,2,4",              # [1] triad
    "add octave": "0,2,4,7",       # [57] big room
    "open triad": "0,4,7,9",       # [6] open triad
    "spread triad": "-7,0,2,4,7",  # [2] spread triad
    "epic stack": "0,4,7,11,14",   # [58] epic trance stack
    "anthem": "0,7,9,11",          # [26] fifth + upper triad
}


def voicing_steps(voicing: str) -> list[int]:
    try:
        steps = sorted({int(x) for x in str(voicing).split(",")})
    except ValueError:
        steps = []
    return steps or [0, 2, 4, 6]


def arp_sequences(voicing: str) -> dict[str, str]:
    """Switch Angel's trancearp over a voicing: its notes forward (F),
    backward (B), up and down, and around the root; all chord tones."""
    tones = [t for t in voicing_steps(voicing) if -7 <= t <= 11] or [0, 2, 4]   # the arp's range
    up = " ".join(map(str, tones))
    down = " ".join(map(str, reversed(tones)))
    updown = " ".join(map(str, tones + tones[-2:0:-1])) if len(tones) > 2 else up
    pedal = " ".join(f"{tones[0]} {t}" for t in tones[1:]) if len(tones) > 1 else up
    return {"up": up, "down": down, "updown": updown, "pedal": pedal}


# Echo lengths that sit on the beat, in beats: a 16th, an 8th, a dotted 8th
# (the Balearic and trance delay), a quarter, a dotted quarter, a half.
BEAT_DIVISIONS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)


def beat_delay(value: str, bpm: float) -> str:
    """A block's echo time (seconds, written for one tempo) moved to the
    nearest division of the beat at `bpm`, so the echoes fall on the groove
    instead of smearing it; anything but a plain number is left alone."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return value
    if seconds <= 0 or bpm <= 0:
        return value
    beat = 60.0 / bpm
    import math

    best = min(BEAT_DIVISIONS, key=lambda d: abs(math.log(seconds / (d * beat))))
    return f"{best * beat:.4f}"


@dataclass
class Track:
    """The musical frame of the current track: key, tempo, progression."""
    title: str
    style: str
    key: str
    bpm: float
    degrees: list[int]              # chord progression as scale degrees (0 = tonic)
    bars_per_chord: int = 1
    # The progression the track starts its harmony from, per part of the arc
    # ("main", "breakdown", "peak": name and degrees), and the cycle the
    # current progression started on, so a change always begins on its first
    # chord (Tidal's <...> otherwise picks the chord by cycle number).
    progressions: dict = field(default_factory=dict)
    start_cycle: int = 0
    # The Director's sound per part for this track (blocks.LIVE_SOUNDS).
    sounds: dict = field(default_factory=dict)
    # The Director's hook ({"role", "pattern"}) and its idea for the track.
    hook: dict = field(default_factory=dict)
    idea: str = ""
    # The rest of the Director's sketch (plan.Piece.parts / .motion).
    parts: dict = field(default_factory=dict)
    motion: dict = field(default_factory=dict)
    # How each chord is voiced, as scale steps above its root (Switch Angel's
    # chordshapes, e.g. "0,4,7,9" an open triad); every line playing
    # {chords} uses it, and arps run through its notes.
    voicing: str = DEFAULT_VOICING
    # The style's variant (blocks.SUBGENRES) and its drum kit / bass rhythms.
    subgenre: str = ""
    kit: dict = field(default_factory=dict)
    surprise: str = ""              # the art director's rule-break for this track (music/surprises.py)
    # The acapella this track sings (music/vocals.fit: bank, n, cycles, ...);
    # it set the key and tempo, so the tempo stays.
    vocal: dict = field(default_factory=dict)

    def make(self, block, rng) -> "LineSpec":
        """A line from a block, with this track's sounds, sketch, voicing and
        kit, its echo on the beat at this track's tempo."""
        line = block.make(rng, self.sounds, self.hook, self.parts, self.motion, voicing=self.voicing, kit=self.kit)
        line.fx = [[n, beat_delay(v, self.bpm) if n == "delaytime" else v] for n, v in line.fx]
        return line

    @property
    def cps(self) -> float:
        return self.bpm / 60.0 / 4.0

    def harmony(self) -> dict[str, str]:
        """Placeholders for line templates, all in scale-degree space."""
        key = Key.parse(self.key)
        shape = voicing_steps(self.voicing)

        def stack(d: int) -> str:
            base = d - 7 if d > 3 else d        # chord stacks kept near the tonic
            return "[" + ",".join(str(base + i) for i in shape) + "]"

        def root_of(d: int) -> str:
            return str(d - 7 if d > 3 else d)

        steps = len(self.degrees) * self.bars_per_chord
        shift = self.start_cycle % steps if steps else 0
        if shift == 0:
            rep = f"!{self.bars_per_chord}" if self.bars_per_chord > 1 else ""
            chords = " ".join(stack(d) + rep for d in self.degrees)
            roots = " ".join(root_of(d) + rep for d in self.degrees)
        else:
            # One entry per cycle, rotated so the first chord falls on start_cycle.
            per_cycle = [d for d in self.degrees for _ in range(self.bars_per_chord)]
            order = [per_cycle[(j - shift) % steps] for j in range(steps)]
            chords = " ".join(stack(d) for d in order)
            roots = " ".join(root_of(d) for d in order)
        root = key.root if key.root <= 5 else key.root - 12
        root_text = f" + {root}" if root > 0 else (f" - {-root}" if root < 0 else "")
        bar = 1.0 / self.cps
        return {
            "mode": key.mode,
            "root": root_text,          # e.g. " + 2", " - 3" or "" (C)
            "chords": chords,
            "roots": roots,
            # Cycles a tune written against the key (music/hooks.py) is shifted
            # by, so its first bar falls on the progression's first chord.
            "tune_shift": str(shift),
            "bar": f"{bar:.4f}",          # seconds per bar (trance gate locks to it)
            "beat": f"{bar / 4:.4f}",     # seconds per beat (sidechain duck)
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Program:
    track: Track
    lines: dict[str, LineSpec] = field(default_factory=dict)   # role -> line

    def expression(self, role: str) -> str:
        """The line's pattern as played, including the live mix (beds step
        back when the front is busy, see music/mix.py)."""
        from .mix import mix_factor

        expr = self.lines[role].render(self.track.harmony())
        factor = mix_factor(role, self.active_roles())
        return f"({expr}) |* gain {factor:g}" if factor < 1 else expr

    def statement(self, role: str) -> str:
        line = self.lines[role]
        if line.muted:
            return f"d{line.channel} $ silence"
        return f"d{line.channel} $ {self.expression(role)}"

    def tempo_statement(self) -> str:
        return f"setcps ({self.track.bpm:g}/60/4)"

    def code_lines(self, highlight: str | None = None) -> list[str]:
        """The program as shown on screen (muted lines commented out)."""
        out = [self.tempo_statement(), ""]
        for role in sorted(self.lines, key=lambda r: ROLE_CHANNELS[r]):
            line = self.lines[role]
            text = f"d{line.channel} $ {self.expression(role) if not line.muted else line.render(self.track.harmony())}"
            if line.muted:
                text = f"-- {text}"
            mark = "▶ " if role == highlight else "  "
            out.append(mark + text)
        return out

    def active_roles(self) -> list[str]:
        return [r for r, l in self.lines.items() if not l.muted]

    def to_dict(self) -> dict:
        return {"track": self.track.to_dict(), "lines": {r: l.to_dict() for r, l in self.lines.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "Program":
        return cls(Track.from_dict(data["track"]),
                   {r: LineSpec.from_dict(l) for r, l in data.get("lines", {}).items()})


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

EDIT_OPS = ("add", "remove", "mute", "unmute", "set_fx", "remove_fx", "set_pf", "set_pattern",
            "wrap", "unwrap", "tempo", "set_sound")


@dataclass
class Edit:
    op: str
    role: str = ""
    name: str = ""          # fx / pF / pattern slot name, or wrapper text for wrap/unwrap
    value: str = ""         # new value (number, sine range, or mini-notation)
    line: LineSpec | None = None   # for "add"
    reason: str = ""
    narration: str = ""
    source: str = ""        # who chose it: "LLM" or "built-in"
    listening: str = ""     # what the performer heard that led to it (LLM only)
    judgement: dict = field(default_factory=dict)   # with a judge: the options it weighed and chose from
    request: str = ""       # the host's request this edit answers ("more bass")
    review: str = ""        # the performer's answer to it: what it does and why
    boldness: str = ""      # a proposal's own label: "safe" or "bold" (performer.BOLDNESS)

    def describe(self) -> str:
        if self.op == "add":
            return f"add d{ROLE_CHANNELS[self.role]} ({self.role})"
        if self.op in ("remove", "mute", "unmute"):
            return f"{self.op} d{ROLE_CHANNELS.get(self.role, 0)} ({self.role})"
        if self.op == "tempo":
            return f"tempo -> {self.value} BPM"
        if self.op == "set_sound":
            return f'd{ROLE_CHANNELS.get(self.role, 0)}: s -> "{self.value}"'
        if self.op in ("wrap", "unwrap"):
            return f"{self.op} d{ROLE_CHANNELS.get(self.role, 0)} {'with' if self.op == 'wrap' else 'from'} `{self.name}`"
        if self.op == "remove_fx":
            return f"remove # {self.name} from d{ROLE_CHANNELS.get(self.role, 0)}"
        what = {"set_fx": f"# {self.name}", "set_pf": f'pF "{self.name}"', "set_pattern": self.name}.get(self.op, self.name)
        return f"d{ROLE_CHANNELS.get(self.role, 0)}: {what} -> {self.value}"


class EditError(ValueError):
    pass


def _check_number(value: str, lo: float, hi: float) -> str:
    value = str(value).strip()
    if _NUMBER_RE.match(value):
        v = float(value)
        if not lo <= v <= hi:
            raise EditError(f"{value} outside {lo}..{hi}")
        return f"{round(v, 4):g}"
    m = _SINE_RE.match(value)
    if m:
        rate, a, b = int(m.group(1)), float(m.group(2)), float(m.group(3))
        if not (1 <= rate <= 64 and lo <= a <= hi and lo <= b <= hi):
            raise EditError(f"{value} outside {lo}..{hi}")
        return f"(slow {rate} $ range {a:g} {b:g} {m.group(4)})"
    raise EditError(f"{value!r} is not a number or (slow N $ range a b sine)")


def check_pattern(value: str) -> str:
    value = " ".join(str(value).split())
    # (Long enough for a tune of 8 bars written across the progression.)
    if len(value) > 260:
        raise EditError(f"pattern too long ({len(value)} characters, at most 260)")
    if not value or not _PATTERN_RE.match(value):
        raise EditError(f"invalid pattern {value!r}")
    for a, b in ("[]", "<>"):
        if value.count(a) != value.count(b):
            raise EditError(f"unbalanced {a}{b} in {value!r}")
    return value


# A sound's whole name in a drum pattern, with its sample number: "808oh",
# "ys_kick", "cpu:3" (never a piece of one: "oh" out of "808oh" let an edit
# ask for a sample that doesn't exist).
_SOUND_RE = re.compile(r"(?<![\w:])\w*[a-z]\w*(?::\d+)?")
_KNOWN_SOUNDS: set[str] | None = None


def known_sounds() -> set[str]:
    """Every sound SuperDirt can play here: the sample banks (Dirt-Samples and
    the project's samples/) and the SynthDefs. Empty if they can't be found
    (then nothing is checked against it)."""
    global _KNOWN_SOUNDS
    if _KNOWN_SOUNDS is None:
        import os

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        names: set[str] = set()
        for folder in (os.path.expanduser("~/.local/share/SuperCollider/downloaded-quarks/Dirt-Samples"),
                       os.path.join(repo, "samples")):
            if os.path.isdir(folder):
                names |= {d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))}
        try:
            with open(os.path.join(repo, "tidal", "synthdefs.scd")) as f:
                names |= set(re.findall(r"SynthDef\(\\(\w+)", f.read()))
        except OSError:
            pass
        if names:
            names |= {"superpiano", "supersaw", "superhammond", "superchip", "supergong", "superfm"}
        _KNOWN_SOUNDS = names
    return _KNOWN_SOUNDS

# Harmony. Melodic slots are scale degrees relative to the current chord's
# root, so every note is in the key, but not every note is in the chord:
# degrees 1, 3 and 5 (2nd, 4th, 6th) clash with the chord that is sounding,
# e.g. degree 5 in minor is a semitone above the chord's fifth. So:
# - bass, acid and fast repeating lines (a "*" in the pattern) play chord
#   tones only (root, 3rd, 5th, 7th in any octave);
# - melodies land on a chord tone on every strong step and may pass through
#   other scale notes only on the weak steps in between.
CHORD_TONES = {0, 2, 4, 6}
_DEGREE_RE = re.compile(r"(?<![*!/@%\d.:])-?\d+")
_STRICT_ROLES = ("bass", "acid", "arp")


_MOVING_RE = re.compile(r"\b(sine|cosine|saw|isaw|tri|square|rand|perlin)\b")


def line_complexity(line: "LineSpec") -> float:
    """How much a line contributes to the music's complexity: 1 for the
    layer itself, plus half a point each for every wrapper (a variation
    or transformation), every control that moves on its own (a slow sine
    on the filter or acid knob), a phrase that changes bar to bar
    (`<[...] [...]>`), and a 16th-note stream."""
    score = 1.0 + 0.5 * len(line.wrappers)
    score += 0.5 * sum(bool(_MOVING_RE.search(v)) for _, v in line.fx + line.pfs)
    values = list(line.patterns.values())
    if any("<" in v for v in values) or re.search(r'"<\{(?!roots|chords)\w+\}>', line.body):
        score += 0.5
    if any(re.search(r"\*(16|8)\b", v) for v in values) or re.search(r"\*(16|8)\b", line.body):
        score += 0.5
    return score


def complexity(program: "Program") -> float:
    """The complexity of what plays now: the sum over active lines."""
    return sum(line_complexity(program.lines[r]) for r in program.active_roles())


_RHYTHM_RE = re.compile(r"^[t~_\s\[\]<>*!\d.]+$")
# How far each part's degrees may go (7 = an octave above its own register),
# so lines stay in their lane: no lead or arp climbing into the shrill zone,
# no bass rising into the chords.
ROLE_DEGREES = {"bass": (-7, 7), "acid": (-7, 11), "arp": (-7, 11), "lead": (-3, 11), "keys": (-7, 11),
                "chords": (-7, 11)}


def range_problem(role: str, value: str) -> str | None:
    lo, hi = ROLE_DEGREES.get(role, (-14, 14))
    degrees = [int(d) for d in _DEGREE_RE.findall(value)]
    out = sorted({d for d in degrees if not lo <= d <= hi})
    if out:
        return f"degrees {out} are outside the {role}'s range ({lo} to {hi}): keep it in its register"
    return None


def _top_split(text: str) -> list[str]:
    """Split mini-notation on spaces outside [] and <>."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch in "[<"
        depth -= ch in "]>"
        if ch.isspace() and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + ([cur] if cur else [])


def _unwrap(text: str, open_: str, close: str) -> str | None:
    """The inside of text if one bracket pair wraps all of it, else None."""
    if not (text.startswith(open_) and text.endswith(close)):
        return None
    depth = 0
    for i, ch in enumerate(text):
        depth += ch in "[<"
        depth -= ch in "]>"
        if depth == 0 and i < len(text) - 1:
            return None
    return text[1:-1]


def phrase_steps(value: str) -> list[list[tuple[str, bool]]] | None:
    """A melody's bars as (token, on the beat?) per step: one bar, or one per
    alternation step in <[bar 1] [bar 2] ...>. 8 steps are 8ths (beats on
    1, 3, 5, 7), 16 are 16ths (beats on 1, 5, 9, 13), a [..] inside a step
    splits it (its first note keeps the step's place). None when the timing
    can't be read (fast repeats, stretched or random steps, chords)."""
    if re.search(r"[*!{}(),|@/.]", value):
        return None
    inner = _unwrap(value.strip(), "<", ">")
    bars = []
    for bar in _top_split(inner) if inner is not None else [value.strip()]:
        steps = _top_split(_unwrap(bar, "[", "]") or bar)
        n = len(steps)
        per_beat = n // 4 if n % 4 == 0 else (2 if n % 2 == 0 else 0)
        seq = []
        for i, step in enumerate(steps):
            strong = per_beat <= 1 or i % per_beat == 0
            if step.startswith("<"):
                return None
            sub = _unwrap(step, "[", "]")
            for j, tok in enumerate(_top_split(sub) if sub is not None else [step]):
                if tok.startswith(("[", "<")):
                    return None
                seq.append((tok.rstrip("?"), strong and j == 0))
        bars.append(seq)
    return bars


def harmony_problem(role: str, value: str) -> str | None:
    """Why a melodic pattern would clash with the chords, or None if it fits.
    Bass, acid, arps and fast repeating figures play chord tones only. A lead
    or keys melody puts chord tones on the beats and may use the scale's other
    notes between them, as passing or neighbour notes: a step from or to the
    note beside it, not held on into the next beat."""
    degrees = [int(d) for d in _DEGREE_RE.findall(value)]
    if not degrees:
        return None
    off = sorted({d for d in degrees if d % 7 not in CHORD_TONES})
    if not off:
        return None
    bars = None if role in _STRICT_ROLES else phrase_steps(value)
    if bars is None:
        return (f"degrees {off} are not in the chord; {role} lines and repeating patterns play chord "
                "tones only (0, 2, 4, 6, 7, 9 ... and below: -1, -3, -5)")
    seq = [tok for bar in bars for tok in bar]
    clash, leaps = set(), set()
    for i, (tok, strong) in enumerate(seq):
        if not _DEGREE_RE.fullmatch(tok) or int(tok) % 7 in CHORD_TONES:
            continue
        d = int(tok)
        if strong:
            clash.add(d)
            continue
        # (The phrase loops: the last note's neighbour is the first.)
        prev = seq[i - 1][0] if i > 0 else seq[-1][0]
        nxt = seq[i + 1][0] if i + 1 < len(seq) else seq[0][0]
        by_step = any(_DEGREE_RE.fullmatch(t) and abs(int(t) - d) == 1 for t in (prev, nxt))
        if not by_step or nxt == "_":
            leaps.add(d)
    if clash:
        return (f"degrees {sorted(clash)} fall on the beat but aren't in the chord; put chord tones "
                "(0, 2, 4, 6, 7 ...) on the beats and other notes only in between")
    if leaps:
        return (f"degrees {sorted(leaps)} aren't in the chord: between the beats they must step from or to "
                "the note beside them (a passing or neighbour note, e.g. 4 3 2 or 2 3 2) and not be held")
    return None


def kit_sounds(style: str, block_name: str, slot: str) -> set[str]:
    """The sample names a drum slot may use: those in its block's variants."""
    from .blocks import find_block

    block = find_block(style, block_name)
    if block is None or slot not in block.patterns:
        return set()
    return {m for variant in block.patterns[slot] for m in _SOUND_RE.findall(variant)}


def gain_range(line: LineSpec) -> tuple[float, float]:
    """Gain edits balance a line, they don't blow up the mix: at most about
    +/-3 dB (gain x0.92..x1.09, SuperDirt's curve is amplitude ~ gain^4)
    around the level the line entered at."""
    lo, hi = FX_RANGES["gain"]
    try:
        base = float(line.base_gain or dict(line.fx).get("gain", ""))
    except ValueError:
        return lo, hi
    return round(max(lo, base * 0.92), 3), round(min(hi, base * 1.09), 3)


def hook_kept_problem(hook: str, value: str) -> str | None:
    """Why a new pattern for the line carrying the hook loses it, or None.
    The hook is what people remember, so it stays: all its bars but the last
    (the answer) as written; the answer, and any bars added after, may vary.
    A one-bar hook stays as its first bar."""
    old, new = _bars(hook), _bars(value)
    keep = max(1, len(old) - 1)
    if len(new) < keep or [b for b in new[:keep]] != [b for b in old[:keep]]:
        shown = " ".join("[" + " ".join(b) + "]" for b in old[:keep])
        return (f"the hook stays: keep its first {keep} bar{'s' if keep > 1 else ''} ({shown}) as they are and "
                "vary only its last bar, the answer")
    return None


def apply_edit(program: Program, edit: Edit, max_lines: int = 7) -> Program:
    """Validate and apply an edit, returning a new Program. Raises EditError."""
    new = copy.deepcopy(program)
    op, role = edit.op, edit.role
    if op not in EDIT_OPS:
        raise EditError(f"unknown op {op!r}")
    if op == "tempo":
        lo, hi = 50, 200
        bpm = float(_check_number(edit.value, lo, hi))
        if abs(bpm - program.track.bpm) > 12:
            raise EditError("tempo changes must be gradual (at most 12 BPM at a time)")
        if program.track.vocal and bpm != program.track.bpm:
            raise EditError(f"the tempo stays at {program.track.bpm:g} BPM: the acapella "
                            f"({program.track.vocal.get('name', '')}) is sung at it, faster or slower puts it out of tune")
        new.track.bpm = bpm
        return new
    if op == "add":
        if edit.line is None or edit.line.role not in ROLE_CHANNELS:
            raise EditError("add needs a line")
        if edit.line.role in new.lines and not new.lines[edit.line.role].muted:
            raise EditError(f"{edit.line.role} is already playing")
        if len(new.active_roles()) >= max_lines:
            raise EditError(f"at most {max_lines} lines")
        new.lines[edit.line.role] = copy.deepcopy(edit.line)
        return new
    if role not in new.lines:
        raise EditError(f"no line for {role!r}")
    line = new.lines[role]
    if op == "remove":
        del new.lines[role]
    elif op == "mute":
        line.muted = True
    elif op == "unmute":
        if len(new.active_roles()) >= max_lines:
            raise EditError(f"at most {max_lines} lines")
        line.muted = False
    elif op in ("set_fx", "remove_fx"):
        if edit.name in PF_RANGES and edit.name not in FX_RANGES:
            raise EditError(f"{edit.name!r} is a synth control: use set_pf")
        if edit.name not in FX_RANGES or edit.name not in ROLE_FX.get(role, list(FX_RANGES)):
            raise EditError(f"effect {edit.name!r} not allowed on the {role}")
        line.fx = [pair for pair in line.fx if pair[0] != edit.name]
        if op == "set_fx":
            lo, hi = gain_range(line) if edit.name == "gain" else FX_RANGES[edit.name]
            line.fx.append([edit.name, _check_number(edit.value, lo, hi)])
        elif edit.name == "gain":
            raise EditError("gain can be adjusted but not removed")
    elif op == "set_pf":
        if edit.name not in PF_RANGES or edit.name not in line_controls(line):
            raise EditError(f"control {edit.name!r} isn't one this line's synth takes"
                            f" ({', '.join(line_controls(line)) or 'none'})")
        value = _check_number(edit.value, *PF_RANGES[edit.name])
        if any(p[0] == edit.name for p in line.pfs):
            line.pfs = [[n, value if n == edit.name else v] for n, v in line.pfs]
        else:
            line.pfs.append([edit.name, value])
    elif op == "set_pattern":
        if edit.name not in line.patterns:
            raise EditError(f"pattern {edit.name!r} not on this line (has {sorted(line.patterns)})")
        budget = line.max_notes or NOTE_BUDGET.get(line.role, 0)
        if budget and not line.drums:
            busy = notes_per_bar(line.body, edit.name, str(edit.value))
            if busy > budget:
                raise EditError(f"{busy:g} new notes in a bar is too busy for the {line.role}: at most {budget} "
                                "(less is more: hold notes with _ and breathe with ~ rests)")
        if edit.name in LOCKED_SLOTS:
            raise EditError(f"{edit.name!r} can't be edited")
        if edit.name in CHOICE_SLOTS:
            if str(edit.value) not in CHOICE_SLOTS[edit.name]:
                raise EditError(f"{edit.name} must be one of {CHOICE_SLOTS[edit.name]}")
            line.patterns[edit.name] = str(edit.value)
            return new
        value = check_pattern(edit.value)
        if f'struct "{{{edit.name}}}"' in line.body:
            # A rhythm (chord stabs, gates): t a hit, ~ a rest, _ a hold.
            if not _RHYTHM_RE.match(value) or "t" not in value:
                raise EditError('a rhythm is t (hit), ~ (rest) and _ (hold), e.g. "~ t _ t ~ t t _"')
        elif not line.drums:
            if re.search(r"[a-z]", value.replace("~", "")):
                raise EditError("melodic patterns are scale degrees (numbers), not names")
            if line.key_tune and edit.name == line.hook_slot:
                from .hooks import key_harmony_problem

                problem = key_harmony_problem(value, program.track.degrees, program.track.bars_per_chord)
            else:
                problem = harmony_problem(line.role, value) or range_problem(line.role, value)
            if problem:
                raise EditError(problem)
            if line.hook and edit.name == line.hook_slot:
                problem = hook_kept_problem(line.hook, value)
                if problem:
                    raise EditError(problem)
        if line.drums:
            # Drum lines keep their kit: only the sounds the block defines for
            # this slot (any other sample could be pitched, out of key, or
            # simply not belong to the track).
            allowed = kit_sounds(program.track.style, line.block, edit.name)
            allowed |= {m for v in (program.track.kit or {}).get(edit.name, []) for m in _SOUND_RE.findall(v)}
            used = set(_SOUND_RE.findall(value))
            if allowed and not used <= allowed:
                raise EditError(f"{sorted(used - allowed)} not in this line's kit; use {sorted(allowed)}")
            # Never a sound that isn't there (SuperDirt would play silence and complain).
            missing = sorted(u for u in used if u.split(":")[0] not in known_sounds()) if known_sounds() else []
            if missing:
                raise EditError(f"no sample or synth named {', '.join(missing)}"
                                + (f"; this line's kit: {sorted(allowed)}" if allowed else ""))
        line.patterns[edit.name] = value
    elif op == "set_sound":
        # Another sound from the part's palette (blocks.LIVE_SOUNDS), on a line
        # that plays one of them: the drums and special sounds keep theirs.
        from .blocks import LIVE_SOUNDS

        palette = LIVE_SOUNDS.get(role, {})
        current = line_synth(line)
        if edit.value not in palette:
            raise EditError(f"{edit.value!r} isn't a {role} sound ({', '.join(palette) or 'none'})")
        if current not in palette:
            raise EditError(f"the {role} plays {current or 'its own sound'}, which doesn't change")
        if "synth" in line.patterns:
            line.patterns["synth"] = edit.value
        else:
            line.body = line.body.replace(f'# s "{current}"', f'# s "{edit.value}"')
    elif op == "wrap":
        allowed = allowed_wrappers(line, program.track.style)
        if edit.name not in allowed:
            raise EditError(f"wrapper {edit.name!r} not allowed here")
        if edit.name in line.wrappers:
            raise EditError("already wrapped")
        if len(line.wrappers) >= 3:
            raise EditError("at most 3 wrappers per line")
        line.wrappers.append(edit.name)
    elif op == "unwrap":
        if edit.name not in line.wrappers:
            raise EditError(f"{edit.name!r} is not on this line")
        line.wrappers.remove(edit.name)
    return new


def changed_statements(before: Program, after: Program) -> list[str]:
    """Tidal statements needed to go from `before` to `after`."""
    out = []
    if before.track.bpm != after.track.bpm:
        out.append(after.tempo_statement())
    harmony_changed = before.track.harmony() != after.track.harmony()
    for role in after.lines:
        if harmony_changed or role not in before.lines or \
                before.statement(role) != after.statement(role):
            out.append(after.statement(role))
    for role in before.lines:
        if role not in after.lines:
            out.append(f"d{ROLE_CHANNELS[role]} $ silence")
    return out
