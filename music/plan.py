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

import config

from . import presets as P
from .blocks import LIVE_SOUNDS, RETIRED_SOUNDS, sound_fits_style
from .theory import (CHORD_COLORS, MODES, PROGRESSIONS_BY_NAME, Key, mode_chords, parse_progression,
                     progression_named, progressions_for)

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
        # (126-146 over its subgenres: progressive 126-132 ... psy 142-146, blocks.SUBGENRES)
        "bpm": (126, 146), "modes": ["minor", "phrygian", "dorian"], "cycles_per_chord": 2,
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
# Darkwave (coldwave / post-punk / minimal synth): minor keys, slow harmony,
# the bass and drum machine carry it (100-140 BPM over its subgenres:
# ethereal 100-116 ... post-punk 128-140, blocks.SUBGENRES). Shares trance's
# arranger presets too.
STYLES["darkwave"] = {**STYLES["trance"], "bpm": (100, 140), "cycles_per_chord": 2,
                      "openers": [["bass"], ["drums", "bass"]],
                      "energy_order": ["bass", "drums", "pad", "arp", "melody", "texture", "keys"]}

for _name, _bpm in (("techno", (138, 150)), ("hardcore", (160, 185))):
    STYLES[_name] = {**STYLES["trance"], "bpm": _bpm, "cycles_per_chord": 2,
                     "openers": [["drums"], ["drums", "bass"]],
                     "energy_order": ["drums", "bass", "arp", "pad", "texture", "melody", "keys"]}
# Happy hardcore is in major keys (its subgenre's modes keep gabber dark).
STYLES["hardcore"]["modes"] = ["minor", "phrygian", "dorian", "major", "mixolydian"]
# Ambient house is trance's bright, slower side: major and lydian, from 124
# BPM (blocks.SUBGENRES: the other trance subgenres keep the dark modes).
STYLES["trance"] = {**STYLES["trance"], "bpm": (124, 146), "modes": ["minor", "phrygian", "dorian", "major", "lydian"]}

# Sounds that aren't wanted: presets built on detuned saws (supersaw, gated
# saw, the saw pad, saw strings, the saw-fed choir), the organ and the
# Juno-style pulse pad (too buzzy, like the saws) are never
# picked, by default, by the Director or by the model. Styles fall back to
# the soft pad, air, piano, glass and electric piano.
UNWANTED_SYNTHS = {"ys_supersaw", "ys_gatesaw", "ys_pad", "ys_strings", "ys_choir", "supersaw",
                   "ys_organ", "superhammond", "ys_pulsepad"} | RETIRED_SOUNDS
UNWANTED_PRESETS = {name for role in P.PRESETS.values() for name, pr in role.items()
                    if pr.synth in UNWANTED_SYNTHS}
_FALLBACK = {"pad": ["soft_pad", "air_pad", "piano_pad"], "melody": ["glass"], "arp": ["glass_arp"],
             "keys": ["rhodes", "felt"]}
for _cfg in STYLES.values():
    # Where unwanted sounds were taken out, the alternatives keep the choice varied.
    _palette = {role: [n for n in names if n not in UNWANTED_PRESETS]
                + ([f for f in _FALLBACK[role] if f not in names] if set(names) & UNWANTED_PRESETS else [])
                for role, names in _cfg.get("palette", {}).items()}
    _presets = {role: name if name not in UNWANTED_PRESETS else (_palette.get(role) or _FALLBACK[role])[0]
                for role, name in _cfg["presets"].items()}
    _cfg["palette"], _cfg["presets"] = _palette, _presets

# Styles whose chords use Switch Angel's trance voicings (livecode.TRANCE_VOICINGS)
# instead of seventh chords.
VOICED_STYLES = {"trance", "techno", "hardcore", "darkwave"}

# Theme words that select a style (checked in this order).
THEME_STYLE_KEYWORDS = [
    ("hardcore", ("hardcore", "gabber", "gabba", "speedcore", "frenchcore", "terrorcore", "rotterdam")),
    ("darkwave", ("darkwave", "dark wave", "coldwave", "cold wave", "cold electro", "minimal wave",
                  "minimal synth", "post-punk", "post punk", "ebm")),
    ("techno", ("techno", "hard techno", "acid techno", "industrial", "rave", "berghain")),
    ("trance", ("trance", "switch angel", "switchangel", "uplifting", "psytrance", "supersaw", "chicane",
                "saltwater", "balearic trance")),
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

# Theme words that ask for a subgenre of the style (blocks.SUBGENRES): every
# track of a stream with that theme is one, by either planner.
THEME_SUBGENRE_KEYWORDS = {
    "trance": [("balearic", ("saltwater", "chicane", "balearic", "ibiza", "sunset", "cafe del mar", "café del mar")),
               ("psy", ("psytrance", "psy trance", "goa")),
               ("ambient house", ("ambient house", "orbital", "the orb", "future sound of london", "fsol",
                                  "brown album"))],
    "hardcore": [("happy", ("happy hardcore", "wonderful days")), ("gabber", ("gabber", "gabba", "rotterdam"))],
}


def subgenre_from_theme(style: str | None, theme: str | None) -> str:
    """The subgenre of `style` the theme asks for ("Chicane's Saltwater at
    sunset" -> trance's balearic), or ""."""
    text = f" {(theme or '').lower()} "
    for name, words in THEME_SUBGENRE_KEYWORDS.get(style or "", []):
        if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words):
            return name
    return ""


# A live track's target length: long enough to build and drop, short enough
# that the next track's key, chords, hook and sound come before it wears thin.
LIVE_PIECE_MINUTES = 5.0
# A track's usual length per style, for planning a set of a given length
# (Director.plan_set): extended trance and techno mixes run long, hardcore
# and post-punk short, lofi beats shorter still.
TYPICAL_MINUTES = {"trance": 6.5, "techno": 6.0, "deephouse": 6.0, "hardcore": 4.5, "darkwave": 4.5, "lofi": 3.5,
                   "downtempo": 4.5, "ambient": 6.0, "piano": 4.0}
TRACK_MINUTES = (2.5, 10.0)


def set_plan(remaining: float, elapsed: float, typical: float, assumed: list[float] | None = None) -> list[float]:
    """Track lengths (minutes) for the rest of a set: `remaining` minutes
    left after `elapsed`. A set breathes like a club night: a shorter track to
    open, longer ones at peak time (the middle of the set), a closer a little
    shorter; together they end on time. An earlier assumption (the
    Director's) is kept and fitted to the time actually left, a track added
    or dropped when it no longer fits."""
    lo, hi = TRACK_MINUTES
    if remaining <= lo:
        return [lo]
    total = elapsed + remaining
    if assumed:
        plan = [min(hi, max(lo, float(m))) for m in assumed]
        while len(plan) > 1 and sum(plan) > remaining * 1.33:
            plan.pop(-2 if len(plan) > 2 else -1)
        while sum(plan) < remaining * 0.75:
            plan.insert(-1, typical)
    else:
        count = max(1, round(remaining / typical))
        plan, at = [], elapsed
        for i in range(count):
            p = (at + typical / 2) / total            # where in the set this track falls
            shape = 0.8 if p < 0.15 else 1.15 if 0.35 <= p <= 0.8 else 0.9 if i == count - 1 else 1.0
            plan.append(typical * shape)
            at += typical
    scale = remaining / sum(plan)
    plan = [min(hi, max(lo, m * scale)) for m in plan]
    plan[-1] = min(hi, max(lo, remaining - sum(plan[:-1])))
    return [round(m, 1) for m in plan]

# The section types of real songs (eval/song_forms.py folds their labels into
# these): lift a pre-chorus, tag a post-chorus, interlude an instrumental or
# solo, break a few bars with the kick and bass out.
SECTION_TYPES = ["intro", "verse", "lift", "build", "chorus", "tag", "bridge", "interlude", "break", "breakdown",
                 "outro"]

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
    progression: str    # Roman numerals: a library name or one the Director wrote (theory.progression_named)
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
    # The live engine's sound per part (blocks.LIVE_SOUNDS), e.g.
    # {"chords": "ys_housepiano", "lead": "ys_pulselead"}; parts not listed
    # keep their block's own sound.
    sounds: dict[str, str] = field(default_factory=dict)
    # The track's identity from the Director: a hook ({"role": "acid",
    # "pattern": "0 4 7 4 9"}, scale degrees the role's block plays when it
    # comes in) and one idea for the performer to build toward.
    hook: dict[str, str] = field(default_factory=dict)
    idea: str = ""
    # The rest of the Director's sketch: the starting pattern of other parts
    # (role -> {"kind", "pattern"}) and knobs that move on their own (role ->
    # {control: "(slow 32 $ range 0.3 0.85 sine)"}), so the track is layered
    # and moving from the start, like a live-coded set.
    parts: dict[str, dict] = field(default_factory=dict)
    motion: dict[str, dict] = field(default_factory=dict)
    voicing: str = ""   # a name in livecode.TRANCE_VOICINGS (club styles); "" = the style's default
    minutes: float = 0.0  # the length the Director planned for it (its set plan); 0 = the default
    subgenre: str = ""  # a name in blocks.SUBGENRES[style] (its tempo, kit, sounds, voicings)
    surprise: str = ""  # one rule-break from music/surprises.py, or "" (most tracks)
    # The kind of tune the hook is (music/hooks.py): "" a hook, "hymn" a
    # plainer, catchier one to sing along to, where the Director thinks it fits.
    melody: str = ""
    # Chords from outside the key (theory.parse_chromatic, e.g.
    # "I@4-bII@4-bIII@2"), played instead of the progressions when set: the
    # Director's chromatic colour. Every line then plays relative to the
    # chord sounding, the hook too.
    chromatic: str = ""
    # The Director's instructions to the Performer, per part of the live arc
    # (build, groove, breakdown, rebuild, groove2, outro): short and plain,
    # "Now start the bass", "Change the melody"; the Performer turns each
    # into code, one per move.
    cues: dict[str, list[dict]] = field(default_factory=dict)

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

def fit_mode(key: Key, modes: list[str] | None) -> Key:
    """The key itself if its mode is allowed, else the allowed key sharing the
    most notes with it (the same root first): A minor -> C major."""
    if not modes or key.mode in modes:
        return key
    options = [Key(r, m) for m in modes for r in range(12)]
    return max(options, key=lambda k: (len(k.pitch_classes & key.pitch_classes), k.root == key.root))


def _mood_keys(options: list, moods: set[str], rng: random.Random) -> list:
    """The keys among `options` in the modes the theme's moods lean to (most of
    the time; now and then any, so a dark stream still breathes)."""
    wanted = {m for mood in moods for m in MOOD_MODES.get(mood, ())}
    themed = [k for k in options if k.mode in wanted]
    return themed if themed and rng.random() < 0.8 else options


def _title(rng: random.Random, moods: set[str]) -> str:
    """Two words: from the theme's moods' title words where there are some."""
    themed = [MOOD_TITLES[m] for m in sorted(moods) if m in MOOD_TITLES]
    if themed and rng.random() < 0.85:
        first, second = rng.choice(themed)
        return f"{rng.choice(first)} {rng.choice(second)}"
    return f"{rng.choice(TITLE_WORDS_A)} {rng.choice(TITLE_WORDS_B)}"


def plan_piece(rng: random.Random, previous: Piece | None = None, style: str | None = None,
               avoid_progressions: set[str] | None = None, target_minutes: float = 12.0,
               disliked: set[str] | None = None, theme: str | None = None,
               recent_sounds: list[dict] | None = None, disliked_sounds: set[str] | None = None) -> Piece:
    """avoid_progressions: soft preference (e.g. recently used);
    disliked: excluded whenever any alternative exists (progressions; the
    host's disliked sounds likewise)."""
    disliked = set(disliked or ())
    rotation = [name for name, cfg in STYLES.items() if cfg.get("rotate", True)]
    style = style if style in STYLES else (previous.style if previous and rng.random() < 0.6 else rng.choice(rotation))
    cfg = STYLES[style]
    moods = theme_moods(theme)

    if previous is not None:
        prev_key = Key.parse(previous.key)
        options = [k for k in prev_key.neighbours() if k.mode in cfg["modes"]]
        if not options:
            # Style changed modes: pick the style-compatible key sharing the
            # most notes with the previous one (at most one note different).
            candidates = [Key(r, m) for r in range(12) for m in cfg["modes"] if Key(r, m) != prev_key]
            best = max(len(prev_key.pitch_classes & k.pitch_classes) for k in candidates)
            options = [k for k in candidates if len(prev_key.pitch_classes & k.pitch_classes) == best]
        key = rng.choice(_mood_keys(options, moods, rng))
    else:
        key = (rng.choice(_mood_keys([Key(r, m) for r in range(12) for m in cfg["modes"]], moods, rng)) if moods
               else Key(rng.randrange(12), rng.choice(cfg["modes"])))

    from .blocks import SUBGENRES

    # A subgenre (not the previous track's) brings its own tempo range.
    rotation = [v for v, cfg_ in SUBGENRES.get(style, {}).items() if cfg_.get("rotate", True)]
    variants = [v for v in rotation if not previous or v != previous.subgenre] or rotation
    variant = rng.choice(variants) if variants else ""
    if subgenre_from_theme(style, theme) in SUBGENRES.get(style, {}):
        variant = subgenre_from_theme(style, theme)        # the theme asks for it, every track
    # A subgenre with its own modes (happy hardcore: major) moves the key to
    # the nearest one of them.
    key = fit_mode(key, SUBGENRES[style][variant].get("modes") if variant else None)
    lo, hi = SUBGENRES[style][variant]["bpm"] if variant else cfg["bpm"]
    bpm = float(rng.randint(lo, hi))
    if previous is not None and not variant:
        # Keep tempo changes between pieces gentle.
        bpm = float(max(lo, min(hi, previous.bpm + rng.randint(-8, 8))))

    avoid = set(avoid_progressions or ())
    if previous is not None:
        avoid.add(previous.progression)
    pool = progressions_for(key.mode)
    candidates = ([p for p in pool if p.name not in avoid and p.name not in disliked]
                  or [p for p in pool if p.name not in disliked] or pool)
    # As common as they are in dance music (NRG-CP counts, theory.PROGRESSIONS).
    progression = rng.choices(candidates, weights=[p.weight for p in candidates])[0]

    piece = Piece(
        title=_title(rng, moods),
        style=style,
        key=key.name,
        bpm=bpm,
        progression=progression.name,
        cycles_per_chord=cfg["cycles_per_chord"],
        mood=rng.choice(sorted(moods)) if moods else rng.choice(MOODS),
        sections=[],
        seed=rng.randrange(1_000_000),
        subgenre=variant,
    )
    piece.sections = _default_sections(piece, target_minutes,
                                       avoid=[s.name for s in previous.sections] if previous else None)
    piece.melody = choose_melody(theme, random.Random(piece.seed))
    piece.chromatic = choose_chromatic(theme, random.Random(piece.seed + 1), previous)
    _add_variety(piece, rng, avoid | disliked, previous, theme=theme, recent_sounds=recent_sounds,
                 disliked_sounds=disliked_sounds)
    return piece


# Styles where a lifted final chorus fits; chance of ninth chords per style.
_LIFT_STYLES = {"lofi", "downtempo", "deephouse", "trance"}
_NINTH_CHANCE = {"lofi": 0.6, "deephouse": 0.7, "downtempo": 0.5, "ambient": 0.5, "piano": 0.4, "trance": 0.2,
                 "techno": 0.0, "hardcore": 0.0, "darkwave": 0.0}


def _add_variety(piece: Piece, rng: random.Random, avoid: set[str] | None = None,
                 previous: Piece | None = None, theme: str | None = None,
                 recent_sounds: list[dict] | None = None, disliked_sounds: set[str] | None = None) -> None:
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
    choose_sounds(piece, rng, previous, theme=theme, recent=recent_sounds, disliked=disliked_sounds)
    # A hook written for the progression (music/hooks.py), not one the last track had.
    write_piece_hook(piece, rng, [previous.hook["pattern"]] if previous is not None and previous.hook else None)
    # A subgenre whose hook is a riff (ambient house): a cycle on the arp instead of a sung phrase.
    from .blocks import subgenre as _subgenre

    if _subgenre(piece.style, piece.subgenre).get("hook") == "riff":
        from .hooks import write_riff

        recent = [previous.hook.get("pattern", "")] if previous is not None and previous.hook else None
        piece.hook = {"role": "arp", "kind": "cycle", "pattern": write_riff(rng, recent)}
    if piece.style in _LIFT_STYLES and rng.random() < 0.5:
        apply_key_lift(piece)


# The built-in planner's 1990s trance and house sounds per style (the
# Director picks from all of blocks.LIVE_SOUNDS). The chill styles keep their
# blocks' own sounds unless the Director chooses.
_CLUB_SOUNDS = {"chords": ["ys_housepiano", "ys_epiano"], "keys": ["ys_housepiano", "ys_epiano"],
                "lead": ["ys_pulselead", "ys_glass", "ys_fmlead"], "arp": ["ys_pluck", "ys_glass", "ys_fmlead"]}
STYLE_SOUNDS = {
    "trance": _CLUB_SOUNDS, "techno": _CLUB_SOUNDS, "hardcore": _CLUB_SOUNDS,
    "darkwave": {"chords": ["ys_coldstrings", "ys_glass"], "keys": ["ys_glass", "ys_epiano"],
                 "lead": ["ys_wavelead", "ys_pulselead", "ys_fmlead"],
                 "arp": ["ys_wavesynth", "ys_pluck"]},
    "deephouse": {"chords": ["ys_epiano", "ys_housepiano", "ys_m1organ"],
                  "keys": ["ys_housepiano", "ys_epiano", "ys_marimba", "ys_vibes", "ys_m1organ"],
                  "lead": ["ys_glass", "ys_pulselead", "ys_marimba", "ys_vox", "ys_trumpet", "ys_melodica"],
                  "arp": ["ys_pluck", "ys_glass", "ys_marimba", "ys_vibes"]},
    # The chill styles (Thievery Corporation, Nightmares on Wax, Yonderboi,
    # Massive Attack): their blocks' own sound first, the 90s lounge, dub and
    # trip-hop voices for the theme to choose from.
    "lofi": {"lead": ["ys_keys", "ys_glass", "ys_vibes", "ys_melodica", "ys_trumpet", "ys_flute", "ys_nylon"],
             "keys": ["ys_epiano", "ys_keys", "ys_vibes"],
             "arp": ["ys_glass", "ys_vibes", "ys_nylon", "ys_harpsichord", "ys_kalimba"]},
    "downtempo": {"lead": ["ys_epiano", "ys_melodica", "ys_trumpet", "ys_vibes", "ys_flute", "ys_harpsichord",
                           "ys_sitar"],
                  "keys": ["ys_keys", "ys_vibes", "ys_epiano"],
                  "arp": ["ys_pluck", "ys_nylon", "ys_vibes", "ys_harpsichord", "ys_harp"]},
    "ambient": {"lead": ["ys_glass", "ys_flute", "ys_vibes", "ys_vox"], "keys": ["ys_keys", "ys_vibes"],
                "arp": ["ys_glass", "ys_vibes", "ys_nylon", "ys_harp", "ys_kalimba"]},
}


# The real instruments (blocks.REAL_SOUNDS) each style's built-in planner may
# pick, where they're installed (the frame below drops the others).
_REAL_FOR_STYLE = {
    "trance": {"chords": ["ys_grandpiano", "ys_violins"], "keys": ["ys_grandpiano", "ys_glockenspiel"],
               "lead": ["ys_grandpiano", "ys_violins", "ys_concertflute", "ys_oboe", "ys_violin", "ys_femalevox"],
               "arp": ["ys_pizz", "ys_glockenspiel", "ys_dantranh", "ys_voxchop"]},
    "hardcore": {"chords": ["ys_grandpiano"], "lead": ["ys_grandpiano", "ys_violins", "ys_femalevox"],
                 "arp": ["ys_voxchop"]},
    "darkwave": {"chords": ["ys_cellos", "ys_violins"], "lead": ["ys_cellos", "ys_violin", "ys_horn"],
                 "keys": ["ys_chimes", "ys_glockenspiel"], "arp": ["ys_glockenspiel"]},
    "deephouse": {"chords": ["ys_grandpiano"], "keys": ["ys_grandpiano", "ys_mbira", "ys_pizz"],
                  "lead": ["ys_grandpiano", "ys_clarinet", "ys_violins", "ys_femalevox"],
                  "arp": ["ys_mbira", "ys_pizz", "ys_voxchop"]},
    "lofi": {"lead": ["ys_clarinet", "ys_harmonica", "ys_grandpiano", "ys_violin"],
             "keys": ["ys_grandpiano", "ys_glockenspiel"], "arp": ["ys_pizz", "ys_mbira"]},
    "downtempo": {"lead": ["ys_oboe", "ys_clarinet", "ys_cellos", "ys_dantranh", "ys_ocarina", "ys_harmonica",
                           "ys_femalevox"],
                  "keys": ["ys_grandpiano", "ys_mbira"], "arp": ["ys_dantranh", "ys_mbira", "ys_pizz"]},
    "ambient": {"lead": ["ys_concertflute", "ys_ocarina", "ys_violin", "ys_horn", "ys_grandpiano", "ys_femalevox"],
                "keys": ["ys_grandpiano", "ys_chimes", "ys_glockenspiel"], "arp": ["ys_glockenspiel", "ys_dantranh"]},
}
for _style, _roles in _REAL_FOR_STYLE.items():
    STYLE_SOUNDS[_style] = {r: list(o) for r, o in STYLE_SOUNDS.get(_style, {}).items()}
    for _role, _options in _roles.items():
        STYLE_SOUNDS[_style][_role] = [*STYLE_SOUNDS[_style].get(_role, []), *_options]

if config.HYPERSAW:
    # Hardcore's lead: the saw lead (done properly) and the hoover as often as the square.
    STYLE_SOUNDS["hardcore"] = {**_CLUB_SOUNDS, "lead": ["ys_sawlead", "ys_hoover", "ys_pulselead", "ys_glass"]}
    STYLE_SOUNDS["techno"] = {**_CLUB_SOUNDS, "lead": [*_CLUB_SOUNDS["lead"], "ys_hoover"],
                              "chords": [*_CLUB_SOUNDS["chords"], "ys_m1organ"]}
# The sounds taken off air (blocks.RETIRED_SOUNDS), and real instruments not
# installed here (blocks: harp, kalimba), aren't in any style's frame.
STYLE_SOUNDS = {style: {role: [o for o in options if o not in RETIRED_SOUNDS and o in LIVE_SOUNDS.get(role, {})]
                        for role, options in roles.items()}
                for style, roles in STYLE_SOUNDS.items()}


def sounds_summary(sounds: dict[str, str]) -> str:
    """E.g. "chords: M1-style house piano chords · lead: square-wave trance lead"."""
    return " · ".join(f"{role}: {LIVE_SOUNDS[role][synth].split(' (')[0]}"
                      for role, synth in sounds.items() if synth in LIVE_SOUNDS.get(role, {}))


# Theme words and the moods they ask for (blocks.SOUND_CHARACTER): the style
# and subgenre set the frame, the theme picks the sounds inside it.
THEME_MOODS = {
    "dark": ("night", "midnight", "dark", "darkness", "vampire", "vampyre", "gothic", "goth", "blood", "shadow",
             "shadows", "fog", "grave", "ghost", "haunted", "noir", "black", "crypt", "cathedral", "moon", "raven",
             "abyss", "underground", "bunker"),
    "cold": ("cold", "winter", "ice", "snow", "frost", "steel", "concrete", "berlin", "machine", "chrome", "glass"),
    "melancholic": ("rain", "lonely", "sad", "tears", "lost", "longing", "memory", "memories", "farewell",
                    "heartbreak", "autumn", "grey", "gray"),
    "dreamy": ("dream", "dreams", "dreaming", "sleep", "space", "stars", "cosmic", "ocean", "cloud", "clouds",
               "float", "floating", "study", "relax", "calm", "mist", "aurora", "nebula", "chill", "chillout"),
    "warm": ("coffee", "cozy", "cosy", "home", "sunset", "soul", "jazz", "vinyl", "candle", "fireplace",
             "summer", "evening", "love", "lounge", "bossa", "dub", "balearic", "cafe", "bar"),
    "bright": ("sun", "sunny", "sunrise", "morning", "beach", "ibiza", "island", "gold", "golden", "happy",
               "light", "spring", "sky"),
    "euphoric": ("euphoric", "euphoria", "festival", "anthem", "hands", "rave", "uplifting", "heaven", "peak",
                 "celebration", "party"),
    "hard": ("hard", "warehouse", "industrial", "rage", "storm", "fire", "war", "riot", "gabber", "hardcore",
             "speed", "rotterdam", "thunder"),
    "retro": ("80s", "90s", "retro", "arcade", "neon", "vhs", "synthwave", "cassette", "8bit", "chiptune"),
    "cinematic": ("film", "movie", "epic", "cinema", "saga", "legend", "cathedral", "odyssey", "journey"),
    "mystic": ("goa", "india", "indian", "temple", "mystic", "mystical", "desert", "spiritual", "shaman", "monk",
               "mantra", "bazaar", "kyoto", "zen", "sufi", "oracle", "incense", "lotus", "himalaya", "ancient",
               "celtic", "irish", "gaelic", "ireland", "clannad", "enya", "highlands", "druid"),
    "eerie": ("halloween", "spooky", "witch", "witches", "witching", "pumpkin", "zombie", "skeleton", "skull",
              "horror", "haunted", "ghost", "creepy", "monster", "coffin", "seance", "cemetery", "graveyard",
              "phantom", "specter", "spectre", "banshee", "werewolf", "samhain", "hex", "curse", "cursed",
              "wicked", "occult", "ritual", "trick", "treat"),
}

# Themes that ask for a hymn outright, and the moods a hymn often suits: the
# built-in planner's art direction (the Director chooses for itself).
HYMN_WORDS = {"hymn", "hymns", "choir", "church", "gospel", "prayer", "sacred", "chant", "chorale", "psalm",
              "cathedral", "monk", "singalong", "anthem", "carol", "carols", "christmas", "amen"}
HYMN_MOODS = {"euphoric", "bright", "cinematic", "mystic"}
UNHYMNLIKE_MOODS = {"dark", "eerie", "hard"}
HYMN_CHANCE = 0.4


def choose_melody(theme: str | None, rng: random.Random) -> str:
    """"hymn" when the theme asks for one, often when its moods suit one (and
    none goes against it: a Halloween party isn't sung in rounds), else ""."""
    words = set(re.findall(r"[a-z0-9]+", (theme or "").lower()))
    if words & HYMN_WORDS:
        return "hymn"
    moods = theme_moods(theme)
    return "hymn" if moods & HYMN_MOODS and not moods & UNHYMNLIKE_MOODS and rng.random() < HYMN_CHANCE else ""


# Moods a chromatic colour (theory.CHROMATIC_COLOURS) suits, how often the
# built-in planner plays one there, and theme words that ask for one.
CHROMATIC_MOODS = {"dreamy", "melancholic", "cold", "cinematic"}
CHROMATIC_CHANCE = 0.12
CHROMATIC_WORDS = {"radiohead", "chromatic", "kid", "uncanny", "dissolve", "dissolving", "drift", "drifting"}


def choose_chromatic(theme: str | None, rng: random.Random, previous: "Piece | None" = None) -> str:
    """One of the built-in chromatic colours now and then where the theme's
    moods suit it (always when the theme asks), never two tracks running."""
    from .theory import CHROMATIC_COLOURS

    if previous is not None and previous.chromatic:
        return ""
    words = set(re.findall(r"[a-z0-9]+", (theme or "").lower()))
    if words & CHROMATIC_WORDS or (theme_moods(theme) & CHROMATIC_MOODS and rng.random() < CHROMATIC_CHANCE):
        return rng.choice(sorted(CHROMATIC_COLOURS))
    return ""


# The modes each mood leans to (when the style allows them), brightest first
# among them; the built-in planner prefers them for the key.
MOOD_MODES = {
    "dark": ("phrygian", "minor"), "eerie": ("phrygian", "minor"), "cold": ("minor", "phrygian"),
    "hard": ("phrygian", "minor"), "melancholic": ("minor", "dorian"), "dreamy": ("lydian", "major", "dorian"),
    "warm": ("major", "mixolydian", "dorian"), "bright": ("major", "lydian", "mixolydian"),
    "euphoric": ("minor", "major", "dorian"), "retro": ("minor", "dorian"), "cinematic": ("minor", "phrygian"),
    "mystic": ("phrygian", "dorian"),
}
# Title words per mood (first word, second word), for the built-in planner's titles.
MOOD_TITLES = {
    "eerie": (["Witching", "Phantom", "Hollow", "Haunted", "Crimson", "Spectral", "Cursed", "Moonlit", "Wicked",
               "Lantern"],
              ["Hour", "Pumpkins", "Graves", "Masks", "Crypts", "Spirits", "Bones", "Moon", "Ritual", "Carnival"]),
    "dark": (["Midnight", "Obsidian", "Shadow", "Black", "Crimson", "Nocturnal", "Velvet", "Ashen"],
             ["Cathedral", "Veil", "Fog", "Thorns", "Ravens", "Streets", "Rites", "Eclipse"]),
    "bright": (["Golden", "Sunlit", "Morning", "Bright", "Amber", "Coral"],
               ["Horizon", "Shores", "Skies", "Islands", "Days", "Waves"]),
    "euphoric": (["Endless", "Rising", "Infinite", "Electric", "Burning", "Higher"],
                 ["Hearts", "Lights", "Hands", "Skies", "Anthem", "Signal"]),
    "dreamy": (["Drifting", "Distant", "Silver", "Slow", "Floating", "Quiet"],
               ["Stars", "Clouds", "Dreams", "Tides", "Echoes", "Nebula"]),
    "cold": (["Frozen", "Steel", "Glass", "Chrome", "Winter", "Pale"],
             ["Machines", "Rooms", "Signals", "Grids", "Static", "Lines"]),
}


def theme_moods(theme: str | None) -> set[str]:
    """The moods a free-text theme asks for, e.g. "Vampyre Nights in London" -> {"dark"}."""
    words = set(re.findall(r"[a-z0-9]+", (theme or "").lower()))
    words |= {w[:-1] for w in words if w.endswith("s")}          # nights -> night
    return {mood for mood, cues in THEME_MOODS.items() if words & set(cues)}


def choose_sounds(piece: Piece, rng: random.Random, previous: Piece | None = None, theme: str | None = None,
                  recent: list[dict] | None = None, disliked: set[str] | None = None) -> None:
    """Pick the live engine's sound per part for this piece. The style and
    subgenre give the frame (the sounds on offer, the subgenre's first); the
    theme picks among them by the sounds' character, and reaches for another
    of the part's sounds only when the frame has none in its mood. No part
    repeats the previous track's sound, and the whole set is never one of
    the last tracks' sets, where there's a choice. A sound the host
    disliked (taste.disliked_sounds) plays only when the part has no other."""
    from .blocks import SOUND_CHARACTER, subgenre, subgenre_sounds

    piece.sounds = {}
    preferred = subgenre_sounds(piece.style, piece.subgenre)
    voicings = subgenre(piece.style, piece.subgenre).get("voicings")
    if voicings:
        piece.voicing = rng.choice(voicings)
    moods = theme_moods(theme)
    style_sounds = STYLE_SOUNDS.get(piece.style, {})
    avoid = set(disliked or ())

    def fits(synth: str) -> bool:
        return bool(moods & set(SOUND_CHARACTER.get(synth, ())))

    choices: dict[str, list[str]] = {}
    # A theme that names the subgenre ("Chicane's Saltwater") wants its
    # signature: the subgenre's first sound for a part (the choir, the nylon
    # guitar) on most tracks, even two in a row.
    signature = bool(preferred) and subgenre_from_theme(piece.style, theme) == piece.subgenre
    for role in dict.fromkeys([*style_sounds, *preferred]):
        if signature and preferred.get(role) and preferred[role][0] not in avoid and rng.random() < 0.65:
            choices[role] = preferred[role][:1]
            piece.sounds[role] = preferred[role][0]
            continue
        frame = [o for o in dict.fromkeys([*(preferred.get(role) or []), *style_sounds.get(role, [])])
                 if o in LIVE_SOUNDS.get(role, {})] or preferred.get(role) or style_sounds.get(role, [])
        frame = [o for o in frame if o not in avoid] or frame
        if previous is not None and previous.sounds.get(role) and len(frame) > 1:
            frame = [o for o in frame if o != previous.sounds[role]] or frame
        themed = [o for o in frame if fits(o)]
        if moods and not themed and role in ("lead", "arp"):
            # Nothing in the theme's mood here: any lead or arp sound that is
            # (a melodic line works with any voice; chord stabs don't).
            themed = [o for o in LIVE_SOUNDS.get(role, {}) if fits(o) and sound_fits_style(piece.style, o)
                      and o not in avoid
                      and (previous is None or o != previous.sounds.get(role))]
        if themed and rng.random() < 0.85:
            choices[role] = themed
        else:
            choices[role] = [o for o in frame if o in (preferred.get(role) or frame)] or frame
        piece.sounds[role] = rng.choice(choices[role])
    # Not a set a recent track played: change one part, the theme's choices
    # first, then any of the part's sounds in the frame.
    recent_sets = [r for r in (recent or []) if r]
    if any(piece.sounds == r for r in recent_sets):
        roles = list(choices)
        rng.shuffle(roles)
        options = [(role, alt) for pool in ("theme", "frame") for role in roles
                   for alt in (choices[role] if pool == "theme" else
                               [o for o in LIVE_SOUNDS.get(role, {}) if o in style_sounds.get(role, [])
                                or o in (preferred.get(role) or [])])
                   if alt != piece.sounds[role] and alt not in avoid]
        for role, alt in options:
            if not any({**piece.sounds, role: alt} == r for r in recent_sets):
                piece.sounds[role] = alt
                break


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
LIVE_PHASES = ("build", "groove", "lift", "breakdown", "rebuild", "groove2", "tag", "bridge", "interlude", "break",
               "outro")
# Build and rebuild end once their lines are in; their bars are only a limit,
# and never shorter than this (the performer needs a few edits for them).
MIN_BUILD_BARS = 16
# For a track resumed from a state saved before arcs were planned.
DEFAULT_LIVE_PLAN = [["build", 32], ["groove", 64], ["breakdown", 32], ["rebuild", 32], ["groove2", 64],
                     ["outro", 32]]


def fit_live_plan(plan: list[list], cps: float, target_minutes: float) -> list[list]:
    """Trim a live arc that runs well over the target length (slow styles,
    where even the shortest sections take long), longest steps first, never
    below the steps' minimums (MIN_BUILD_BARS for build and rebuild, 8)."""
    plan = [list(step) for step in plan]
    target = target_minutes * 60 * cps                     # in bars
    floor = lambda p: MIN_BUILD_BARS if p in ("build", "rebuild") else 8
    while sum(b for _, b in plan) > 1.15 * target:
        trimmable = [s for s in plan if s[1] - 8 >= floor(s[0])]
        if not trimmable:
            break
        max(trimmable, key=lambda s: s[1])[1] -= 8
    return plan


def arc_summary(plan) -> str:
    """E.g. "build ≤16 · groove 64 · peak 32 · breakdown 32 · rebuild ≤16 · peak 64 · outro 16 bars"."""
    names = {"groove2": "peak"}
    return " · ".join(f"{names.get(p, p)} {'≤' if p in ('build', 'rebuild') else ''}{bars}"
                      for p, bars in plan) + " bars"


# How the Director's sections become live phases (music/performer.py).
_SECTION_PHASE = {"intro": "build", "verse": "groove", "build": "groove", "chorus": "groove2",
                  "breakdown": "breakdown", "outro": "outro",
                  # Real songs' other sections (music/forms.py), each played its own way (performer.PHASE_HELP).
                  "lift": "lift", "tag": "tag", "bridge": "bridge", "interlude": "interlude", "break": "break"}


def live_plan(piece: Piece) -> list[list]:
    """The Director's sections, in its order, as the live arc: [[phase, bars],
    ...], decided before the piece starts. Intro -> build, verse / bridge ->
    groove, chorus -> peak (groove2), breakdown -> breakdown, a build right
    after a breakdown -> rebuild (elsewhere it's part of the groove), outro ->
    outro. Neighbours with the same phase merge. It always starts with a
    build and ends with an outro, and every breakdown is followed by a
    rebuild, so what it muted comes back before the next part."""
    sections = piece.sections if len(piece.sections) >= 3 else _default_sections(piece, 12.0)
    steps: list[list] = []
    for i, section in enumerate(sections):
        phase = _SECTION_PHASE.get(section.name, "groove")
        if i == 0:
            phase = "build"
        elif phase == "build":
            phase = "groove"
        if section.name in ("build", "lift") and steps and steps[-1][0] == "breakdown":
            phase = "rebuild"                # (a lift after a breakdown is its rebuild)
        if steps and steps[-1][0] == phase and phase != "build":
            steps[-1][1] += section.bars
        else:
            steps.append([phase, section.bars])
    out: list[list] = []
    for i, (phase, bars) in enumerate(steps):
        out.append([phase, bars])
        after = steps[i + 1][0] if i + 1 < len(steps) else None
        if phase == "breakdown" and after not in ("rebuild", "outro", None):
            out.append(["rebuild", MIN_BUILD_BARS])
    if out[-1][0] != "outro":
        # A song that ends on its chorus: a short outro, the engine's fade (a cold end).
        out.append(["outro", 8])
    for step in out:
        step[1] = max(MIN_BUILD_BARS if step[0] in ("build", "rebuild") else 8, int(step[1]))
    return out


# Shapes the built-in planner picks from besides the style's own arc (the
# Director is offered the same ones): (section, energy, length in periods).
ARC_SHAPES = [
    [("intro", 0.2, 2), ("chorus", 0.8, 4), ("breakdown", 0.3, 2), ("build", 0.6, 1), ("chorus", 0.9, 4),
     ("chorus", 0.85, 2), ("outro", 0.2, 2)],                                          # early drop
    [("intro", 0.2, 2), ("verse", 0.5, 3), ("breakdown", 0.3, 2), ("build", 0.6, 1), ("chorus", 0.85, 3),
     ("breakdown", 0.3, 2), ("build", 0.65, 1), ("chorus", 0.9, 3), ("outro", 0.2, 2)],  # double breakdown
    [("intro", 0.2, 2), ("verse", 0.45, 4), ("verse", 0.55, 3), ("bridge", 0.5, 2), ("chorus", 0.8, 4),
     ("outro", 0.2, 2)],                                                               # slow burn
    [("intro", 0.2, 2), ("verse", 0.5, 3), ("breakdown", 0.3, 2), ("build", 0.6, 1), ("chorus", 0.85, 3),
     ("chorus", 0.9, 3), ("chorus", 0.85, 2), ("outro", 0.2, 2)],                      # long peak
    [("intro", 0.2, 2), ("verse", 0.5, 3), ("chorus", 0.8, 3), ("bridge", 0.45, 2), ("verse", 0.55, 2),
     ("chorus", 0.85, 3), ("outro", 0.2, 2)],                                          # two moods
]


def _default_sections(piece: Piece, target_minutes: float, avoid: list[str] | None = None) -> list[Section]:
    """The built-in planner's shape: a real song's form for the style's
    genre (music/forms.py), else the style's own arc or one of ARC_SHAPES,
    never the shape in `avoid` (the previous piece's section names),
    stretched to about target_minutes."""
    period = piece.period
    from . import forms

    real = forms.suggest(piece.style, random.Random(piece.seed), target_minutes * 60 * piece.cps, period,
                         avoid=[" ".join(avoid)] if avoid else None)
    if real:
        return [Section(name, bars=bars, energy=energy) for name, bars, energy in real]
    shapes = [STYLES.get(piece.style, {}).get("arc", DEFAULT_ARC)] + ARC_SHAPES
    fresh = [a for a in shapes if [name for name, _, _ in a] != list(avoid or [])] or shapes
    arc = random.Random(piece.seed).choice(fresh)
    base_seconds = sum(m for _, _, m in arc) * period / piece.cps
    factor = target_minutes * 60 / base_seconds
    return [Section(name, bars=max(1, round(m * factor)) * period, energy=e) for name, e, m in arc]


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


_HOOK_TOKEN_RE = re.compile(r"^(~|-?\d{1,2})$")


# Controls that hold a rhythm (the 303 accent pattern), not a knob position.
_PATTERN_CONTROLS = {"accent"}
_RHYTHM_RE = re.compile(r"^[t~_\s\[\]<>*!\d.]+$")


def _part_problem(role: str, kind: str, pattern: str) -> str | None:
    """Why a sketch pattern of this kind can't play, or None. Tunes are
    scale degrees on chord tones where it matters (livecode.harmony_problem);
    a chord rhythm is t (hit) and ~ (rest)."""
    from .livecode import harmony_problem, range_problem

    if kind == "rhythm":
        if not _RHYTHM_RE.match(pattern) or "t" not in pattern:
            return "a chord rhythm is t (hit), ~ (rest) and _ (hold), e.g. \"~ t _ t ~ t t _\""
        return None
    if kind == "cycle":
        tokens = pattern.split()
        if not 3 <= len(tokens) <= 16 or not all(_HOOK_TOKEN_RE.match(t) for t in tokens):
            return "a cycle is 3-16 space-separated degrees or ~"
    if not any(t.lstrip("-").isdigit() for t in re.split(r"[\s\[\]<>*!]+", pattern)):
        return "no notes"
    return (harmony_problem({"cycle": "acid", "bassline": "bass"}.get(kind, "lead"), pattern)
            or range_problem(role, pattern))


# Names the Director may use for the parts of the live arc.
_CUE_PHASES = {"build": "build", "intro": "build", "groove": "groove", "verse": "groove", "breakdown": "breakdown",
               "rebuild": "rebuild", "build up": "rebuild", "buildup": "rebuild", "peak": "groove2",
               "groove2": "groove2", "drop": "groove2", "chorus": "groove2", "outro": "outro",
               "lift": "lift", "pre-chorus": "lift", "prechorus": "lift", "tag": "tag", "post-chorus": "tag",
               "postchorus": "tag", "bridge": "bridge", "interlude": "interlude", "instrumental": "interlude",
               "solo": "interlude", "break": "break"}
MAX_CUES_PER_PHASE = 5


# What a cue may tell the Performer to do, and the plain names of the parts.
CUE_ACTIONS = ("start", "stop", "bring back", "pattern", "knob", "sound", "wrap", "tempo")
CUE_PARTS = {"kick": "beat", "beat": "beat", "drums": "beat", "hats": "perc", "claps": "perc",
             "hats and claps": "perc", "perc": "perc", "bass": "bass", "chords": "chords", "stabs": "chords",
             "keys": "keys", "acid": "acid", "arp": "arp", "lead": "lead", "pad": "texture", "texture": "texture",
             "vocal": "vocal", "vocals": "vocal", "voice": "vocal", "vox": "vocal", "acapella": "vocal"}
PART_NAMES = {"beat": "kick", "perc": "hats and claps", "texture": "pad"}


def _sound_name(part: str, sound: str) -> str:
    from .blocks import LIVE_SOUNDS

    return LIVE_SOUNDS.get(part, {}).get(sound, sound).split(" (")[0].split(",")[0]


def cue_text(cue: dict) -> str:
    """A precise cue in plain, exact words: what, which part, with what."""
    part = PART_NAMES.get(cue.get("part", ""), cue.get("part", ""))
    do = cue.get("do")
    if do == "start":
        what = ", ".join(x for x in (cue.get("block", "").replace("_", " "),
                                     _sound_name(cue["part"], cue["sound"]) if cue.get("sound") else "") if x)
        return f"Start the {part}" + (f": {what}" if what else "") + (f" · {cue['pattern']}" if cue.get("pattern") else "")
    if do == "stop":
        return f"Drop the {part}"
    if do == "bring back":
        return f"Bring the {part} back"
    if do == "pattern":
        return f"New {part} pattern" + (f": {cue['pattern']}" if cue.get("pattern") else "")
    if do == "wrap":
        from .livecode import WRAPPER_NOTES

        return f"The {part}: {cue.get('wrap')}" + (f" ({WRAPPER_NOTES[cue['wrap']].split(':')[0]})"
                                                    if cue.get("wrap") in WRAPPER_NOTES else "")
    if do == "knob":
        to = cue.get("to")
        return (f"The {part}'s {cue.get('knob')} from {to[0]:g} to {to[1]:g} over {to[2]} bars" if isinstance(to, list)
                else f"The {part}'s {cue.get('knob')} to {to:g}")
    if do == "sound":
        return f"The {part} to the {_sound_name(cue['part'], cue['sound'])}"
    if do == "tempo":
        return f"Tempo to {cue.get('to')} BPM"
    return ""


def validate_cue(raw, style: str) -> tuple[dict | None, str]:
    """One instruction: a plain sentence ({"say"}), or a precise one: {"do",
    "part", and what it needs: "block", "sound", "pattern", "knob" + "to",
    "wrap", "say"}, every name checked against the style's own blocks,
    sounds, knobs and wrappers. A detail that doesn't fit is dropped (noted)
    and the cue keeps its words for the Performer to interpret."""
    from .blocks import BLOCKS, LIVE_SOUNDS, sketch_kinds
    from .livecode import (ALL_WRAPPERS, FX_RANGES, PF_RANGES, RETIRED_WRAPPERS, ROLE_FX, EditError, _check_number,
                           fx_range)

    if isinstance(raw, str):
        say = " ".join(raw.split())[:90]
        return ({"say": say}, "") if say else (None, "empty cue")
    if not isinstance(raw, dict):
        return None, "a cue is a sentence or {do, part, ...}"
    say = " ".join(str(raw.get("say") or "").split())[:90]
    do = str(raw.get("do", "")).strip().lower()
    # A cue that names a surprise ({"do": "wrap", "wrap": "key_lift"}): the
    # surprise plays itself at its moment, so it's marked here and resolved
    # once the track's surprise is chosen (resolve_surprise_cues).
    from .surprises import SURPRISES

    named = next((str(v).strip().lower() for v in (raw.get("surprise"), raw.get("wrap"), raw.get("name"), do)
                  if str(v or "").strip().lower() in SURPRISES), None)
    if named or do == "surprise":
        return {"surprise": named or "", "say": say}, ""
    part = CUE_PARTS.get(str(raw.get("part", "")).strip().lower(), str(raw.get("part", "")).strip().lower())
    blocks = [b for b in BLOCKS.get(style, []) if b.role == part]
    if do not in CUE_ACTIONS:
        return ({"say": say}, f"unknown cue action {do!r}") if say else (None, f"unknown cue action {do!r}")
    if do != "tempo" and not blocks:
        why = f"{style} has no {part!r}"
        return ({"say": say}, why) if say else (None, why)
    cue: dict = {"do": do, "part": part}
    problems = []
    if raw.get("block") and do == "start":
        if any(b.name == raw["block"] for b in blocks):
            cue["block"] = raw["block"]
        else:
            problems.append(f"no {part} block {raw['block']!r}")
    if raw.get("sound") and do in ("start", "sound"):
        if raw["sound"] in LIVE_SOUNDS.get(part, {}) and sound_fits_style(style, raw["sound"]):
            cue["sound"] = raw["sound"]
        else:
            problems.append(f"{raw['sound']!r} isn't a {part} sound")
    if do == "start" and cue.get("sound"):
        # The sound only reaches a block that plays one of the part's sounds
        # (the acid arp stays acid): pick one that can, if the named one can't.
        from . import mix

        def takes_sound(block) -> bool:
            own = {mix.synth_of(block.body), *(block.patterns.get("synth") or [])}
            return bool(own & set(LIVE_SOUNDS.get(part, {})))

        named = next((b for b in blocks if b.name == cue.get("block")), None)
        if named is None or not takes_sound(named):
            able = next((b for b in blocks if takes_sound(b)), None)
            if able is not None:
                if named is not None:
                    problems.append(f"{named.name} can't play {cue['sound']}; using {able.name}")
                cue["block"] = able.name
    if raw.get("pattern") and do in ("start", "pattern"):
        kind = sketch_kinds(style).get(part)
        pattern = str(raw["pattern"]).strip()
        problem = _part_problem(part, kind, pattern) if kind else None
        if problem:
            problems.append(f"{part} pattern: {problem}")
        else:
            cue["pattern"] = pattern
            if raw.get("slot"):
                cue["slot"] = str(raw["slot"])
    if do == "knob":
        knob, to = str(raw.get("knob", "")).strip().lower(), raw.get("to")
        limits = (fx_range(part, knob) if knob in FX_RANGES else None) if knob in ROLE_FX.get(part, ()) \
            else PF_RANGES.get(knob)
        try:
            if not limits or knob == "gain":
                raise EditError(f"{knob!r} isn't a {part} knob")
            if isinstance(to, list):
                _check_number(f"(slow {int(to[2])} $ range {float(to[0]):g} {float(to[1]):g} sine)", *limits)
                cue["to"] = [float(to[0]), float(to[1]), int(to[2])]
            else:
                _check_number(f"{float(to):g}", *limits)
                cue["to"] = float(to)
            cue["knob"] = knob
        except (EditError, TypeError, ValueError, IndexError) as e:
            problems.append(f"knob: {e}")
    if do == "wrap":
        if raw.get("wrap") in ALL_WRAPPERS and raw.get("wrap") not in RETIRED_WRAPPERS:
            cue["wrap"] = raw["wrap"]
        else:
            problems.append(f"unknown wrapper {raw.get('wrap')!r}")
    if do == "tempo":
        try:
            cue["to"] = float(raw.get("to"))
        except (TypeError, ValueError):
            problems.append("tempo needs a number")
    if do == "sound" and "sound" not in cue or do == "knob" and "knob" not in cue or do == "wrap" and "wrap" not in cue:
        cue = {"say": say} if say else None
    if cue is not None and cue.get("do"):
        # A precise cue says exactly what it does (the Director's own words,
        # often imagery, stay for plain cues only).
        cue["say"] = cue_text(cue) if cue.get("do") != "pattern" or cue.get("pattern") or not say else say
    elif cue is not None:
        cue["say"] = say
    return cue, "; ".join(problems)


def resolve_surprise_cues(piece: "Piece") -> list[str]:
    """Take out the cues that name a surprise (validate_cue marks them): the
    track's own surprise plays by itself at its moment, so its cue goes
    quietly; one for another surprise can't happen (a track has one), so it
    goes with a note. Returns the notes."""
    notes = []
    for phase, cues in list((piece.cues or {}).items()):
        kept = []
        for cue in cues:
            if "surprise" not in cue:
                kept.append(cue)
            elif cue["surprise"] and cue["surprise"] != piece.surprise:
                notes.append(f"cue {phase}: {cue['surprise']} isn't this track's surprise "
                             f"({piece.surprise or 'none'}); dropped")
        if kept:
            piece.cues[phase] = kept
        else:
            piece.cues.pop(phase, None)
    return notes


def validate_cues(raw, style: str = "trance") -> tuple[dict[str, list[dict]], list[str]]:
    """The Director's instructions: {part of the arc: [cue, ...]} (validate_cue)."""
    if not isinstance(raw, dict):
        return {}, (["cues: expected {section: [instruction, ...]}"] if raw else [])
    out: dict[str, list[dict]] = {}
    notes = []
    for name, items in raw.items():
        phase = _CUE_PHASES.get(str(name).strip().lower())
        if phase is None:
            notes.append(f"cues for unknown section {name!r}")
            continue
        for item in items if isinstance(items, list) else [items]:
            cue, why = validate_cue(item, STYLES.get(style) and style or "trance")
            if why:
                notes.append(f"cue {name}: {why}")
            if cue is not None and len(out.get(phase, [])) < MAX_CUES_PER_PHASE:
                out.setdefault(phase, []).append(cue)
    return out, notes


def validate_hook(raw, style: str, degrees: list[int] | None = None,
                  bars_per_chord: int = 1) -> tuple[dict[str, str], str]:
    """The Director's hook as {"role", "kind", "pattern"}, or ({}, why not).
    A "cycle" hook (acid, arps) is 3-16 chord-tone degrees or rests that
    cycle as 16ths; a "phrase" hook (a lead) is a melody over the bar with
    chord tones on the strong steps. Either way it can't leave the chords."""
    from .blocks import hook_kinds
    from .livecode import EditError, check_pattern

    if not isinstance(raw, dict):
        return {}, "expected {\"role\": ..., \"pattern\": ...}"
    role = str(raw.get("role", "")).strip().lower()
    kinds = hook_kinds(style)
    if role not in kinds:
        return {}, f"{style} can carry a hook on {', '.join(sorted(kinds))}, not {role!r}"
    try:
        pattern = check_pattern(raw.get("pattern", ""))
    except EditError as e:
        return {}, str(e)
    # A phrase (a lead's tune) is written against the key across the
    # progression (music/hooks.py), unless it says it follows each chord's
    # root ("relative": "chord"); a cycle always follows the chord.
    relative = "key" if kinds[role] == "phrase" and degrees and raw.get("relative", "key") == "key" else "chord"
    fitted = ""
    if relative == "key":
        from .hooks import key_harmony_problem, refit

        problem = key_harmony_problem(pattern, degrees, bars_per_chord)
        if problem:
            # A note or two off the chords: those moved to the nearest chord tone,
            # if the tune stays itself (its rhythm and most of its notes).
            candidate = refit(pattern, degrees, bars_per_chord)
            if (candidate != pattern and key_harmony_problem(candidate, degrees, bars_per_chord) is None
                    and hook_similarity(pattern, candidate) >= HOOK_SIMILAR):
                pattern, problem, fitted = candidate, None, problem
    else:
        problem = _part_problem(role, kinds[role], pattern)
    if problem:
        return {}, problem
    # Less is more: a phrase within its part's note budget (livecode.NOTE_BUDGET),
    # the notes between the beats held, so the tune keeps its shape.
    from .livecode import NOTE_BUDGET, within_budget

    if kinds[role] == "phrase" and NOTE_BUDGET.get(role):
        pattern = within_budget('"{hook}"', "hook", pattern, NOTE_BUDGET[role])
    hook = {"role": role, "kind": kinds[role], "pattern": pattern}
    if relative == "key":
        hook["relative"] = "key"
    if fitted:
        hook["fitted"] = fitted[:160]              # (what was moved onto the chords)
    return hook, ""


def progression_degrees(piece) -> list[int]:
    """The degrees of a piece's main progression in its key's mode. A
    chromatic piece's are all 0 (each chord its own tonic, eight bars of
    them): its tunes are written relative to the chord sounding."""
    if getattr(piece, "chromatic", ""):
        return [0] * max(1, 8 // max(1, piece.cycles_per_chord))
    found = progression_named(piece.progression, Key.parse(piece.key).mode)
    return list(found.degrees) if found else []


def write_piece_hook(piece, rng: random.Random, recent: list[str] | None = None) -> bool:
    """Give a piece a hook written for its progression (music/hooks.py) on its
    lead (or keys), when the style has a part that sings one. False if none."""
    from .blocks import hook_kinds
    from .hooks import write_hook

    kinds = hook_kinds(piece.style)
    role = next((r for r in ("lead", "keys") if kinds.get(r) == "phrase"), None)
    degrees = progression_degrees(piece)
    if role is None or not degrees:
        return False
    piece.hook = {"role": role, "kind": "phrase", "relative": "key",
                  "pattern": write_hook(rng, degrees, piece.cycles_per_chord, recent, melody=piece.melody)}
    return True


# What the Director is told about chromatic colours (its own to write).
CHROMATIC_COLOURS_FOR_DIRECTOR = {
    "numerals": "relative to the key's tonic as in a major scale (I II III IV V VI VII = 0 2 4 5 7 9 11 "
                "semitones up), b or # before one for a chord off the key (bII bIII bVI bVII), upper case major, "
                "lower case minor",
    "beats": "@n after a chord: how many beats it lasts (1-8, 4 = a bar); uneven lengths roll the chords "
             "across the 4/4 bars",
}

# The kinds of tune a hook can be (beside a plain hook, the default).
MELODIES = {"hymn": "plainer and catchier, to sing along to: about a note a beat, within an octave, almost all "
                    "steps, each two-bar line ending held, its first line sung again (AABA) or a step higher"}

# Tunes this alike (hook_similarity) are the same tune to a listener.
HOOK_SIMILAR = 0.6
_TUNE_DEGREE_RE = re.compile(r"(?<![*!/@%\d.:])-?\d+")


def tune_degrees(pattern: str) -> list[int]:
    """The notes of a tune in order (rests, holds and step counts aside)."""
    return [int(d) for d in _TUNE_DEGREE_RE.findall(pattern or "")]


def hook_similarity(a: str, b: str) -> float:
    """How alike two tunes are, 0 to 1: how much of their note sequence
    they share, in order. A tune and the same tune with one note changed
    or a rest moved score about 0.8; different tunes under 0.5."""
    import difflib

    da, db = tune_degrees(a), tune_degrees(b)
    if not da or not db:
        return 0.0
    return difflib.SequenceMatcher(None, da, db, autojunk=False).ratio()


def _tune_bars(pattern: str) -> list[list[str]]:
    """A tune's bars as their steps: each [..] of a <[..] [..]> is a bar,
    else the whole pattern is one."""
    groups = re.findall(r"\[([^\[\]]*)\]", pattern)
    if not groups or pattern.strip().startswith("<") and not pattern.strip().startswith("<["):
        groups = [pattern.strip("<> ")]
    return [g.split() for g in groups if g.split()]


def melody_brief(patterns: list[str]) -> str:
    """What the recent tunes have in common (their step grid, range and
    ending), said plainly so the next one can be different: an LLM shown a
    list of tunes and told "write something different" writes their sibling."""
    tunes = [p for p in patterns if tune_degrees(p)]
    if len(tunes) < 2:
        return ""
    traits = []
    grids = {len(bar) for p in tunes for bar in _tune_bars(p)}
    if len(grids) == 1:
        traits.append(f"all have {grids.pop()} steps a bar")
    lows, highs = [min(tune_degrees(p)) for p in tunes], [max(tune_degrees(p)) for p in tunes]
    if max(highs) - min(lows) <= 9:
        traits.append(f"stay between {min(lows)} and {max(highs)}")
    endings = [" ".join(map(str, tune_degrees(p)[-3:])) for p in tunes]
    common = max(set(endings), key=endings.count)
    if endings.count(common) >= 2:
        traits.append(f"end on \"{common}\"")
    finals = [tune_degrees(p)[-1] for p in tunes]
    if finals.count(0) >= len(finals) - 1:
        traits.append("come home to the root (0) at the end")
    if not traits:
        return ""
    return (f"The last {len(tunes)} tunes {', '.join(traits)}. This track's hook must differ from them in at "
            "least two of: its rhythm (16ths, or long notes held with _, or entries off the beat), its register "
            "(up on 7, 9, 11 or higher), its contour and its last note.")


def validate_parts(raw, style: str) -> tuple[dict[str, dict], list[str]]:
    """The sketch's starting patterns: role -> pattern text in, role ->
    {"kind", "pattern"} out; parts that don't fit are left out (noted)."""
    from .blocks import sketch_kinds
    from .livecode import EditError, check_pattern

    if not isinstance(raw, dict):
        return {}, (["parts: expected {role: pattern}"] if raw else [])
    kinds, out, notes = sketch_kinds(style), {}, []
    # A part named by its kind of pattern ("bassline", "rhythm") means the
    # part that plays that kind here.
    by_kind = {k: r for r, k in kinds.items() if list(kinds.values()).count(k) == 1}
    for role, pattern in raw.items():
        role = str(role).strip().lower()
        role = by_kind.get(role, role) if role not in kinds else role
        if role not in kinds:
            notes.append(f"part {role!r} can't be sketched in {style} ({', '.join(sorted(kinds))} can)")
            continue
        try:
            pattern = check_pattern(pattern.get("pattern", "") if isinstance(pattern, dict) else pattern)
        except EditError as e:
            notes.append(f"part {role}: {e}")
            continue
        problem = _part_problem(role, kinds[role], pattern)
        if problem:
            notes.append(f"part {role}: {problem}")
            continue
        out[role] = {"kind": kinds[role], "pattern": pattern}
    return out, notes


def validate_motion(raw, style: str) -> tuple[dict[str, dict], list[str]]:
    """Knobs that move on their own: {role: {control: [low, high, bars]}} in,
    {role: {control: "(slow bars $ range low high sine)"}} out. The control
    must be one the part takes (livecode.ROLE_FX, or its block's own pF
    controls such as the acid knob) and the range inside its limits."""
    from .blocks import BLOCKS
    from .livecode import FX_RANGES, PF_RANGES, ROLE_FX, EditError, _check_number, line_controls

    if not isinstance(raw, dict):
        return {}, (["motion: expected {role: {control: [low, high, bars]}}"] if raw else [])
    out, notes = {}, []
    for role, controls in raw.items():
        role = str(role).strip().lower()
        own_pfs = {n for b in BLOCKS.get(style, []) if b.role == role and not b.drums
                   for n in line_controls(b.make(random.Random(0)))} - _PATTERN_CONTROLS
        if not isinstance(controls, dict):
            notes.append(f"motion {role}: expected {{control: [low, high, bars]}}")
            continue
        for control, spec in controls.items():
            control = str(control).strip().lower()
            allowed = (control in FX_RANGES and control in ROLE_FX.get(role, ()) and control != "gain") \
                or (control in own_pfs and control in PF_RANGES)
            if not allowed:
                notes.append(f"motion {role}.{control}: not a knob the {role} has")
                continue
            try:
                low, high, bars = (list(spec) + [None, None, None])[:3]
                value = f"(slow {int(bars)} $ range {float(low):g} {float(high):g} sine)"
                value = _check_number(value, *(FX_RANGES.get(control) or PF_RANGES[control]))
            except (TypeError, ValueError, EditError) as e:
                notes.append(f"motion {role}.{control}: {e}")
                continue
            out.setdefault(role, {})[control] = value
    return out, notes


def _written_progression(prog: str, mode: str, notes: list[str], what: str):
    """A library or written progression; a written one with numerals in the
    wrong case gets the mode's own chords on those degrees (noted)."""
    if not prog:
        return None
    found = progression_named(prog, mode)
    if found is None:
        try:
            found = parse_progression(prog, mode, fix_case=True)
            notes.append(f"{what} {prog} -> {found.name} (the chords {mode} has on those degrees)")
        except ValueError:
            return None
    return found


def _why_not(prog: str, mode: str) -> str:
    try:
        parse_progression(prog, mode)
    except ValueError as e:
        return f" ({e})"
    return ""


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

    from .blocks import SUBGENRES

    if piece.style != fallback.style:
        piece.subgenre = next(iter(SUBGENRES.get(piece.style, {})), "")
    name = str(data.get("subgenre") or "").strip().lower()
    if name:
        if name in SUBGENRES.get(piece.style, {}):
            piece.subgenre = name
        else:
            notes.append(f"unknown subgenre {name!r}")
    if "bpm" in data:
        lo, hi = SUBGENRES[piece.style][piece.subgenre]["bpm"] if piece.subgenre else STYLES[piece.style]["bpm"]
        piece.bpm = float(round(_clamp(data["bpm"], lo, hi, piece.bpm)))

    # A progression from the library, or one the Director wrote itself; both
    # must use only the mode's own chords (theory.parse_progression).
    prog = str(data.get("progression", "")).strip()
    written = _written_progression(prog, key.mode, notes, "progression")
    if written is not None:
        piece.progression = written.name
    else:
        if prog:
            notes.append(f"progression {prog!r} not valid for {key.mode}{_why_not(prog, key.mode)}")
        if progression_named(piece.progression, key.mode) is None:
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
                     if progression_named(prog, key.mode) is not None}
    raw_progs = data.get("section_progressions")
    if isinstance(raw_progs, dict):
        for name, prog in raw_progs.items():
            name = str(name).strip().lower()
            written = _written_progression(str(prog).strip(), key.mode, notes, f"{name} progression")
            if name in SECTION_TYPES and written is not None:
                section_progs[name] = written.name
            else:
                notes.append(f"section progression {name}={prog!r} rejected{_why_not(str(prog), key.mode)}")
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
            # Not much longer than asked: a long track wears thin.
            seconds = sum(s.bars for s in sections) / piece.cps
            if seconds > 1.5 * target_minutes * 60:
                factor = target_minutes * 60 / seconds
                for s in sections:
                    s.bars = max(1, round(s.bars / period * factor)) * period
                notes.append(f"sections shortened from {seconds / 60:.0f} to about {target_minutes:.0f} minutes")
            piece.sections = sections
        else:
            notes.append("sections rejected")
    elif piece.style != fallback.style or piece.period != fallback.period or piece.bpm != fallback.bpm:
        # Style or tempo changed: rebuild the default arc to fit (the same
        # choice the fallback made, so it still differs from the last piece's).
        piece.sections = _default_sections(piece, target_minutes)

    raw_instruments = data.get("instruments")
    if isinstance(raw_instruments, dict):
        for role, name in raw_instruments.items():
            if (role in P.ENERGY_ORDER and isinstance(name, str) and name in P.PRESETS.get(role, {})
                    and name not in UNWANTED_PRESETS):
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
        choose_sounds(piece, random.Random(piece.seed))

    # The live engine's sounds, from blocks.LIVE_SOUNDS (1990s trance and house).
    raw_sounds = data.get("sounds")
    if isinstance(raw_sounds, dict):
        for role, synth in raw_sounds.items():
            if role in LIVE_SOUNDS and synth in LIVE_SOUNDS[role] and sound_fits_style(piece.style, synth):
                piece.sounds[role] = synth
            else:
                notes.append(f"sound {role}={synth!r} rejected")

    # The kind of tune the Director asked for: a hymn, or (left out) a hook.
    melody = str(data.get("melody") or "").strip().lower()
    piece.melody = melody if melody in MELODIES else ""
    if melody and melody not in MELODIES and melody not in ("none", "hook", "default"):
        notes.append(f"unknown melody {melody!r}")
    # Chords from outside the key, when the Director asks for them.
    chromatic = str(data.get("chromatic") or "").strip()
    piece.chromatic = ""
    if chromatic and chromatic.lower() not in ("none", "no", "false"):
        from .theory import parse_chromatic

        try:
            piece.chromatic = parse_chromatic(chromatic, key.mode).name
        except ValueError as e:
            notes.append(f"chromatic {chromatic!r} rejected ({e})")
    if "hook" in data:
        hook, why = validate_hook(data.get("hook"), piece.style, progression_degrees(piece), piece.cycles_per_chord)
        if why:
            notes.append(f"hook rejected ({why})")
            if (fallback.hook and fallback.progression == piece.progression and fallback.melody == piece.melody
                    and fallback.chromatic == piece.chromatic):
                piece.hook = dict(fallback.hook)          # (the hook written for these chords stays)
            else:
                piece.hook = {}
                write_piece_hook(piece, random.Random(hash(piece.title) & 0xFFFF))
        else:
            if hook.get("fitted"):
                notes.append(f"hook fitted to the chords ({hook.pop('fitted')})")
            piece.hook = hook
    elif (piece.melody, piece.chromatic) != (fallback.melody, fallback.chromatic) and piece.hook.get("relative") == "key":
        write_piece_hook(piece, random.Random(hash(piece.title) & 0xFFFF))   # (the fallback's is the other kind)
    if "parts" in data:
        piece.parts, part_notes = validate_parts(data.get("parts"), piece.style)
        notes.extend(part_notes)
    if "motion" in data:
        piece.motion, motion_notes = validate_motion(data.get("motion"), piece.style)
        notes.extend(motion_notes)
    if "cues" in data:
        piece.cues, cue_notes = validate_cues(data.get("cues"), piece.style)
        notes.extend(cue_notes)
    voicing = str(data.get("voicing") or "").strip().lower()
    if voicing:
        from .livecode import TRANCE_VOICINGS

        if voicing in TRANCE_VOICINGS and piece.style in VOICED_STYLES:
            piece.voicing = voicing
        else:
            notes.append(f"voicing {voicing!r} rejected")
    idea = " ".join(str(data.get("idea") or "").split())
    if idea:
        piece.idea = idea[:200]

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


def block_catalogue(style: str) -> dict[str, dict[str, str]]:
    """Part -> block -> what it is, for the Director's precise cues: its
    sound and an example of its pattern."""
    from . import mix
    from .blocks import BLOCKS, LIVE_SOUNDS

    out: dict[str, dict[str, str]] = {}
    for block in BLOCKS.get(style, []):
        synth = mix.synth_of(block.body) or (block.patterns.get("synth") or [""])[0]
        sound = LIVE_SOUNDS.get(block.role, {}).get(synth, synth.replace("ys_", "").replace("_", " "))
        slot = block.sketch_slot()
        example = (block.patterns.get(slot[0]) or [""])[0] if slot else ""
        if block.drums:
            example = " | ".join(f"{k}: {v[0]}" for k, v in block.patterns.items())
        part = PART_NAMES.get(block.role, block.role)
        out.setdefault(part, {})[block.name] = (sound or "drums") + (f"; e.g. {example}" if example and "{" not in example
                                                                       else "")
    return out


def director_vocabulary(style: str | None) -> dict:
    """What the Director may choose from for a piece: the style's tempo and
    modes, the chords each mode allows for written progressions (with a few
    examples), the live sounds, and which parts can carry a hook."""
    from .blocks import BLOCKS, SOUND_CHARACTER, STYLE_SETTINGS, SUBGENRES, hook_kinds, sketch_kinds, subgenre_sounds
    from .livecode import FX_RANGES, PF_RANGES, ROLE_FX, TRANCE_VOICINGS, line_controls

    styles = [style] if style in STYLES else list(STYLES)
    knobs = {}
    for s in styles:
        for block in BLOCKS.get(s, []):
            if block.drums:
                continue
            own = {n: list(PF_RANGES[n]) for n in line_controls(block.make(random.Random(0)))
                   if n in PF_RANGES and n not in _PATTERN_CONTROLS}
            fx = {n: list(FX_RANGES[n]) for n in ROLE_FX.get(block.role, ()) if n not in ("gain", "legato")}
            knobs.setdefault(block.role, {}).update({**fx, **own})
    modes = sorted({m for s in styles for m in STYLES[s]["modes"]}, key=list(MODES).index)
    return {
        "styles": {s: {"bpm": list(STYLES[s]["bpm"]), "modes": STYLES[s]["modes"]} for s in styles},
        "chords_per_mode": {m: mode_chords(m) for m in modes},
        "example_progressions": {m: [p.name for p in progressions_for(m)][:6] for m in modes},
        "section_types": SECTION_TYPES,
        "sounds": LIVE_SOUNDS,
        # What each sound feels like, so the theme can choose among them.
        "sound_character": {synth: ", ".join(SOUND_CHARACTER[synth]) for role in LIVE_SOUNDS.values()
                            for synth in role if synth in SOUND_CHARACTER},
        "hook_parts": {s: hook_kinds(s) for s in styles},
        "sketch_parts": {s: sketch_kinds(s) for s in styles},
        "subgenres": {s: {n: f"{v['bpm'][0]}-{v['bpm'][1]} BPM"
                             + (f", modes {'/'.join(v['modes'])}" if v.get("modes") else "")
                             + f"; {v['about']}; sounds: "
                             + ", ".join(f"{r} {'/'.join(o)}" for r, o in subgenre_sounds(s, n).items())
                          for n, v in SUBGENRES.get(s, {}).items()} for s in styles if SUBGENRES.get(s)},
        "blocks": {s: block_catalogue(s) for s in styles},
        "max_melodic": {s: STYLE_SETTINGS[s].get("max_melodic") for s in styles if STYLE_SETTINGS[s].get("max_melodic")},
        "voicings": ({name: f"scale steps above the chord root: {shape}" for name, shape in TRANCE_VOICINGS.items()}
                     if any(s in VOICED_STYLES for s in styles) else {}),
        "knobs": knobs,
        "melodies": MELODIES,
        "chromatic_colours": CHROMATIC_COLOURS_FOR_DIRECTOR,
    }


def describe_options() -> dict:
    """Vocabulary the LLM Director is allowed to use (embedded in its prompt)."""
    return {
        "styles": {name: {"bpm": cfg["bpm"], "modes": cfg["modes"]} for name, cfg in STYLES.items()},
        "modes": list(MODES),
        "progressions": {p.name: {"modes": list(p.modes), "mood": p.mood} for p in PROGRESSIONS_BY_NAME.values()},
        "section_types": SECTION_TYPES,
        "chord_colors": list(CHORD_COLORS),
        "palettes": {name: cfg.get("palette", {}) for name, cfg in STYLES.items()},
        "sounds": LIVE_SOUNDS,
        "presets": {role: {n: pr.description for n, pr in P.PRESETS[role].items() if n not in UNWANTED_PRESETS}
                    for role in P.ENERGY_ORDER},
    }
