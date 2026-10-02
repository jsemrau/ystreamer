"""Less repetition, in every style: every part can come in, the music itself
changes at least every third move, tracks are about 7 minutes and change
shape, and the harmony moves."""

import random

import pytest

from music.blocks import BLOCKS, find_block
from music.livecode import Edit, EditError, Program, apply_edit
from music.performer import Performer
from music.plan import STYLES, plan_piece
from music.theory import parse_progression

STYLE_NAMES = sorted(BLOCKS)


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_every_part_the_style_has_can_come_in(style):
    performer = Performer(random.Random(0), style=style, log=lambda *_: None)
    program = Program(performer.new_track(None))
    performer.settings = dict(performer.settings, max_melodic=None)      # count every role, not the cap
    assert set(performer._missing_roles(program)) == {b.role for b in BLOCKS[style]}


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_after_two_knob_turns_the_music_itself_changes(style):
    performer = Performer(random.Random(1), style=style, log=lambda *_: None)
    program = Program(performer.new_track(None))
    first = {}
    for block in BLOCKS[style]:
        first.setdefault(block.role, block)
    for block in list(first.values())[:4]:
        program = apply_edit(program, Edit("add", role=block.role, line=program.track.make(block, random.Random(0))))
    role = next(r for r in program.lines if not program.lines[r].drums)
    for _ in range(2):
        performer.applied(Edit("set_fx", role=role, name="lpf", value="2000"))
    assert performer.must_change_music() and "must change the music" in performer.complexity_note(program)
    with pytest.raises(EditError):
        performer._edit_from_json({"op": "set_fx", "role": role, "name": "lpf", "value": "1500"}, program)
    assert all(e.op not in Performer.KNOB_OPS for e in performer._candidates(program))
    schema_ops = {v["properties"]["op"]["const"] for v in performer._edit_schema(program)["anyOf"]}
    assert not schema_ops & set(Performer.KNOB_OPS)
    for _ in range(10):
        assert performer._tweak(program).op not in Performer.KNOB_OPS
    performer.applied(Edit("set_pattern", role=role, name="x", value="0"))
    assert not performer.must_change_music()


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_tracks_are_about_seven_minutes_and_change_shape(style):
    rng = random.Random(3)
    previous = None
    for _ in range(4):
        piece = plan_piece(rng, previous, style=style, target_minutes=7)
        assert 4.5 <= piece.total_bars / piece.cps / 60 <= 9.5
        if previous is not None:
            assert [s.name for s in piece.sections] != [s.name for s in previous.sections]
        previous = piece


def test_one_chord_is_not_a_progression():
    for mode, name in (("minor", "i-i-i-i"), ("phrygian", "II-II-II-II")):
        with pytest.raises(ValueError, match="two different chords"):
            parse_progression(name, mode)
    assert parse_progression("i-i-VI-VII", "minor").degrees == (0, 0, 5, 6)


def test_club_styles_change_chord_every_two_bars():
    assert all(STYLES[s]["cycles_per_chord"] <= 2 for s in STYLES)


def test_a_slow_track_is_trimmed_to_about_its_target_but_keeps_its_minimums():
    from music.plan import MIN_BUILD_BARS, fit_live_plan

    plan = [["build", 32], ["groove", 64], ["breakdown", 32], ["rebuild", 32], ["groove2", 64], ["outro", 32]]
    cps = 66 / 240                                           # ambient's tempo: a bar is 3.6 s
    fitted = fit_live_plan(plan, cps, 5)
    assert sum(b for _, b in fitted) <= 1.15 * 5 * 60 * cps + 8
    assert all(b >= (MIN_BUILD_BARS if p in ("build", "rebuild") else 8) for p, b in fitted)
    assert [p for p, _ in fitted] == [p for p, _ in plan]   # the shape stays
    assert fit_live_plan(plan, 138 / 240, 12) == plan        # already short enough: untouched
