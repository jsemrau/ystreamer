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
    "beat": 1, "bass": 2, "chords": 3, "keys": 4, "lead": 5, "acid": 6, "texture": 7, "perc": 8,
}
ROLES = list(ROLE_CHANNELS)

# Effects an edit may set, with value ranges (value may also be a slow sine
# between two in-range numbers, e.g. "(slow 8 $ range 400 2400 sine)").
FX_RANGES: dict[str, tuple[float, float]] = {
    "gain": (0.5, 1.15), "room": (0.0, 0.8), "size": (0.0, 0.95), "lpf": (200, 9000), "hpf": (20, 2000),
    "resonance": (0.0, 0.5), "delay": (0.0, 0.6), "delaytime": (0.05, 1.0), "delayfeedback": (0.0, 0.7),
    "pan": (0.0, 1.0), "legato": (0.1, 2.0), "crush": (4.0, 16.0), "shape": (0.0, 0.5), "speed": (0.5, 2.0),
}
# Custom SynthDef controls (sent with pF).
PF_RANGES: dict[str, tuple[float, float]] = {
    "acid": (0.1, 0.95), "acidres": (0.08, 0.6), "gatedepth": (0.0, 1.0), "duckdepth": (0.0, 0.9),
}

# Wrappers an edit may add, per kind of line. Pitch-safe: only octave shifts.
MELODIC_WRAPPERS = [
    "every 4 (fast 2)", "every 3 (0.25 <~)", "every 4 (0.125 ~>)", "sometimesBy 0.2 (degradeBy 0.5)",
    "off 0.125 (|+ n 12)", "off 0.25 (|+ n 12)", "jux rev", "every 4 rev", "degradeBy 0.1",
    "whenmod 8 6 (# lpf 800)", "every 2 (# room 0.5)",
]
DRUM_WRAPPERS = [
    "every 4 (fast 2)", "degradeBy 0.1", "every 8 (0.25 <~)",
    "whenmod 8 7 (# crush 6)", "swingBy (1/8) 4", "swingBy (1/12) 4", "every 3 (# lpf 1500)",
    "sometimesBy 0.1 (echo 3 0.125 0.5)",
]
BASS_WRAPPERS = ["every 3 (0.25 <~)", "every 4 (0.125 ~>)", "degradeBy 0.1", "sometimesBy 0.2 (degradeBy 0.5)",
                 "whenmod 8 6 (# lpf 300)"]
ALL_WRAPPERS = set(MELODIC_WRAPPERS) | set(DRUM_WRAPPERS) | set(BASS_WRAPPERS)

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
    "whenmod 8 6 (# lpf 800)": "last 2 bars of every 8 go muffled: a dip before the next phrase",
    "every 2 (# room 0.5)": "every other bar gets more reverb: the space pulses",
    "every 8 (0.25 <~)": "every 8th bar shifts early: a turnaround",
    "whenmod 8 7 (# crush 6)": "last bar of every 8 is bitcrushed: a gritty fill",
    "swingBy (1/8) 4": "strong swing: laid-back hip-hop feel",
    "swingBy (1/12) 4": "light swing: loosens a stiff groove",
    "every 3 (# lpf 1500)": "every 3rd bar darker: subtle variation",
    "sometimesBy 0.1 (echo 3 0.125 0.5)": "occasional stuttered echo: a glitchy fill",
    "whenmod 8 6 (# lpf 300)": "last 2 bars of every 8 the bass goes subby: tension before the turn",
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
    "acidres": "resonance of the acid filter: higher is more whistling",
    "gatedepth": "how hard the trance gate chops the chords",
    "duckdepth": "sidechain pump from the kick: 0 none, 0.8 strong pumping",
}

# Effects that make sense per role (bass stays dry and dark; textures only
# get space and tone).
ROLE_FX = {
    "beat": ["lpf", "room", "crush", "shape", "gain"], "perc": ["room", "pan", "gain", "lpf"],
    "bass": ["lpf", "gain", "legato"], "texture": ["room", "lpf", "gain"],
    "chords": ["lpf", "room", "size", "gain"], "keys": ["lpf", "room", "delay", "legato", "gain"],
    "lead": ["lpf", "room", "delay", "delayfeedback", "legato", "gain"],
    "acid": ["lpf", "room", "delay", "legato", "gain", "resonance"],
}


def allowed_wrappers(line: "LineSpec") -> list[str]:
    if line.role == "texture":
        return []
    if line.drums:
        return DRUM_WRAPPERS
    if line.role == "bass":
        return BASS_WRAPPERS
    return MELODIC_WRAPPERS

# Pattern slots that aren't free mini-notation.
CHOICE_SLOTS = {"gate": ["0", "1", "2", "3"]}   # trance gate: chopper, classic, two stutters
LOCKED_SLOTS = {"synth"}                         # chosen when the line is added

# What a mini-notation pattern value may contain.
_PATTERN_RE = re.compile(r"^[0-9a-z_~\s\[\]<>*!.,:\-|/?@]+$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SINE_RE = re.compile(r"^\(slow (\d+) \$ range (-?[\d.]+) (-?[\d.]+) (sine|saw|tri)\)$")


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


@dataclass
class Track:
    """The musical frame of the current track: key, tempo, progression."""
    title: str
    style: str
    key: str
    bpm: float
    degrees: list[int]              # chord progression as scale degrees (0 = tonic)
    bars_per_chord: int = 1

    @property
    def cps(self) -> float:
        return self.bpm / 60.0 / 4.0

    def harmony(self) -> dict[str, str]:
        """Placeholders for line templates, all in scale-degree space."""
        key = Key.parse(self.key)
        rep = f"!{self.bars_per_chord}" if self.bars_per_chord > 1 else ""
        # Chord stacks kept near the tonic (degrees above 3 drop an octave).
        stacks = []
        for d in self.degrees:
            base = d - 7 if d > 3 else d
            stacks.append("[" + ",".join(str(base + i) for i in (0, 2, 4, 6)) + "]" + rep)
        roots = " ".join(f"{d - 7 if d > 3 else d}{rep}" for d in self.degrees)
        root = key.root if key.root <= 5 else key.root - 12
        root_text = f" + {root}" if root > 0 else (f" - {-root}" if root < 0 else "")
        bar = 1.0 / self.cps
        return {
            "mode": key.mode,
            "root": root_text,          # e.g. " + 2", " - 3" or "" (C)
            "chords": " ".join(stacks),
            "roots": roots,
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
            "wrap", "unwrap", "tempo")


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

    def describe(self) -> str:
        if self.op == "add":
            return f"add d{ROLE_CHANNELS[self.role]} ({self.role})"
        if self.op in ("remove", "mute", "unmute"):
            return f"{self.op} d{ROLE_CHANNELS.get(self.role, 0)} ({self.role})"
        if self.op == "tempo":
            return f"tempo -> {self.value} BPM"
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
    if not value or len(value) > 120 or not _PATTERN_RE.match(value):
        raise EditError(f"invalid pattern {value!r}")
    for a, b in ("[]", "<>"):
        if value.count(a) != value.count(b):
            raise EditError(f"unbalanced {a}{b} in {value!r}")
    return value


_SOUND_RE = re.compile(r"[a-z][a-z0-9]*(?::\d+)?")

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
_STRICT_ROLES = ("bass", "acid")


def harmony_problem(role: str, value: str) -> str | None:
    """Why a melodic pattern would clash with the chords, or None if it fits."""
    degrees = [int(d) for d in _DEGREE_RE.findall(value)]
    if not degrees:
        return None
    off = sorted({d for d in degrees if d % 7 not in CHORD_TONES})
    if not off:
        return None
    tokens = value.split()
    flat = not re.search(r"[\[\]<>*!{}(),|]", value)
    if role in _STRICT_ROLES or not flat:
        return (f"degrees {off} are not in the chord; {role} lines and repeating patterns play chord "
                "tones only (0, 2, 4, 6, 7, 9 ... and below: -1, -3, -5)")
    strong = [t for i, t in enumerate(tokens) if i % 2 == 0 and _DEGREE_RE.fullmatch(t)]
    clash = sorted({int(t) for t in strong if int(t) % 7 not in CHORD_TONES})
    if clash:
        return (f"degrees {clash} fall on strong steps but aren't in the chord; put chord tones "
                "(0, 2, 4, 6, 7 ...) on the strong steps and other notes only in between")
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
        if edit.name not in FX_RANGES or edit.name not in ROLE_FX.get(role, list(FX_RANGES)):
            raise EditError(f"effect {edit.name!r} not allowed on the {role}")
        line.fx = [pair for pair in line.fx if pair[0] != edit.name]
        if op == "set_fx":
            lo, hi = gain_range(line) if edit.name == "gain" else FX_RANGES[edit.name]
            line.fx.append([edit.name, _check_number(edit.value, lo, hi)])
        elif edit.name == "gain":
            raise EditError("gain can be adjusted but not removed")
    elif op == "set_pf":
        if edit.name not in PF_RANGES or not any(p[0] == edit.name for p in line.pfs):
            raise EditError(f"control {edit.name!r} not on this line")
        value = _check_number(edit.value, *PF_RANGES[edit.name])
        line.pfs = [[n, value if n == edit.name else v] for n, v in line.pfs]
    elif op == "set_pattern":
        if edit.name not in line.patterns:
            raise EditError(f"pattern {edit.name!r} not on this line (has {sorted(line.patterns)})")
        if edit.name in LOCKED_SLOTS:
            raise EditError(f"{edit.name!r} can't be edited")
        if edit.name in CHOICE_SLOTS:
            if str(edit.value) not in CHOICE_SLOTS[edit.name]:
                raise EditError(f"{edit.name} must be one of {CHOICE_SLOTS[edit.name]}")
            line.patterns[edit.name] = str(edit.value)
            return new
        value = check_pattern(edit.value)
        if not line.drums and re.search(r"[a-z]", value.replace("~", "")):
            raise EditError("melodic patterns are scale degrees (numbers), not names")
        if not line.drums:
            problem = harmony_problem(line.role, value)
            if problem:
                raise EditError(problem)
        if line.drums:
            # Drum lines keep their kit: only the sounds the block defines for
            # this slot (any other sample could be pitched, out of key, or
            # simply not belong to the track).
            allowed = kit_sounds(program.track.style, line.block, edit.name)
            used = set(_SOUND_RE.findall(value))
            if allowed and not used <= allowed:
                raise EditError(f"{sorted(used - allowed)} not in this line's kit; use {sorted(allowed)}")
        line.patterns[edit.name] = value
    elif op == "wrap":
        allowed = allowed_wrappers(line)
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
