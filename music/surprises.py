"""
The surprise menu: rule-breaks the art director (the Director) may choose,
at most one per track, built only from moves the system already makes
safely (mute, unmute, the drop, tempo, key, the arc plan, precise cues), so
none can break the harmony or the code. Code decides when one is allowed
(SURPRISE_EVERY, no repeat of a recent one, what the style and track have)
and places it at its moment; the page shows it when it fires.

    name: (shown label, what it does - for the Director, when it happens)
"""

from __future__ import annotations

import re

SURPRISES = {
    "fake_drop": ("FAKE DROP", "the build-up leads to 8 bars of peak, then falls back into a short breakdown "
                               "before the real drop", "before the peak"),
    "kickless_drop": ("NO KICK", "the drop lands with everything but the kick; it comes back a move later",
                      "the drop"),
    "silent_bar": ("SILENCE", "a whole bar of silence, every line, right before the drop", "the drop"),
    "key_lift": ("KEY CHANGE", "the last peak goes up a whole step, every line with it", "the last peak"),
    "solo_breakdown": ("SOLO", "the breakdown strips to one line alone, the acid (or the hook's part)",
                       "the breakdown"),
    "stripped_peak": ("LESS IS MORE", "the drop brings only kick, bass, hats and the hook; the rest returns "
                                      "move by move", "the drop"),
    "tempo_lift": ("FASTER", "+2 then +4 BPM through the build-up into the peak", "the build-up"),
    "three_note_hook": ("THREE NOTES", "the track's hook uses only three different notes", "the whole track"),
}

# Surprises a track can't take, and why: what it needs to have.
_DEGREE_RE = re.compile(r"(?<![*!/@%\d.:])-?\d+")


def hook_notes(pattern: str) -> int:
    """Different pitches in a hook pattern (degrees an octave apart count once)."""
    return len({int(d) % 7 for d in _DEGREE_RE.findall(pattern or "")})


def problem(name: str, style: str, plan: list | None = None, hook: dict | None = None) -> str | None:
    """Why this surprise can't go into this track, or None."""
    from .blocks import blocks_for

    if name not in SURPRISES:
        return f"{name!r} isn't on the menu"
    phases = [p for p, _ in plan] if plan else []
    roles = {b.role for b in blocks_for(style)}
    if name in ("fake_drop", "kickless_drop", "silent_bar", "stripped_peak", "tempo_lift", "key_lift") \
            and plan is not None and "groove2" not in phases:
        return "the track has no peak"
    if name == "solo_breakdown" and plan is not None and "breakdown" not in phases:
        return "the track has no breakdown"
    if name == "kickless_drop" and "beat" not in roles:
        return "the style has no kick"
    if name == "three_note_hook" and (not hook or hook_notes(hook.get("pattern", "")) > 3):
        return "the hook has more than three different notes"
    return None
