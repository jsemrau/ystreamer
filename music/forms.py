"""
Song forms from real songs (music/data/song_forms.json, learned by
eval/song_forms.py from the Harmonix Set): each track takes the section
order of a real song of its genre family, its sections' proportions scaled
to the track's length, so the stream stops playing one shape.

The style keeps its frame on top: trance, techno and hardcore always have a
breakdown before their last peak (a dance-pop song often doesn't), and every
form has at least one chorus, the peak.
"""

from __future__ import annotations

import json
import os
import random

FORMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "song_forms.json")

# Energy per section type (the Performer's density, the arranger's devices):
# the last chorus a touch higher.
ENERGY = {"intro": 0.2, "verse": 0.5, "lift": 0.65, "build": 0.6, "chorus": 0.85, "tag": 0.7, "bridge": 0.5,
          "interlude": 0.6, "break": 0.25, "breakdown": 0.3, "outro": 0.2}
# Sections that keep about their real length, however long the track.
SHORT = {"lift", "tag", "break"}
# Styles whose frame needs a breakdown before the last peak.
BREAKDOWN_STYLES = {"trance", "techno", "hardcore"}
_cache: dict | None = None


def _data() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(FORMS_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def family(style: str) -> str | None:
    return next((f for f, d in _data().items() if not f.startswith("_") and style in d.get("styles", [])), None)


def shape(form: list) -> str:
    """A form's section order, e.g. "intro verse lift chorus bridge chorus"."""
    return " ".join(s[0] if isinstance(s, (list, tuple)) else s.name for s in form)


def _with_frame(form: list[list], style: str) -> list[list]:
    form = [list(s) for s in form]
    # The intro's music coming back later is an interlude (or, last, the outro).
    for i, s in enumerate(form):
        if s[0] == "intro" and i:
            s[0] = "outro" if i == len(form) - 1 else "interlude"
    if form and form[0][0] != "intro":
        form.insert(0, ["intro", 8])               # the live engine always builds a track up
    if not any(k == "chorus" for k, _ in form):
        at = max(1, len(form) - 1) if form and form[-1][0] == "outro" else len(form)
        form.insert(at, ["chorus", 16])
    # A lift leads into a chorus and a tag follows one; one stranded elsewhere (a trimmed form) goes.
    form = [s for i, s in enumerate(form)
            if (s[0] != "lift" or (i + 1 < len(form) and form[i + 1][0] == "chorus"))
            and (s[0] != "tag" or (i and form[i - 1][0] == "chorus"))]
    if style in BREAKDOWN_STYLES and not any(k == "breakdown" for k, _ in form):
        last = max(i for i, (k, _) in enumerate(form) if k == "chorus")
        if last and form[last - 1][0] == "lift":
            last -= 1                                # the breakdown, then the lift as its rebuild
        before = form[last - 1][0] if last else ""
        if before in ("bridge", "interlude", "break"):
            form[last - 1][0] = "breakdown"         # the bridge becomes the breakdown
        else:
            form.insert(last, ["breakdown", 16])
    return form


def suggest(style: str, rng: random.Random, target_bars: float, period: int = 8, max_sections: int = 10,
            avoid: list[str] | None = None) -> list[tuple[str, int, float]] | None:
    """A real song's form for the style's family, as (section, bars, energy):
    not one of the shapes in `avoid`, at most `max_sections` sections (a
    track of 11 edits can't turn 15 times), bars scaled to target_bars and
    rounded to the style's period. None when there's no data for the style."""
    fam = family(style)
    if not fam:
        return None
    # Room for what the frame may add (an intro, a breakdown) within max_sections.
    room = max(3, max_sections - 2)
    trimmed = [f if len(f) <= room else f[:room - 1] + [f[-1]] for f in _data()[fam]["forms"] if len(f) >= 4]
    fresh = [f for f in trimmed if shape(_with_frame(f, style)) not in set(avoid or [])] or trimmed
    form = _with_frame(rng.choice(fresh), style)
    # Short sections keep about their real length (a lift is a lift); the
    # long ones (verses, choruses, bridges ...) stretch to fill the track.
    fixed = sum(min(16, max(period, round(max(b, 4) / period) * period)) for k, b in form if k in SHORT)
    long_real = sum(max(b, 4) for k, b in form if k not in SHORT) or 1
    scale = max(0.5, (target_bars - fixed) / long_real)
    out = []
    last_chorus = max(i for i, (k, _) in enumerate(form) if k == "chorus")
    for i, (kind, bars) in enumerate(form):
        if kind in SHORT:
            length = min(16, max(period, round(max(bars, 4) / period) * period))
        else:
            length = max(1, round(max(bars, 4) * scale / period)) * period
            if kind == "intro":
                length = min(length, 32)
        energy = ENERGY.get(kind, 0.5) + (0.1 if i == last_chorus else 0.0)
        out.append((kind, length, round(min(energy, 1.0), 2)))
    return out


def describe(form: list[tuple[str, int, float]]) -> str:
    """For the Director: "intro 16 → verse 32 → lift 8 → chorus 32 → ..."."""
    return " → ".join(f"{kind} {bars}" for kind, bars, _ in form)
