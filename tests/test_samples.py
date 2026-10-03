import os
import random
import re

import pytest

from music.blocks import BLOCKS, BREAK_LOOPS, find_block
from music.livecode import Edit, EditError, Program, Track, apply_edit
from music.performer import Performer

REPO = os.path.join(os.path.dirname(__file__), "..")
DIRT = os.path.expanduser("~/.local/share/SuperCollider/downloaded-quarks/Dirt-Samples")
AUDIO = (".wav", ".aif", ".aiff", ".aifc")


def _bank_size(bank):
    for root in (os.path.join(REPO, "samples"), DIRT):
        folder = os.path.join(root, bank)
        if os.path.isdir(folder):
            return sum(f.lower().endswith(AUDIO) for f in os.listdir(folder))
    return None


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO, "samples")), reason="samples/ isn't here")
def test_every_numbered_sample_in_a_block_exists():
    for style, blocks in BLOCKS.items():
        for block in blocks:
            text = block.body + " " + " ".join(v for vs in block.patterns.values() for v in vs)
            for bank, n in re.findall(r"\b([a-z][a-z0-9]*):(\d+)", text):
                size = _bank_size(bank)
                assert size is not None, (style, block.name, bank)
                assert int(n) < size, (style, block.name, f"{bank}:{n}", size)


def test_the_new_sample_lines_are_where_they_belong():
    assert find_block("lofi", "break_loop") and find_block("downtempo", "break_loop")
    assert find_block("lofi", "foley_bed") and find_block("ambient", "foley_bed")
    assert find_block("darkwave", "electro_kit").role == "beat"
    assert find_block("darkwave", "guitar_shards").role == "perc"


def test_a_break_loop_stays_the_loop_it_started_as():
    track = Track("T", "lofi", "A minor", 80, [0, 5, 2, 6], 1)
    program = Program(track)
    block = find_block("lofi", "break_loop")
    program = apply_edit(program, Edit("add", role="beat", line=block.make(random.Random(0))))
    assert program.lines["beat"].patterns["loop"] in BREAK_LOOPS
    with pytest.raises(EditError):
        apply_edit(program, Edit("set_pattern", role="beat", name="loop", value="break:3 break:3"))
    performer = Performer(random.Random(0), style="lofi", log=lambda *_: None)
    for seed in range(30):
        performer.rng = random.Random(seed)
        assert performer._pattern_edit("beat", program.lines["beat"]) is None


def test_a_drum_edit_names_whole_sounds_that_exist():
    from music.livecode import _SOUND_RE, known_sounds

    # Whole names, never a piece of one: "oh" out of "808oh" once let Claude
    # ask SuperDirt for an "oh" that doesn't exist.
    assert _SOUND_RE.findall("[~ 808oh]*4, hh*16") == ["808oh", "hh"]
    assert _SOUND_RE.findall("ys_kick*4 cpu:3") == ["ys_kick", "cpu:3"]
    track = Track("T", "trance", "E dorian", 128, [0, -1], 2)
    program = apply_edit(Program(track), Edit("add", role="perc",
                                              line=find_block("trance", "trance_hats").make(random.Random(0))))
    with pytest.raises(EditError, match="808oh"):
        apply_edit(program, Edit("set_pattern", role="perc", name="hats", value="[~ hh hh ~]*4, [~ ~ oh ~]*4"))
    apply_edit(program, Edit("set_pattern", role="perc", name="hats", value="[~ hh hh ~]*4, [~ ~ 808oh ~]*4"))
    if known_sounds():
        assert "808oh" in known_sounds() and "oh" not in known_sounds()
