#!/usr/bin/env python3
"""
Mine chord progressions from the NRG-CP dataset (WaivOps / Patchbanks, CC BY
4.0, https://doi.org/10.5281/zenodo.15304989): 30,943 eight-bar MIDI files of
rhythmic EDM / trance chord progressions.

Only the progressions are used (the rhythms, voicings and everything else
are not): each file's chords are read as triads, the key is the mode in
which every chord is one of its own (as theory.parse_progression demands),
and the progression is written in Roman numerals. The counts per mode go to
music/data/nrg_progressions.json, which theory.py loads as part of the
progression library.

    python -m eval.mine_nrg /path/to/nrgcp_midi_dataset/ [--top 40]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music.theory import MODES, ROMAN, Key, diatonic_chord, parse_progression  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "music", "data",
                   "nrg_progressions.json")
# The modes the stream plays, in the order a tie is resolved (minor first:
# most trance is minor).
MINE_MODES = ["minor", "major", "dorian", "phrygian", "mixolydian", "lydian"]


def read_notes(path: str) -> tuple[int, list[tuple[int, int, int]]]:
    """(ticks per beat, [(start tick, end tick, pitch)]) from a type 0/1 MIDI file."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    _, ntracks, division = struct.unpack(">HHH", data[8:14])
    pos, notes = 14, []
    for _ in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("bad track")
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        track, pos = data[pos + 8:pos + 8 + length], pos + 8 + length
        i, tick, status, on = 0, 0, 0, {}
        while i < len(track):
            delta = 0
            while True:
                b = track[i]; i += 1
                delta = (delta << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += delta
            b = track[i]
            if b & 0x80:
                status = b; i += 1
            if status == 0xFF:                       # meta: type, length, data
                i += 1
                n = 0
                while True:
                    c = track[i]; i += 1
                    n = (n << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                i += n
                continue
            if status in (0xF0, 0xF7):               # sysex
                n = 0
                while True:
                    c = track[i]; i += 1
                    n = (n << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                i += n
                continue
            kind = status & 0xF0
            size = 1 if kind in (0xC0, 0xD0) else 2
            args = track[i:i + size]; i += size
            if kind == 0x90 and args[1] > 0:
                on[args[0]] = tick
            elif kind in (0x80, 0x90):
                start = on.pop(args[0], None)
                if start is not None:
                    notes.append((start, tick, args[0]))
    return division, notes


def bar_chords(division: int, notes: list[tuple[int, int, int]], bars: int = 8) -> list[tuple[frozenset[int], int]]:
    """One chord per bar: the pitch classes of the bar's first hit with 3+
    notes, and its lowest note's pitch class (the root, when the set holds
    two triads). A bar without one repeats the previous chord."""
    bar_len = division * 4
    hits = defaultdict(list)
    for start, _, pitch in notes:
        hits[start].append(pitch)
    out: list[tuple[frozenset[int], int]] = []
    for bar in range(bars):
        lo, hi = bar * bar_len, (bar + 1) * bar_len
        chord = None
        for t in sorted(t for t in hits if lo <= t < hi):
            if len({p % 12 for p in hits[t]}) >= 3:
                chord = (frozenset(p % 12 for p in hits[t]), min(hits[t]) % 12)
                break
        if chord is None and out:
            chord = out[-1]
        if chord is not None:
            out.append(chord)
    return out


def roman_in(key: Key, chord: tuple[frozenset[int], int]) -> str | None:
    """The chord as a numeral of the key: a diatonic major or minor triad
    contained in the set (whose other notes must be in the key too), the
    one rooted on the lowest note when there are two."""
    pcs, bass = chord
    if not pcs <= key.pitch_classes:
        return None
    found = []
    for degree in range(7):
        c = diatonic_chord(key, degree, seventh=False)
        if c.quality in ("maj", "min") and c.triad_pcs <= pcs:
            found.append((c.root != bass, ROMAN[degree].upper() if c.quality == "maj" else ROMAN[degree]))
    return min(found)[1] if found else None


def fold(chords: list) -> list:
    """8 bar-chords to the 4-chord cycle: chords held two bars, or a 4-bar
    cycle played twice."""
    if len(chords) == 8 and all(chords[i] == chords[i + 1] for i in range(0, 8, 2)):
        return chords[::2]
    if len(chords) == 8 and chords[:4] == chords[4:]:
        return chords[:4]
    return chords


def progression_of(chords: list[tuple[frozenset[int], int]]) -> tuple[str, str] | None:
    """(mode, "i-VI-III-VII") or None: the bar chords folded to a 4-chord
    cycle, named in the mode where all four are the mode's own chords,
    preferring a reading that starts or ends on the tonic (then minor, as
    most trance is)."""
    chords = fold(chords)
    if len(chords) != 4:
        return None
    candidates = []
    for mode in MINE_MODES:
        for root in range(12):
            key = Key(root, mode)
            names = [roman_in(key, c) for c in chords]
            if None in names:
                continue
            name = "-".join(names)
            try:
                parse_progression(name, mode)
            except ValueError:
                continue
            tonic = names[0].lower() == "i" or names[-1].lower() == "i"
            candidates.append((not tonic, MINE_MODES.index(mode), mode, name))
    if not candidates:
        return None
    _, _, mode, name = min(candidates)
    return mode, name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--top", type=int, default=40, help="progressions kept per mode")
    args = parser.parse_args()
    files = sorted(glob.glob(os.path.join(args.folder, "**", "*.mid"), recursive=True))
    counts: dict[str, Counter] = defaultdict(Counter)
    skipped = Counter()
    for path in files:
        try:
            division, notes = read_notes(path)
        except (ValueError, IndexError, struct.error):
            skipped["unreadable"] += 1
            continue
        found = progression_of(bar_chords(division, notes))
        if found is None:
            skipped["not a diatonic 4-chord cycle"] += 1
            continue
        counts[found[0]][found[1]] += 1
    out = {
        "source": "NRG-CP by WaivOps / Patchbanks, CC BY 4.0, https://doi.org/10.5281/zenodo.15304989",
        "files": len(files), "used": sum(sum(c.values()) for c in counts.values()), "skipped": dict(skipped),
        "progressions": {mode: [[name, n] for name, n in counts[mode].most_common(args.top)]
                         for mode in MINE_MODES if counts[mode]},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "progressions"}, indent=1))
    for mode, items in out["progressions"].items():
        print(f"{mode}: {sum(n for _, n in counts[mode].items())} files; top: "
              + ", ".join(f"{name} ({n})" for name, n in items[:8]))


if __name__ == "__main__":
    main()
