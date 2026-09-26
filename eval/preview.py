#!/usr/bin/env python3
"""
Offline preview: render what the engine would play to a WAV file without
SuperCollider or Tidal, using simple additive tones.

It interprets exactly the mini-notation subset the engine's templates emit
(`slow k $ note "<[..] [..]>"`, chords `[a,b,c]`, rests `~`, holds `_`,
`# legato x`, `# gain g`), so it doubles as an end-to-end check that the
generated patterns line up harmonically when played together. Drums and the
vinyl texture are skipped (unpitched).

    python -m eval.preview --style lofi --seed 3 --out preview.wav
    python -m eval.analyze preview.wav --key "<key printed by preview>"

This is a sketch of the harmony and timing, not of the final sound: the real
timbres come from the SynthDefs in tidal/synthdefs.scd.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SR = 22050


def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in body.strip():
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == " " and depth == 0:
            if cur:
                parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def parse_statement(line: str) -> dict | None:
    """Extract the pitched pattern from one engine statement."""
    m = re.search(r'note (?:\(slow \d+ )?"<(.*?)>"', line)
    if not m or "ys_vinyl" in line:
        return None
    slow = re.search(r"slow (\d+) \$ note|note \(slow (\d+) ", line)
    legato = re.search(r"# legato ([\d.]+)", line)
    gain = re.search(r"# gain ([\d.]+)", line)
    return {
        "entries": _split_top(m.group(1)),
        "slow": int(slow.group(1) or slow.group(2)) if slow else 1,
        "legato": float(legato.group(1)) if legato else 1.0,
        "gain": float(gain.group(1)) if gain else 0.9,
    }


def events_for_cycle(pattern: dict, cycle: int) -> list[tuple[float, float, list[int]]]:
    """(start, duration, notes) in cycles, for one cycle of a pattern."""
    k = pattern["slow"]
    entry = pattern["entries"][(cycle // k) % len(pattern["entries"])]
    offset = cycle % k  # which part of a slowed entry we're in
    # "[a b c]" is a step sequence; "[a,b,c]" or "a" is a single chord/note.
    inner = entry[1:-1] if entry.startswith("[") and entry.endswith("]") else entry
    steps = _split_top(inner) if len(_split_top(inner)) > 1 else [entry]
    n = len(steps)
    out = []
    i = 0
    while i < n:
        tok = steps[i]
        if tok in ("~", "_"):
            i += 1
            continue
        length = 1
        while i + length < n and steps[i + length] == "_":
            length += 1
        notes = [int(x) for x in re.findall(r"-?\d+", tok)]
        start = (i / n) * k - offset
        dur = (length / n) * k
        if -dur < start < 1:
            out.append((start, dur, notes))
        i += length
    return out


def synth_note(midi: float, seconds: float, sr: int = SR) -> np.ndarray:
    n = max(1, int(seconds * sr))
    t = np.arange(n) / sr
    f = 440 * 2 ** ((midi - 69) / 12)
    tone = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)
    attack = min(n, int(0.02 * sr))
    env = np.ones(n)
    env[:attack] = np.linspace(0, 1, attack)
    env *= np.exp(-t / max(0.4, seconds))
    return tone * env


def render(sections: list[tuple[list[str], int, float]], sr: int = SR) -> np.ndarray:
    """sections: (statements, cycles, cps)."""
    total_seconds = sum(c / cps for _, c, cps in sections) + 4
    out = np.zeros(int(total_seconds * sr))
    t0 = 0.0
    for statements, cycles, cps in sections:
        patterns = [p for p in (parse_statement(s) for s in statements) if p]
        for cycle in range(cycles):
            for p in patterns:
                for start, dur, notes in events_for_cycle(p, cycle):
                    begin = t0 + (cycle + start) / cps
                    if begin < 0:
                        continue
                    seconds = dur * p["legato"] / cps
                    amp = (p["gain"] ** 4) * 0.12
                    for note in notes:
                        tone = synth_note(note + 60, seconds, sr) * amp
                        i = int(begin * sr)
                        j = min(len(out), i + len(tone))
                        out[i:j] += tone[: j - i]
        t0 += cycles / cps
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.8


def write_wav(path: str, samples: np.ndarray, sr: int = SR) -> None:
    data = (samples * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def preview_piece(style: str | None, seed: int, max_cycles_per_section: int = 8):
    from music.director import Director
    from music.engine import MusicEngine

    rng = random.Random(seed)
    director = Director(None, rng, style=style, log=lambda m: None)
    piece = director.next_piece(None)
    engine = MusicEngine(lambda lines: None, director, log=lambda m: None)
    sections = []
    active: dict[str, str] = {}
    start = 0
    for idx, section in enumerate(piece.sections):
        arrangement = director.arrange_section(piece, idx, None)
        rendered = engine.render_section(piece, idx, arrangement, active, start=start)
        start += section.bars
        active = rendered.statements
        cycles = min(section.bars, max_cycles_per_section)
        cycles -= cycles % piece.period or 0
        sections.append((list(rendered.statements.values()), max(cycles, piece.period), piece.cps))
    return piece, sections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--style", choices=["lofi", "ambient", "downtempo", "piano", "deephouse", "trance"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="preview.wav")
    args = parser.parse_args()
    piece, sections = preview_piece(args.style, args.seed)
    write_wav(args.out, render(sections))
    print(f"{piece.title}: {piece.key}, {piece.progression}, {piece.bpm:.0f} BPM -> {args.out}")


if __name__ == "__main__":
    main()
