"""
Listener/quality feedback store.

Each record is one JSON line in runtime/feedback.jsonl:
    {"ts": ..., "source": "human"|"auto", "rating": -1|1, "reason": "...",
     "piece": {...}, "arrangement": {...}}

Human thumbs-down (eval/feedback.py) and automatic quality checks
(eval/monitor.py) both write here. The Director reads it to steer away from
progressions and presets that were rated badly.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter

import config


def feedback_path() -> str:
    return os.path.join(config.RUNTIME_DIR, "feedback.jsonl")


def append(record: dict, path: str | None = None) -> None:
    path = path or feedback_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {"ts": time.time(), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load(path: str | None = None, limit: int = 2000) -> list[dict]:
    path = path or feedback_path()
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records[-limit:]


def disliked(path: str | None = None, threshold: int = 2) -> dict[str, set[str]]:
    """Progressions / presets whose net rating is <= -threshold."""
    scores: dict[str, Counter] = {"progressions": Counter(), "presets": Counter()}
    for rec in load(path):
        rating = rec.get("rating", 0)
        piece = rec.get("piece") or {}
        if piece.get("progression"):
            scores["progressions"][piece["progression"]] += rating
        for preset in (rec.get("arrangement") or {}).get("layers", {}).values():
            if preset:
                scores["presets"][preset] += rating
    return {kind: {name for name, score in counter.items() if score <= -threshold}
            for kind, counter in scores.items()}
