"""
Piece plans and section arrangements: the structured vocabulary the Director
(LLM or deterministic fallback) speaks.

A stream is an endless sequence of *pieces*. A piece has a key, tempo, style,
progression and a list of sections; each section has a length in bars
(a multiple of the progression period) and an energy level. An *arrangement*
says which preset plays each role in a section.

Everything coming from an LLM goes through `validate_piece` /
`validate_arrangement`, which clamp or replace anything invalid, so a bad LLM
answer can never produce broken or out-of-key music.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field

from . import presets as P
from .theory import CHORD_COLORS, MODES, PROGRESSIONS_BY_NAME, Key, progressions_for

STYLES = {
    "lofi": {
        "palette": {"pad": ["warm_pad", "strings_pad", "organ_pad", "choir_pad"],
                    "keys": ["rhodes", "felt", "piano"], "bass": ["sub", "pluck_bass"],
                    "melody": ["felt_lead", "epiano_lead", "glass", "mandolin"],
                    "arp": ["pluck_arp", "glass_arp"]},
        "openers": [["keys", "texture"], ["pad", "texture"], ["drums", "keys"], ["arp", "texture"]],
        "bpm": (70, 88), "modes": ["minor", "dorian", "major"], "cycles_per_chord": 1,
        "presets": {"pad": "warm_pad", "keys": "rhodes", "bass": "sub", "melody": "felt_lead",
                    "arp": "pluck_arp", "drums": "lofi_kit", "texture": "vinyl"},
    },
    "ambient": {
        "palette": {"pad": ["air_pad", "strings_pad", "choir_pad", "warm_pad"],
                    "keys": ["felt", "piano"], "melody": ["glass", "felt_lead"],
                    "arp": ["glass_arp", "pluck_arp"], "texture": ["air", "vinyl"]},
        "openers": [["pad", "texture"], ["arp", "texture"], ["keys", "texture"], ["texture", "melody"]],
        "bpm": (60, 76), "modes": ["lydian", "major", "minor"], "cycles_per_chord": 2,
        "presets": {"pad": "air_pad", "keys": "felt", "bass": "sub", "melody": "glass",
                    "arp": "glass_arp", "drums": "soft_pulse", "texture": "air"},
    },
    "downtempo": {
        "palette": {"pad": ["warm_pad", "strings_pad", "organ_pad"], "keys": ["felt", "rhodes"],
                    "bass": ["pluck_bass", "sub"], "melody": ["epiano_lead", "glass", "felt_lead"],
                    "texture": ["vinyl", "air"]},
        "openers": [["keys", "bass"], ["drums", "bass"], ["pad", "texture"], ["arp", "bass"]],
        "bpm": (80, 96), "modes": ["minor", "dorian", "mixolydian"], "cycles_per_chord": 1,
        "presets": {"pad": "warm_pad", "keys": "felt", "bass": "pluck_bass", "melody": "epiano_lead",
                    "arp": "pluck_arp", "drums": "downtempo_kit", "texture": "vinyl"},
    },
    "piano": {
        "palette": {"pad": ["piano_pad", "strings_pad", "choir_pad"], "keys": ["piano", "felt"],
                    "melody": ["felt_lead", "glass"]},
        "openers": [["keys", "texture"], ["pad", "keys"], ["melody", "keys"]],
        "bpm": (64, 80), "modes": ["major", "minor", "lydian"], "cycles_per_chord": 2,
        "presets": {"pad": "piano_pad", "keys": "piano", "bass": "sub", "melody": "felt_lead",
                    "arp": "glass_arp", "drums": "soft_pulse", "texture": "air"},
    },
    "deephouse": {
        "palette": {"pad": ["warm_pad", "strings_pad", "choir_pad", "organ_pad"],
                    "keys": ["deep_stab", "organ_stab"], "melody": ["epiano_lead", "glass"]},
        "openers": [["drums", "bass"], ["drums", "keys"], ["keys", "drums"], ["drums", "arp"]],
        "bpm": (118, 124), "modes": ["minor", "dorian"], "cycles_per_chord": 2,
        "presets": {"pad": "warm_pad", "keys": "deep_stab", "bass": "deep_bass", "melody": "epiano_lead",
                    "arp": "pluck_arp", "drums": "house_kit", "texture": "vinyl"},
        # The groove comes first: kick and bass open the track.
        "energy_order": ["drums", "bass", "keys", "pad", "texture", "melody", "arp"],
        # Only when asked for (--style, a theme, or the LLM): not in the chill rotation.
        "rotate": False,
    },
    "trance": {
        # After Switch Angel's live-coded trance: 138 BPM, four-on-the-floor,
        # rolling bass, gated supersaw arps, breakdown -> build -> drop.
        "bpm": (134, 140), "modes": ["minor"], "cycles_per_chord": 2,
        # The acid line is the heart of it (Switch Angel's Strudel acid trance):
        # a squelchy 16th saw line, a kick, and everything pumping on the kick.
        "presets": {"pad": "saw_pad", "keys": "piano", "bass": "acid_line", "melody": "saw_lead",
                    "arp": "acid_hi", "drums": "trance_kit", "texture": "air"},
        "palette": {"pad": ["saw_pad"], "keys": ["piano", "felt"],
                    "bass": ["acid_line"],
                    "arp": ["acid_hi", "trance_gate"], "melody": ["saw_lead", "glass"]},
        "energy_order": ["bass", "drums", "arp", "pad", "texture", "melody", "keys"],
        "openers": [["bass"], ["bass", "drums"], ["drums", "bass"]],
        # The acid line keeps going under the pad in breakdowns, knob turned down.
        "breakdown": ["bass", "pad"],
        "arc": [("intro", 0.3, 2), ("verse", 0.6, 4), ("breakdown", 0.3, 2), ("build", 0.65, 2),
                ("chorus", 0.9, 4), ("verse", 0.6, 2), ("build", 0.7, 2), ("chorus", 0.95, 4),
                ("outro", 0.3, 2)],
        "rotate": False,
    },
}

# Techno and hardcore: a pounding four-on-the-floor kick, offbeat rumble,
# 16th hats, acid and short stabs. They share trance's arranger presets; the
# live engine has its own blocks for them (music/blocks.py).
# Darkwave (cold electro / coldwave): minor keys, slow harmony, the sequencer
# bass and drum machine carry it. Shares trance's arranger presets too.
STYLES["darkwave"] = {**STYLES["trance"], "bpm": (110, 128), "cycles_per_chord": 2,
                      "openers": [["bass"], ["drums", "bass"]],
                      "energy_order": ["bass", "drums", "pad", "arp", "melody", "texture", "keys"]}

for _name, _bpm in (("techno", (138, 150)), ("hardcore", (160, 185))):
    STYLES[_name] = {**STYLES["trance"], "bpm": _bpm, "cycles_per_chord": 4,
                     "openers": [["drums"], ["drums", "bass"]],
                     "energy_order": ["drums", "bass", "arp", "pad", "texture", "melody", "keys"]}

# Theme words that select a style (checked in this order).
THEME_STYLE_KEYWORDS = [
    ("hardcore", ("hardcore", "gabber", "gabba", "speedcore", "frenchcore", "terrorcore", "rotterdam")),
    ("darkwave", ("darkwave", "dark wave", "coldwave", "cold wave", "cold electro", "minimal wave",
                  "minimal synth", "post-punk", "post punk", "ebm")),
    ("techno", ("techno", "hard techno", "acid techno", "industrial", "rave", "berghain")),
    ("trance", ("trance", "switch angel", "switchangel", "uplifting", "psytrance", "supersaw")),
    ("deephouse", ("deep house", "house", "garage", "club", "dance")),
    ("lofi", ("lofi", "lo-fi", "hip hop", "hiphop", "study", "beats")),
    ("ambient", ("ambient", "sleep", "drone", "meditation", "space")),
    ("downtempo", ("downtempo", "trip hop", "trip-hop", "chillout", "chill-out")),
    ("piano", ("piano",)),
]


def style_from_theme(theme: str | None) -> str | None:
    """Pick a style from words in a free-text theme, e.g. "frankfurt deep house"."""
    text = f" {(theme or '').lower()} "
    for style, words in THEME_STYLE_KEYWORDS:
        if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words):
            return style
    return None

SECTION_TYPES = ["intro", "verse", "build", "chorus", "breakdown", "bridge", "outro"]

# Default arc: (section, energy, bars multiplier in progression periods)
DEFAULT_ARC = [
    ("intro", 0.2, 2),
    ("verse", 0.45, 4),
    ("build", 0.6, 2),
    ("chorus", 0.8, 4),
    ("breakdown", 0.3, 2),
    ("verse", 0.55, 4),
    ("chorus", 0.8, 2),
    ("outro", 0.15, 2),
]

MOODS = ["calm", "nostalgic", "dreamy", "warm", "hopeful", "melancholic", "floating", "cozy", "late-night"]
TITLE_WORDS_A = ["Quiet", "Amber", "Paper", "Slow", "Velvet", "Distant", "Soft", "Golden", "Rainy", "Late",
                 "Hidden", "Silver", "Lantern", "Morning", "Blue", "Drifting", "Warm", "Faded"]
TITLE_WORDS_B = ["Harbor", "Window", "Streets", "Tides", "Letters", "Gardens", "Echoes", "Rooms", "Skies",
                 "Rivers", "Hours", "Lights", "Clouds", "Dreams", "Fields", "Waves", "Trains", "Pages"]


@dataclass
class Section:
    name: str
    bars: int
    energy: float
    key_shift: int = 0  # semitones above the piece key (e.g. +2 for a lifted final chorus)


@dataclass
class Piece:
    title: str
    style: str
    key: str            # e.g. "C minor"
    bpm: float
    progression: str    # name in theory.PROGRESSIONS_BY_NAME
    cycles_per_chord: int
    mood: str
    sections: list[Section]
    seed: int = 0
    narration: str = ""  # spoken to listeners when the piece starts
    # Section name -> progression, so choruses and breakdowns get their own
    # harmony; sections not listed use `progression`.
    section_progressions: dict[str, str] = field(default_factory=dict)
    color: str = "sevenths"  # chord colour: sevenths | ninths
    # This piece's own sound: role -> preset, and which roles open the track.
    instruments: dict[str, str] = field(default_factory=dict)
    opening: list[str] = field(default_factory=list)

    def energy_order(self) -> list[str]:
        """Order in which layers come in as energy rises, starting with the opening."""
        base = STYLES.get(self.style, {}).get("energy_order", P.ENERGY_ORDER)
        first = [r for r in self.opening if r in base]
        return first + [r for r in base if r not in first]

    def instrument(self, role: str) -> str:
        return self.instruments.get(role) or STYLES[self.style]["presets"][role]

    def progression_for(self, section_name: str) -> str:
        return self.section_progressions.get(section_name, self.progression)

    @property
    def cps(self) -> float:
        return self.bpm / 60.0 / 4.0

    @property
    def period(self) -> int:
        return 4 * self.cycles_per_chord

    @property
    def total_bars(self) -> int:
        return sum(s.bars for s in self.sections)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Piece":
        data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        data["sections"] = [Section(**s) for s in data["sections"]]
        return cls(**data)


@dataclass
class Arrangement:
    layers: dict[str, str | None] = field(default_factory=dict)  # role -> preset name
    melody_character: str | None = None
    variation_seed: int = 0
    narration: str = ""  # spoken to listeners when the section starts
    fills: bool = True        # drum fills / variations at phrase ends
    movement: str = "gentle"  # filter movement on pads and keys: still | gentle | lively

    def active_roles(self) -> list[str]:
        return [r for r, p in self.layers.items() if p]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Arrangement":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Deterministic planners (always available; used when the LLM is off/failing)
# ---------------------------------------------------------------------------

def plan_piece(rng: random.Random, previous: Piece | None = None, style: str | None = None,
               avoid_progressions: set[str] | None = None, target_minutes: float = 12.0,
               disliked: set[str] | None = None) -> Piece:
    """avoid_progressions: soft preference (e.g. recently used);
    disliked: excluded whenever any alternative exists."""
    disliked = set(disliked or ())
    rotation = [name for name, cfg in STYLES.items() if cfg.get("rotate", True)]
    style = style if style in STYLES else (previous.style if previous and rng.random() < 0.6 else rng.choice(rotation))
    cfg = STYLES[style]

    if previous is not None:
        prev_key = Key.parse(previous.key)
        options = [k for k in prev_key.neighbours() if k.mode in cfg["modes"]]
        if not options:
            # Style changed modes: pick the style-compatible key sharing the
            # most notes with the previous one (at most one note different).
            candidates = [Key(r, m) for r in range(12) for m in cfg["modes"] if Key(r, m) != prev_key]
            best = max(len(prev_key.pitch_classes & k.pitch_classes) for k in candidates)
            options = [k for k in candidates if len(prev_key.pitch_classes & k.pitch_classes) == best]
        key = rng.choice(options)
    else:
        key = Key(rng.randrange(12), rng.choice(cfg["modes"]))

    lo, hi = cfg["bpm"]
    bpm = float(rng.randint(lo, hi))
    if previous is not None:
        # Keep tempo changes between pieces gentle.
        bpm = float(max(lo, min(hi, previous.bpm + rng.randint(-8, 8))))

    avoid = set(avoid_progressions or ())
    if previous is not None:
        avoid.add(previous.progression)
    pool = progressions_for(key.mode)
    candidates = ([p for p in pool if p.name not in avoid and p.name not in disliked]
                  or [p for p in pool if p.name not in disliked] or pool)
    progression = rng.choice(candidates)

    piece = Piece(
        title=f"{rng.choice(TITLE_WORDS_A)} {rng.choice(TITLE_WORDS_B)}",
        style=style,
        key=key.name,
        bpm=bpm,
        progression=progression.name,
        cycles_per_chord=cfg["cycles_per_chord"],
        mood=rng.choice(MOODS),
        sections=[],
        seed=rng.randrange(1_000_000),
    )
    piece.sections = _default_sections(piece, target_minutes)
    _add_variety(piece, rng, avoid | disliked, previous)
    return piece


# Styles where a lifted final chorus fits; chance of ninth chords per style.
_LIFT_STYLES = {"lofi", "downtempo", "deephouse", "trance"}
_NINTH_CHANCE = {"lofi": 0.6, "deephouse": 0.7, "downtempo": 0.5, "ambient": 0.5, "piano": 0.4, "trance": 0.2,
                 "techno": 0.0, "hardcore": 0.0, "darkwave": 0.0}


def _add_variety(piece: Piece, rng: random.Random, avoid: set[str] | None = None,
                 previous: Piece | None = None) -> None:
    """Give choruses and breakdowns their own progressions, pick a chord
    colour, and sometimes lift the final chorus a whole step."""
    mode = Key.parse(piece.key).mode
    options = [p.name for p in progressions_for(mode) if p.name != piece.progression]
    preferred = [p for p in options if p not in (avoid or ())] or options
    rng.shuffle(preferred)
    piece.section_progressions = {}
    if preferred:
        piece.section_progressions["chorus"] = preferred[0]
        piece.section_progressions["breakdown"] = preferred[1] if len(preferred) > 1 else preferred[0]
        piece.section_progressions["bridge"] = piece.section_progressions["breakdown"]
    piece.color = "ninths" if rng.random() < _NINTH_CHANCE.get(piece.style, 0.5) else "sevenths"
    choose_instruments(piece, rng, previous)
    if piece.style in _LIFT_STYLES and rng.random() < 0.5:
        apply_key_lift(piece)


def choose_instruments(piece: Piece, rng: random.Random, previous: Piece | None = None) -> None:
    """Pick this piece's sound from the style's palette, and how it opens.
    Avoids reusing the previous piece's opening and pad when there's a choice."""
    cfg = STYLES[piece.style]
    palette = cfg.get("palette", {})
    piece.instruments = {}
    for role in P.ENERGY_ORDER:
        options = palette.get(role) or [cfg["presets"][role]]
        if previous is not None and len(options) > 1:
            fresh = [o for o in options if o != previous.instruments.get(role)]
            options = fresh or options
        piece.instruments[role] = rng.choice(options)
    openers = cfg.get("openers") or [cfg.get("energy_order", P.ENERGY_ORDER)[:2]]
    if previous is not None and len(openers) > 1:
        openers = [o for o in openers if list(o) != previous.opening] or openers
    piece.opening = list(rng.choice(openers))


def apply_key_lift(piece: Piece, semitones: int = 2) -> bool:
    """Lift the last chorus (only when there are at least two)."""
    choruses = [i for i, s in enumerate(piece.sections) if s.name == "chorus"]
    if len(choruses) < 2:
        return False
    for s in piece.sections:
        s.key_shift = 0
    piece.sections[choruses[-1]].key_shift = semitones
    return True


# The live engine's arc (music/performer.py), in the order it plays.
LIVE_PHASES = ("build", "groove", "breakdown", "rebuild", "groove2", "outro")
# Build and rebuild end once their lines are in; their bars are only a limit,
# and never shorter than this (the performer needs a few edits for them).
MIN_BUILD_BARS = 16
# For a track resumed from a state saved before arcs were planned.
DEFAULT_LIVE_ARC = {"build": 32, "groove": 64, "breakdown": 32, "rebuild": 32, "groove2": 64, "outro": 32}


def arc_summary(arc: dict[str, int]) -> str:
    """E.g. "build ≤32 · groove 64 · breakdown 32 · rebuild ≤16 · peak 96 · outro 16 bars"."""
    names = {"groove2": "peak"}
    return " · ".join(f"{names.get(p, p)} {'≤' if p in ('build', 'rebuild') else ''}{arc[p]}"
                      for p in LIVE_PHASES if p in arc) + " bars"


def live_arc(piece: Piece) -> dict[str, int]:
    """The Director's sections as the live arc: bars per phase, decided
    before the piece starts. Intro -> build, what comes before the first
    breakdown -> groove, the breakdown -> breakdown, a build right after it
    -> rebuild, the rest -> peak (groove2), the closing outro -> outro.
    Without a breakdown in the middle, the style's own arc is used."""
    sections = piece.sections
    names = [s.name for s in sections]
    if "breakdown" not in names[1:-1]:
        sections = _default_sections(piece, max(4.0, piece.total_bars / piece.cps / 60) if sections else 12.0)
        names = [s.name for s in sections]
    bd = names.index("breakdown", 1)
    before, rest = sections[:bd], sections[bd:]
    n_break = next((i for i, s in enumerate(rest) if s.name != "breakdown"), len(rest))
    breakdown, after = rest[:n_break], rest[n_break:]
    n_intro = next((i for i, s in enumerate(before) if s.name != "intro"), len(before))
    n_outro = next((i for i, s in enumerate(reversed(after)) if s.name != "outro"), len(after))
    middle, outro = after[:len(after) - n_outro], after[len(after) - n_outro:]
    n_rebuild = next((i for i, s in enumerate(middle) if s.name != "build"), len(middle))

    def bars(part) -> int:
        return sum(s.bars for s in part)

    groove = bars(before[n_intro:]) or bars(before) // 2
    return {"build": max(MIN_BUILD_BARS, bars(before[:n_intro]) or bars(before) - groove),
            "groove": max(8, groove),
            "breakdown": max(8, bars(breakdown)),
            "rebuild": max(MIN_BUILD_BARS, bars(middle[:n_rebuild])),
            "groove2": max(8, bars(middle[n_rebuild:])),
            "outro": max(8, bars(outro))}


def _default_sections(piece: Piece, target_minutes: float) -> list[Section]:
    period = piece.period
    arc = STYLES.get(piece.style, {}).get("arc", DEFAULT_ARC)
    base_bars = sum(m for _, _, m in arc) * period
    seconds_per_bar = 1.0 / piece.cps
    scale = max(1, round(target_minutes * 60 / (base_bars * seconds_per_bar)))
    return [Section(name, bars=m * period * scale, energy=e) for name, e, m in arc]


def arrange(piece: Piece, section: Section, rng: random.Random, max_layers: int = 6) -> Arrangement:
    """Energy decides how many layers play; the piece's instruments decide which presets."""
    energy = section.energy
    order = piece.energy_order()
    count = 1 + int(round(energy * (len(order) - 1)))
    if section.name == "breakdown":
        count = min(count, 2)
    if section.name == "outro":
        count = min(count, 3)
    count = max(1, min(count, max_layers))
    roles = order[:count]
    # In a breakdown keep the pad and let the melody float over it (styles can
    # choose other breakdown layers, e.g. trance keeps its acid line, knob down).
    if section.name == "breakdown":
        style_roles = STYLES.get(piece.style, {}).get("breakdown")
        if style_roles:
            roles = list(style_roles)
        elif "melody" not in roles:
            roles = ["pad", "melody"]
    layers = {role: (piece.instrument(role) if role in roles else None) for role in P.ENERGY_ORDER}
    return Arrangement(layers=layers, melody_character=None, variation_seed=rng.randrange(1_000_000))


# ---------------------------------------------------------------------------
# Validation of LLM output
# ---------------------------------------------------------------------------

def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def validate_piece(data: dict, fallback: Piece, target_minutes: float = 12.0) -> tuple[Piece, list[str]]:
    """Merge an LLM-proposed piece plan onto a valid fallback plan.

    Returns the merged piece and a list of notes about what was rejected.
    """
    notes: list[str] = []
    piece = Piece.from_dict(fallback.to_dict())

    title = str(data.get("title", "")).strip()
    if 0 < len(title) <= 60 and all(ch.isprintable() for ch in title):
        piece.title = title
    elif title:
        notes.append("title rejected")

    style = str(data.get("style", "")).strip().lower()
    if style in STYLES:
        piece.style = style
        piece.cycles_per_chord = STYLES[style]["cycles_per_chord"]
    elif style:
        notes.append(f"unknown style {style!r}")

    key_text = str(data.get("key", "")).strip()
    if key_text:
        try:
            key = Key.parse(key_text)
            piece.key = key.name
        except ValueError:
            notes.append(f"invalid key {key_text!r}")
    key = Key.parse(piece.key)

    if "bpm" in data:
        lo, hi = STYLES[piece.style]["bpm"]
        piece.bpm = float(round(_clamp(data["bpm"], lo, hi, piece.bpm)))

    prog = str(data.get("progression", "")).strip()
    if prog in PROGRESSIONS_BY_NAME and key.mode in PROGRESSIONS_BY_NAME[prog].modes:
        piece.progression = prog
    else:
        if prog:
            notes.append(f"progression {prog!r} not valid for {key.mode}")
        if key.mode not in PROGRESSIONS_BY_NAME[piece.progression].modes:
            piece.progression = progressions_for(key.mode)[0].name

    mood = str(data.get("mood", "")).strip()
    if 0 < len(mood) <= 40:
        piece.mood = mood

    color = str(data.get("color", "")).strip().lower()
    if color in CHORD_COLORS:
        piece.color = color
    elif color:
        notes.append(f"unknown chord color {color!r}")

    # Per-section progressions must suit the (possibly new) mode.
    section_progs = {name: prog for name, prog in piece.section_progressions.items()
                     if key.mode in PROGRESSIONS_BY_NAME[prog].modes}
    raw_progs = data.get("section_progressions")
    if isinstance(raw_progs, dict):
        for name, prog in raw_progs.items():
            name = str(name).strip().lower()
            if name in SECTION_TYPES and prog in PROGRESSIONS_BY_NAME and key.mode in PROGRESSIONS_BY_NAME[prog].modes:
                section_progs[name] = prog
            else:
                notes.append(f"section progression {name}={prog!r} rejected")
    piece.section_progressions = section_progs
    if not piece.section_progressions and key.mode != Key.parse(fallback.key).mode:
        _add_variety(piece, random.Random(piece.seed))

    raw_sections = data.get("sections")
    if isinstance(raw_sections, list) and 3 <= len(raw_sections) <= 12:
        sections = []
        period = piece.period
        for raw in raw_sections:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip().lower()
            if name not in SECTION_TYPES:
                name = "verse"
            periods = int(_clamp(raw.get("bars", period * 2), period, period * 16, period * 2) // period)
            energy = _clamp(raw.get("energy", 0.5), 0.05, 1.0, 0.5)
            sections.append(Section(name=name, bars=max(1, periods) * period, energy=round(energy, 2)))
        if len(sections) >= 3:
            piece.sections = sections
        else:
            notes.append("sections rejected")
    else:
        # Style/tempo may have changed: rebuild the default arc to fit.
        piece.sections = _default_sections(piece, target_minutes)

    raw_instruments = data.get("instruments")
    if isinstance(raw_instruments, dict):
        for role, name in raw_instruments.items():
            if role in P.ENERGY_ORDER and isinstance(name, str) and name in P.PRESETS.get(role, {}):
                piece.instruments[role] = name
            else:
                notes.append(f"instrument {role}={name!r} rejected")
    opening = data.get("opening")
    if isinstance(opening, list):
        roles = [r for r in opening if r in P.ENERGY_ORDER][:3]
        if roles:
            piece.opening = roles
        else:
            notes.append("opening rejected")
    if piece.style != fallback.style:
        # Instruments from another style's palette would clash; re-pick.
        keep = {r: n for r, n in piece.instruments.items()
                if isinstance(raw_instruments, dict) and raw_instruments.get(r) == n}
        choose_instruments(piece, random.Random(piece.seed))
        piece.instruments.update(keep)

    fallback_lift = any(s.key_shift for s in fallback.sections)
    lift = data.get("key_lift", fallback_lift)
    if lift is True or (lift and str(lift).lower() == "true"):
        apply_key_lift(piece)
    else:
        for s in piece.sections:
            s.key_shift = 0
    return piece, notes


def validate_arrangement(data: dict, piece: Piece, fallback: Arrangement, max_layers: int = 6) -> tuple[Arrangement, list[str]]:
    notes: list[str] = []
    arrangement = Arrangement.from_dict(fallback.to_dict())
    raw_layers = data.get("layers")
    if isinstance(raw_layers, dict):
        layers: dict[str, str | None] = {}
        for role in P.ENERGY_ORDER:
            value = raw_layers.get(role, arrangement.layers.get(role))
            if value in (None, "", False, "none", "off"):
                layers[role] = None
            elif isinstance(value, str) and value in P.PRESETS.get(role, {}):
                layers[role] = value
            else:
                notes.append(f"unknown preset {value!r} for {role}")
                layers[role] = arrangement.layers.get(role) or None
        active = [r for r in P.ENERGY_ORDER if layers.get(r)]
        if not active:
            notes.append("arrangement had no layers; keeping pad")
            layers["pad"] = STYLES[piece.style]["presets"]["pad"]
        # Enforce the layer cap, dropping the most "decorative" roles first.
        while len([r for r in layers if layers[r]]) > max_layers:
            for role in reversed(P.ENERGY_ORDER):
                if layers.get(role):
                    layers[role] = None
                    notes.append(f"dropped {role} (layer cap {max_layers})")
                    break
        arrangement.layers = layers

    if isinstance(data.get("fills"), bool):
        arrangement.fills = data["fills"]
    movement = data.get("movement")
    if movement in ("still", "gentle", "lively"):
        arrangement.movement = movement
    elif movement:
        notes.append(f"unknown movement {movement!r}")

    character = data.get("melody_character")
    if character in ("sparse", "flowing", "lively"):
        arrangement.melody_character = character
    elif character:
        notes.append(f"unknown melody_character {character!r}")
    return arrangement, notes


def describe_options() -> dict:
    """Vocabulary the LLM Director is allowed to use (embedded in its prompt)."""
    return {
        "styles": {name: {"bpm": cfg["bpm"], "modes": cfg["modes"]} for name, cfg in STYLES.items()},
        "modes": list(MODES),
        "progressions": {p.name: {"modes": list(p.modes), "mood": p.mood} for p in PROGRESSIONS_BY_NAME.values()},
        "section_types": SECTION_TYPES,
        "chord_colors": list(CHORD_COLORS),
        "palettes": {name: cfg.get("palette", {}) for name, cfg in STYLES.items()},
        "presets": {role: {n: pr.description for n, pr in P.PRESETS[role].items()} for role in P.ENERGY_ORDER},
    }
