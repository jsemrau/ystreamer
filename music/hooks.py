"""
Hooks: what makes a melody memorable, measured, and a writer that uses it.

A hook people remember has one idea, a rhythm of its own, and a shape:

- a motif: a bar's rhythm stated, repeated and varied (AA'AB, call and answer)
- one high point, reached once, about two thirds of the way through
- mostly steps, a leap or two, each leap turning back by a step
- a long held note, a rest to breathe, a note off the beat
- the call ends open, the answer ends home (the root or the third)
- within an octave and a third

`analyze` checks a tune against these and says, in words, what it misses;
`score` is the share it passes. `write_hook` writes one: a rhythmic motif
from a few archetypes, developed over the progression (stated, varied,
answered), its notes the chord's tones nearest an arch-shaped contour, and
the best-scoring of a couple of dozen tries.

Tunes here are written against the key ("relative": "key", 0 = the key's
tonic) across the progression, bar by bar: a note on a beat is one of the
chord's tones in that bar (key_harmony_problem), so the tune can hold a note
while the chord changes under it, which a tune that follows each chord's root
can't.
"""

from __future__ import annotations

import random
import re

from .livecode import phrase_steps

GOOD = 0.75                     # a hook scoring below this is written again
_NOTE_RE = re.compile(r"^-?\d+$")
# A bar's rhythm on 8 steps: x a new note, _ held, ~ a rest. The motifs
# (A), and the cadences that answer them (B).
RHYTHMS = ["x _ _ x _ x _ ~", "x _ x _ _ _ x ~", "~ x _ x _ _ x _", "x _ _ _ x x _ ~", "x x _ _ x _ _ ~",
           "~ ~ x _ x _ x _", "x _ x x _ _ _ ~", "x _ _ ~ x _ x _", "x _ x _ x _ _ ~", "~ x x _ _ _ x _"]
CADENCES = ["x _ _ _ x _ _ ~", "x _ x _ _ _ ~ ~", "x _ _ _ _ _ ~ ~", "x _ _ x _ _ _ ~"]


# ------------------------------------------------------------- reading

def parse(pattern: str) -> list[list[str]] | None:
    """A tune's bars as tokens (a degree, ~ or _), or None if it has nested
    steps, repeats or anything else this can't read."""
    bars = phrase_steps(pattern)
    if not bars:
        return None
    out = []
    for bar in bars:
        tokens = [t for t, _ in bar]
        if not all(_NOTE_RE.match(t) or t in ("~", "_") for t in tokens):
            return None
        out.append(tokens)
    return out


def events(bars: list[list[str]]) -> list[dict]:
    """The notes in order: bar, step (on an 8-step grid), degree, length (8ths)."""
    out: list[dict] = []
    for b, bar in enumerate(bars):
        scale = 8 / max(1, len(bar))
        for i, t in enumerate(bar):
            if _NOTE_RE.match(t):
                out.append({"bar": b, "step": i * scale, "degree": int(t), "length": scale,
                            "time": b * 8 + i * scale})
            elif t == "_" and out:
                out[-1]["length"] += scale
    return out


def _onsets(bar: list[str]) -> frozenset:
    scale = 8 / max(1, len(bar))
    return frozenset(round(i * scale) for i, t in enumerate(bar) if _NOTE_RE.match(t))


# ------------------------------------------------------------- judging

def analyze(pattern: str, degrees: list[int] | None = None, bars_per_chord: int = 1) -> dict | None:
    """Which of the memorable-hook rules a tune passes: {"checks": {name:
    bool}, "problems": [what it misses, in words], "score": 0-1}; None if the
    tune can't be read. Given the progression (`degrees`, a tune written
    against the key), "home" is the root or third of the chord under the last
    note and "open" anything but the root of the chord under the call's end;
    without it (a tune that follows each chord's root), 0 and 2."""
    progression = degrees
    bars = parse(pattern)
    if bars is None:
        return None
    notes = events(bars)
    if len(notes) < 2:
        return {"checks": {}, "problems": ["it needs more than one note"], "score": 0.0}
    degrees = [n["degree"] for n in notes]
    total = len(bars) * 8
    checks, problems = {}, []

    def check(name: str, ok: bool, problem: str) -> None:
        checks[name] = ok
        if not ok:
            problems.append(problem)

    first = _onsets(bars[0])
    echoes = [b for b in bars[1:] if first and len(first & _onsets(b)) / len(first | _onsets(b)) >= 0.6]
    check("motif", len(bars) >= 2 and bool(echoes),
          "no motif: let a bar's rhythm come back (AA'AB: stated, repeated, varied, answered)")
    top = max(degrees)
    peaks = [n for n in notes if n["degree"] == top]
    where = peaks[0]["time"] / total
    check("high point", len(peaks) == 1 and 0.3 <= where <= 0.85,
          "no single high point: reach the top note once, about two thirds of the way through")
    moves = [b - a for a, b in zip(degrees, degrees[1:])]
    steps = sum(abs(m) <= 2 for m in moves) / max(1, len(moves))
    check("steps", steps >= 0.6, "it leaps around: move mostly by step (a 2nd or a 3rd)")
    same = sum(m == 0 for m in moves) / max(1, len(moves))
    check("moves", same <= 0.25, "it sits on one note: let it move (a repeated note now and then, not most of the time)")
    leaps = [i for i, m in enumerate(moves) if abs(m) >= 3]
    turned = sum(1 for i in leaps if i + 1 >= len(moves) or (moves[i + 1] * moves[i] < 0 and abs(moves[i + 1]) <= 2))
    check("leaps turn", not leaps or turned / len(leaps) >= 0.75,
          "a leap should turn back by a step (up a 5th, then down a step)")
    check("held note", any(n["length"] >= 3 for n in notes), "no long note: hold one (_ _ _) to sing out")
    check("rest", any("~" in bar for bar in bars), "no rest: leave a breath (~)")
    check("off the beat", any(round(n["step"]) % 2 for n in notes), "no note off the beat: give it a lilt")
    def chord_root(bar: int) -> int:
        return chord_at(bar, progression, bars_per_chord) % 7 if progression else 0

    last = notes[-1]
    home = {chord_root(last["bar"]), (chord_root(last["bar"]) + 2) % 7}
    check("ends home", last["degree"] % 7 in home,
          "it doesn't end home: end on the root or the third of the chord under the last note")
    if len(bars) >= 2:
        half = [n for n in notes if n["time"] < total / 2]
        check("call ends open", bool(half) and half[-1]["degree"] % 7 != chord_root(half[-1]["bar"]),
              "the call (first half) ends on the chord's root: end it open (the 3rd or 5th) so the answer resolves it")
    check("range", top - min(degrees) <= 9, "too wide: keep it within an octave and a third")
    return {"checks": checks, "problems": problems, "score": sum(checks.values()) / len(checks)}


def score(pattern: str, degrees: list[int] | None = None, bars_per_chord: int = 1) -> float | None:
    a = analyze(pattern, degrees, bars_per_chord)
    return a["score"] if a else None


def summary(pattern: str, degrees: list[int] | None = None, bars_per_chord: int = 1) -> str:
    """"melody 0.82 (no rest; ...)" for a judge or a log."""
    a = analyze(pattern, degrees, bars_per_chord)
    if not a:
        return ""
    return f"melody {a['score']:.2f}" + (f" ({'; '.join(p.split(':')[0] for p in a['problems'])})"
                                          if a["problems"] else "")


def chord_at(bar: int, degrees: list[int], bars_per_chord: int) -> int:
    return degrees[(bar // max(1, bars_per_chord)) % len(degrees)]


def bar_chords(progression: str, degrees: list[int], bars_per_chord: int) -> str:
    """Which chord sounds in which bar, and its tones as key degrees: "bars
    1-2: i (tones 0 2 4), bars 3-4: VI (5 0 2) ..." (for the Director)."""
    names = progression.split("-") if progression.count("-") + 1 == len(degrees) else [str(d) for d in degrees]
    out = []
    for k, (name, d) in enumerate(zip(names, degrees)):
        first, last = k * bars_per_chord + 1, (k + 1) * bars_per_chord
        span = f"bar {first}" if first == last else f"bars {first}-{last}"
        out.append(f"{span}: {name} (tones {' '.join(str((d + s) % 7) for s in (0, 2, 4))})")
    return ", ".join(out)


def tones(chord: int) -> set[int]:
    """A chord's tones as key degrees (mod 7): root, third, fifth, seventh."""
    return {(chord + k) % 7 for k in (0, 2, 4, 6)}


def key_harmony_problem(pattern: str, degrees: list[int], bars_per_chord: int = 1) -> str | None:
    """Why a tune written against the key clashes with the progression, or
    None. Bar by bar: a note on a beat is one of that bar's chord tones; a
    note between the beats may be any scale note that steps from or to the
    note beside it (passing, neighbour), not held on; a note held over into a
    new chord must be one of its tones too. The tune's bars must fit the
    progression's (its length divides the progression's)."""
    bars = phrase_steps(pattern)
    if bars is None:
        return "the tune can't be read: one bar per [..] in <[..] [..]>, of degrees, ~ and _"
    if not degrees:
        return None
    length = len(degrees) * max(1, bars_per_chord)
    if length % len(bars):
        return f"{len(bars)} bars don't fit a progression of {length} bars: write {length} (or a divisor)"
    clash, loose, held = [], [], []
    # (A tune shorter than the progression repeats over it: each pass is checked.)
    for pb in range(length):
        b = pb % len(bars)
        bar = bars[b]
        chord = tones(chord_at(pb, degrees, bars_per_chord))
        seq = [t for t, _ in bar]
        for i, (tok, strong) in enumerate(bar):
            if tok == "_" and i == 0:
                prev = next((t for t in reversed([t for t, _ in bars[b - 1]]) if _NOTE_RE.match(t)), None)
                if prev is not None and int(prev) % 7 not in chord:
                    held.append(f"{prev} into bar {pb + 1}")
            if not _NOTE_RE.match(tok) or int(tok) % 7 in chord:
                continue
            d = int(tok)
            if strong:
                clash.append(f"{d} in bar {pb + 1}")
                continue
            prev = seq[i - 1] if i else None
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            by_step = any(t and _NOTE_RE.match(t) and abs(int(t) - d) == 1 for t in (prev, nxt))
            if not by_step or nxt == "_":
                loose.append(f"{d} in bar {pb + 1}")
    clash, held, loose = (list(dict.fromkeys(x)) for x in (clash, held, loose))
    if clash:
        return (f"{', '.join(clash)} fall on the beat outside the bar's chord: on the beats, play that bar's "
                "chord tones (key degrees: its root, third, fifth)")
    if held:
        return f"{', '.join(held)}: a note held over a chord change must belong to the new chord too"
    if loose:
        return (f"{', '.join(loose)} between the beats must step from or to the note beside it "
                "(a passing or neighbour note) and not be held")
    return None


def refit(pattern: str, degrees: list[int], bars_per_chord: int = 1) -> str:
    """The tune over other chords (the breakdown's): the same rhythm and
    shape, each note that would clash moved to the nearest tone of its bar's
    new chord (a note held into a bar whose chord lacks it is struck again
    there). The tune stays recognisable; the harmony stays clean. Unchanged
    if it already fits, or if its bars don't fit the progression."""
    bars = parse(pattern)
    if not bars or not degrees or (len(degrees) * max(1, bars_per_chord)) % len(bars):
        return pattern
    if key_harmony_problem(pattern, degrees, bars_per_chord) is None:
        return pattern
    out, prev = [], None
    for b, bar in enumerate(bars):
        chord = tones(chord_at(b, degrees, bars_per_chord))
        per_beat = max(1, len(bar) // 4)
        tokens = []
        for i, t in enumerate(bar):
            if t == "_" and i == 0 and prev is not None and prev % 7 not in chord:
                prev = _nearest(prev, chord, prev)
                tokens.append(str(prev))
                continue
            if _NOTE_RE.match(t):
                d = int(t)
                if d % 7 not in chord:
                    strong = i % per_beat == 0
                    nxt = bar[i + 1] if i + 1 < len(bar) else None
                    if strong or nxt == "_" or not any(
                            x is not None and _NOTE_RE.match(str(x)) and abs(int(x) - d) == 1
                            for x in (prev, nxt)):
                        d = _nearest(d, chord, None)
                prev = d
                tokens.append(str(d))
            else:
                tokens.append(t)
        out.append(tokens)
    fitted = "<" + " ".join("[" + " ".join(t) + "]" for t in out) + ">"
    return fitted if key_harmony_problem(fitted, degrees, bars_per_chord) is None else pattern


# ------------------------------------------------------------- writing

def _rhythm(text: str) -> list[str]:
    return text.split()


def _vary(rhythm: list[str], rng: random.Random) -> list[str]:
    """A' : the motif with its last note moved or one note added in a rest."""
    r = list(rhythm)
    onsets = [i for i, t in enumerate(r) if t == "x"]
    if len(onsets) > 2 and rng.random() < 0.5:
        r[onsets[-1]] = "_" if r[onsets[-1] - 1] != "~" else "~"
    else:
        rests = [i for i, t in enumerate(r) if t == "~" and i > 0]
        if rests:
            r[rests[0]] = "x"
    return r


def _plan(bars: int, rng: random.Random) -> list[list[str]]:
    a = _rhythm(rng.choice(RHYTHMS))
    a2 = _vary(a, rng)
    b = _rhythm(rng.choice(CADENCES))
    b_open = _rhythm(rng.choice([c for c in CADENCES if c.split() != b] or CADENCES))
    if bars <= 2:
        return [a, b][:max(1, bars)]
    if bars <= 4:
        return ([a, a2, a, b] * 2)[:bars]
    half = bars // 2
    first = ([a, a2, a, b_open] * 4)[:half]
    return first + ([a, a2, a, b] * 4)[:bars - half - 1] + [b]


def _contour(t: float, low: int, high: int, end: int, peak: float = 0.65, start: int | None = None) -> float:
    """The arch: from `start` (the low note) rising to the high point at
    `peak`, then down towards `end`."""
    start = low if start is None else start
    if t <= peak:
        return start + (high - start) * (t / peak)
    return high + (end - high) * ((t - peak) / (1 - peak))


def _nearest(target: float, allowed: set[int], prev: int | None, lo: int = -3, hi: int = 11) -> int:
    best, cost = None, 1e9
    for d in range(lo, hi + 1):
        if d % 7 not in allowed:
            continue
        c = abs(d - target) + (0.5 * abs(d - prev) if prev is not None else 0) + (3.0 if d == prev else 0)
        if c < cost:
            best, cost = d, c
    return best if best is not None else 0


def _one(rng: random.Random, degrees: list[int], bars_per_chord: int) -> str:
    length = len(degrees) * max(1, bars_per_chord)
    bars = length if length <= 8 else 8
    while length % bars:
        bars -= 1
    rhythms = _plan(bars, rng)
    low, high = rng.choice([(0, 7), (2, 7), (0, 6), (2, 9), (-1, 6), (-2, 4), (4, 9), (0, 4)])
    # Where it peaks, and whether it starts low and climbs or starts high and dips first.
    peak = rng.choice([0.55, 0.62, 0.7, 0.75])
    start = rng.choice([low, low, (low + high) // 2, high - 1])
    ending = rng.choice([0, 2, low])
    total = bars * 8
    out, prev, motif = [], None, None          # motif: the first bar's rhythm and intervals
    for b, rhythm in enumerate(rhythms):
        chord = tones(chord_at(b, degrees, bars_per_chord))
        root = chord_at(b, degrees, bars_per_chord) % 7
        onsets = [i for i, t in enumerate(rhythm) if t == "x"]
        last_bar = b == bars - 1
        call_end = bars >= 2 and b == bars // 2 - 1
        restates = motif is not None and len(onsets) == len(motif[1]) + 1 and not last_bar and not call_end
        tokens, notes = [], []
        for k, i in enumerate(onsets):
            target = _contour((b * 8 + i) / total, low, high, ending, peak, start)
            allowed = chord
            if last_bar and k == len(onsets) - 1:
                allowed = {root, (root + 2) % 7}               # the answer ends home (its chord's root or third)
            elif call_end and k == len(onsets) - 1:
                allowed = chord - {root}                       # the call ends open
            if restates and k:
                # The motif again: its interval, moved to this chord's nearest tone.
                target = notes[-1] + motif[1][k - 1]
                prev = _nearest(target, allowed if k == len(onsets) - 1 else chord | {(notes[-1] + motif[1][k - 1]) % 7},
                                notes[-1])
                if prev % 7 not in chord and (k + 1 >= len(onsets) or i % 2 == 0):
                    prev = _nearest(target, chord, notes[-1])  # (a passing note only between the beats)
            else:
                prev = _nearest(target, allowed, prev)
            notes.append(prev)
        if motif is None and len(notes) >= 2:
            # (A motif that moves: a repeated note in it becomes a step, or every
            # restatement would sit on one note.)
            motif = (rhythm, [(b2 - a2) or rng.choice([1, -1, 2]) for a2, b2 in zip(notes, notes[1:])])
        it = iter(notes)
        tokens = [str(next(it)) if t == "x" else t for t in rhythm]
        # (A bar can't start by holding a note the new chord doesn't have.)
        if tokens[0] == "_" and out:
            tokens[0] = "~"
        out.append(tokens)
    _one_peak(out, degrees, bars_per_chord, total)
    return "<" + " ".join("[" + " ".join(t) + "]" for t in out) + ">"


def _one_peak(bars: list[list[str]], degrees: list[int], bars_per_chord: int, total: int) -> None:
    """The highest note once: keep the one nearest two thirds of the way, the
    others step down to their chord's next lower tone."""
    places = [(b, i) for b, bar in enumerate(bars) for i, t in enumerate(bar) if _NOTE_RE.match(t)]
    if not places:
        return
    top = max(int(bars[b][i]) for b, i in places)
    peaks = [(b, i) for b, i in places if int(bars[b][i]) == top]
    keep = min(peaks, key=lambda p: abs((p[0] * 8 + p[1]) / total - 0.65))
    for b, i in peaks:
        if (b, i) == keep:
            continue
        chord = tones(chord_at(b, degrees, bars_per_chord))
        lower = next((d for d in range(top - 1, top - 6, -1) if d % 7 in chord), top - 1)
        bars[b][i] = str(lower)


def write_hook(rng: random.Random, degrees: list[int], bars_per_chord: int = 1, recent: list[str] | None = None,
               tries: int = 48) -> str:
    """A memorable hook for this progression, written against the key: the
    best-scoring of `tries` that fit the chords and aren't close to a recent
    tune (plan.hook_similarity)."""
    from .plan import HOOK_SIMILAR, hook_similarity

    fresh, stale = [], []          # (score, tune) / (closeness to a recent tune, -score, tune)
    for _ in range(tries):
        tune = _one(rng, degrees, bars_per_chord)
        if key_harmony_problem(tune, degrees, bars_per_chord):
            continue
        s = score(tune, degrees, bars_per_chord) or 0.0
        close = max((hook_similarity(tune, r) for r in recent or []), default=0.0)
        if close < HOOK_SIMILAR:
            fresh.append((s, tune))
        else:
            stale.append((close, -s, tune))
    if fresh:
        return max(fresh)[1]
    if stale:
        return min(stale)[2]           # (none fresh enough: the least like a recent one)
    return _one(rng, degrees, bars_per_chord)


def describe(pattern: str) -> str:
    """A tune as rules, not notes (for the Director to learn from without
    copying): its bars, motif rhythm, high point, motion, how it ends."""
    bars = parse(pattern)
    if not bars:
        return ""
    notes = events(bars)
    if not notes:
        return ""
    degrees = [n["degree"] for n in notes]
    top = max(degrees)
    peak_bar = next(n["bar"] for n in notes if n["degree"] == top) + 1
    first = bars[0]
    rhythm = " ".join("x" if _NOTE_RE.match(t) else t for t in first)
    moves = [b - a for a, b in zip(degrees, degrees[1:])]
    stepwise = sum(abs(m) <= 2 for m in moves) / max(1, len(moves))
    longest = max(n["length"] for n in notes)
    return (f"{len(bars)} bars, about {len(notes) / len(bars):.0f} notes a bar, its first bar's rhythm "
            f"\"{rhythm}\", the high point in bar {peak_bar}, {'mostly steps' if stepwise >= 0.6 else 'leaps'}, "
            f"a note held {longest:g} 8ths, ending on degree {degrees[-1] % 7}")


# ------------------------------------------------------------- learning

def _ratings_path() -> str:
    import os

    import config

    return os.path.join(config.RUNTIME_DIR, "hook_ratings.jsonl")


def record_rating(hook: dict, rating: int, title: str = "", degrees: list[int] | None = None,
                  bars_per_chord: int = 1) -> None:
    """The host's 👍 / 👎 while a hook played, with its melody score."""
    import json
    import os
    import time

    if not hook or not hook.get("pattern"):
        return
    rel = hook.get("relative") == "key"
    entry = {"t": time.time(), "rating": rating, "title": title, "hook": hook["pattern"],
             "relative": hook.get("relative", "chord"),
             "score": score(hook["pattern"], degrees if rel else None, bars_per_chord)}
    os.makedirs(os.path.dirname(_ratings_path()), exist_ok=True)
    with open(_ratings_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def liked(n: int = 3, days: float = 30.0) -> list[str]:
    """The hooks the host liked most lately (net 👍, the last `days`), as rules
    (describe), so the Director learns what worked without copying it."""
    import json
    import os
    import time

    if not os.path.exists(_ratings_path()):
        return []
    net: dict[str, int] = {}
    since = time.time() - days * 86400
    with open(_ratings_path(), encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("t", 0) >= since and e.get("hook"):
                net[e["hook"]] = net.get(e["hook"], 0) + (1 if e.get("rating", 0) > 0 else -1)
    best = [h for h, v in sorted(net.items(), key=lambda kv: -kv[1]) if v > 0][:n]
    return [d for d in (describe(h) for h in best) if d]
