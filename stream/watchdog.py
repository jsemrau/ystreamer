#!/usr/bin/env python3
"""
Stream watchdog: keeps a 24/7 stream from going silent or stale.

Checks, in a loop:
1. Silence on the stream sink (ffmpeg silencedetect). Sustained silence
   means scsynth/ghci died or audio routing broke -> restart the engine
   service, which reboots the audio stack and resumes the saved piece
   (orchestrator --resume replays runtime/state.json).
2. Engine heartbeat age (runtime/heartbeat). A stale heartbeat means the
   engine loop is hung -> restart it.

Every restart is rate-limited and reported to an optional ntfy.sh topic
(YS_NTFY_URL), so you get a push notification on your phone.

    python -m stream.watchdog                      # uses defaults below
    YS_ENGINE_PIDFILE=runtime/engine.pid python -m stream.watchdog   # what stream/live.sh runs
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music import runtime  # noqa: E402

SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")


@dataclass
class WatchdogConfig:
    source: str = os.getenv("YS_WATCH_SOURCE", f"{os.getenv('YS_SINK_NAME', 'ystream_sink')}.monitor")
    silence_db: float = float(os.getenv("YS_SILENCE_DB", "-55"))
    silence_seconds: float = float(os.getenv("YS_SILENCE_SECONDS", "20"))
    heartbeat_limit: float = float(os.getenv("YS_HEARTBEAT_LIMIT", "180"))
    # Any command, e.g. for your own process supervisor (systemd). Ignored
    # under stream/live.sh (YS_ENGINE_PIDFILE set), which restarts the engine
    # itself with the flags it was started with.
    restart_cmd: str = os.getenv("YS_RESTART_CMD", "")
    engine_pidfile: str = os.getenv("YS_ENGINE_PIDFILE", "")
    cooldown: float = float(os.getenv("YS_RESTART_COOLDOWN", "120"))
    ntfy_url: str = os.getenv("YS_NTFY_URL", "")
    startup_grace: float = float(os.getenv("YS_WATCH_GRACE", "120"))


class SilenceTracker:
    """Turns silencedetect output lines into 'silent for N seconds' state."""

    def __init__(self):
        self.silent_since: float | None = None  # monotonic time
        self._lock = threading.Lock()

    def feed(self, line: str, now: float) -> None:
        with self._lock:
            if SILENCE_START_RE.search(line):
                self.silent_since = now
            elif SILENCE_END_RE.search(line):
                self.silent_since = None

    def silent_for(self, now: float) -> float:
        with self._lock:
            return 0.0 if self.silent_since is None else now - self.silent_since

    def reset(self) -> None:
        with self._lock:
            self.silent_since = None


def decide(silent_for: float, heartbeat_age: float | None, cfg: WatchdogConfig,
           since_last_restart: float, uptime: float) -> str | None:
    """Return a restart reason, or None if everything is healthy."""
    if uptime < cfg.startup_grace or since_last_restart < cfg.cooldown:
        return None
    if silent_for >= cfg.silence_seconds:
        return f"stream silent for {silent_for:.0f}s"
    if heartbeat_age is not None and heartbeat_age > cfg.heartbeat_limit:
        return f"engine heartbeat is {heartbeat_age:.0f}s old"
    return None


def notify(cfg: WatchdogConfig, message: str) -> None:
    print(f"[watchdog] {message}", flush=True)
    if not cfg.ntfy_url:
        return
    try:
        import requests

        requests.post(cfg.ntfy_url, data=message.encode("utf-8"), timeout=5,
                      headers={"Title": "ystreamer watchdog"})
    except Exception as e:
        print(f"[watchdog] notify failed: {e}", flush=True)


def listen_for_silence(cfg: WatchdogConfig, tracker: SilenceTracker, stop: threading.Event) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-f", "pulse", "-i", cfg.source,
           "-af", f"silencedetect=n={cfg.silence_db}dB:d=5", "-f", "null", "-"]
    while not stop.is_set():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            print("[watchdog] ffmpeg not found; silence detection disabled", flush=True)
            return
        assert proc.stderr is not None
        for line in proc.stderr:
            tracker.feed(line, time.monotonic())
            if stop.is_set():
                break
        proc.kill()
        time.sleep(5)  # source vanished (e.g. PipeWire restart); retry


def main() -> None:
    cfg = WatchdogConfig()
    tracker = SilenceTracker()
    stop = threading.Event()
    threading.Thread(target=listen_for_silence, args=(cfg, tracker, stop), daemon=True).start()
    started = time.monotonic()
    last_restart = -1e9
    notify(cfg, f"watching {cfg.source} (silence > {cfg.silence_seconds:.0f}s, heartbeat > {cfg.heartbeat_limit:.0f}s)")
    try:
        while True:
            time.sleep(5)
            now = time.monotonic()
            reason = decide(tracker.silent_for(now), runtime.heartbeat_age(), cfg,
                            now - last_restart, now - started)
            if reason:
                notify(cfg, f"restarting engine: {reason}")
                restart_engine(cfg)
                last_restart = time.monotonic()
                tracker.reset()
    except KeyboardInterrupt:
        stop.set()


def restart_engine(cfg: WatchdogConfig) -> None:
    """Stop the engine from its pid file so the supervisor (stream/live.sh)
    starts it again with --resume and its own flags, or run YS_RESTART_CMD.
    The pid file wins: a YS_RESTART_CMD left in .env for systemd would start
    a second engine next to live.sh's, without its --style."""
    if cfg.restart_cmd and not cfg.engine_pidfile:
        result = subprocess.run(shlex.split(cfg.restart_cmd), capture_output=True, text=True)
        if result.returncode != 0:
            notify(cfg, f"restart command failed ({result.returncode}): {result.stderr.strip()[:300]}")
        return
    try:
        with open(cfg.engine_pidfile) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as e:
        notify(cfg, f"couldn't stop the engine (pid file {cfg.engine_pidfile or 'not set'}): {e}")


if __name__ == "__main__":
    main()
