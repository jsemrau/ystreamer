"""
The host's favourite songs (music/data/touchstones.json), as touchstones:
each track's brief carries one in the track's style, never one of the last
few, saying what makes it work and what to borrow from it: a device, a
sound, a shape, never its tune.
"""

from __future__ import annotations

import json
import os
import random

TOUCHSTONES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "touchstones.json")
_cache: list[dict] | None = None


def songs() -> list[dict]:
    global _cache
    if _cache is None:
        try:
            with open(TOUCHSTONES_PATH, encoding="utf-8") as f:
                _cache = [s for s in json.load(f).get("songs", []) if isinstance(s, dict) and s.get("title")]
        except (OSError, ValueError, AttributeError):
            _cache = []
    return _cache


def pick(style: str | None, rng: random.Random, avoid: list[str] | None = None) -> dict | None:
    """A song in the style (any song when the style is open or none fits),
    not one of the titles in `avoid` while another is left."""
    pool = [s for s in songs() if style in s.get("styles", [])] if style else []
    pool = pool or songs()
    fresh = [s for s in pool if s["title"] not in set(avoid or [])]
    return rng.choice(fresh or pool) if pool else None


def describe(song: dict | None) -> str:
    """For the Director's brief."""
    if not song:
        return "none this time: go by the theme and the host's taste."
    return (f"**{song['title']}** by {song['artist']} ({song.get('year', '')}; {song.get('tempo', '')}). "
            f"Its sounds: {song.get('sounds', '')}. What makes it: {song.get('signature', '')}. "
            f"Borrow: {song.get('borrow', '')}.")
