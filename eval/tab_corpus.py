#!/usr/bin/env python3
"""
Melody and harmony from real songs' tabs and scores, against what the
engine plays: the keys and modes, the chords and how they move, the bass
figures and the riffs, written the way the engine writes them (scale
degrees, the bass relative to each chord's root).

    python -m eval.tab_corpus tabs/darkwave/                 # the report
    python -m eval.tab_corpus tabs/darkwave/ --style darkwave  # ...against darkwave's patterns
    python -m eval.tab_corpus tabs/ --json runtime/tab_corpus.json

Reads everything eval/tabs.py reads (Guitar Pro 3-8, ASCII tabs, MIDI,
MusicXML), folders recursively. It learns shapes and statistics, never a
song's tune: what a style's bass does under its chords, which chord follows
which, how far a riff moves and how often it repeats.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

from eval.tabs import SUFFIXES, Song, Track, load
from music.theory import DISPLAY_NAMES, MODES

# Krumhansl-Kessler key profiles (major, minor), from C.
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII"]


# ------------------------------------------------------------------ key

def _profile(song: Song) -> list[float]:
    """Pitch-class weight: note lengths, the bass counted half again (it names the key)."""
    w = [0.0] * 12
    for t in song.tracks:
        for n in t.notes:
            w[n.pitch % 12] += max(n.length, 0.125) * (1.5 if t.bass else 1.0)
    return w


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / 12, sum(b) / 12
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def _bass_home(song: Song) -> Counter:
    """Where the bass (else the lowest notes) puts its weight: on the downbeats,
    the first note and the last, which name the tonic."""
    line = next((t for t in song.tracks if t.bass), None)
    notes = sorted(line.notes if line else
                   [min(g, key=lambda n: n.pitch) for g in _by_onset(song)], key=lambda n: n.start)
    home = Counter()
    if not notes:
        return home
    bar = song.beats_per_bar or 4.0
    for n in notes:
        if song.timed and abs(n.start / bar - round(n.start / bar)) < 1e-3:
            home[n.pitch % 12] += 1
    # The first note most (a tab of a loop can stop anywhere), the last a little.
    home[notes[0].pitch % 12] += 2 + len(notes) / 50
    home[notes[-1].pitch % 12] += 1 + len(notes) / 100
    return home


def _by_onset(song: Song):
    groups: dict[float, list] = {}
    for t in song.tracks:
        for n in t.notes:
            groups.setdefault(round(n.start, 4), []).append(n)
    return list(groups.values())


def find_key(song: Song) -> tuple[int, str, float]:
    """(tonic pitch class, mode, how much of the music is in that scale). The
    profile finds the scale (its seven notes); the bass decides which of them
    is home, so A minor isn't heard as C major for sharing its notes, and the
    mode follows (D in C major's notes is dorian). Locrian homes, which the
    engine doesn't play, keep the profile's own key."""
    w = _profile(song)
    total = sum(w) or 1.0
    score, tonic, minor = max(((_corr(w, prof[-r:] + prof[:-r]), r, minor) for r in range(12)
                               for minor, prof in ((False, _MAJOR), (True, _MINOR))))
    family = ("minor", "dorian", "phrygian") if minor else ("major", "mixolydian", "lydian")
    fit = {m: sum(w[(tonic + i) % 12] for i in MODES[m]) / total for m in family}
    mode = max(family, key=lambda m: (round(fit[m], 3), m in ("major", "minor")))
    scale = {(tonic + i) % 12 for i in MODES[mode]}
    home = _bass_home(song)
    if home:
        best = max(scale, key=lambda pc: (home[pc], pc == tonic))
        if home[best] > home[tonic]:
            steps = sorted((pc - best) % 12 for pc in scale)
            named = next((m for m, iv in MODES.items() if iv == steps), None)
            if named:
                tonic, mode = best, named
    return tonic, mode, sum(w[pc] for pc in scale) / total


@dataclass
class Scale:
    tonic: int
    mode: str

    @property
    def name(self) -> str:
        return f"{DISPLAY_NAMES[self.tonic]} {self.mode}"

    def degree(self, pitch: int) -> tuple[int, bool]:
        """(scale degree from the tonic below middle C's octave, 7 = an octave
        up; in the scale?). A note outside it counts as the degree below."""
        rel = pitch - self.tonic - 48
        octave, pc = divmod(rel, 12)
        steps = MODES[self.mode]
        idx = max(i for i, s in enumerate(steps) if s <= pc)
        return idx + 7 * octave, steps[idx] == pc

    def numeral(self, root_pc: int, minor: bool | None) -> str:
        """Roman numeral of a chord on root_pc: case from its third when it has
        one, else from the key (a power chord); b/# for a root off the scale."""
        steps = MODES[self.mode]
        rel = (root_pc - self.tonic) % 12
        if rel in steps:
            i = steps.index(rel)
            if minor is None:
                third = (steps[(i + 2) % 7] - steps[i]) % 12
                minor = third == 3
            return _NUMERALS[i].lower() if minor else _NUMERALS[i]
        if rel > steps[-1]:
            return "#" + (_NUMERALS[6].lower() if minor else _NUMERALS[6])
        numeral = _NUMERALS[next(j for j in range(7) if steps[j] > rel)]
        return "b" + (numeral.lower() if minor else numeral)


# ------------------------------------------------------------------ per song

@dataclass
class SongStats:
    title: str
    key: str
    in_scale: float
    tempo: float
    timed: bool
    chords: list[tuple[str, float]] = field(default_factory=list)   # (numeral, bars)
    loop: str = ""
    bass_figures: list[str] = field(default_factory=list)            # one per bar
    bass_rhythms: list[str] = field(default_factory=list)
    riff_figures: list[str] = field(default_factory=list)
    steps: list[int] = field(default_factory=list)                   # scale steps between riff notes
    riff_range: int = 0
    repeat_rate: float = 0.0
    notes_per_bar: float = 0.0


def _bars(song: Song) -> int:
    end = max((n.start + n.length for t in song.tracks for n in t.notes), default=0)
    return int(end // song.beats_per_bar) + 1


def _in_window(track: Track, a: float, b: float):
    return [n for n in track.notes if a - 1e-6 <= n.start < b - 1e-6]


def _sounding(track: Track, t: float):
    return [n for n in track.notes if n.start - 1e-6 <= t < n.start + n.length - 1e-6]


def _chord_at(song: Song, scale: Scale, a: float, b: float) -> tuple[int, str] | None:
    """The chord over [a, b): its root from the bass (the note on the downbeat,
    else the longest), else the lowest note; its third from all the notes."""
    bass = [t for t in song.tracks if t.bass]
    pool = [n for t in bass for n in (_sounding(t, a) or _in_window(t, a, b))]
    everything = [n for t in song.tracks for n in _in_window(t, a, b) + _sounding(t, a)]
    if not everything:
        return None
    if pool:
        weight = Counter()
        for n in pool:
            weight[n.pitch % 12] += n.length + (1.0 if n.start <= a + 1e-6 else 0.0)
        root = weight.most_common(1)[0][0]
    else:
        root = min(everything, key=lambda n: n.pitch).pitch % 12
    pcs = {n.pitch % 12 for n in everything}
    has_min, has_maj = (root + 3) % 12 in pcs, (root + 4) % 12 in pcs
    minor = True if has_min and not has_maj else False if has_maj and not has_min else None
    return root, scale.numeral(root, minor)


def _figure(notes, root_degree: int, scale: Scale, a: float, length: float, slots: int) -> str:
    """A bar of a line as the engine writes it: per slot the scale steps from
    the chord's root (7 an octave up), ~ nothing, _ the note before held."""
    step, out = length / slots, []
    for i in range(slots):
        t0 = a + i * step
        hit = [n for n in notes if t0 - step / 2 <= n.start < t0 + step / 2]
        if hit:
            n = max(hit, key=lambda n: n.pitch) if len(hit) > 1 else hit[0]
            out.append(str(scale.degree(n.pitch)[0] - root_degree))
        elif any(n.start < t0 < n.start + n.length - 1e-6 for n in notes):
            out.append("_" if not out or out[-1] != "~" else "~")
        else:
            out.append("~")
    return " ".join(out)


def _slots(notes, a: float, length: float) -> int:
    """8 slots a bar, 16 when the line moves in 16ths."""
    off8 = sum(1 for n in notes if abs(((n.start - a) / (length / 8)) % 1 - 0.5) < 0.25)
    return 16 if notes and off8 / len(notes) > 0.2 else 8


def _top_line(track: Track) -> Track:
    """A riff's line: the highest note at each onset."""
    by_start: dict[float, object] = {}
    for n in track.notes:
        k = round(n.start, 4)
        if k not in by_start or n.pitch > by_start[k].pitch:
            by_start[k] = n
    return Track(track.name, track.bass, [by_start[k] for k in sorted(by_start)])


def _loop(chords: list[str]) -> str:
    """The progression the song cycles through most: the commonest run of 2-4
    chord changes (repeated chords merged) that comes back to its start,
    turned to begin on the tonic chord when it has one: "i-VI-III-VII"."""
    seq = [c for i, c in enumerate(chords) if i == 0 or c != chords[i - 1]]
    best, best_score = (), 0
    for size in (4, 3, 2):
        grams = Counter(tuple(seq[i:i + size]) for i in range(len(seq) - size + 1))
        for gram, n in grams.most_common():
            if n >= 2 and n * size > best_score and len(set(gram)) == size:
                best, best_score = gram, n * size
                break
    if not best:
        return ""
    home = next((i for i, c in enumerate(best) if c in ("i", "I")), 0)
    return "-".join(best[home:] + best[:home])


def in_library(loop: str, library: set[str]) -> bool:
    """A loop as the engine names four chords: "i-VII" is "i-VII-i-VII"."""
    chords = loop.split("-")
    return loop in library or (4 % len(chords) == 0 and "-".join(chords * (4 // len(chords))) in library)


def analyse(song: Song) -> SongStats:
    tonic, mode, fit = find_key(song)
    scale = Scale(tonic, mode)
    stats = SongStats(song.title, scale.name, round(fit, 3), song.tempo, song.timed)
    riffs = [_top_line(t) for t in song.tracks if not t.bass]
    lead = max(riffs, key=lambda t: len(t.notes), default=None)
    if lead and len(lead.notes) > 1:
        degrees = [scale.degree(n.pitch)[0] for n in lead.notes]
        stats.steps = [b - a for a, b in zip(degrees, degrees[1:])]
        lo, hi = sorted(degrees)[len(degrees) // 20], sorted(degrees)[-1 - len(degrees) // 20]
        stats.riff_range = hi - lo                 # without the odd outlier
    if not song.timed:
        return stats
    bar = song.beats_per_bar
    bass = next((t for t in song.tracks if t.bass), None)
    chords: list[str] = []
    bass_by_bar: list[str] = []
    for b in range(_bars(song)):
        a = b * bar
        halves = [_chord_at(song, scale, a, a + bar / 2), _chord_at(song, scale, a + bar / 2, a + bar)]
        chord = _chord_at(song, scale, a, a + bar)
        if chord is None:
            continue
        # A new chord in the bar's second half only when its root isn't a note of the first's
        # (a bass walking to the fifth or the third is still the same chord).
        if all(halves) and halves[0][1] != halves[1][1] and \
                (halves[1][0] - halves[0][0]) % 12 not in (0, 3, 4, 7):
            stats.chords += [(halves[0][1], 0.5), (halves[1][1], 0.5)]
        else:
            stats.chords.append((chord[1], 1.0))
        chords.append(chord[1])
        root_degree = None
        if bass:
            notes = _in_window(bass, a, a + bar)
            held = [n for n in _sounding(bass, a) if n.start < a - 1e-6]
            if notes or held:
                notes = notes or held
                roots = [n for n in notes if n.pitch % 12 == chord[0]]
                ref = min(roots or notes, key=lambda n: n.pitch)
                root_degree = scale.degree(ref.pitch)[0] if roots else \
                    scale.degree(ref.pitch - (ref.pitch - chord[0]) % 12)[0]
                fig = _figure(held + [n for n in notes if n not in held], root_degree, scale, a, bar,
                              _slots(notes, a, bar))
                stats.bass_figures.append(fig)
                stats.bass_rhythms.append(" ".join("x" if re.fullmatch(r"-?\d+", s) else s
                                                   for s in fig.split()))
                bass_by_bar.append(fig)
        if lead:
            notes = _in_window(lead, a, a + bar)
            if notes:
                ref = root_degree if root_degree is not None else \
                    scale.degree(min(n.pitch for n in notes) - (min(n.pitch for n in notes) - chord[0]) % 12)[0]
                # The riff against the chord, an octave or two above the bass: fold to 0-13.
                fig = _figure(notes, ref, scale, a, bar, _slots(notes, a, bar))
                stats.riff_figures.append(" ".join(s if not re.fullmatch(r"-?\d+", s) else str(int(s) % 14)
                                                   for s in fig.split()))
    stats.loop = _loop(chords)
    figs = stats.riff_figures or bass_by_bar
    if len(figs) > 2:
        stats.repeat_rate = round(sum(figs[i] in (figs[i - 1], figs[i - 2] if i > 1 else None)
                                      for i in range(1, len(figs))) / (len(figs) - 1), 3)
    if lead:
        stats.notes_per_bar = round(len(lead.notes) / max(1, _bars(song)), 2)
    return stats


# ------------------------------------------------------------------ the engine's side

def expand(pattern: str) -> list[str]:
    """The engine's simple mini-notation as bars of slots: "0*8", "[0 0 7 0]*4",
    "0 ~ 0 0", "<[bar 1] [bar 2]>" (one bar each)."""
    pattern = pattern.strip()
    if pattern.startswith("<") and pattern.endswith(">"):
        return [b for part in re.findall(r"\[[^\[\]]*\]", pattern[1:-1]) for b in expand(part[1:-1])]
    m = re.fullmatch(r"\[([^\[\]]*)\]\*(\d+)", pattern)
    if m:
        return [" ".join(m.group(1).split() * int(m.group(2)))]
    out = []
    for tok in pattern.split():
        m = re.fullmatch(r"(-?\d+|~)\*(\d+)", tok)
        out += [m.group(1)] * int(m.group(2)) if m else [tok]
    return [" ".join(out)]


def engine_patterns(style: str) -> dict:
    """What the engine plays in a style: its subgenres' bass figures, its
    progression library for the style's modes, bars per chord."""
    from music.blocks import SUBGENRES
    from music.plan import STYLES
    from music.theory import progressions_for

    figures = {f for sub in SUBGENRES.get(style, {}).values()
               for p in sub.get("kit", {}).get("rhythm", []) for f in expand(p)}
    modes = STYLES[style]["modes"]
    return {"bass_figures": sorted(figures), "modes": modes,
            "progressions": sorted({p.name for m in modes for p in progressions_for(m)}),
            "bars_per_chord": STYLES[style].get("cycles_per_chord", 1)}


# ------------------------------------------------------------------ the corpus

def files(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                out += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(SUFFIXES)]
        elif p.lower().endswith(SUFFIXES):
            out.append(p)
    return out


def corpus(stats: list[SongStats], style: str | None = None, top: int = 12) -> dict:
    timed = [s for s in stats if s.timed]
    chord_seq = [[c for c, _ in s.chords] for s in timed]
    moves = Counter((a, b) for seq in chord_seq for a, b in zip(seq, seq[1:]) if a != b)
    leaving = Counter()
    for (a, _), n in moves.items():
        leaving[a] += n
    durations = []
    for s in timed:
        run = 0.0
        for i, (c, length) in enumerate(s.chords):
            run += length
            if i == len(s.chords) - 1 or s.chords[i + 1][0] != c:
                durations.append(run)
                run = 0.0
    bass = Counter(f for s in timed for f in s.bass_figures)
    bass_total = sum(bass.values()) or 1
    rhythms = Counter(r for s in timed for r in s.bass_rhythms)
    riffs = Counter(f for s in timed for f in s.riff_figures)
    steps = Counter(min(max(st, -5), 5) for s in stats for st in s.steps)
    step_total = sum(steps.values()) or 1
    out = {
        "songs": len(stats), "timed": len(timed),
        "keys": Counter(s.key.split()[1] for s in stats).most_common(),
        "in_scale_median": _median([s.in_scale for s in stats]),
        "tempo_median": _median([s.tempo for s in stats if s.tempo]),
        "bars_per_chord_median": _median(durations),
        "loops": Counter(s.loop for s in timed if s.loop).most_common(top),
        "moves": [(f"{a} -> {b}", n, round(n / leaving[a], 2)) for (a, b), n in moves.most_common(top)],
        "chords": Counter(c for seq in chord_seq for c in seq).most_common(top),
        "bass_figures": [(f, n, round(n / bass_total, 3)) for f, n in bass.most_common(top)],
        "bass_rhythms": rhythms.most_common(top),
        "riff_figures": riffs.most_common(top),
        "riff_steps": {str(k): round(v / step_total, 3) for k, v in sorted(steps.items())},
        "riff_range_median": _median([s.riff_range for s in stats if s.steps]),
        "repeat_rate_median": _median([s.repeat_rate for s in timed if s.repeat_rate]),
        "notes_per_bar_median": _median([s.notes_per_bar for s in timed if s.notes_per_bar]),
    }
    if style:
        ours = engine_patterns(style)
        covered = sum(n for f, n in bass.items() if f in ours["bass_figures"])
        out["engine"] = {
            **ours,
            "bass_coverage": round(covered / bass_total, 3),
            "bass_missing": [(f, n) for f, n in bass.most_common(top) if f not in ours["bass_figures"]],
            "loops_in_library": [(loop, n, in_library(loop, set(ours["progressions"]))) for loop, n in out["loops"]],
            "modes_outside": [m for m, _ in out["keys"] if m not in ours["modes"]],
        }
    return out


def _median(values: list[float]) -> float:
    values = sorted(v for v in values if v is not None)
    return values[len(values) // 2] if values else 0.0


def report(c: dict, stats: list[SongStats]) -> str:
    lines = [f"{c['songs']} songs ({c['timed']} with rhythm), median {c['tempo_median']:g} BPM; "
             f"{c['in_scale_median']:.0%} of the notes in the key (median)"]
    lines.append("Modes: " + ", ".join(f"{m} {n}" for m, n in c["keys"]))
    lines.append(f"A chord lasts {c['bars_per_chord_median']:g} bars (median)")
    for title, key, rows in (("Progressions (the loop each song cycles)", "loops", c["loops"]),
                             ("Chord moves (count, share of moves from that chord)", "moves", c["moves"]),
                             ("Bass figures (relative to the chord's root; count, share of bars)", "", c["bass_figures"]),
                             ("Bass rhythms (x a note, _ held, ~ rest)", "", c["bass_rhythms"]),
                             ("Riff figures (against the chord)", "", c["riff_figures"])):
        lines.append(f"\n{title}:")
        lines += [f"  {'  '.join(str(x) for x in row)}" for row in rows] or ["  (none)"]
    lines.append("\nRiff steps between notes (scale steps, ±5 = a 6th or more): "
                 + "  ".join(f"{k}:{v:.0%}" for k, v in c["riff_steps"].items()))
    lines.append(f"Riff range {c['riff_range_median']:g} steps; a bar repeats one of the two before "
                 f"{c['repeat_rate_median']:.0%} of the time; {c['notes_per_bar_median']:g} notes a bar (medians)")
    if "engine" in c:
        e = c["engine"]
        lines.append("\nAgainst the engine:")
        lines.append(f"  its bass figures cover {e['bass_coverage']:.0%} of the corpus's bass bars")
        lines += [f"  missing: {f}  ({n} bars)" for f, n in e["bass_missing"]]
        lines += [f"  loop {loop} ({n} songs): {'in' if ok else 'NOT in'} the engine's progression library"
                  for loop, n, ok in e["loops_in_library"]]
        lines.append(f"  engine: {e['bars_per_chord']} bars per chord, modes {', '.join(e['modes'])}"
                     + (f"; the corpus also uses {', '.join(e['modes_outside'])}" if e["modes_outside"] else ""))
    lines.append("\nSongs:")
    lines += [f"  {s.title[:40]:40s} {s.key:14s} {s.in_scale:.0%} in key  {s.loop or '-'}" for s in stats]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="files or folders of tabs and scores")
    ap.add_argument("--style", help="compare with the engine's patterns for this style (e.g. darkwave)")
    ap.add_argument("--json", help="also write the corpus statistics here")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args(argv)
    stats = []
    for path in files(args.paths):
        try:
            song = load(path)
        except Exception as e:                       # one bad file doesn't stop the corpus
            print(f"skipped {path}: {e}", file=sys.stderr)
            continue
        if song.tracks:
            stats.append(analyse(song))
        else:
            print(f"skipped {path}: no pitched notes", file=sys.stderr)
    if not stats:
        print("no songs read", file=sys.stderr)
        return 1
    c = corpus(stats, args.style, args.top)
    print(report(c, stats))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"corpus": c, "songs": [s.__dict__ for s in stats]}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
