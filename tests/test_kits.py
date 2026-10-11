"""Each track its own drum kit and bass sound (music/kits.py), and a vocal
that sings the track's hook (the refrain)."""
import random

import config
from music import kits


def test_a_kit_takes_the_place_of_the_stock_hits():
    expr = 's "[[~ 808oh]*4, hh27*16, ~ cp ~ cp]" # gain 0.8'
    assert kits.apply(expr, "kit_808") == 's "[[~ kit_808:4]*4, kit_808:3*16, ~ kit_808:2 ~ kit_808:2]" # gain 0.8'
    assert kits.apply('s "ys_kick909*4"', "kit_house") == 's "kit_house:0*4"'
    assert kits.apply('s "bd:3 ~ sn:1 ~"', "kit_jazz") == 's "kit_jazz:0 ~ kit_jazz:1 ~"'
    # Not a stock hit (the gabber kick, a break, a vocal chop), not touched.
    for other in ('s "ys_gabber*4"', 'loopAt 2 $ chop 16 $ s "break:3"', 's "~ bsvocals:2"', 's "kit_808:0"'):
        assert kits.apply(other, "kit_808") == other
    assert kits.apply(expr, "") == expr


def test_no_kit_twice_running_and_the_bass_changes_too(monkeypatch):
    monkeypatch.setattr(config, "DRUM_KITS", True)
    monkeypatch.setattr(kits, "_cache", [{"name": n, "bank": f"kit_{n}", "styles": ["trance"]}
                                          for n in ("a", "b", "c", "d")])
    rng = random.Random(0)
    recent = []
    for _ in range(20):
        k = kits.pick("trance", rng, avoid=recent[-3:])
        assert k["name"] not in recent[-3:]
        recent.append(k["name"])
    basses = []
    for _ in range(10):
        b = kits.pick_bass("trance", rng, avoid=basses[-2:])
        assert b in kits.BASS_SOUNDS["trance"] and b not in basses[-2:]
        basses.append(b)
    assert kits.pick("piano", rng) is None


def test_the_kit_plays_when_the_line_does_and_edits_keep_the_stock_names():
    from music.blocks import find_block
    from music.livecode import Edit, Program, apply_edit
    from music.performer import Performer

    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    track.kit = {**track.kit, "drums": "kit_oberheim", "bass": "ys_chorusbass"}
    program = apply_edit(Program(track), Edit("add", role="perc",
                                              line=track.make(find_block("trance", "trance_hats"), random.Random(0))))
    assert "kit_oberheim:" in program.statement("perc") and "808oh" not in program.statement("perc")
    program = apply_edit(program, Edit("set_pattern", role="perc", name="hats", value="[~ 808oh]*4"))
    assert program.lines["perc"].patterns["hats"] == "[~ 808oh]*4" and "kit_oberheim:4" in program.statement("perc")
    bass = track.make(find_block("trance", "rolling_bass"), random.Random(0))
    assert '# s "ys_chorusbass"' in bass.body


def test_the_vocal_sings_the_tracks_hook():
    from music.blocks import Block, _chopped_hook, _sung_hook, held_phrase, track_blocks
    from music.livecode import Track

    sung, chopped = _sung_hook(), _chopped_hook()
    assert sung.hook_slot() == ("melody", "phrase") and chopped.hook_slot() == ("motif", "cycle")
    phrase = {"role": "lead", "kind": "phrase", "pattern": "<[0 _ 2 4 _ _ 2 _] [4 _ _ _ 2 _ ~ ~]>"}
    track = Track(title="t", style="trance", key="A minor", bpm=128, degrees=[0, 5, 3, 4], hook=phrase)
    line = track.make(sung, random.Random(0))
    assert line.patterns["melody"] == held_phrase(phrase["pattern"]) and '"ys_femalevox"' in line.body
    riff = {"role": "arp", "kind": "cycle", "pattern": "0 2 4 7 4 2"}
    line = Track(title="t", style="trance", key="A minor", bpm=128, degrees=[0, 5, 3, 4], hook=riff).make(chopped, random.Random(0))
    assert line.patterns["motif"] == "0 2 4 7 4 2"
    # The vocal part brings in the singer that fits the hook.
    import music.blocks as B

    saved = list(B.BLOCKS["trance"])
    try:
        B.BLOCKS["trance"] += [sung, chopped]
        assert [b.name for b in track_blocks("trance", "", {}, "vocal", track)] == ["sung_hook"]
        riff_track = Track(title="t", style="trance", key="A minor", bpm=128, degrees=[0, 5, 3, 4], hook=riff)
        assert [b.name for b in track_blocks("trance", "", {}, "vocal", riff_track)] == ["chopped_hook"]
    finally:
        B.BLOCKS["trance"] = saved


def test_a_singing_line_is_never_played_backwards():
    from music.blocks import _sung_hook
    from music.livecode import ACAPELLA_WRAPPERS, Track, allowed_wrappers

    line = Track(title="t", style="trance", key="A minor", bpm=128, degrees=[0, 5, 3, 4]).make(_sung_hook(), random.Random(0))
    assert allowed_wrappers(line, "trance") == ACAPELLA_WRAPPERS
