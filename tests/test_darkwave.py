import os
import random
import re

from music.blocks import BLOCKS, LIVE_SOUNDS, SUBGENRES, find_block, subgenre_blocks, track_blocks
from music.livecode import Program, Track
from music.mix import SYNTH_LUFS
from music.performer import Performer
from music.plan import STYLES, plan_piece

SAMPLES = {"808bd", "808sd", "808hc", "808oh", "cp"}
NEW_SYNTHS = {"ys_chorusbass", "ys_coldguitar", "ys_coldstrings", "ys_gatesnare", "ys_wavelead", "ys_wavesynth"}


def _defined_synths():
    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        return set(re.findall(r"SynthDef\(\\(\w+)", f.read()))


def test_the_new_voices_exist_and_the_tonal_ones_are_measured():
    assert NEW_SYNTHS <= _defined_synths()
    assert {"ys_chorusbass", "ys_coldguitar", "ys_coldstrings", "ys_wavelead", "ys_wavesynth"} <= set(SYNTH_LUFS)


def test_every_subgenre_names_real_blocks_sounds_and_kit_sounds():
    defined = _defined_synths()
    lo, hi = STYLES["darkwave"]["bpm"]
    assert set(SUBGENRES["darkwave"]) == {"post-punk", "minimal synth", "ethereal"}
    for name, variant in SUBGENRES["darkwave"].items():
        assert lo <= variant["bpm"][0] <= variant["bpm"][1] <= hi, name
        for role, blocks in variant["blocks"].items():
            for block in blocks:
                assert find_block("darkwave", block).role == role, (name, block)
        for role, sounds in variant["sounds"].items():
            assert set(sounds) <= set(LIVE_SOUNDS[role]), (name, role)
        for slot in ("kick", "snare", "hats"):
            for pattern in variant["kit"].get(slot, []):
                names = set(re.findall(r"\b\w*[a-z]\w*", pattern))
                assert names <= SAMPLES | defined, (name, pattern)


def test_the_subgenre_picks_its_bass():
    assert [b.name for b in subgenre_blocks("darkwave", "post-punk", "bass")] == ["chorus_bass"]
    assert [b.name for b in subgenre_blocks("darkwave", "minimal synth", "bass")] == ["sequencer_bass"]
    # No subgenre (or another style): every block for the part.
    assert len(subgenre_blocks("darkwave", "", "bass")) == 2
    assert subgenre_blocks("trance", "psy", "bass") == [b for b in BLOCKS["trance"] if b.role == "bass"]


def test_each_subgenre_brings_its_own_drums():
    assert [b.name for b in subgenre_blocks("darkwave", "minimal synth", "beat")] == ["electro_kit"]
    assert [b.name for b in subgenre_blocks("darkwave", "post-punk", "beat")] == ["drum_machine"]
    assert [b.name for b in subgenre_blocks("darkwave", "ethereal", "beat")] == ["drum_machine"]
    # The minimal synth kit leaves the electro kit's drums alone (its parts have their own levels).
    assert set(SUBGENRES["darkwave"]["minimal synth"]["kit"]) == {"rhythm"}


def test_a_post_punk_track_plays_chorused_bass_and_the_sequencer_arp():
    # (The chorused clean guitar is off air: blocks.RETIRED_SOUNDS.)
    for synth_role, synth in (("bass", "ys_chorusbass"), ("arp", "ys_pluck")):
        for seed in range(5):
            performer = Performer(random.Random(seed), style="darkwave", log=lambda *_: None)
            track = Track("Test", "darkwave", "A minor", 132, [0, 5, 2, 6], 2, subgenre="post-punk",
                          kit=dict(SUBGENRES["darkwave"]["post-punk"]["kit"]))
            edit = performer._add(Program(track), synth_role)
            assert f'# s "{synth}"' in edit.line.body, edit.line.body


def test_planned_darkwave_pieces_take_a_subgenre_and_its_tempo():
    rng = random.Random(3)
    seen, previous = set(), None
    for _ in range(9):
        piece = plan_piece(rng, previous, style="darkwave", target_minutes=5)
        lo, hi = SUBGENRES["darkwave"][piece.subgenre]["bpm"]
        assert lo <= piece.bpm <= hi
        assert piece.sounds.get("chords") in {"ys_coldstrings", "ys_glass"}
        seen.add(piece.subgenre)
        previous = piece
    assert seen == set(SUBGENRES["darkwave"])


def test_a_wave_sound_brings_the_lines_written_for_it():
    # The track picked the wave lead: the long held notes, not a choppy phrase.
    assert [b.name for b in track_blocks("darkwave", "minimal synth", {"lead": "ys_wavelead"}, "lead")] == ["wave_lead"]
    assert [b.name for b in track_blocks("darkwave", "minimal synth", {"arp": "ys_wavesynth"}, "arp")] == ["wave_arp"]
    # Another sound: the blocks that take it (the wave lead keeps its own voice).
    assert [b.name for b in track_blocks("darkwave", "minimal synth", {"lead": "ys_fmlead"}, "lead")] == ["cold_arp"]
    line = find_block("darkwave", "wave_lead").make(random.Random(0), {"lead": "ys_glass"})
    assert '# s "ys_wavelead"' in line.body


def test_the_wave_lead_holds_its_notes():
    block = find_block("darkwave", "wave_lead")
    assert float(dict(block.fx)["legato"]) >= 1.5
    for melody in block.patterns["melody"]:
        steps = re.sub(r"[<>\[\]]", " ", melody).split()
        assert steps.count("~") >= len(steps) * 0.6, melody     # mostly held, few attacks
