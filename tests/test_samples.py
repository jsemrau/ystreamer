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



def test_an_sfz_map_gives_one_recording_per_note_from_one_layer(tmp_path):
    from music.instruments import parse_sfz, pick_regions, sfz_key

    assert sfz_key("a4") == 69 and sfz_key("c#2") == 37 and sfz_key("60") == 60
    (tmp_path / "x.sfz").write_text("""
<control> default_path=samples/
<global> ampeg_release=0.5
<group> loop_mode=loop_continuous
<region> lovel=1 hivel=80 pitch_keycenter=60 sample=C4_soft.wav loop_start=100 loop_end=9000
<region> lovel=81 hivel=127 pitch_keycenter=60 sample=C4_loud.wav
<region> key=c#4 seq_position=1 sample=Cs4_a.wav // a round robin
<region> key=c#4 seq_position=2 sample=Cs4_b.wav
<region> key=d4 trigger=release sample=D4_rel.wav
""")
    regions = parse_sfz(str(tmp_path / "x.sfz"))
    assert regions[0]["ampeg_release"] == "0.5" and regions[0]["loop_mode"] == "loop_continuous"
    assert regions[0]["_path"].endswith("samples/C4_soft.wav")
    soft = pick_regions(regions, 60)
    assert sorted(soft) == [60, 61] and soft[60]["sample"] == "C4_soft.wav" and soft[61]["sample"] == "Cs4_a.wav"
    assert pick_regions(regions, 110)[60]["sample"] == "C4_loud.wav"


def test_a_note_is_cut_to_its_attack_levelled_and_keeps_its_loop():
    import numpy as np

    from music.instruments import SR, prepare_note

    t = np.arange(SR) / SR
    late = SR // 10
    x = np.concatenate([np.zeros(late), 0.05 * np.sin(2 * np.pi * 220 * t) * np.exp(-3 * t)]).astype(np.float32)
    y, loop = prepare_note(x, (late + 10000, late + 30000))
    cut = len(x) - len(y)
    assert late - SR // 200 < cut <= late and loop == (late + 10000 - cut, late + 30000 - cut)
    assert abs(20 * np.log10(np.sqrt(np.mean(y[:SR // 2] ** 2))) - (-20.0)) < 0.5
    assert prepare_note(x, (5, 10))[1] is None                      # too short a loop: none


def test_the_real_instruments_load_at_boot_and_replace_their_models():
    with open(os.path.join(REPO, "tidal", "synthdefs.scd")) as f:
        defs = f.read()
    with open(os.path.join(REPO, "tidal", "superdirt_boot.scd")) as f:
        boot = f.read()
    assert "instruments" in boot and "~ysInstruments" in boot and "meta.scd" in boot
    # The sampled SynthDefs come after the models, so a loaded instrument wins.
    for synth in ("ys_nylon", "ys_keys", "ys_sax", "ys_chorusbass"):
        assert defs.index(f"SynthDef(\\{synth},") < defs.index(f"~ysSampled.(\\{synth},"), synth
    from music.instruments import INSTRUMENTS

    assert {i.synth for i in INSTRUMENTS.values()} <= set(re.findall(r"~ysSampled\.\(\\(\w+)", defs))
    assert all(i.page.startswith("https://freepats.zenvoid.org/") for i in INSTRUMENTS.values())


def test_harp_and_kalimba_are_offered_only_where_installed():
    from music.blocks import LIVE_SOUNDS
    from music.plan import STYLE_SOUNDS

    # (conftest: no instruments here.)
    assert "ys_harp" not in LIVE_SOUNDS["arp"] and "ys_kalimba" not in LIVE_SOUNDS["arp"]
    assert all("ys_harp" not in roles.get("arp", []) for roles in STYLE_SOUNDS.values())


def test_the_sax_holds_its_notes_instead_of_running_16ths():
    from music.blocks import held_phrase, track_blocks
    from music.livecode import Track, harmony_problem

    hook = "<[0 ~ 2 3 4 _ ~ 2 0 ~ ~ ~ 7 _ _ ~] [2 ~ 4 3 2 _ ~ 0 4 ~ ~ ~ 9 _ _ ~]>"
    held = held_phrase(hook)
    # At most a new note a beat; a pickup right after a breath stays.
    assert held == "<[0 ~ 2 _ 4 _ ~ 2 0 ~ ~ ~ 7 _ _ ~] [2 ~ 4 _ 2 _ ~ 0 4 ~ ~ ~ 9 _ _ ~]>"
    assert held_phrase("[0 2 3 4 4 3 2 0 2 3 4 2 0 _ _ ~]") == "[0 _ _ _ 4 _ _ _ 2 _ _ _ 0 _ _ ~]"
    assert harmony_problem("lead", held) is None
    assert held_phrase("0 2 4 2 0 ~ 2 ~") == "0 _ 4 _ 0 ~ 2 ~"           # 8ths: one a beat
    for style in ("deephouse", "lofi", "downtempo"):
        track = Track("T", style, "A minor", 122, [0, 5, 2, 6], 2, sounds={"lead": "ys_sax"},
                      hook={"role": "lead", "kind": "phrase", "pattern": hook})
        [block] = track_blocks(style, "", track.sounds, "lead", track)
        line = track.make(block, random.Random(0))
        from music.livecode import notes_per_bar

        # Held the sax's way, then within the lead's budget (5 new notes a bar).
        assert block.name == "sax_lead" and not line.wrappers
        assert notes_per_bar(line.body, "melody", line.patterns["melody"]) <= 5
        assert harmony_problem("lead", line.patterns["melody"]) is None
        assert dict(line.fx)["legato"] == "1.0" and "- 12)" in line.body     # an octave down
