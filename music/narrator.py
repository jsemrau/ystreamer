"""
Spoken narration: the Director's listener-facing sentences, read in a calm
voice into the stream.

- Text-to-speech runs locally with Piper (https://github.com/OHF-Voice/piper1-gpl),
  slowed slightly (length scale) for a relaxed delivery.
- ffmpeg softens the voice (gentle high/low-pass, a touch of room echo).
- The clip plays into the stream sink (so it reaches YouTube and the local
  monitor), while the music dips a little (Tidal `all (|* gain ...)`).
- Narration never blocks the music: it runs on its own thread, drops
  sentences that arrive while it's still speaking, and respects a minimum
  gap between announcements.

Configure in .env: YS_NARRATION, YS_TTS_VOICE, YS_VOICE_STYLE, YS_TTS_LENGTH_SCALE,
YS_NARRATION_MIN_GAP, YS_NARRATION_DUCK, YS_NARRATION_VOLUME_DB, YS_TTS_CMD.
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable

import config

from . import runtime

MODES = ("off", "pieces", "sections")
VOICE_STYLES = ("warm", "calm")

# Listener text must not read like a log line.
_TECHNICAL_RE = re.compile(r"\d|_|\blayers?\b|\benergy\b|\bpreset|\borbit\b|\bcps\b", re.IGNORECASE)


def clean_narration(text) -> str:
    """Return a speakable sentence, or "" if the text is missing or technical."""
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split()).strip().strip('"')
    if not text or len(text) > 240 or _TECHNICAL_RE.search(text):
        return ""
    if not all(ch.isprintable() for ch in text):
        return ""
    return text


def sink_exists(name: str) -> bool:
    try:
        out = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split("\t")[1:2] == [name] or f"\t{name}\t" in line for line in out.splitlines())


class Narrator:
    def __init__(self, log: Callable[[str], None] = print, duck: Callable[[bool], None] | None = None,
                 mode: str | None = None, voice: str | None = None, speak: bool = True):
        self.log = log
        self.duck = duck
        self.mode = (mode or os.getenv("YS_NARRATION", "sections")).lower()
        if self.mode not in MODES:
            self.mode = "sections"
        self.voice = voice if voice is not None else os.getenv("YS_TTS_VOICE", "")
        if self.voice and not os.path.isabs(self.voice) and not os.path.exists(self.voice):
            # A relative path in .env means relative to the repo, wherever we start from.
            self.voice = os.path.join(config.REPO_DIR, self.voice)
        self.tts_cmd = os.getenv("YS_TTS_CMD", "")
        # warm = low, intimate late-night radio voice (default); calm = clean and soft
        self.style = os.getenv("YS_VOICE_STYLE", "warm").lower()
        if self.style not in VOICE_STYLES:
            self.style = "warm"
        self.length_scale = os.getenv("YS_TTS_LENGTH_SCALE", "1.25" if self.style == "warm" else "1.15")
        self.min_gap = float(os.getenv("YS_NARRATION_MIN_GAP", "60"))
        # Voice is loudness-normalised to the stream's -14 LUFS target, then
        # raised by this many dB so it sits clearly on top of the music.
        self.volume_db = float(os.getenv("YS_NARRATION_VOLUME_DB", "2"))
        self.sink = os.getenv("YS_SINK_NAME", "ystream_sink")
        self._queue: queue.Queue[str] = queue.Queue(maxsize=1)
        self._last_spoken = -1e9
        self._busy = threading.Event()
        # speak=False: only print the lines (dry runs); no audio, no setup hints.
        self.available = self._check() if speak else False
        self._speak = speak
        if self.available:
            threading.Thread(target=self._worker, daemon=True, name="narrator").start()

    # ------------------------------------------------------------------ setup

    def _check(self) -> bool:
        if self.mode == "off":
            return False
        if self.tts_cmd:
            return True
        if not self.voice or not os.path.exists(self.voice):
            self.log("[Narrator] voice off: set YS_TTS_VOICE in .env to a Piper voice (.onnx). Download one with\n"
                     "           ./venv/bin/python -m piper.download_voices en_GB-jenny_dioco-medium --download-dir voices\n"
                     "           and set YS_TTS_VOICE=voices/en_GB-jenny_dioco-medium.onnx")
            return False
        try:
            import piper  # noqa: F401
        except ImportError:
            self.log("[Narrator] voice off: piper-tts isn't installed (./venv/bin/pip install piper-tts)")
            return False
        if not shutil.which("paplay"):
            self.log("[Narrator] voice off: paplay not found (sudo apt-get install pulseaudio-utils)")
            return False
        return True

    # ------------------------------------------------------------------ public

    def wants(self, kind: str) -> bool:
        """kind is "piece" or "section"."""
        return self.mode == "sections" or (self.mode == "pieces" and kind == "piece")

    def say(self, text: str, kind: str = "section") -> None:
        if not self.wants(kind):
            return
        raw = " ".join(str(text or "").split())
        text = clean_narration(text)
        if not text:
            if raw:
                self.log(f'🗣  (not spoken: numbers or technical words) "{raw[:120]}"')
            return
        if not self.available:
            # speak=False is a dry run: just print the line.
            self.log(f'🗣  (voice off) "{text}"' if self._speak else f'🗣  "{text}"')
            runtime.append_thought(f"🗣  {text}")
            runtime.ui_event("narrator", text=text)
            return
        since = time.monotonic() - self._last_spoken
        if since < self.min_gap and kind != "piece":
            self.log(f'🗣  (skipped: last line {since:.0f}s ago, min gap {self.min_gap:.0f}s) "{text}"')
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            self.log(f'🗣  (skipped: still speaking) "{text}"')  # still speaking the previous sentence
            return
        self.log(f'🗣  "{text}"')
        runtime.append_thought(f"🗣  {text}")

    # ----------------------------------------------------------------- worker

    def _synthesize(self, text: str, path: str) -> bool:
        if self.tts_cmd:
            cmd = [part.replace("{out}", path) for part in shlex.split(self.tts_cmd)]
        else:
            cmd = [sys.executable, "-m", "piper", "-m", self.voice, "-f", path,
                   "--length-scale", str(self.length_scale), "--sentence-silence", "0.5"]
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(path):
            self.log(f"[Narrator] TTS failed: {proc.stderr.strip()[-300:]}")
            return False
        return True

    def _filter_chain(self, sample_rate: int) -> str:
        if self.style == "warm":
            # Intimate late-night radio voice: pitch ~5% lower (tempo kept),
            # fuller lows, softer highs, gentle close-mic compression.
            pitch = 0.95
            return (f"asetrate={int(sample_rate * pitch)},aresample={sample_rate},atempo={1 / pitch:.4f},"
                    "highpass=f=70,bass=g=4:f=160,treble=g=-3:f=5000,lowpass=f=9000,"
                    "acompressor=threshold=-20dB:ratio=2.5:attack=10:release=250:makeup=2,"
                    f"aecho=0.8:0.4:40:0.07,{self._level()}")
        # calm: clean and soft, a little room
        return (f"highpass=f=90,lowpass=f=7500,aecho=0.8:0.5:60:0.12,"
                f"{self._level()}")

    def _level(self) -> str:
        """Same loudness as the stream (-14 LUFS), plus YS_NARRATION_VOLUME_DB,
        with a limiter so the boost can't clip."""
        return (f"apad=pad_dur=0.6,loudnorm=I=-14:TP=-2:LRA=7,volume={self.volume_db}dB,"
                "alimiter=limit=0.95:level=disabled,aresample=48000")

    def _soften(self, src: str, dst: str) -> str:
        """Apply the voice style. Falls back to the raw clip if ffmpeg fails."""
        import wave

        try:
            with wave.open(src) as w:
                sample_rate = w.getframerate()
        except (wave.Error, OSError):
            sample_rate = 22050
        chain = self._filter_chain(sample_rate)
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                               "-af", chain, "-ar", "48000", "-ac", "2", dst],
                              capture_output=True, text=True, timeout=60)
        return dst if proc.returncode == 0 else src

    def _play(self, path: str) -> None:
        cmd = ["paplay"]
        if sink_exists(self.sink):
            cmd.append(f"--device={self.sink}")
        subprocess.run(cmd + [path], capture_output=True, timeout=120)

    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            self._busy.set()
            try:
                with tempfile.TemporaryDirectory(prefix="ys_voice_") as tmp:
                    raw = os.path.join(tmp, "raw.wav")
                    if not self._synthesize(text, raw):
                        continue
                    clip = self._soften(raw, os.path.join(tmp, "voice.wav"))
                    runtime.atomic_write(os.path.join(config.RUNTIME_DIR, "narration.txt"), text + "\n")
                    runtime.ui_update(narration=text)
                    runtime.ui_event("narrator", text=text)
                    if self.duck:
                        self.duck(True)
                    try:
                        self._play(clip)
                    finally:
                        if self.duck:
                            self.duck(False)
                        time.sleep(2)
                        runtime.atomic_write(os.path.join(config.RUNTIME_DIR, "narration.txt"), "\n")
                        runtime.ui_update(narration="")
                self._last_spoken = time.monotonic()
            except Exception as e:  # never let narration take anything else down
                self.log(f"[Narrator] error: {e}")
            finally:
                self._busy.clear()


def tidal_ducker(send: Callable[[list[str]], str | None]) -> Callable[[bool], None] | None:
    """Dip all Tidal patterns while the voice speaks (YS_NARRATION_DUCK, gain factor)."""
    factor = float(os.getenv("YS_NARRATION_DUCK", "0.85"))
    if factor >= 1.0:
        return None

    def duck(on: bool) -> None:
        send([f"all (|* gain {factor})"] if on else ["all id"])

    return duck
