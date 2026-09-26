import random

from music import presets as P
from music.plan import STYLES, arrange, plan_piece, style_from_theme
from music.templates import HarmonyContext, render_layer
from music.theory import PROGRESSIONS_BY_NAME, Key, progression_chords

from helpers import note_numbers, sequence


def ctx(**kw):
    key = Key.parse("A minor")
    return HarmonyContext(key, progression_chords(key, PROGRESSIONS_BY_NAME["i-VI-III-VII"]), 2, seed=1, **kw)


def test_theme_words_pick_trance():
    for theme in ("switch angel trance", "uplifting trance at sunrise", "supersaw madness"):
        assert style_from_theme(theme) == "trance"


def test_trance_gate_arp_is_gated_sixteenths_in_key_with_pump():
    c = ctx()
    arp = render_layer("arp", c, P.PRESETS["arp"]["trance_gate"], 0.6)
    for entry in sequence(arp):
        steps = entry.strip("[]").split()
        assert len(steps) == 16
        gate = "".join("0" if s == "~" else "1" for s in steps[:8])
        assert gate == "10110101"  # the classic trance gate
    assert all(c.key.contains(n) for n in note_numbers(arp))
    assert arp.endswith('|* gain "[0.82 1 0.94 1]*4"')
    assert arp.startswith("(note")  # parenthesised so |* doesn't mix with #


def test_rolling_bass_leaves_room_for_the_kick():
    c = ctx()
    bass = render_layer("bass", c, P.PRESETS["bass"]["rolling_bass"], 0.6)
    for i, entry in enumerate(sequence(bass)):
        steps = entry.strip("[]").split()
        assert len(steps) == 16
        assert all(steps[b] == "~" for b in (0, 4, 8, 12))  # kick beats
        chord = c.chords[i // c.cycles_per_chord]
        assert all(int(n) % 12 == chord.root for n in steps if n != "~")


def test_trance_kit_uses_synth_kick_with_sample_fallback():
    c = ctx()
    kit = P.PRESETS["drums"]["trance_kit"]
    assert 's "ys_kick*4"' in render_layer("drums", c, kit, 0.6, use_custom=True)
    fallback = render_layer("drums", c, kit, 0.6, use_custom=False)
    assert "ys_kick" not in fallback and 's "bd*4"' in fallback


def test_trance_piece_has_breakdown_build_drop():
    piece = plan_piece(random.Random(5), style="trance")
    names = [s.name for s in piece.sections]
    i = names.index("breakdown")
    assert names[i + 1:i + 3] == ["build", "chorus"]
    lo, hi = STYLES["trance"]["bpm"]
    assert lo <= piece.bpm <= hi
    intro = arrange(piece, piece.sections[0], random.Random(0))
    assert "drums" in intro.active_roles() or "arp" in intro.active_roles()
    breakdown = arrange(piece, piece.sections[i], random.Random(0))
    assert "drums" not in breakdown.active_roles()


def test_gated_saw_locks_gate_to_the_bar_and_varies_by_section():
    import re

    def params(section, energy, next_section=""):
        c = ctx(section=section, next_section=next_section, bars=16, cps=138 / 240)
        pad = render_layer("pad", c, P.PRESETS["pad"]["gated_saw"], energy)
        gatetime = float(re.search(r'pF "gatetime" ([\d.]+)', pad).group(1))
        pattern = int(re.search(r'pI "gatepat" (\d)', pad).group(1))
        depth = float(re.search(r'pF "gatedepth" ([\d.]+)', pad).group(1))
        return gatetime, pattern, depth

    gatetime, _, _ = params("verse", 0.6)
    assert abs(gatetime - 240 / 138) < 1e-3          # one bar at 138 BPM
    assert params("breakdown", 0.3)[2] == 0          # ungated swell
    assert params("build", 0.65, "chorus")[1] == 0   # straight chopper
    assert params("chorus", 0.9)[1] in (2, 3)        # stutter gate in the drop
    assert params("verse", 0.6)[1] == 1              # classic 1 0 1 1 0 1 0 1


def test_synthdef_gate_patterns_are_16_steps():
    import os
    import re

    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        text = f.read()
    block = text[text.index("~ysGatePatterns = ["):text.index("];", text.index("~ysGatePatterns"))]
    patterns = re.findall(r'"([01]+)"', block)
    assert patterns[0] == "1011010110110101"
    assert len(patterns) == 3 and all(len(p) == 16 for p in patterns)
    assert "PulseDPW" in text and "LFPulse" in text  # clean chopper with a fallback
