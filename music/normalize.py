"""
Defensive rewrites for LLM-written Tidal code (legacy composer path).

The most important one: `scale "c minor" ...` is NOT valid Tidal. `scale`
takes a scale *name* only; an unknown name silently falls back to a one-note
scale, so every degree becomes a whole octave jump (degree 7 = 7 octaves up).
We rewrite it to `(scale "minor" ... + 0)` with the root as a semitone offset.
"""

from __future__ import annotations

import re

from . import presets as P
from .theory import _NAME_TO_PC

# Mode words -> Tidal scaleTable names.
TIDAL_SCALE_NAMES = {
    "major": "major", "maj": "major", "ionian": "major",
    "minor": "minor", "min": "minor", "m": "minor", "aeolian": "minor", "natural minor": "minor",
    "dorian": "dorian", "phrygian": "phrygian", "lydian": "lydian",
    "mixolydian": "mixolydian", "locrian": "locrian",
    "pentatonic": "majPent", "major pentatonic": "majPent", "majpent": "majPent",
    "minor pentatonic": "minPent", "minpent": "minPent",
    "harmonic minor": "harmonicMinor", "harmonicminor": "harmonicMinor",
    "melodic minor": "melodicMinor", "melodicminor": "melodicMinor",
    "blues": "minPent",
}

_SCALE_WITH_ROOT_RE = re.compile(
    r'\bscale\s+"\s*([A-Ga-g])\s*(#|s|b|f|♯|♭)?\s*[- ]?\s*([A-Za-z][A-Za-z ]*?)\s*"\s*'
)
_BARE_SCALE_RE = re.compile(r'\bscale\s+"\s*([A-Za-z][A-Za-z ]*?)\s*"')


def _read_argument(code: str, pos: int) -> int | None:
    """Return the end index of the Haskell argument starting at pos
    (a "quoted string" or a balanced (parenthesised) expression)."""
    if pos >= len(code):
        return None
    if code[pos] == '"':
        end = code.find('"', pos + 1)
        return end + 1 if end != -1 else None
    if code[pos] == "(":
        depth = 0
        in_str = False
        for i in range(pos, len(code)):
            ch = code[i]
            if ch == '"':
                in_str = not in_str
            elif not in_str and ch == "(":
                depth += 1
            elif not in_str and ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        return None
    return None


def rewrite_scale_roots(code: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    out = []
    pos = 0
    while True:
        m = _SCALE_WITH_ROOT_RE.search(code, pos)
        if not m:
            out.append(code[pos:])
            break
        letter, accidental, mode_word = m.group(1), m.group(2) or "", m.group(3).strip().lower()
        accidental = {"♯": "#", "♭": "b"}.get(accidental, accidental)
        root_key = (letter + accidental).lower()
        tidal_mode = TIDAL_SCALE_NAMES.get(mode_word)
        arg_end = _read_argument(code, m.end())
        if root_key not in _NAME_TO_PC or tidal_mode is None or arg_end is None:
            out.append(code[pos:m.end()])
            pos = m.end()
            continue
        root = _NAME_TO_PC[root_key]
        argument = code[m.end():arg_end]
        out.append(code[pos:m.start()])
        replacement = f'(scale "{tidal_mode}" {argument} + {root})' if root else f'scale "{tidal_mode}" {argument}'
        out.append(replacement)
        notes.append(f"scale-root:{letter}{accidental} {mode_word}->{tidal_mode}+{root}")
        pos = arg_end
    code = "".join(out)

    # Bare but invalid names like scale "pentatonic" -> scale "majPent".
    def fix_bare(match: re.Match) -> str:
        word = match.group(1).strip()
        mapped = TIDAL_SCALE_NAMES.get(word.lower())
        if mapped and mapped != word:
            notes.append(f"scale-name:{word}->{mapped}")
            return f'scale "{mapped}"'
        return match.group(0)

    code = _BARE_SCALE_RE.sub(fix_bare, code)
    return code, notes


def replace_harsh_instruments(code: str) -> tuple[str, list[str]]:
    notes: list[str] = []

    def repl(match: re.Match) -> str:
        keyword, body = match.group(1), match.group(2)
        new_body = body
        for src, dst in P.LEGACY_INSTRUMENT_REPLACEMENTS.items():
            pattern = re.compile(rf"(?<![\w]){re.escape(src)}(?::\d+)?(?![\w])")
            if pattern.search(new_body):
                new_body = pattern.sub(dst, new_body)
                notes.append(f"{src}->{dst}")
        return f'{keyword} "{new_body}"'

    return re.sub(r'\b(sound|s)\s+"([^"]*)"', repl, code), notes


_MELODIC_SYNTHS = {"superpiano", "superfm", "supermandolin", "superhammond", "supervibe", "superpwm",
                   "ys_pad", "ys_softpad", "ys_epiano", "ys_keys", "ys_sub", "ys_pluck", "ys_glass",
                   "ys_air"}


def key_usage_error(code: str) -> str | None:
    """Reject melodic lines that play raw numbers without `scale` (i.e. ignore the key)."""
    for line in code.splitlines():
        synths = set(re.findall(r'\b(?:s|sound)\s+"([\w:]+)', line))
        if not synths & _MELODIC_SYNTHS:
            continue
        uses_numbers = re.search(r'\b(?:n|note)\s+"[^"]*\d', line)
        if uses_numbers and "scale" not in line and "arp" not in line:
            return ("Melodic patterns must stay in the song's key: wrap the degrees in scale, e.g. "
                    'n (scale "minor" "0 2 4 7" + 0) for C minor, or + 7 for G minor. '
                    "Raw note numbers without scale ignore the key.")
    return None
