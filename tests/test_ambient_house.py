"""Ambient house (after Orbital's "Halcyon + On + On", The Orb): a bright,
wistful major key at 124-128 BPM, few layers that stay, a wordless reversed
voice as texture (ys_revvox, off air until heard), a riff for a hook, long
tracks that change slowly. Its traits, not any record's notes."""
import random
import sys

import config
from music.blocks import BLOCKS, LIVE_SOUNDS, SUBGENRES
from music.director import Director
from music.hooks import RIFF_TONES, write_riff
from music.livecode import Edit, EditError, harmony_problem
from music.performer import Performer
from music.plan import STYLES, plan_piece, subgenre_from_theme

AH = SUBGENRES["trance"]["ambient house"]


def test_its_frame():
    assert AH["bpm"] == (124, 128) and set(AH["modes"]) == {"major", "lydian"}
    assert {"major", "lydian"} <= set(STYLES["trance"]["modes"]) and STYLES["trance"]["bpm"][0] == 124
    # The rest of trance stays dark.
    assert all(set(v["modes"]) <= {"minor", "phrygian", "dorian"} for n, v in SUBGENRES["trance"].items()
               if n != "ambient house")
    assert {b.name for b in BLOCKS["trance"]} >= {"echo_riff", "held_chords", "offbeat_sub"}


def test_the_theme_asks_for_it():
    assert subgenre_from_theme("trance", "Orbital at sunrise") == "ambient house"
    assert subgenre_from_theme("trance", "an ambient house afternoon") == "ambient house"


def test_the_reversed_voice_waits_until_its_been_heard():
    assert config.REVVOX or "ys_revvox" not in LIVE_SOUNDS["texture"]
    assert config.REVVOX or "ys_revvox" not in AH["sounds"]["texture"]
    defs = open(config.SYNTHDEFS_FILE).read()
    assert "SynthDef(\\ys_revvox" in defs


def test_riffs_are_chord_tones_and_vary():
    rng, riffs = random.Random(0), []
    for _ in range(12):
        riff = write_riff(rng, riffs[-3:])
        notes = [t for t in riff.split() if t != "~"]
        assert 6 <= len(riff.split()) <= 12 and len(set(notes)) >= 3
        assert all(int(n) in RIFF_TONES for n in notes) and harmony_problem("arp", riff) is None
        assert "~ ~" not in riff
        riffs.append(riff)
    assert len(set(riffs)) >= 10


def _ambient_piece(seed=3):
    director = Director(None, random.Random(seed), style="trance", theme="Orbital, ambient house",
                        log=lambda *_: None, target_minutes=5)
    return director, director.next_piece(None)


def test_a_track_is_bright_slow_long_and_hooked_on_a_riff():
    director, piece = _ambient_piece()
    assert piece.subgenre == "ambient house"
    assert piece.key.split()[1] in ("major", "lydian") and 124 <= piece.bpm <= 128
    assert piece.hook["role"] == "arp" and piece.hook["kind"] == "cycle"
    assert piece.minutes == AH["pace"]["minutes"]
    assert abs(sum(s.bars for s in piece.sections) / piece.cps / 60 - piece.minutes) < 1.0


def test_it_takes_its_time_and_leaves_the_acid_out():
    sys.path.insert(0, "tests")
    from test_livecode import _program_with

    director, piece = _ambient_piece()
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    performer.director = director
    performer.new_track(None)
    if performer.pace:                          # (the performer plans its own piece: force ours)
        pass
    performer._pace = dict(AH["pace"])
    assert performer.edit_interval() == 32
    performer.state.phase = "groove"
    assert performer.boldness() == "gentle"
    performer.state.phase = "groove2"
    assert performer.boldness() == "medium"
    program = _program_with("trance", "trance_beat")
    program.track.subgenre = "ambient house"
    assert "acid" not in performer._missing_roles(program)
    try:
        performer._edit_from_json({"op": "add", "block": "acid"}, program)
        assert False, "the acid came in"
    except EditError as e:
        assert "no acid" in str(e)
