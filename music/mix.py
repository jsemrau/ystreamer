"""
Mixing for the live engine.

Levels are calibrated from measured loudness, not guessed: each ys_* synth
was rendered offline (eval/synth_levels.sh, scsynth NRT, integrated LUFS)
and every block's gain is set so its role sits at a target level in the
mix. SuperDirt's gain curve is amplitude ~ gain^4, so a gain change is
80*log10(gain) dB.

Two things happen live on top of that: beds (chords, textures) make room
when leads are playing, and everything gives a little when the mix is full.
"""

from __future__ import annotations

import math
import re

# Single-voice integrated loudness per synth (LUFS), from eval/synth_levels.sh.
SYNTH_LUFS = {
    "ys_pad": -23.0, "ys_strings": -22.6, "ys_organ": -21.1, "ys_choir": -21.9, "ys_air": -30.0,
    "ys_epiano": -21.9, "ys_keys": -22.2, "ys_sub": -17.1, "ys_glass": -21.7, "ys_pluck": -31.5,
    "ys_vinyl": -42.8, "ys_softpad": -22.2,
    "ys_pulsepad": -21.0, "ys_housepiano": -22.9, "ys_pulselead": -19.4, "ys_hypersaw": -17.8,
    "ys_chorusbass": -18.6, "ys_coldguitar": -21.5, "ys_coldstrings": -22.0,
    "ys_wavelead": -20.1, "ys_wavesynth": -23.4,
    "ys_sawlead": -20.4, "ys_sawpad": -21.2,
    "ys_flute": -20.8, "ys_marimba": -24.2, "ys_sitar": -22.9, "ys_vox": -24.3, "ys_fmlead": -19.2,
    "ys_hoover": -22.3, "ys_m1organ": -23.6, "ys_nylon": -20.0, "ys_melodica": -22.2, "ys_vibes": -22.6,
    "ys_trumpet": -22.2, "ys_harpsichord": -23.8,
    "ys_sax": -19.1, "ys_angelchoir": -21.6, "ys_chipvox": -21.6,
}
# Chords measured as chords (3 voices), for synths that are only used that way.
CHORD_LUFS = {"ys_gatesaw": -18.0, "ys_supersaw": -15.7, "ys_hypersaw": -12.7, "ys_m1organ": -18.0}
# A four-note chord of one synth is ~5.6 dB louder than one voice (ys_pad x1 vs x4).
CHORD_DB = 5.6

# Where each role should sit in the mix (LUFS): melody in front, then bass,
# keys, and beds (pads, strings) and textures well behind.
ROLE_TARGET_LUFS = {"lead": -21.0, "bass": -19.0, "keys": -25.0, "chords": -29.0, "texture": -34.0}

# Synths whose single-note measurement doesn't tell how loud a fast
# 16th-note stream is; their blocks keep hand-tuned gains.
UNCALIBRATED = {"ys_tbass", "ys_acid", "ys_kick", "ys_gabber", "ys_kick909"}

_SYNTH_RE = re.compile(r'# s "(ys_\w+)"')


def synth_of(body: str) -> str | None:
    m = _SYNTH_RE.search(body)
    return m.group(1) if m else None


def level_db(synth: str, chord: bool, gain: float) -> float | None:
    """Loudness (LUFS) of a line of `synth` at `gain`, or None if unmeasured."""
    if chord and synth in CHORD_LUFS:
        base = CHORD_LUFS[synth] + 1.2        # the live chords have 4 notes
    elif synth in SYNTH_LUFS:
        base = SYNTH_LUFS[synth] + (CHORD_DB if chord else 0.0)
    else:
        return None
    return base + 80 * math.log10(gain)


def calibrated_gain(synth: str | None, role: str, chord: bool) -> float | None:
    """The gain that puts this synth at its role's target level, or None to
    keep the block's own gain (drums, fast sequences, unmeasured synths)."""
    if not synth or synth in UNCALIBRATED or role not in ROLE_TARGET_LUFS:
        return None
    level = level_db(synth, chord, 1.0)
    if level is None:
        return None
    return round(min(1.2, max(0.3, 10 ** ((ROLE_TARGET_LUFS[role] - level) / 80))), 3)


# Live density: beds step back when the front is busy; a full mix gives a little.
FRONT_ROLES = ("lead", "keys", "acid", "arp")
BED_ROLES = ("chords", "texture")
UNDUCKED_ROLES = ("beat", "bass", "perc")


def mix_factor(role: str, active: list[str]) -> float:
    """Extra gain multiplier for `role` given the active roles (1.0 = none)."""
    factor = 1.0
    if role in BED_ROLES and sum(r in active for r in FRONT_ROLES) >= 2:
        factor *= 0.93          # -2.5 dB
    if len(active) >= 6 and role not in UNDUCKED_ROLES:
        factor *= 0.96          # -1.4 dB
    return factor


# Fades, in bars.
FADE_IN_BARS = 4
FADE_OUT_BARS = 2
