"""The lead stays out of the shrill zone, and signature sounds stay home.

Sunlit Loop (trance, D dorian): a hook peaking on degree 8, lifted an octave
by `every 4 (|+ n 12)`, on the chipmunk vocal: E7, a shriek."""
import pytest

from music.blocks import sound_fits_style
from music.livecode import Edit, EditError, apply_edit, register_problem
from test_livecode import _program_with

HIGH_HOOK = "[0 _ 4 _ 7 _ 9 _]"
LOW_HOOK = "[0 _ 2 _ 4 _ 2 _]"


def _lead():
    program = _program_with("trance", "glass_lead", key="D dorian")
    line = program.lines["lead"]
    slot = next(n for n in line.patterns if n not in ("synth",))
    return program, slot


def test_a_high_hook_is_not_lifted_an_octave():
    program, slot = _lead()
    program = apply_edit(program, Edit("set_pattern", role="lead", name=slot, value=HIGH_HOOK))
    for wrap in ("every 4 (|+ n 12)", "(|+ n 12)"):
        with pytest.raises(EditError, match="shrill"):
            apply_edit(program, Edit("wrap", role="lead", name=wrap))


def test_a_low_hook_may_go_up_but_not_climb_after():
    program, slot = _lead()
    program = apply_edit(program, Edit("set_pattern", role="lead", name=slot, value=LOW_HOOK))
    program = apply_edit(program, Edit("wrap", role="lead", name="(|+ n 12)"))
    with pytest.raises(EditError, match="shrill"):
        apply_edit(program, Edit("set_pattern", role="lead", name=slot, value=HIGH_HOOK))


def test_no_lift_no_problem():
    program, slot = _lead()
    line = program.lines["lead"]
    assert register_problem(line, [], {slot: HIGH_HOOK}) is None


def test_the_chipmunk_vocal_is_happy_hardcore_only():
    assert sound_fits_style("hardcore", "ys_chipvox")
    for style in ("trance", "techno", "deephouse", "lofi"):
        assert not sound_fits_style(style, "ys_chipvox")
    assert sound_fits_style("trance", "ys_glass")
    program, _ = _lead()
    with pytest.raises(EditError):
        apply_edit(program, Edit("set_sound", role="lead", value="ys_chipvox"))
