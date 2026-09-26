"""
Runtime files shared with the streaming/ops side:

- state.json      : current piece/section/arrangement + active Tidal code,
                    used to resume after a crash (watchdog replay)
- history.jsonl   : bounded log of every section played (dataset export)
- now_playing.txt : overlay text for ffmpeg drawtext
- code.txt        : the live Tidal code, for the overlay
- heartbeat       : unix timestamp, touched while the engine is healthy
- thoughts.log    : what the Director decided and why (human-readable)
"""

from __future__ import annotations

import json
import os
import textwrap
import time

import config


def _path(name: str) -> str:
    return os.path.join(config.RUNTIME_DIR, name)


def atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_state(state: dict) -> None:
    atomic_write(_path("state.json"), json.dumps(state, indent=1))


def load_state() -> dict | None:
    try:
        with open(_path("state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def append_history(record: dict, limit: int | None = None) -> None:
    limit = limit or config.HISTORY_LIMIT * 10
    path = _path("history.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), **record}) + "\n")
    # Trim occasionally so the file can't grow forever.
    try:
        if os.path.getsize(path) > 5_000_000:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
            atomic_write(path, "".join(lines))
    except OSError:
        pass


def append_llm_call(record: dict) -> None:
    """Full prompt and reply of every LLM call (runtime/llm_calls.jsonl), for auditing."""
    path = _path("llm_calls.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), **record}) + "\n")
    try:
        if os.path.getsize(path) > 20_000_000:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-2000:]
            atomic_write(path, "".join(lines))
    except OSError:
        pass


def write_overlay(now_playing: str, code_lines: list[str], width: int = 110, max_lines: int = 14) -> None:
    atomic_write(_path("now_playing.txt"), now_playing + "\n")
    wrapped: list[str] = []
    for line in code_lines:
        wrapped.extend(textwrap.wrap(line, width=width, subsequent_indent="    ") or [""])
    if len(wrapped) > max_lines:
        wrapped = wrapped[: max_lines - 1] + ["    ..."]
    # drawtext treats '%' specially when expansion is on; the stream script
    # disables expansion, but strip it anyway to be safe.
    atomic_write(_path("code.txt"), "\n".join(wrapped).replace("%", "") + "\n")


def append_thought(line: str) -> None:
    """Human-readable Director log for `tail -f runtime/thoughts.log`."""
    path = _path("thoughts.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')}  {line}\n")
    try:
        if os.path.getsize(path) > 2_000_000:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-2000:]
            atomic_write(path, "".join(lines))
    except OSError:
        pass


def heartbeat() -> None:
    atomic_write(_path("heartbeat"), f"{time.time():.0f}\n")


def heartbeat_age() -> float | None:
    try:
        with open(_path("heartbeat"), encoding="utf-8") as f:
            return time.time() - float(f.read().strip())
    except (OSError, ValueError):
        return None
