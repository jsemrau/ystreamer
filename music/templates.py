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

import math
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
    # Where this section sits in time, for section-relative fills and builds.
    start: int = 0            # absolute cycle the section starts on
    bars: int = 0             # section length (0 = unknown: no section-relative effects)
    section: str = ""
    next_section: str = ""
    variation: int = 0        # how many times this section type was already played
    fills: bool = True
    movement: str = "gentle"  # still | gentle | lively
    busy: int = 0             # other layers playing in this section (pads back off when busy)

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

    @property
    def is_build(self) -> bool:
        """A build that leads into a peak: filters rise, drums roll, kick drops."""
        return self.section == "build" and self.bars >= 4 and self.next_section in ("chorus", "drop")


PHRASE = 8  # bars per phrase (fills land on the last bar of each phrase)


def section_relative(ctx: HarmonyContext, length: int, transform: str, pattern: str) -> str:
    """Apply `transform` (a Tidal function of the pattern) relative to the
    section start instead of absolute cycle 0: rotate back, transform, rotate
    forward. `transform` may use whenmod with cycle numbers counted from the
    section start."""
    offset = ctx.start % length
    if offset == 0:
        return f"{transform} $ {pattern}"
    return f"rotR {offset} $ {transform} $ rotL {offset} $ {pattern}"


def _chord_token(voicing: tuple[int, ...]) -> str:
    return "[" + ",".join(str(n) for n in voicing) + "]"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _chord_gain_db(voices: int, reference: int) -> float:
    """Each chord note is its own synth voice, so a 5-note chord is louder
    than a 3-note one. Keep chords at the loudness of `reference` notes."""
    return -10 * math.log10(max(1, voices) / reference)


def _fx(preset: P.Preset, energy: float, extra: dict | None = None,
        ctx: HarmonyContext | None = None, move: bool = False, gain_db: float = 0.0) -> str:
    """Mix settings for a layer. With move=True the low-pass filter moves:
    it opens across a build, or drifts slowly (ctx.movement) otherwise.
    gain_db shifts the level (SuperDirt amplitude ~ gain^4)."""
    gain = P.energy_gain(preset.gain, energy) * 10 ** (gain_db / 80)
    parts = [f'# gain {_fmt(round(gain, 3))}']
    fx = dict(preset.fx)
    if extra:
        fx.update(extra)
    # Brighter filters as energy rises (never past the preset's ceiling).
    if "lpf" in fx:
        fx["lpf"] = round(fx["lpf"] * (0.7 + 0.3 * max(0.0, min(1.0, energy))))
    for name, value in fx.items():
        if name == "lpf" and move and ctx is not None:
            parts.append(f"# lpf {_moving_lpf(ctx, value)}")
        else:
            parts.append(f"# {name} {_fmt(value)}")
    return " ".join(parts)


def _moving_lpf(ctx: HarmonyContext, cutoff: float) -> str:
    if ctx.is_build:
        # Open the filter from dark to bright across the whole build.
        lo, hi = round(cutoff * 0.3), round(cutoff * 1.25)
        return f"({section_relative(ctx, ctx.bars, f'slow {ctx.bars}', f'range {lo} {hi} saw')})"
    if ctx.movement == "lively":
        return f"(slow 8 $ range {round(cutoff * 0.45)} {round(cutoff * 1.1)} sine)"
    if ctx.movement == "gentle":
        return f"(slow 16 $ range {round(cutoff * 0.7)} {round(cutoff)} sine)"
    return _fmt(cutoff)


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
    # Background, not foreground: level-matched to a 4-note chord, and 2.5 dB
    # further back when four or more other layers are playing.
    gain_db = _chord_gain_db(len(ctx.pad_voicings[0]), 4) - (2.5 if ctx.busy >= 4 else 0.0)
    # Only the chord sequence is slowed; effects stay on the normal clock so
    # filter sweeps line up with the section.
    return (f'note (slow {ctx.cycles_per_chord} "<{chords}>") # s "{synth}" '
            f'# legato {_fmt(preset.legato)} {_fx(preset, energy, ctx=ctx, move=True, gain_db=gain_db)} '
            f'# pan (slow 16 $ range 0.4 0.6 sine)')


_KEYS_RHYTHMS = [
    ["x_______", "x_______"],                      # whole-note chords
    ["x___..x_", "..x___x_"],                      # gentle comping
    ["x__x__x_", ".x_x__x_", "x_..x_x_"],          # busier comping
]


# Deep house: short off-beat and tresillo (3-3-2) chord stabs.
_STAB_RHYTHMS = [
    ["..x.....", "......x."],
    ["..x...x.", "x..x..x."],
    ["x..x..x.", ".x.x..x.", "..x...x."],
]


def render_keys(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    rng = random.Random(ctx.seed * 7 + 1)
    rhythms = _STAB_RHYTHMS if preset.name in ("deep_stab", "organ_stab") else _KEYS_RHYTHMS
    cells = rhythms[_intensity(energy)]
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
    gain_db = _chord_gain_db(len(ctx.keys_voicings[0]), 3)
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy, ctx=ctx, move=True, gain_db=gain_db)} # pan 0.42')


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
        if preset.name == "rolling_bass":
            # Rolling 16ths that leave room for the kick on every beat.
            r, o = str(root), str(root + 12)
            beat = (["~", "~", r, "~"] if intensity == 0 else
                    ["~", r, r, r])
            cell = beat * 4
            if intensity == 2:
                cell[15] = o  # octave pop at the end of the bar
            cycles.append("[" + " ".join(cell) + "]")
            continue
        fifth = root + 7 if root + 7 <= high + 5 else root - 5
        last_of_chord = (i % ctx.cycles_per_chord) == ctx.cycles_per_chord - 1
        next_root = bass_note(ctx.chords[(idx + 1) % n_chords], low, high)
        if preset.name == "deep_bass":
            # Syncopated, off-beat deep-house bass (root, octave at high energy).
            octave = str(root + 12)
            r = str(root)
            cell = ([ "~", r, "_", "~", "~", r, "_", "~"] if intensity == 0 else
                    [r, "_", "~", r, "~", r, "_", "~"] if intensity == 1 else
                    [r, "_", "~", r, "~", r, octave, "~"])
        elif intensity == 0:
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
    expr = (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.5')
    if ctx.is_build:
        # The bass drops out on the last bar before the peak, with the kick.
        expr = section_relative(ctx, ctx.bars, f"whenmod {ctx.bars} {ctx.bars - 1} (const silence)", f"({expr})")
    return expr


def render_melody(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool,
                  character: str | None = None) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    if character is None:
        character = ["sparse", "flowing", "lively"][_intensity(energy)]
    low, high = preset.register
    # Short loops get a two-loop phrase (statement, then answer), so the
    # melody doesn't repeat every 4 bars.
    chords = ctx.chords * (2 if ctx.period < PHRASE else 1)
    spec = MelodySpec(character=character, seed=ctx.seed * 13 + 5, low=low, high=high)
    cycles = generate_melody(ctx.key, chords, ctx.cycles_per_chord, spec)
    if ctx.variation:
        # A returning section keeps its opening (recognisable) but answers
        # differently each time it comes back.
        answer = generate_melody(ctx.key, chords, ctx.cycles_per_chord,
                                 MelodySpec(character=character, seed=spec.seed + 101 * ctx.variation,
                                            low=low, high=high))
        half = len(cycles) // 2
        cycles = cycles[:half] + answer[half:]
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy)} # pan 0.58')


# Trance gate (1 = open, 0 = closed) per 8 sixteenths, by intensity; the
# middle one is the classic 1 0 1 1 0 1 0 1.
_TRANCE_GATES = ["10010010", "10110101", "10111011"]
# Order in which the arp walks the chord tones (after Switch Angel's
# 57 60 64 67 64 60 69 64): up, back down, reach for the top, return.
_TRANCE_WALK = [0, 1, 2, 3, 2, 1, 4, 2]


def _render_trance_gate(ctx: HarmonyContext, preset: P.Preset, energy: float, synth: str) -> str:
    low, high = preset.register
    gate = _TRANCE_GATES[_intensity(energy)]
    cycles = []
    for i in range(ctx.period):
        voicing = ctx.pad_voicings[i // ctx.cycles_per_chord]
        tones = sorted({n for n in voicing} | {n + 12 for n in voicing})
        tones = [n for n in tones if low <= n <= high] or sorted(voicing)
        steps = []
        for step in range(16):
            if gate[step % 8] == "1":
                steps.append(str(tones[_TRANCE_WALK[step % 8] % len(tones)]))
            else:
                steps.append("~")
        cycles.append("[" + " ".join(steps) + "]")
    # A sidechain-style pump: each beat's first 16th ducks under the kick.
    return (f'(note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy, ctx=ctx, move=True)} # pan (slow 8 $ range 0.35 0.65 sine)) '
            f'|* gain "[0.82 1 0.94 1]*4"')


def render_arp(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    synth = _synth(preset, use_custom)
    if not synth:
        return None
    if preset.name == "trance_gate":
        return _render_trance_gate(ctx, preset, energy, synth)
    rng = random.Random(ctx.seed * 17 + 3)
    style = rng.choice(["up", "updown", "down"])
    low, high = preset.register
    cycles = []
    for i in range(ctx.period):
        voicing = ctx.pad_voicings[i // ctx.cycles_per_chord]
        shifted = tuple(n + 12 for n in voicing) if max(voicing) + 12 <= high else voicing
        cycles.append(arp_pattern(shifted, low, high, style, rng))
    return (f'note "<{" ".join(cycles)}>" # s "{synth}" # legato {_fmt(preset.legato)} '
            f'{_fx(preset, energy, ctx=ctx, move=True)} # pan (slow 8 $ range 0.3 0.7 sine)')


def _drum_stack(voices: list[tuple[str, str, float]], energy: float, loose: bool) -> str:
    parts = []
    for sample, pattern, gain in voices:
        g = P.energy_gain(gain, energy)
        # Humanise: every hit a little softer or louder, like a real player.
        expr = f's "{pattern}" # gain (range {_fmt(round(g * 0.88, 3))} {_fmt(g)} rand)'
        if loose:
            expr += " # nudge (range 0 0.012 rand)"
        if sample.startswith("hh") or sample.endswith("hc"):
            expr = f"degradeBy 0.1 $ {expr}"
        parts.append(expr)
    return "stack [" + ", ".join(parts) + "]"


def render_drums(ctx: HarmonyContext, preset: P.Preset, energy: float, use_custom: bool) -> str | None:
    kit = P.DRUM_PATTERNS.get(preset.name)
    if not kit:
        return None
    loose = preset.name in P.LOOSE_KITS

    def voices(items):
        if use_custom:
            return items
        out = []
        for sample, pattern, gain in items:
            for synth, fallback in P.DRUM_SYNTH_FALLBACK.items():
                pattern = pattern.replace(synth, fallback)
            out.append((P.DRUM_SYNTH_FALLBACK.get(sample, sample), pattern, gain))
        return out

    expr = _drum_stack(voices(kit[_intensity(energy)]), energy, loose)

    if ctx.fills and ctx.bars >= PHRASE:
        variation = P.DRUM_VARIATIONS.get(preset.name)
        if variation:
            expr = section_relative(ctx, 4, f"whenmod 4 3 (overlay ({_drum_stack(voices(variation), energy, loose)}))",
                                    f"({expr})")
        fill = P.DRUM_FILLS.get(preset.name)
        if fill:
            expr = section_relative(ctx, PHRASE, f"whenmod {PHRASE} {PHRASE - 1} (const ({_drum_stack(voices(fill), energy, loose)}))",
                                    f"({expr})")

    if ctx.is_build:
        roll = P.DRUM_ROLL_SAMPLE.get(preset.name, "sn")
        g = P.energy_gain(0.85, energy)
        eighths = f's "{roll}*8" # gain (range {_fmt(round(g * 0.6, 3))} {_fmt(g)} saw)'
        # Last bar: the kick drops out and a 16th roll swells into the peak.
        sixteenths = f's "{roll}*16" # gain (range {_fmt(round(g * 0.55, 3))} {_fmt(g)} saw)'
        expr = section_relative(ctx, ctx.bars, f"whenmod {ctx.bars} {ctx.bars - 2} (overlay ({eighths}))", f"({expr})")
        expr = section_relative(ctx, ctx.bars, f"whenmod {ctx.bars} {ctx.bars - 1} (const ({sixteenths}))", f"({expr})")

    fx = " ".join(f"# {k} {_fmt(v)}" for k, v in preset.fx.items())
    swing = {"lofi_kit": "swingBy (1/8) 4 $ ", "house_kit": "swingBy (1/24) 4 $ "}.get(preset.name, "")
    return f"{swing}({expr}) {fx}".strip()


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
