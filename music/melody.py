"""
Melody generation constrained by harmony.

Rules (classic, simple, effective):
- Strong beats land on chord tones of the current chord.
- Weak beats use the key's safe pentatonic, mostly stepwise.
- Phrases are built as call/response: motif A over chords 1-2, a varied
  repeat over chords 3-4, ending on a stable tone (root/third of the last chord).
- Everything stays inside the preset's register.

Output is one Tidal mini-notation string per cycle, with "~" for rests and
"_" to hold the previous note, so note lengths are real, not just legato.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .theory import Chord, Key

STEPS_PER_CYCLE = 8  # eighth notes in a 4/4 bar

# Rhythm cells for one cycle: "x" = note onset, "_" = hold, "." = rest.
RHYTHMS = {
    "sparse": [
        "x___....",
        "x_______",
        "....x___",
        "x___x___",
        "..x_____",
    ],
    "flowing": [
        "x_x_x___",
        "x__x__x_",
        "x_x___x_",
        "..x_x_x_",
        "x___x_x_",
    ],
    "lively": [
        "x_xxx_x_",
        "x_x_xxx_",
        "xx_x__x_",
        "x_x_x_xx",
    ],
}
CHARACTERS = list(RHYTHMS)
STRONG_STEPS = {0, 4}


@dataclass
class MelodySpec:
    character: str = "flowing"  # sparse | flowing | lively
    seed: int = 0
    low: int = 0
    high: int = 19


def _nearest(candidates: list[int], target: int) -> int:
    return min(candidates, key=lambda n: (abs(n - target), n))


def _in_range(pcs: set[int], low: int, high: int) -> list[int]:
    return [n for n in range(low, high + 1) if n % 12 in pcs]


def _stable_tones(chord: Chord) -> set[int]:
    # root and third
    return {chord.notes[0], chord.notes[1]}


def generate_melody(key: Key, chords: list[Chord], cycles_per_chord: int, spec: MelodySpec) -> list[str]:
    """Return one mini-notation string per cycle (len = len(chords) * cycles_per_chord)."""
    rng = random.Random(spec.seed)
    rhythms = RHYTHMS.get(spec.character, RHYTHMS["flowing"])
    safe = key.safe_melody_pcs()
    scale_notes = _in_range(safe, spec.low, spec.high)
    center = (spec.low + spec.high) // 2
    total_cycles = len(chords) * cycles_per_chord
    half = total_cycles // 2

    # Motif A: rhythm cells for the first half; response reuses them with
    # one cell varied.
    cells_a = [rng.choice(rhythms) for _ in range(half)]
    cells_b = list(cells_a)
    if cells_b:
        cells_b[rng.randrange(len(cells_b))] = rng.choice(rhythms)
    # Final cycle ends with a held note so the phrase resolves.
    cells = cells_a + cells_b
    if total_cycles % 2:
        cells.append(rng.choice(rhythms))
    cells[-1] = "x___x___" if spec.character != "sparse" else "x_______"

    prev = _nearest(scale_notes, center + rng.choice([-2, 0, 2]))
    contour = rng.choice([1, -1])
    out: list[str] = []
    for cycle in range(total_cycles):
        chord = chords[cycle // cycles_per_chord]
        chord_notes = _in_range(chord.pitch_classes & (safe | chord.triad_pcs), spec.low, spec.high)
        chord_notes = chord_notes or _in_range(chord.triad_pcs, spec.low, spec.high)
        is_last = cycle == total_cycles - 1
        tokens: list[str] = []
        for step, sym in enumerate(cells[cycle]):
            if sym == ".":
                tokens.append("~")
                continue
            if sym == "_":
                tokens.append("_" if tokens and tokens[-1] != "~" else "~")
                continue
            if step in STRONG_STEPS:
                pool = chord_notes
                if is_last and step == 4:
                    pool = _in_range(_stable_tones(chord), spec.low, spec.high) or chord_notes
                target = prev + contour * rng.choice([0, 2, 3])
                note = _nearest(pool, target)
            else:
                idx = scale_notes.index(_nearest(scale_notes, prev))
                move = contour * rng.choice([1, 1, 1, 2, -1])
                idx = max(0, min(len(scale_notes) - 1, idx + move))
                note = scale_notes[idx]
            # Keep leaps singable.
            if abs(note - prev) > 9:
                note = _nearest(chord_notes if step in STRONG_STEPS else scale_notes, prev)
            # Turn the contour around near the register edges.
            if note >= spec.high - 2:
                contour = -1
            elif note <= spec.low + 2:
                contour = 1
            elif rng.random() < 0.2:
                contour = -contour
            tokens.append(str(note))
            prev = note
        out.append("[" + " ".join(tokens) + "]")
    return out


def arp_pattern(voicing: tuple[int, ...], low: int, high: int, style: str, rng: random.Random) -> str:
    """One cycle of arpeggio over a chord voicing, as 8 steps."""
    tones = sorted(set(voicing))
    extended = tones + [t + 12 for t in tones]
    notes = [n for n in extended if low <= n <= high] or tones
    if style == "updown":
        seq = notes + notes[-2:0:-1]
    elif style == "down":
        seq = list(reversed(notes))
    else:
        seq = notes
    steps = [str(seq[i % len(seq)]) for i in range(STEPS_PER_CYCLE)]
    # leave a little air: occasionally rest on an offbeat
    if rng.random() < 0.5:
        steps[7] = "~"
    return "[" + " ".join(steps) + "]"
