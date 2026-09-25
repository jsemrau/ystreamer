#!/usr/bin/env python3
"""
Objective quality metrics for a stretch of stream audio.

    python -m eval.analyze clip.wav --key "C minor"

- Integrated loudness (LUFS), loudness range and true peak via ffmpeg's
  EBU R128 meter. YouTube normalises to about -14 LUFS.
- Clipped-sample ratio.
- Key conformance: share of pitched spectral energy (65 Hz - 2 kHz) that falls
  on the key's pitch classes, plus the best-matching key by Krumhansl-Kessler
  profile correlation. Harmonic music in the planned key scores ~0.8+.
- Silence ratio.

Only needs ffmpeg and numpy.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import venv_guard  # noqa: E402

venv_guard.ensure_venv()

import numpy as np  # noqa: E402

from music.theory import DISPLAY_NAMES, Key  # noqa: E402

SAMPLE_RATE = 22050

# Krumhansl-Kessler key profiles.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def ebur128(path: str) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = proc.stderr
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s*(-?[\d.]+|-inf)", summary)
        if not m or m.group(1) == "-inf":
            return None
        return float(m.group(1))

    return {"lufs": grab("I"), "lra": grab("LRA"), "true_peak_dbfs": grab("Peak")}


def decode(path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(sample_rate),
         "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def decode_stereo_peak(path: str) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def chroma(samples: np.ndarray, sample_rate: int = SAMPLE_RATE, frame: int = 8192, hop: int = 4096) -> np.ndarray:
    if len(samples) < frame:
        samples = np.pad(samples, (0, frame - len(samples)))
    window = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1 / sample_rate)
    band = (freqs >= 65) & (freqs <= 2000)
    midi = 69 + 12 * np.log2(freqs[band] / 440.0)
    pcs = np.round(midi).astype(int) % 12
    # Weight bins close to an equal-tempered pitch more than bins in between.
    closeness = 1 - 2 * np.abs(midi - np.round(midi))
    total = np.zeros(12)
    for start in range(0, len(samples) - frame + 1, hop):
        spectrum = np.abs(np.fft.rfft(samples[start:start + frame] * window))[band] ** 2
        # Keep spectral peaks only (sinusoidal components, not noise floor).
        threshold = np.median(spectrum) * 10
        mask = spectrum > threshold
        np.add.at(total, pcs[mask], (spectrum * closeness.clip(0))[mask])
    return total


def key_scores(profile: np.ndarray) -> list[tuple[float, str]]:
    out = []
    for root in range(12):
        for mode, template in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            corr = float(np.corrcoef(profile, np.roll(template, root))[0, 1])
            out.append((corr, f"{DISPLAY_NAMES[root]} {mode}"))
    return sorted(out, reverse=True)


def analyze(path: str, key: str | None = None) -> dict:
    result = ebur128(path)
    raw = decode_stereo_peak(path)
    result["clipped_ratio"] = float(np.mean(np.abs(raw) >= 0.999)) if raw.size else 0.0
    mono = decode(path)
    rms = np.sqrt(np.convolve(mono ** 2, np.ones(2048) / 2048, mode="valid")) if mono.size > 2048 else np.array([0.0])
    result["silence_ratio"] = float(np.mean(rms < 10 ** (-55 / 20)))
    profile = chroma(mono)
    energy = profile.sum()
    result["chroma"] = [round(float(x / energy), 4) if energy else 0.0 for x in profile]
    ranked = key_scores(profile) if energy else []
    result["estimated_key"] = ranked[0][1] if ranked else None
    if key and energy:
        k = Key.parse(key)
        result["key"] = k.name
        result["key_conformance"] = round(float(sum(profile[pc] for pc in k.pitch_classes) / energy), 4)
    return result


def verdict(metrics: dict, min_conformance: float = 0.7) -> list[str]:
    """Human-readable problems; empty list = fine."""
    problems = []
    lufs = metrics.get("lufs")
    if lufs is not None and not -20 <= lufs <= -10:
        problems.append(f"loudness {lufs:.1f} LUFS outside -20..-10")
    peak = metrics.get("true_peak_dbfs")
    if peak is not None and peak > -0.5:
        problems.append(f"true peak {peak:.1f} dBFS (risk of clipping)")
    if metrics.get("clipped_ratio", 0) > 1e-4:
        problems.append(f"{metrics['clipped_ratio'] * 100:.3f}% samples clipped")
    if metrics.get("silence_ratio", 0) > 0.5:
        problems.append(f"{metrics['silence_ratio'] * 100:.0f}% silence")
    conf = metrics.get("key_conformance")
    if conf is not None and conf < min_conformance:
        problems.append(f"key conformance {conf:.2f} < {min_conformance}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--key", help='Expected key, e.g. "C minor"')
    args = parser.parse_args()
    metrics = analyze(args.path, args.key)
    metrics["problems"] = verdict(metrics)
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
