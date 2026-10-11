"""
Drum kits (music/data/drum_kits.json, built by eval/drum_kits.py): each
track plays one, so the drums sound like that track's and not like every
track's. A kit is a bank in samples/ (0 kick, 1 snare, 2 clap, 3 closed hat,
4 open hat), levelled to the hits it stands in for.

The drum blocks and the performer's edits keep writing the stream's stock
names (ys_kick909, bd, sn, cp, hh27, 808oh ...): they're what the patterns
and their checks know. The kit comes in only when a line is played
(Program.expression): each stock hit becomes the kit's own.
"""

from __future__ import annotations

import json
import os
import random
import re

import config

KITS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "drum_kits.json")
SLOT_INDEX = {"kick": 0, "snare": 1, "clap": 2, "hat": 3, "openhat": 4}
# The stock hits and the slot each stands for (the gabber kick stays: it's hardcore's lead).
STOCK = {"ys_kick909": "kick", "ys_kick": "kick", "808bd": "kick", "bd": "kick",
         "808sd": "snare", "sd": "snare", "sn": "snare",
         "realclaps": "clap", "cp": "clap",
         "hh27": "hat", "hh": "hat", "808hc": "hat",
         "808oh": "openhat"}
_STOCK_RE = re.compile(r"(?<![\w:.])(" + "|".join(sorted(STOCK, key=len, reverse=True)) + r")(:\d+)?(?![\w])")
_cache: list[dict] | None = None


def kits() -> list[dict]:
    """The kits whose banks are installed (none with YS_DRUM_KITS=0)."""
    global _cache
    if not config.DRUM_KITS:
        return []
    if _cache is None:
        try:
            with open(KITS_PATH, encoding="utf-8") as f:
                listed = json.load(f).get("kits", [])
        except (OSError, ValueError):
            listed = []
        samples = os.path.join(config.REPO_DIR, "samples")
        _cache = [k for k in listed if os.path.isdir(os.path.join(samples, k.get("bank", "")))]
    return _cache


def pick(style: str, rng: random.Random, avoid: list[str] | None = None) -> dict | None:
    """A kit for the style, none of the names in `avoid` while another is left."""
    pool = [k for k in kits() if style in k.get("styles", [])]
    fresh = [k for k in pool if k["name"] not in set(avoid or [])] or pool
    return rng.choice(fresh) if fresh else None


# Each style's bass sounds, one a track (not the last two tracks'): the sub,
# the TB, the picked electric bass and the upright, a recording where installed.
BASS_SOUNDS = {
    "trance": ["ys_sub", "ys_tbass", "ys_chorusbass"],
    "techno": ["ys_sub", "ys_tbass"],
    "deephouse": ["ys_sub", "ys_chorusbass", "ys_uprightbass", "ys_tbass"],
    "darkwave": ["ys_chorusbass", "ys_tbass", "ys_sub"],
    "lofi": ["ys_uprightbass", "ys_sub", "ys_chorusbass"],
    "downtempo": ["ys_uprightbass", "ys_chorusbass", "ys_sub"],
    "ambient": ["ys_sub", "ys_uprightbass"],
}


def pick_bass(style: str, rng: random.Random, avoid: list[str] | None = None) -> str | None:
    from . import instruments

    if not config.DRUM_KITS:
        return None
    pool = [s for s in BASS_SOUNDS.get(style, [])
            if s != "ys_uprightbass" or instruments.installed("uprightbass")]
    fresh = [s for s in pool if s not in set(avoid or [])] or pool
    return rng.choice(fresh) if fresh else None


def apply(expr: str, bank: str) -> str:
    """A drum line's pattern with the kit's hits for the stock ones."""
    if not bank:
        return expr
    return _STOCK_RE.sub(lambda m: f"{bank}:{SLOT_INDEX[STOCK[m.group(1)]]}", expr)
