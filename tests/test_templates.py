import random

import pytest

from music import presets as P
from music.templates import HarmonyContext, render_layer, statement, with_orbit
from music.theory import PROGRESSIONS, Key, progression_chords

from helpers import note_numbers, sequence


def contexts():
    rng = random.Random(0)
    for prog in PROGRESSIONS:
        for mode in prog.modes:
            key = Key(rng.randrange(12), mode)
            for cpc in (1, 2):
                yield HarmonyContext(key, progression_chords(key, prog), cpc, seed=rng.randrange(100))


PITCHED_ROLES = ["pad", "keys", "bass", "melody", "arp", "texture"]


@pytest.mark.parametrize("role", PITCHED_ROLES)
def test_every_pitched_layer_stays_in_key(role):
    for ctx in contexts():
        for preset in P.PRESETS[role].values():
            for energy in (0.1, 0.5, 0.9):
                expr = render_layer(role, ctx, preset, energy)
                if expr is None or preset.name == "vinyl":
                    continue
                notes = note_numbers(expr)
                assert notes, expr
                off = [n for n in notes if not ctx.key.contains(n)]
                assert not off, (role, preset.name, ctx.key, off, expr)


@pytest.mark.parametrize("role", PITCHED_ROLES)
def test_layers_share_the_harmonic_clock(role):
    """Every layer loops with the same period, so chords change together."""
    for ctx in contexts():
        preset = P.default_for(role)
        expr = render_layer(role, ctx, preset, 0.8)
        if expr is None or preset.name == "vinyl":
            continue
        entries = sequence(expr)
        if expr.startswith(f"slow {ctx.cycles_per_chord} $"):
            assert len(entries) * ctx.cycles_per_chord == ctx.period
        else:
            assert len(entries) == ctx.period, (role, expr)


def test_bass_plays_chord_roots_on_the_downbeat():
    for ctx in contexts():
        expr = render_layer("bass", ctx, P.default_for("bass"), 0.9)
        for i, entry in enumerate(sequence(expr)):
            chord = ctx.chords[i // ctx.cycles_per_chord]
            first = int(entry.strip("[]").split()[0])
            assert first % 12 == chord.root


def test_melody_strong_beats_are_chord_tones():
    for ctx in contexts():
        expr = render_layer("melody", ctx, P.default_for("melody"), 0.6)
        for i, entry in enumerate(sequence(expr)):
            chord = ctx.chords[i // ctx.cycles_per_chord]
            steps = entry.strip("[]").split()
            assert len(steps) == 8
            for pos in (0, 4):
                tok = steps[pos]
                if tok not in ("~", "_"):
                    assert int(tok) % 12 in chord.pitch_classes, (ctx.key, chord, entry)


def test_registers_are_respected():
    for ctx in contexts():
        for role in ("bass", "melody"):
            preset = P.default_for(role)
            lo, hi = preset.register
            notes = note_numbers(render_layer(role, ctx, preset, 0.9))
            assert all(lo <= n <= hi + 7 for n in notes), (role, notes)


def test_gains_stay_within_three_db_of_preset():
    for role, presets in P.PRESETS.items():
        for preset in presets.values():
            for energy in (0.0, 1.0):
                g = P.energy_gain(preset.gain, energy)
                ratio_db = 80 * __import__("math").log10(g / preset.gain)
                assert abs(ratio_db) <= 3.1


def test_non_bass_roles_have_highpass():
    for role, presets in P.PRESETS.items():
        if role in ("bass", "drums"):
            continue
        for preset in presets.values():
            assert "hpf" in preset.fx, preset.name


def test_fallback_synths_when_custom_synthdefs_missing():
    ctx = next(contexts())
    expr = render_layer("pad", ctx, P.PRESETS["pad"]["warm_pad"], 0.5, use_custom=False)
    assert 's "superpiano"' in expr
    assert render_layer("texture", ctx, P.PRESETS["texture"]["vinyl"], 0.5, use_custom=False) is None


def test_statement_forms():
    assert statement("pad", "x", "jumpMod", 8) == "jumpMod 3 8 $ (x) # orbit 2"
    assert statement("pad", "x", "xfade", 8) == "xfadeIn 3 8 $ (x) # orbit 2"
    assert statement("drums", "x", "now") == "d1 $ (x) # orbit 0"
    assert statement("bass", None, "jumpMod", 4) == "jumpMod 2 4 $ silence"
    assert with_orbit("melody", "e") == "(e) # orbit 4"


def test_generated_code_is_single_line_and_balanced():
    for ctx in contexts():
        for role in P.ROLES:
            for preset in P.PRESETS[role].values():
                kw = {"notes_by_cycle": ["[0 ~ 2 _ ~ ~ 4 ~]"] * ctx.period} if role == "free" else {}
                expr = render_layer(role, ctx, preset, 0.7, **kw)
                if expr is None:
                    continue
                assert "\n" not in expr
                assert expr.count("(") == expr.count(")")
                assert expr.count("[") == expr.count("]")
                assert expr.count("<") == expr.count(">")
                assert expr.count('"') % 2 == 0
                # `_` may never start a step sequence or follow a rest
                assert "[_" not in expr and "~ _" not in expr


def test_deephouse_kit_is_four_on_the_floor_and_stabs_are_short():
    ctx = next(contexts())
    drums = render_layer("drums", ctx, P.PRESETS["drums"]["house_kit"], 0.2)
    assert 's "bd*4"' in drums
    stabs = render_layer("keys", ctx, P.PRESETS["keys"]["deep_stab"], 0.5)
    assert "# legato 0.35" in stabs
    bass = render_layer("bass", ctx, P.PRESETS["bass"]["deep_bass"], 0.2)
    assert sequence(bass)[0].startswith("[~ ")  # off-beat entry at low energy
