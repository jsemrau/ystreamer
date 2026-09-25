#!/usr/bin/env python3
"""
Automatic quality loop: every few minutes record a short clip of the stream,
measure it (eval/analyze.py) against the key the engine says it's playing,
log the result to runtime/quality.jsonl, and file an automatic thumbs-down
(runtime/feedback.jsonl) when something is wrong. The Director reads that
feedback and steers away from the progression/presets involved.

    python -m eval.monitor                      # every 10 min, 45 s clips
    YS_QUALITY_INTERVAL=300 python -m eval.monitor
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import venv_guard  # noqa: E402

venv_guard.ensure_venv()

import config  # noqa: E402
from eval.analyze import analyze, verdict  # noqa: E402
from music import feedback, runtime  # noqa: E402

INTERVAL = float(os.getenv("YS_QUALITY_INTERVAL", "600"))
CLIP_SECONDS = float(os.getenv("YS_QUALITY_CLIP_SECONDS", "45"))
SOURCE = os.getenv("YS_AUDIO_SOURCE") or f"{os.getenv('YS_SINK_NAME', 'ystream_sink')}.monitor"
NTFY = os.getenv("YS_NTFY_URL", "")


def record_clip(path: str, seconds: float = CLIP_SECONDS, source: str = SOURCE) -> bool:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "pulse", "-i", source,
         "-t", str(seconds), "-ac", "2", "-ar", "48000", "-y", path],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and os.path.exists(path)


def check_once() -> dict | None:
    state_before = runtime.load_state()
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "clip.wav")
        if not record_clip(clip):
            print("[quality] could not record from", SOURCE, flush=True)
            return None
        state = runtime.load_state()
        # Only judge clips that didn't straddle a section change.
        if not state or not state_before or state.get("start_cycle") != state_before.get("start_cycle"):
            print("[quality] section changed during the clip; skipping", flush=True)
            return None
        key = state["piece"]["key"]
        metrics = analyze(clip, key)
    problems = verdict(metrics)
    record = {"ts": time.time(), "piece": state["piece"]["title"], "section_index": state["section_index"],
              "key": key, "metrics": {k: v for k, v in metrics.items() if k != "chroma"}, "problems": problems}
    path = os.path.join(config.RUNTIME_DIR, "quality.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(f"[quality] {state['piece']['title']}: {metrics.get('lufs')} LUFS, "
          f"key conformance {metrics.get('key_conformance')}, problems: {problems or 'none'}", flush=True)
    # Mixing problems (loudness/clipping) are the master chain's job; only
    # harmonic problems are the Director's choices, so only those get a
    # thumbs-down that affects future decisions.
    harmonic = [p for p in problems if p.startswith("key conformance")]
    if harmonic:
        feedback.append({"source": "auto", "rating": -1, "reason": "; ".join(harmonic),
                         "piece": state["piece"], "arrangement": state["arrangement"]})
    if problems and NTFY:
        try:
            import requests

            requests.post(NTFY, data=f"quality: {'; '.join(problems)}".encode(), timeout=5)
        except Exception:
            pass
    return record


def main() -> None:
    config.ensure_dirs()
    while True:
        try:
            check_once()
        except Exception as e:  # never let the monitor die on one bad clip
            print(f"[quality] error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
