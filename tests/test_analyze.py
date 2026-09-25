import shutil
import wave

import numpy as np
import pytest

from eval.analyze import analyze, verdict

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SR = 44100


def write_wav(path, samples):
    data = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    stereo = np.repeat(data[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def tones(midis, seconds=6.0, amp=0.1):
    t = np.arange(int(SR * seconds)) / SR
    out = np.zeros_like(t)
    for m in midis:
        f = 440 * 2 ** ((m - 69) / 12)
        out += amp * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))
    return out


def test_in_key_progression_scores_high(tmp_path):
    # i - VI - III - VII in C minor, 1.5 s each
    chords = [[48, 60, 63, 67], [44, 60, 63, 68], [51, 58, 63, 67], [46, 58, 62, 65]]
    audio = np.concatenate([tones(c, 1.5) for c in chords])
    path = tmp_path / "cminor.wav"
    write_wav(path, audio)
    m = analyze(str(path), "C minor")
    assert m["key_conformance"] > 0.9
    assert m["estimated_key"] in ("C minor", "Eb major")
    assert not [p for p in verdict(m) if "key" in p]


def test_chromatic_cluster_scores_low(tmp_path):
    path = tmp_path / "cluster.wav"
    write_wav(path, tones(range(60, 72), 4))
    m = analyze(str(path), "C minor")
    assert m["key_conformance"] < 0.7
    assert any("key conformance" in p for p in verdict(m))


def test_clipping_and_loudness_flagged(tmp_path):
    path = tmp_path / "loud.wav"
    write_wav(path, tones([60, 64, 67], 4, amp=0.9))
    m = analyze(str(path))
    problems = verdict(m)
    assert m["clipped_ratio"] > 0
    assert any("clipped" in p for p in problems)
