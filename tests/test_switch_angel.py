"""Trance harmony after Switch Angel's strudel-scripts, and the NRG-CP progressions."""

import random

import pytest

from music.blocks import TRANCE_GATES, find_block, trance_gate
from music.livecode import (LEAD_WRAPPERS, TRANCE_VOICINGS, Edit, EditError, Program, apply_edit, arp_sequences,
                            harmony_problem, range_problem)
from music.performer import Performer
from music.theory import PROGRESSIONS_BY_NAME, progressions_for


def test_the_progression_library_comes_from_nrg_cp_weighted_by_use():
    minor = progressions_for("minor")
    assert minor[0].name == "i-VI-III-VII" and minor[0].weight > 400        # the most common in the dataset
    assert all(p.weight >= minor[-1].weight for p in minor)
    assert all(len(set(p.name.split("-"))) >= 2 for p in PROGRESSIONS_BY_NAME.values())


def test_a_trance_voicing_shapes_every_chord_and_the_arps_play_only_its_notes():
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    assert track.voicing in TRANCE_VOICINGS.values()
    track.voicing = TRANCE_VOICINGS["open triad"]
    chords = track.harmony()["chords"]
    assert "[0,4,7,9]" in chords and ",6]" not in chords                      # no added sevenths
    arp = track.make(find_block("trance", "pluck_16"), random.Random(0))
    assert arp.patterns["arp"] in arp_sequences(track.voicing).values()
    assert harmony_problem("arp", arp.patterns["arp"]) is None


def test_trance_gates_hold_each_hit_until_the_next():
    gate = trance_gate(45, 1.0)
    steps = gate.split()
    assert len(steps) == 16 and set(steps) <= {"t", "_", "~"}
    assert "~" not in steps[steps.index("t"):]            # after the first hit, holds rather than rests
    assert trance_gate(45, 1.0) == gate                   # seeded: the same gate every time
    assert len(set(TRANCE_GATES)) == len(TRANCE_GATES)


def test_pitched_lines_keep_to_their_chord_and_register():
    assert "jux rev" not in LEAD_WRAPPERS and not any("<~" in w or "~>" in w or "off" in w for w in LEAD_WRAPPERS)
    assert range_problem("arp", "0 4 7 21") and range_problem("lead", "<[7 ~ 9] [18 ~ 14]>")
    assert range_problem("bass", "[~ 0 0 7]*4") is None


def test_a_chord_rhythm_can_be_edited_and_the_melody_count_is_capped():
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    program = Program(performer.new_track(None))
    for role, name in (("beat", "trance_beat"), ("chords", "piano_stabs"), ("acid", "acid"), ("arp", "pluck_16")):
        program = apply_edit(program, Edit("add", role=role, line=program.track.make(find_block("trance", name),
                                                                                      random.Random(0))))
    program = apply_edit(program, Edit("set_pattern", role="chords", name="stab", value="~ t _ t ~ t t _"))
    assert program.lines["chords"].patterns["stab"] == "~ t _ t ~ t t _"
    assert "lead" not in performer._missing_roles(program)            # acid and arp already carry the melody
    with pytest.raises(EditError):
        performer._edit_from_json({"op": "add", "block": "glass_lead"}, program)


def test_the_clap_stays_on_the_backbeat():
    from music.blocks import BLOCKS
    from music.livecode import CHOICE_SLOTS
    from test_livecode import _program_with

    program = _program_with("trance", "trance_beat", "trance_hats")
    with pytest.raises(EditError):        # 8 steps: claps after beats 1 and 2
        apply_edit(program, Edit("set_pattern", role="perc", name="clap", value="~ cp ~ cp ~ ~ cp ~"))
    fill = "~ cp ~ <cp cp cp [~ cp cp cp]>"
    assert apply_edit(program, Edit("set_pattern", role="perc", name="clap", value=fill)).lines["perc"].patterns["clap"] == fill
    # Every block's own clap is one of the choices, and the built-in performer picks among them.
    assert all(v in CHOICE_SLOTS["clap"] for blocks in BLOCKS.values() for b in blocks for v in b.patterns.get("clap", []))
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    for _ in range(30):
        edit = performer._pattern_edit("perc", program.lines["perc"])
        if edit and edit.name == "clap":
            assert edit.value in CHOICE_SLOTS["clap"]


def test_club_drums_stay_straight_and_the_kick_is_alone():
    from music.livecode import CLUB_DRUM_WRAPPERS, allowed_wrappers
    from test_livecode import _program_with

    program = _program_with("trance", "trance_beat", "trance_hats")
    assert program.lines["beat"].patterns == {"kick": "ys_kick909*4"}
    for role in ("beat", "perc"):
        wraps = allowed_wrappers(program.lines[role], "trance")
        assert wraps == CLUB_DRUM_WRAPPERS
        assert not any(w in " ".join(wraps) for w in ("fast", "<~", "~>", "swing", "echo", "degrade"))
    with pytest.raises(EditError):
        apply_edit(program, Edit("wrap", role="beat", name="every 4 (fast 2)"))
    assert "swingBy (1/8) 4" in allowed_wrappers(_program_with("lofi", "dusty_beat").lines["beat"], "lofi")
