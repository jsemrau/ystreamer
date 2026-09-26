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
