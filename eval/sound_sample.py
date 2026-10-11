#!/usr/bin/env python3
"""
A minute of how the stream sounds, before and after the sound fixes: the
same 32 bars of our own ambient house (126 BPM, D major, I-iii-vi-IV, the
riff and chords of eval/sound_preview's ambient-house demos), arranged as a
track plays: an intro, the groove landing, the full section, a break, the
return.

    python -m eval.sound_sample
    # recordings/preview/58_sample_after.wav    width (~ysWide) and the human feel (livecode.HUMANIZE)
    # recordings/preview/59_sample_before.wav   the same, mono and exactly on the grid

The synths are rendered offline with the stream's SynthDefs (scsynth NRT);
the hats and the clap are the stream's own samples (Dirt-Samples hh27,
808oh, cp), mixed here hit by hit, with the stream's velocity and timing
variation in the after version. (The synths' velocity variation, a
decibel or so, isn't rendered: NRT synths have no gain; their timing is.)
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import sound_preview as sp  # noqa: E402
from music.livecode import HUMANIZE  # noqa: E402

BARS = 32
BAR = sp.AH_BAR
SR = 48000
SAMPLES = os.path.expanduser("~/.local/share/SuperCollider/downloaded-quarks/Dirt-Samples")
# Where each part plays: (first bar, last bar + 1) spans.
GROOVE = [(8, 24), (28, 32)]
FULL = [(16, 24), (28, 32)]
ALL = [(0, 32)]


def _in(bar: int, spans) -> bool:
    return any(a <= bar < b for a, b in spans)


def _late(rng: random.Random | None, role: str) -> float:
    """The human feel's timing: up to HUMANIZE[role] seconds late."""
    return rng.uniform(0, HUMANIZE[role][1]) if rng and role in HUMANIZE else 0.0


def synth_stems(rng: random.Random | None) -> dict:
    chord = sp.chord_at
    stems = {
        "pad": [[b * BAR, "ys_softpad", n, BAR * 2 * 1.02, {}] for b in range(0, BARS, 2)
                for n in sp.AH_CHORDS[chord(b)]],
        "choir": [[b * BAR, "ys_revvox", n + 12, BAR * 2 - 0.06, {}] for b in range(0, BARS, 2)
                  for n in sp.AH_CHORDS[chord(b)][1:3]],
        "echolead": [[b * BAR + i * BAR / 8 + _late(rng, "arp"), "ys_pluck",
                      sp.ah_pitch(sp.AH_DEGREES[chord(b)] + step) - 12, BAR / 8 * 0.9, {}]
                     for b in range(4, BARS) for i, step in enumerate(sp.AH_RIFF) if step is not None],
        "kick": [[b * BAR + k * BAR / 4, "ys_kick909", 36, 0.4, {}]
                 for b in range(BARS) if _in(b, GROOVE) for k in range(4)],
        "bass": [[b * BAR + (k * 2 + 1) * BAR / 8, "ys_sub", sp.AH_ROOTS[chord(b)], BAR / 8 * 0.8, {}]
                 for b in range(BARS) if _in(b, GROOVE) for k in range(4)],
        # The e-piano on 1 and the and of 2, the full section only.
        "keys": [[b * BAR + pos * BAR / 8 + _late(rng, "keys"), "ys_epiano", n, BAR / 8 * 1.5, {}]
                 for b in range(BARS) if _in(b, FULL) for pos in (0, 3) for n in sp.AH_CHORDS[chord(b)][:3]],
    }
    return stems


def _load(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _write(path: str, x: np.ndarray) -> None:
    x = x / max(1e-9, np.abs(x).max()) * 0.89
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def sample_stems(tmp: str, rng: random.Random | None) -> dict:
    """The hats (closed on the offbeat 8ths, open on the offbeat quarters in
    the full section) and the clap (2 and 4, the full section), hit by hit."""
    closed, opened, clap = (_load(os.path.join(SAMPLES, d, sorted(os.listdir(os.path.join(SAMPLES, d)))[i]))
                            for d, i in (("hh27", 0), ("808oh", 0), ("cp", 0)))
    length = int((BARS * BAR + 3) * SR)
    hats, claps = np.zeros((length, 2)), np.zeros((length, 2))

    def hit(buf, sound, t, gain):
        at = int((t + _late(rng, "perc")) * SR)
        end = min(length, at + len(sound))
        vel = rng.uniform(HUMANIZE["perc"][0], 1) ** 4 if rng else 1.0       # SuperDirt: amplitude ~ gain^4
        buf[at:end] += sound[:end - at] * gain * vel

    for b in range(BARS):
        if not _in(b, GROOVE):
            continue
        for k in range(4):
            hit(hats, closed, b * BAR + (k + 0.5) * BAR / 4, 0.7)
            if _in(b, FULL):
                hit(hats, opened, b * BAR + (k + 0.5) * BAR / 4, 0.35)
                if k in (1, 3):
                    hit(claps, clap, b * BAR + k * BAR / 4, 1.0)
    paths = {"hats": os.path.join(tmp, "hats.wav"), "clap": os.path.join(tmp, "clap.wav")}
    _write(paths["hats"], hats)
    _write(paths["clap"], claps)
    return paths


def main() -> None:
    sp.LEVELS.update({"keys": -9, "hats": -15, "clap": -12})
    sp.WET.setdefault("pad", sp.WET["bed"])
    os.makedirs(sp.OUT_DIR, exist_ok=True)
    for name, width, rng in (("58_sample_after", "1", random.Random(7)), ("59_sample_before", "0", None)):
        os.environ["YS_WIDTH"] = width
        tmp = sp.render_stems({"sample": synth_stems(rng)}, BAR, bars=BARS)
        stems = {stem: f"{tmp}/sample__{stem}.wav" for stem in synth_stems(rng)}
        stems.update(sample_stems(tmp, random.Random(11) if rng else None))
        print(sp.mix_demo(stems, os.path.join(sp.OUT_DIR, f"{name}.wav")))


if __name__ == "__main__":
    main()
