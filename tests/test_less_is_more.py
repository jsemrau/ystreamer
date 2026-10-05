"""Less is more: a melody sings in a few notes, held and with rests."""

import random

import pytest

from music.blocks import BLOCKS, STYLE_SETTINGS, find_block
from music.livecode import (NOTE_BUDGET, Edit, EditError, Program, Track, apply_edit, harmony_problem,
                            notes_per_bar, within_budget)
from music.plan import validate_hook

PHRASE = 'n (scale "{mode}" ("{melody}" |+ "<{roots}>") {root}) # s "ys_glass"'
CYCLE = 'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root}) # s "ys_kalimba"'


def test_notes_are_counted_per_bar_and_per_stream():
    assert notes_per_bar(PHRASE, "melody", "<[0 _ 2 4 _ 7 9 _] [11 _ 9 7 _ 4 2 0]>") == 6
    assert notes_per_bar(PHRASE, "melody", "4 3 2 ~ 0 _ _ ~") == 4
    assert notes_per_bar(CYCLE, "arp", "0 2 4 7 4 2 9") == 8          # a note every 8th
    assert notes_per_bar(CYCLE, "arp", "0 ~ 4 ~ 7 ~ 4 ~") == 4


@pytest.mark.parametrize("tune", ["<[0 _ 2 4 _ 7 9 _] [11 _ 9 7 _ 4 2 0]>", "[0 2 3 4 4 3 2 0 2 3 4 2 0 _ _ ~]",
                                  "<[0 ~ 2 3 4 _ ~ 2 0 ~ ~ ~ 7 _ _ ~] [2 ~ 4 3 2 _ ~ 0 4 ~ ~ ~ 9 _ _ ~]>"])
def test_a_busy_tune_is_thinned_keeping_its_beats_and_harmony(tune):
    thin = within_budget(PHRASE, "melody", tune, 5)
    assert notes_per_bar(PHRASE, "melody", thin) <= 5 and harmony_problem("lead", thin) is None
    assert len(thin.split()) == len(tune.split())                    # same steps: same rhythm grid
    assert thin.split()[0] == tune.split()[0]                        # the downbeat stays
    assert within_budget(PHRASE, "melody", "4 3 2 ~ 0 _ _ ~", 5) == "4 3 2 ~ 0 _ _ ~"


def test_a_busy_stream_gets_rests():
    thin = within_budget(CYCLE, "arp", "<0 2 4 7 4 2 9 0 2 4 7 4 6 9>", 4)
    assert notes_per_bar(CYCLE, "arp", thin) <= 4 and thin.startswith("<0 ~ 2 ~")


def test_an_edit_that_floods_the_lead_is_refused():
    track = Track("T", "trance", "A minor", 132, [0, 5, 2, 6], 2)
    program = apply_edit(Program(track), Edit("add", role="lead", line=track.make(find_block("trance", "glass_lead"),
                                                                                  random.Random(0))))
    with pytest.raises(EditError, match="less is more"):
        apply_edit(program, Edit("set_pattern", role="lead", name="melody", value="[0 2 4 2 0 2 4 7 4 2 0 2 4 2 0 _]"))
    apply_edit(program, Edit("set_pattern", role="lead", name="melody", value="<[4 _ _ 2 0 _ _ ~] [2 _ 4 _ _ _ ~ ~]>"))


def test_the_directors_hook_and_sketch_come_out_within_budget():
    hook, why = validate_hook({"role": "lead", "pattern": "[0 2 3 4 4 3 2 0 2 3 4 2 0 _ _ ~]"}, "trance")
    assert not why and notes_per_bar('"{h}"', "h", hook["pattern"]) <= NOTE_BUDGET["lead"]
    track = Track("T", "trance", "A minor", 132, [0, 5, 2, 6], 2,
                  hook={"role": "lead", "kind": "phrase", "pattern": "[0 2 3 4 4 3 2 0 2 3 4 2 0 _ _ ~]"})
    line = track.make(find_block("trance", "glass_lead"), random.Random(0))
    assert notes_per_bar(line.body, "melody", line.patterns["melody"]) <= 5


def test_every_written_lead_and_keys_line_is_within_budget():
    for style, blocks in BLOCKS.items():
        for block in blocks:
            budget = block.max_notes or NOTE_BUDGET.get(block.role, 0)
            slot = block.hook_slot() or block.sketch_slot()
            if not budget or block.drums or not slot:
                continue
            for variant in block.patterns.get(slot[0], []):
                assert notes_per_bar(block.body, slot[0], variant) <= budget, (style, block.name, variant)


def test_two_melodic_lines_at_most_in_every_style():
    assert all(settings.get("max_melodic") == 2 for settings in STYLE_SETTINGS.values())
