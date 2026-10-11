"""The music should develop: harmony changes between sections, fills at
phrase ends, builds that lead into peaks, melodies that evolve."""

import random

from music import presets as P
from music.director import Director
from music.engine import MusicEngine
from music.plan import Arrangement, Piece, Section, apply_key_lift, plan_piece, validate_arrangement, validate_piece
from music.templates import HarmonyContext, render_layer
from music.theory import PROGRESSIONS_BY_NAME, Key, progression_chords, with_ninth

from helpers import note_numbers, sequence


def ctx_for(section="verse", next_section="", start=0, bars=16, **kw):
    key = Key.parse("D minor")
    chords = progression_chords(key, PROGRESSIONS_BY_NAME["i-VI-III-VII"])
    return HarmonyContext(key, chords, 1, seed=5, start=start, bars=bars, section=section,
                          next_section=next_section, **kw)


def test_ninths_only_add_whole_step_ninths():
    key = Key.parse("C major")
    for degree in range(7):
        from music.theory import diatonic_chord
        chord = with_ninth(key, diatonic_chord(key, degree))
        if len(chord.notes) == 5:
            assert (chord.notes[4] - chord.root) % 12 == 2
    iii = with_ninth(key, diatonic_chord(key, 2))  # E minor: F would be a flat 9
    assert len(iii.notes) == 4


def test_pieces_change_harmony_between_sections():
    for seed in range(20):
        piece = plan_piece(random.Random(seed))
        mode = Key.parse(piece.key).mode
        assert piece.progression_for("chorus") != piece.progression
        for prog in piece.section_progressions.values():
            assert mode in PROGRESSIONS_BY_NAME[prog].modes


def test_key_lift_only_on_last_of_several_choruses():
    piece = plan_piece(random.Random(0), style="deephouse")     # a plan with several choruses
    assert apply_key_lift(piece)
    lifted = [i for i, s in enumerate(piece.sections) if s.key_shift]
    choruses = [i for i, s in enumerate(piece.sections) if s.name == "chorus"]
    assert lifted == [choruses[-1]]
    single = Piece("t", "lofi", "C minor", 80, "i-VI-III-VII", 1, "calm",
                   [Section("verse", 8, 0.5), Section("chorus", 8, 0.8)])
    assert not apply_key_lift(single)


def test_engine_uses_section_progression_and_lifted_key():
    piece = plan_piece(random.Random(3), style="deephouse")
    apply_key_lift(piece)
    engine = MusicEngine(lambda lines: None, Director(None, random.Random(0), log=lambda m: None), log=lambda m: None)
    chorus = [i for i, s in enumerate(piece.sections) if s.name == "chorus"]
    first = engine.harmony_for(piece, chorus[0])
    last = engine.harmony_for(piece, chorus[-1])
    verse = engine.harmony_for(piece, next(i for i, s in enumerate(piece.sections) if s.name == "verse"))
    assert [c.degree for c in first.chords] == list(PROGRESSIONS_BY_NAME[piece.progression_for("chorus")].degrees)
    assert [c.degree for c in verse.chords] == list(PROGRESSIONS_BY_NAME[piece.progression].degrees)
    assert last.key.root == (Key.parse(piece.key).root + 2) % 12
    arrangement = Arrangement(layers={"pad": "warm_pad", "bass": "deep_bass"})
    rendered = engine.render_section(piece, chorus[-1], arrangement, {}, start=0)
    for role, expr in rendered.expressions.items():
        assert all(last.key.contains(n) for n in note_numbers(expr)), role


def test_drum_fills_and_variations_land_on_phrase_ends():
    ctx = ctx_for(start=12, bars=16)
    drums = render_layer("drums", ctx, P.PRESETS["drums"]["lofi_kit"], 0.6)
    assert "whenmod 8 7 (const" in drums      # fill on bar 8 of each phrase
    assert "whenmod 4 3 (overlay" in drums    # small turnaround on bar 4
    assert "rotR 4 $ whenmod 8 7" in drums     # counted from the section start (12 % 8)
    assert "(range" in drums and "rand)" in drums  # humanised velocities
    assert "nudge" in drums                   # loose timing for lofi
    steady = render_layer("drums", ctx_for(bars=16, fills=False), P.PRESETS["drums"]["lofi_kit"], 0.6)
    assert "whenmod" not in steady


def test_build_rolls_drops_kick_and_bass_and_opens_filter():
    ctx = ctx_for(section="build", next_section="chorus", start=40, bars=16)
    drums = render_layer("drums", ctx, P.PRESETS["drums"]["house_kit"], 0.6)
    assert "whenmod 16 15 (const (s \"cp*16\"" in drums   # last bar: roll only, no kick
    assert "whenmod 16 14 (overlay (s \"cp*8\"" in drums
    assert "rotR 8 $" in drums                             # 40 % 16
    bass = render_layer("bass", ctx, P.PRESETS["bass"]["deep_bass"], 0.6)
    assert "whenmod 16 15 (const silence)" in bass
    pad = render_layer("pad", ctx, P.PRESETS["pad"]["warm_pad"], 0.6)
    assert "slow 16 $ rotL 8 $ range" in pad and "saw" in pad
    # Not before a peak (or not a build): no roll, gentle drift instead.
    calm = render_layer("pad", ctx_for(section="build", next_section="breakdown"), P.PRESETS["pad"]["warm_pad"], 0.6)
    assert "saw" not in calm and "sine" in calm


def test_movement_levels():
    for movement, expect in (("still", None), ("gentle", "slow 16"), ("lively", "slow 8")):
        pad = render_layer("pad", ctx_for(movement=movement), P.PRESETS["pad"]["warm_pad"], 0.6)
        lpf = pad.split("# lpf ")[1]
        if expect:
            assert lpf.startswith(f"({expect} $ range")
        else:
            assert lpf[0].isdigit()


def test_returning_sections_keep_the_opening_but_answer_differently():
    first = sequence(render_layer("melody", ctx_for(variation=0), P.PRESETS["melody"]["felt_lead"], 0.6))
    again = sequence(render_layer("melody", ctx_for(variation=1), P.PRESETS["melody"]["felt_lead"], 0.6))
    half = len(first) // 2
    assert len(first) == 8  # 4-chord loop at one bar per chord -> 8-bar phrase
    assert first[:half] == again[:half]
    assert first[half:] != again[half:]


def test_llm_can_set_variety_controls():
    fallback = plan_piece(random.Random(3), style="lofi")
    mode = Key.parse(fallback.key).mode
    options = [p for p, prog in PROGRESSIONS_BY_NAME.items() if mode in prog.modes and p != fallback.progression]
    data = {"section_progressions": {"chorus": options[0], "verse": "not-a-progression"},
            "color": "ninths", "key_lift": True}
    piece, notes = validate_piece(data, fallback)
    assert piece.section_progressions["chorus"] == options[0]
    assert piece.color == "ninths"
    assert any("verse" in n for n in notes)
    arr, _ = validate_arrangement({"layers": {"pad": "warm_pad"}, "fills": False, "movement": "lively"},
                                  piece, Arrangement(layers={"pad": "warm_pad"}))
    assert arr.fills is False and arr.movement == "lively"


def test_old_state_files_still_load():
    old = {"title": "x", "style": "lofi", "key": "C minor", "bpm": 80, "progression": "i-VI-III-VII",
           "cycles_per_chord": 1, "mood": "calm", "sections": [{"name": "intro", "bars": 8, "energy": 0.2}]}
    piece = Piece.from_dict(old)
    assert piece.sections[0].key_shift == 0 and piece.color == "sevenths"
    assert Arrangement.from_dict({"layers": {}}).fills is True
