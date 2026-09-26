"""
The session log: how the artefact (the live Tidal program) changed, edit by
edit, for every session, kept as memory the system can learn from.

One append-only JSONL file per session under sessions/ (YS_SESSIONS_DIR),
never truncated or overwritten, flushed after every record so a crash loses
nothing. Each record has a `type`:

- `session_start`: theme, style, models, judge, edit pacing, git commit
- `track_start`:   the track (title, key, BPM, progression, mood) and the
                   Director's line; the program it starts from
- `edit`:          one change to the artefact that played: who chose it
                   (LLM, judged, built-in), what it heard, why, the op, the
                   line before and after, the whole code after, the
                   phase, and the complexity before and after (the full
                   program structure is kept at track start and end); with a judge,
                   every option it weighed and their probabilities
- `rejected`:      an edit that never played, and why (invalid, didn't type
                   check, Tidal refused it)
- `arc`:           the track's shape moving on (e.g. groove -> breakdown)
- `track_end`:     how the track went: edits by source, the judge's
                   preference, complexity, the final program
- `session_end`

`iter_records()` / `iter_edits()` read them back (e.g. to retrieve past
moves for a prompt, or to build a training set: eval/export_edits.py).
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import subprocess
import threading
import time
from typing import Iterator

import config

SESSIONS_DIR = os.getenv("YS_SESSIONS_DIR", os.path.join(config.REPO_DIR, "sessions"))


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.REPO_DIR,
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class SessionLog:
    """Appends the records of one session to sessions/<id>.jsonl."""

    def __init__(self, directory: str | None = None, session_id: str | None = None,
                 now=lambda: time.time()):
        self.directory = directory or SESSIONS_DIR
        os.makedirs(self.directory, exist_ok=True)
        self.id = session_id or _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = os.path.join(self.directory, f"{self.id}.jsonl")
        self._now = now
        self._seq = 0
        self._lock = threading.Lock()
        self._file = open(self.path, "a", encoding="utf-8")

    def write(self, type_: str, **data) -> None:
        with self._lock:
            self._seq += 1
            record = {"type": type_, "session": self.id, "seq": self._seq, "t": round(self._now(), 3), **data}
            try:
                self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                self._file.flush()
            except (OSError, ValueError):
                pass    # a full disk must never stop the music

    def start(self, **meta) -> None:
        self.write("session_start", git=_git_commit(), **meta)

    def close(self, **data) -> None:
        self.write("session_end", **data)
        with self._lock:
            try:
                self._file.close()
            except OSError:
                pass


# ------------------------------------------------------------------- reading

def session_files(directory: str | None = None) -> list[str]:
    return sorted(glob.glob(os.path.join(directory or SESSIONS_DIR, "*.jsonl")))


def iter_records(directory: str | None = None, types: set[str] | None = None) -> Iterator[dict]:
    """Every record of every session, oldest session first."""
    for path in session_files(directory):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue    # a line cut off by a crash
                if types is None or record.get("type") in types:
                    yield record


def iter_edits(directory: str | None = None, style: str | None = None,
               sources: tuple[str, ...] | None = None) -> Iterator[dict]:
    """The edits that played, optionally for one style and/or chosen by
    given sources (e.g. ("LLM", "LLM, judged"))."""
    for record in iter_records(directory, {"edit"}):
        if style and record.get("style") != style:
            continue
        if sources and record.get("source") not in sources:
            continue
        yield record
