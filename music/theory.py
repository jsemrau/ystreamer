"""
Music theory core: keys, modes, diatonic chords, a curated progression
library, and voice leading.

Pitches are Tidal note numbers: 0 = Tidal's c5 (MIDI 60, middle C),
12 = one octave up. Everything the engine plays is computed here as absolute
note numbers, so no layer can drift out of key and the rendered Tidal code
doesn't depend on Tidal's scale-name table.
"""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass

NOTE_NAMES = ["c", "cs", "d", "ef", "e", "f", "fs", "g", "af", "a", "bf", "b"]
DISPLAY_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

_NAME_TO_PC = {
    "c": 0, "b#": 0, "bs": 0,
    "c#": 1, "cs": 1, "db": 1, "df": 1,
    "d": 2,
    "d#": 3, "ds": 3, "eb": 3, "ef": 3,
    "e": 4, "fb": 4, "ff": 4,
    "f": 5, "e#": 5, "es": 5,
    "f#": 6, "fs": 6, "gb": 6, "gf": 6,
    "g": 7,
    "g#": 8, "gs": 8, "ab": 8, "af": 8,
    "a": 9,
    "a#": 10, "as": 10, "bb": 10, "bf": 10,
    "b": 11, "cb": 11, "cf": 11,
}

MODES: dict[str, list[int]] = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
}

MODE_ALIASES = {
    "maj": "major", "ionian": "major",
    "min": "minor", "m": "minor", "aeolian": "minor", "natural minor": "minor",
    "dor": "dorian", "phr": "phrygian", "lyd": "lydian", "mix": "mixolydian", "mixo": "mixolydian",
}

# Pentatonic subset (as scale-degree indices into the mode) that is safe to
# play over every chord of the progressions below.
SAFE_PENTATONIC = {
    "major": [0, 1, 2, 4, 5],       # 1 2 3 5 6
    "minor": [0, 2, 3, 4, 6],       # 1 b3 4 5 b7
    "dorian": [0, 2, 3, 4, 6],      # 1 b3 4 5 b7
    "phrygian": [0, 2, 3, 4, 6],    # 1 b3 4 5 b7
    "lydian": [0, 1, 2, 4, 5],      # 1 2 3 5 6
    "mixolydian": [0, 1, 2, 4, 5],  # 1 2 3 5 6
}

ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii"]


def parse_pitch_class(name: str) -> int:
    key = name.strip().lower().replace("♯", "#").replace("♭", "b")
    if key not in _NAME_TO_PC:
        raise ValueError(f"Unknown note name: {name!r}")
    return _NAME_TO_PC[key]


def parse_mode(name: str) -> str:
    key = name.strip().lower()
    key = MODE_ALIASES.get(key, key)
    if key not in MODES:
        raise ValueError(f"Unknown mode: {name!r}")
    return key


@dataclass(frozen=True)
class Key:
    root: int  # pitch class 0-11
    mode: str

    @classmethod
    def parse(cls, text: str) -> "Key":
        """Parse "C minor", "f# dorian", "Bb major", "Am"."""
        text = text.strip()
        parts = text.split(None, 1)
        if len(parts) == 1:
            token = parts[0]
            if len(token) >= 2 and token[-1] == "m" and token[:-1].lower() in _NAME_TO_PC:
                return cls(parse_pitch_class(token[:-1]), "minor")
            return cls(parse_pitch_class(token), "major")
        return cls(parse_pitch_class(parts[0]), parse_mode(parts[1]))

    @property
    def intervals(self) -> list[int]:
        return MODES[self.mode]

    @property
    def pitch_classes(self) -> set[int]:
        return {(self.root + i) % 12 for i in self.intervals}

    @property
    def name(self) -> str:
        return f"{DISPLAY_NAMES[self.root]} {self.mode}"

    def degree_pitch(self, degree: int, octave: int = 0) -> int:
        """Note number of a scale degree (0-based, may exceed 6 or be negative)."""
        octs, idx = divmod(degree, 7)
        return self.root + self.intervals[idx] + 12 * (octs + octave)

    def contains(self, note: int) -> bool:
        return note % 12 in self.pitch_classes

    def safe_melody_pcs(self) -> set[int]:
        return {(self.root + self.intervals[d]) % 12 for d in SAFE_PENTATONIC[self.mode]}

    def relative(self) -> "Key":
        if self.mode == "major":
            return Key((self.root + 9) % 12, "minor")
        if self.mode == "minor":
            return Key((self.root + 3) % 12, "major")
        return self

    def neighbours(self) -> list["Key"]:
        """Closely related keys for smooth piece-to-piece modulation."""
        out = [self.relative(), Key((self.root + 7) % 12, self.mode), Key((self.root + 5) % 12, self.mode)]
        return [k for k in out if k != self]


@dataclass(frozen=True)
class Chord:
    degree: int          # scale degree of the root (0-6)
    notes: tuple[int, ...]  # root-position pitch classes relative to C (0-11), root first
    root: int            # pitch class
    quality: str         # "maj", "min", "dim", "aug"
    seventh: bool

    @property
    def pitch_classes(self) -> set[int]:
        return set(self.notes)

    @property
    def triad_pcs(self) -> set[int]:
        return set(self.notes[:3])


def diatonic_chord(key: Key, degree: int, seventh: bool = True, add9: bool = False) -> Chord:
    stack = [0, 2, 4] + ([6] if seventh else []) + ([8] if add9 else [])
    notes = tuple((key.degree_pitch(degree + s)) % 12 for s in stack)
    root = notes[0]
    third = (notes[1] - root) % 12
    fifth = (notes[2] - root) % 12
    if third == 4 and fifth == 7:
        quality = "maj"
    elif third == 3 and fifth == 7:
        quality = "min"
    elif third == 3 and fifth == 6:
        quality = "dim"
    else:
        quality = "aug"
    return Chord(degree=degree % 7, notes=notes, root=root, quality=quality, seventh=seventh)


@dataclass(frozen=True)
class Progression:
    name: str
    degrees: tuple[int, ...]
    modes: tuple[str, ...]
    mood: str
    energy: tuple[float, float]  # energy range it suits
    weight: int = 1              # how common it is (NRG-CP file count), for weighted picks


# Every progression has exactly 4 chords, so all layers share one period.
# None of them lands on a diminished triad in its listed modes (checked by tests).
_CURATED: list[Progression] = [
    # minor
    Progression("i-VI-III-VII", (0, 5, 2, 6), ("minor",), "moody, dreamy", (0.0, 1.0)),
    Progression("i-iv-VI-v", (0, 3, 5, 4), ("minor",), "melancholic, lofi", (0.0, 0.8)),
    Progression("i-VII-VI-VII", (0, 6, 5, 6), ("minor",), "cinematic", (0.3, 1.0)),
    Progression("VI-VII-i-i", (5, 6, 0, 0), ("minor",), "rising, hopeful", (0.4, 1.0)),
    Progression("i-III-VII-iv", (0, 2, 6, 3), ("minor",), "wistful", (0.0, 1.0)),
    Progression("i-VI-iv-VII", (0, 5, 3, 6), ("minor",), "warm, nostalgic", (0.0, 1.0)),
    Progression("i-iv-i-VI", (0, 3, 0, 5), ("minor",), "calm, hypnotic", (0.0, 0.5)),
    Progression("VI-III-VII-i", (5, 2, 6, 0), ("minor",), "anthemic, euphoric", (0.4, 1.0)),
    Progression("iv-VI-VII-i", (3, 5, 6, 0), ("minor",), "epic, uplifting", (0.4, 1.0)),
    Progression("i-VI-VII-v", (0, 5, 6, 4), ("minor",), "driving, bittersweet", (0.3, 1.0)),
    Progression("i-III-VI-VII", (0, 2, 5, 6), ("minor",), "soaring", (0.3, 1.0)),
    Progression("VI-iv-i-VII", (5, 3, 0, 6), ("minor",), "longing", (0.2, 1.0)),
    Progression("i-v-VI-III", (0, 4, 5, 2), ("minor",), "dark, cinematic", (0.2, 1.0)),
    Progression("i-VII-iv-VI", (0, 6, 3, 5), ("minor",), "restless", (0.3, 1.0)),
    Progression("i-i-VI-VII", (0, 0, 5, 6), ("minor",), "tense, building", (0.4, 1.0)),
    # major
    Progression("I-V-vi-IV", (0, 4, 5, 3), ("major",), "bright, uplifting", (0.3, 1.0)),
    Progression("I-vi-IV-V", (0, 5, 3, 4), ("major",), "nostalgic, sweet", (0.0, 1.0)),
    Progression("IV-I-V-vi", (3, 0, 4, 5), ("major",), "open, hopeful", (0.2, 1.0)),
    Progression("I-iii-vi-IV", (0, 2, 5, 3), ("major",), "gentle, reflective", (0.0, 0.8)),
    Progression("ii-V-I-vi", (1, 4, 0, 5), ("major",), "jazzy, lofi", (0.0, 1.0)),
    Progression("I-IV-vi-V", (0, 3, 5, 4), ("major",), "warm", (0.0, 1.0)),
    Progression("IV-V-iii-vi", (3, 4, 2, 5), ("major",), "emotional", (0.3, 1.0)),
    Progression("vi-IV-I-V", (5, 3, 0, 4), ("major",), "euphoric, anthemic", (0.4, 1.0)),
    Progression("I-V-IV-V", (0, 4, 3, 4), ("major",), "sunny, driving", (0.3, 1.0)),
    Progression("IV-vi-I-V", (3, 5, 0, 4), ("major",), "hopeful, rising", (0.2, 1.0)),
    # modal
    Progression("i-IV-i-IV", (0, 3, 0, 3), ("dorian",), "groovy, floating", (0.0, 1.0)),
    Progression("i-IV-VII-IV", (0, 3, 6, 3), ("dorian",), "soulful", (0.2, 1.0)),
    Progression("i-III-IV-i", (0, 2, 3, 0), ("dorian",), "bright, lifting", (0.2, 1.0)),
    Progression("i-VII-III-IV", (0, 6, 2, 3), ("dorian",), "open, rolling", (0.3, 1.0)),
    Progression("i-IV-v-i", (0, 3, 4, 0), ("dorian",), "cool, driving", (0.2, 1.0)),
    Progression("I-II-I-II", (0, 1, 0, 1), ("lydian",), "dreamy, weightless", (0.0, 0.6)),
    Progression("I-II-V-I", (0, 1, 4, 0), ("lydian",), "wonder", (0.0, 1.0)),
    Progression("I-VII-IV-I", (0, 6, 3, 0), ("mixolydian",), "laid-back, sunny", (0.0, 1.0)),
    Progression("I-v-IV-I", (0, 4, 3, 0), ("mixolydian",), "folky, open", (0.0, 1.0)),
    Progression("i-II-i-II", (0, 1, 0, 1), ("phrygian",), "hypnotic, eastern", (0.0, 1.0)),
    Progression("i-II-III-II", (0, 1, 2, 1), ("phrygian",), "dark, driving", (0.3, 1.0)),
    Progression("i-II-vii-i", (0, 1, 6, 0), ("phrygian",), "tense, psychedelic", (0.2, 1.0)),
    Progression("i-VI-II-i", (0, 5, 1, 0), ("phrygian",), "brooding", (0.0, 1.0)),
    Progression("i-iv-II-i", (0, 3, 1, 0), ("phrygian",), "ominous", (0.2, 1.0)),
    Progression("i-III-II-i", (0, 2, 1, 0), ("phrygian",), "dark, rolling", (0.3, 1.0)),
]

# The library: the progressions of the NRG-CP dataset (WaivOps / Patchbanks,
# CC BY 4.0, https://doi.org/10.5281/zenodo.15304989), mined by
# eval/mine_nrg.py into music/data/nrg_progressions.json: every 4-chord
# cycle found in at least NRG_MIN_FILES of its 30,943 EDM / trance files,
# weighted by how often. The curated list above lends its moods where a name
# matches, and stands in if the data file is missing.
NRG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nrg_progressions.json")
NRG_MIN_FILES = 8
NRG_PER_MODE = 30


def _library() -> list[Progression]:
    try:
        with open(NRG_PATH, encoding="utf-8") as f:
            mined = json.load(f)["progressions"]
    except (OSError, ValueError, KeyError):
        return list(_CURATED)
    curated = {p.name: p for p in _CURATED}
    merged: dict[str, Progression] = {}
    for mode, items in mined.items():
        if mode not in MODES:
            continue
        for name, count in items[:NRG_PER_MODE]:
            if count < NRG_MIN_FILES or len(set(name.split("-"))) < 2:
                continue
            try:
                p = parse_progression(name, mode)
            except ValueError:
                continue
            known = curated.get(name)
            if name in merged:          # the same numerals fit another mode too
                old = merged[name]
                merged[name] = Progression(name, old.degrees, old.modes + (mode,), old.mood, old.energy,
                                           old.weight + count)
            else:
                merged[name] = Progression(name, p.degrees, (mode,), known.mood if known else "",
                                           known.energy if known else (0.0, 1.0), count)
    if not all(any(m in p.modes for p in merged.values()) for m in MODES):
        return list(_CURATED)
    return sorted(merged.values(), key=lambda p: -p.weight)


PROGRESSIONS: list[Progression] = []          # filled below, once parse_progression exists
PROGRESSIONS_BY_NAME: dict[str, Progression] = {}

MAX_CUSTOM_CHORDS = 4   # a written progression has the library's length: one period for every layer


def parse_progression(name: str, mode: str, fix_case: bool = False) -> Progression:
    """A progression written in Roman numerals ("i-II-VI-VII"), checked
    against the mode: 4 chords, each one of the mode's own chords with the
    case matching its quality (upper = major, lower = minor) and no
    diminished chords. Raises ValueError saying what is wrong. With
    `fix_case`, a numeral in the wrong case ("iv" where the mode has "IV")
    becomes the mode's own chord on that degree: same root movement, still
    in key."""
    tokens = [t.strip() for t in str(name).strip().split("-")]
    if len(tokens) != MAX_CUSTOM_CHORDS:
        raise ValueError(f"{name!r}: write exactly {MAX_CUSTOM_CHORDS} chords, e.g. i-VI-III-VII")
    key = Key(0, parse_mode(mode))
    degrees = []
    for token in tokens:
        if token.lower() not in ROMAN:
            raise ValueError(f"{token!r} is not a Roman numeral I-VII")
        degree = ROMAN.index(token.lower())
        quality = diatonic_chord(key, degree, seventh=False).quality
        expected = {"maj": ROMAN[degree].upper(), "min": ROMAN[degree]}.get(quality)
        if expected is None:
            raise ValueError(f"{token!r}: degree {degree + 1} is {quality} in {key.mode}; avoid it")
        if token != expected and not fix_case:
            raise ValueError(f"{token!r} is not in {key.mode}; that chord is {expected}")
        tokens[len(degrees)] = expected
        degrees.append(degree)
    if len(set(degrees)) < 2:
        raise ValueError(f"{name!r} is one chord the whole time: use at least two different chords")
    return Progression("-".join(tokens), tuple(degrees), (key.mode,), "", (0.0, 1.0))


def mode_chords(mode: str) -> list[str]:
    """The Roman numerals a written progression may use in a mode (its major
    and minor chords; diminished ones left out)."""
    key = Key(0, parse_mode(mode))
    out = []
    for degree in range(7):
        quality = diatonic_chord(key, degree, seventh=False).quality
        if quality in ("maj", "min"):
            out.append(ROMAN[degree].upper() if quality == "maj" else ROMAN[degree])
    return out


def progression_named(name: str, mode: str) -> Progression | None:
    """The library progression of that name when it suits the mode, else a
    written one that passes parse_progression, else None."""
    known = PROGRESSIONS_BY_NAME.get(name)
    if known is not None and mode in known.modes:
        return known
    try:
        return parse_progression(name, mode)
    except ValueError:
        return None


PROGRESSIONS.extend(_library())
PROGRESSIONS_BY_NAME.update({p.name: p for p in PROGRESSIONS})


def progressions_for(mode: str, energy: float | None = None) -> list[Progression]:
    out = [p for p in PROGRESSIONS if mode in p.modes]
    if energy is not None:
        fitting = [p for p in out if p.energy[0] <= energy <= p.energy[1]]
        return fitting or out
    return out


CHORD_COLORS = ("sevenths", "ninths")


def with_ninth(key: Key, chord: Chord) -> Chord:
    """Add the diatonic 9th, but only when it's a whole step above the root
    (a flat 9th rubs against the root and sounds harsh in chill music)."""
    ninth = key.degree_pitch(chord.degree + 8) % 12
    if (ninth - chord.root) % 12 != 2:
        return chord
    return Chord(chord.degree, chord.notes + (ninth,), chord.root, chord.quality, chord.seventh)


def progression_chords(key: Key, progression: Progression, seventh: bool = True,
                       color: str = "sevenths") -> list[Chord]:
    chords = [diatonic_chord(key, d, seventh=seventh) for d in progression.degrees]
    if color == "ninths":
        chords = [with_ninth(key, c) for c in chords]
    return chords


def transpose_key(key: Key, semitones: int) -> Key:
    return Key((key.root + semitones) % 12, key.mode)


# ---------------------------------------------------------------------------
# Voice leading
# ---------------------------------------------------------------------------

def _voicing_candidates(pcs: list[int], low: int, high: int) -> list[tuple[int, ...]]:
    options = []
    for pc in pcs:
        choices = [n for n in range(low, high + 1) if n % 12 == pc]
        if not choices:
            return []
        options.append(choices)
    out = []
    for combo in itertools.product(*options):
        voicing = tuple(sorted(combo))
        if len(set(voicing)) != len(voicing):
            continue
        out.append(voicing)
    return out


def _voicing_penalty(voicing: tuple[int, ...]) -> float:
    penalty = 0.0
    span = voicing[-1] - voicing[0]
    if span > 19:
        penalty += (span - 19) * 2
    for a, b in zip(voicing, voicing[1:]):
        gap = b - a
        # Semitone rubs sound muddy, especially in the lower register.
        if gap == 1:
            penalty += 12 if a < 0 else 8
        # Thirds or closer below the bass register are mud.
        if a < -7 and gap < 5:
            penalty += 4
    return penalty


def voice_chords(chords: list[Chord], low: int = -8, high: int = 12, center: int = 2) -> list[tuple[int, ...]]:
    """Choose a voicing per chord that minimises total voice movement.

    Uses a small Viterbi search over all voicings in range so the whole loop
    (including the wrap from last chord back to the first) moves smoothly.
    """
    candidate_lists = [_voicing_candidates(list(c.notes), low, high) for c in chords]
    if any(not c for c in candidate_lists):
        raise ValueError("No voicing fits in the requested range")

    def movement(a: tuple[int, ...], b: tuple[int, ...]) -> float:
        if len(a) == len(b):
            return sum(abs(x - y) for x, y in zip(a, b))
        return abs(sum(a) / len(a) - sum(b) / len(b)) * len(b)

    def start_cost(v: tuple[int, ...]) -> float:
        mean = sum(v) / len(v)
        return abs(mean - center) * 0.5 + _voicing_penalty(v)

    best_total = None
    best_path: list[tuple[int, ...]] = []
    # Try each start voicing (bounded) so the loop-wrap cost is accounted for.
    starts = sorted(candidate_lists[0], key=start_cost)[:12]
    for start in starts:
        costs = {start: start_cost(start)}
        back: list[dict] = [{start: None}]
        for cands in candidate_lists[1:]:
            new_costs = {}
            new_back = {}
            for v in cands:
                pen = _voicing_penalty(v) + abs(sum(v) / len(v) - center) * 0.15
                prev, cost = min(((p, c + movement(p, v)) for p, c in costs.items()), key=lambda x: x[1])
                new_costs[v] = cost + pen
                new_back[v] = prev
            costs = new_costs
            back.append(new_back)
        last, total = min(((v, c + movement(v, start)) for v, c in costs.items()), key=lambda x: x[1])
        if best_total is None or total < best_total:
            path = [last]
            for step in range(len(back) - 1, 0, -1):
                path.append(back[step][path[-1]])
            best_path = list(reversed(path))
            best_total = total
    return best_path


def bass_note(chord: Chord, low: int = -24, high: int = -13) -> int:
    for n in range(low, high + 1):
        if n % 12 == chord.root:
            return n
    raise ValueError("bass range must span an octave")
