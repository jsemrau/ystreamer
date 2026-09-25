"""
Role templates: turn (harmony context, preset, energy) into one line of
Tidal code per layer.

All harmonic layers share one "harmonic clock": the progression has
N chords x cycles_per_chord cycles, and every layer is written as a `<...>`
sequence with exactly that period (or `slow k` over N entries, which is the
same period). Tidal indexes `<...>` by absolute cycle number, so layers added
at any point line up on the same chord automatically.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import presets as P
from .melody import MelodySpec, arp_pattern, generate_melody
from .theory import Chord, Key, bass_note, voice_chords


@dataclass
class HarmonyContext:
    key: Key
    chords: list[Chord]
    cycles_per_chord: int
    seed: int = 0
    pad_voicings: list[tuple[int, ...]] = field(default_factory=list)
    keys_voicings: list[tuple[int, ...]] = field(default_factory=list)

    def __post_init__(self):
        if not self.pad_voicings:
            self.pad_voicings = voice_chords(self.chords, low=-10, high=14, center=2)
        if not self.keys_voicings:
            # Rootless voicings (3rd, 5th, 7th): the bass already plays the root.
            shells = [Chord(c.degree, c.notes[1:], c.root, c.quality, c.seventh) for c in self.chords]
            self.keys_voicings = voice_chords(shells, low=-7, high=9, center=0)

    @property
    def period(self) -> int:
        return len(self.chords) * self.cycles_per_chord


def _chord_token(voicing: tuple[int, ...]) -> str:
    return "[" + ",".join(str(n) for n in voicing) + "]"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _fx(preset: P.Preset, energy: float, extra: dict | None = None) -> str:
    parts = [f'# gain {_fmt(P.energy_gain(preset.gain, energy))}']
    fx = dict(preset.fx)
    if extra:
        fx.update(extra)
    # Brighter filters as energy rises (never past the preset's ceiling).
    if "lpf" in fx:
        fx["lpf"] = round(fx["lpf"] * (0.7 + 0.3 * max(0.0, min(1.0, energy))))
    for name, value in fx.items():
        parts.append(f"# {name} {_fmt(value)}")
    return " ".join(parts)


def _synth(preset: P.Preset, use_custom: bool) -> str | None:
    if use_custom or not preset.synth.startswith("ys_"):
        return preset.synth
    return preset.fallback_synth


def _intensity(energy: float) -> int:
    return 0 if energy < 0.45 else (1 if energy < 0.75 else 2)


# ---------------------------------------------------------------------------
# Per-role renderers. Each returns the pattern expression (no dN/transition
# prefix, no orbit) or None if the layer can't be rendered.
# ---------------------------------------------------------------------------

def render_pad(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    chords = " ".join(_chord_token(v) for v in ctx.pad_voicings)
    return (f'slow {ctx.cycles_per_chord} $ note "<{chords}>" # s "{synth}" '
            f'# legato {_fmt(preset.legato)} {_fx(preset, energy)} '
            f'# pan (slow 16 $ range 0.4 0.6 sine)')


_KEYS_RHYTHMS = [
    ["x_______", "x_______"],                      # whole-note chords
    ["x___..x_", "..x___x_"],                      # gentle comping
    ["x__x__x_", ".x_x__x_", "x_..x_x_"],          # busier comping
]


def render_keys(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    rng = random.Random(ctx.seed * 7 + 1)
    cells = _KEYS_RHYTHMS[_intensity(energy)]
    cycles = []
    for i in range(ctx.period):
        voicing = ctx.keys_voicings[i // ctx.cycles_per_chord]
        tok = _chord_token(voicing)
        cell = rng.choice(cells)
        steps = []
        for sym in cell:
            if sym == "x":
                steps.append(tok)
            elif sym == "_":
                steps.append("_" if steps and steps[-1] != "~" else "~")
            else:
                steps.append("~")
        cycles.append("[" + " ".join(steps) + "]")
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.42')


def render_bass(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    low, high = preset.register
    intensity = _intensity(energy)
    cycles = []
    n_chords = len(ctx.chords)
    for i in range(ctx.period):
        idx = i // ctx.cycles_per_chord
        chord = ctx.chords[idx]
        root = bass_note(chord, low, high)
        fifth = root + 7 if root + 7 <= high + 5 else root - 5
        last_of_chord = (i % ctx.cycles_per_chord) == ctx.cycles_per_chord - 1
        next_root = bass_note(ctx.chords[(idx + 1) % n_chords], low, high)
        if intensity == 0:
            cell = [str(root)] + ["_"] * 7
        elif intensity == 1:
            cell = [str(root), "_", "_", "~", "~", "~", str(root), "_"]
        else:
            cell = [str(root), "_", "~", str(root), "~", "~", str(fifth), "~"]
        if last_of_chord and intensity > 0 and next_root != root:
            # Diatonic approach note into the next chord's root.
            step = 1 if next_root > root else -1
            approach = next_root - step
            while not ctx.key.contains(approach):
                approach -= step
            cell[7] = str(approach)
        cycles.append("[" + " ".join(cell) + "]")
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.5')


def render_melody(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool,
                  character: str | None = None) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    if character is None:
        character = ["sparse", "flowing", "lively"][_intensity(energy)]
    spec = MelodySpec(character=character, seed=ctx.seed * 13 + 5, low=preset.register[0], high=preset.register[1])
    cycles = generate_melody(ctx.key, ctx.chords, ctx.cycles_per_chord, spec)
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.58')


def render_arp(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    rng = random.Random(ctx.seed * 17 + 3)
    style = rng.choice(["up", "updown", "down"])
    low, high = preset.register
    cycles = []
    for i in range(ctx.period):
        voicing = ctx.pad_voicings[i // ctx.cycles_per_chord]
        shifted = tuple(n + 12 for n in voicing) if max(voicing) + 12 <= high else voicing
        cycles.append(arp_pattern(shifted, low, high, style, rng))
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan (slow 8 $ range 0.3 0.7 sine)')


def render_drums(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    kit = P.DRUM_PATTERNS.get(preset.name)
    if not kit:
        return None
    parts = []
    for sample, pattern, gain in kit[_intensity(energy)]:
        g = _fmt(P.energy_gain(gain, energy))
        expr = f's "{pattern}" # gain {g}'
        if sample.startswith("hh") or sample.endswith("hc"):
            expr = f"degradeBy 0.1 $ {expr}"
        parts.append(expr)
    stack = "stack [" + ", ".join(parts) + "]"
    fx = " ".join(f"# {k} {_fmt(v)}" for k, v in preset.fx.items())
    swing = "swingBy (1/8) 4 $ " if preset.name == "lofi_kit" else ""
    return f"{swing}{stack} {fx}".strip()


def render_texture(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    if preset.name == "vinyl":
        return f'slow 4 $ note "0" # s "{synth}" # legato 1.2 {_fx(preset, energy)}'
    low = preset.register[0]
    roots = " ".join(str(bass_note(c, low, low + 11)) for c in ctx.chords)
    return (f'slow {ctx.cycles_per_chord} $ note "<{roots}>" # s "{synth}" # legato 1.3 '
            f'{_fx(preset, energy)}')


def render_free(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool,
                notes_by_cycle: list[str] | None = None) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth or not notes_by_cycle:
        return None
    return (f'note "<{" ".join(notes_by_cycle)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.65')


RENDERERS = {
    "pad": render_pad,
    "keys": render_keys,
    "bass": render_bass,
    "melody": render_melody,
    "arp": render_arp,
    "drums": render_drums,
    "texture": render_texture,
    "free": render_free,
}


def render_layer(role: str, ctx: HarmonyContext, preset: P.Preset, energy: float,
                 use_custom: bool = True, **kwargs) -> str | None:
    fn = RENDERERS[role]
    return fn(ctx, preset, energy, use_custom, **kwargs)


def with_orbit(role: str, expr: str) -> str:
    return f"({expr}) # orbit {P.ROLE_CHANNELS[role] - 1}"


def statement(role: str, expr: str | None, transition: str = "jumpMod", period: int = 8) -> str:
    """Full single-line Tidal statement for a role.

    transition:
      "jumpMod" - switch at the next cycle that is a multiple of `period`
                  (i.e. at the start of the next progression loop)
      "xfade"   - crossfade over `period` cycles
      "now"     - immediate replace via dN
    """
    ch = P.ROLE_CHANNELS[role]
    body = with_orbit(role, expr) if expr else "silence"
    if transition == "jumpMod":
        return f"jumpMod {ch} {period} $ {body}"
    if transition == "xfade":
        return f"xfadeIn {ch} {period} $ {body}"
    return f"d{ch} $ {body}"
