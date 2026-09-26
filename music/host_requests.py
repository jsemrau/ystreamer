"""
Requests from the host ("more bass", "darker", "drop the beat"): typed into
the stream page, queued here, and picked up by the Performer at its next
edit, which reviews each one and turns it into a move (or explains why not).

The page server appends to runtime/requests.jsonl; the engine reads new
lines from where it last stopped (it starts at the end of the file, so old
requests don't fire after a restart). Plain file, one JSON object per line.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

import config

MAX_LENGTH = 200
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _path() -> str:
    return os.path.join(config.RUNTIME_DIR, "requests.jsonl")


def clean(text: str) -> str:
    """One line of plain text, at most MAX_LENGTH characters."""
    return _CONTROL_RE.sub(" ", str(text or "")).strip()[:MAX_LENGTH]


def submit(text: str) -> dict | None:
    """Queue a request (called by the page server). None if it's empty."""
    text = clean(text)
    if not text:
        return None
    record = {"text": text, "t": time.time()}
    os.makedirs(config.RUNTIME_DIR, exist_ok=True)
    with open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


class RequestQueue:
    """The engine's view: requests that arrived since it started."""

    def __init__(self, path: str | None = None, from_start: bool = False):
        self.path = path or _path()
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        try:
            self._offset = 0 if from_start else os.path.getsize(self.path)
        except OSError:
            self._offset = 0

    def _read_new(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self._offset:          # the file was replaced: start over
            self._offset = 0
        if size == self._offset:
            return
        with open(self.path, "rb") as f:
            f.seek(self._offset)
            chunk = f.read()
        # Only whole lines; a half-written one is read next time.
        end = chunk.rfind(b"\n") + 1
        self._offset += end
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = clean(record.get("text", ""))
            if text:
                self._pending.append({"text": text, "t": record.get("t", time.time())})

    def pending(self) -> bool:
        with self._lock:
            self._read_new()
            return bool(self._pending)

    def take(self) -> dict | None:
        """The oldest waiting request, or None."""
        with self._lock:
            self._read_new()
            return self._pending.pop(0) if self._pending else None
