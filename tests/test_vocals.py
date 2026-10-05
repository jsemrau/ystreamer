import json
import os
import random
import shutil
import subprocess

import pytest

from music import vocals
from music.blocks import blocks_for, find_block
from music.livecode import Edit, EditError, Program, Track, allowed_wrappers, apply_edit
from music.performer import VOCAL_CUE, Performer, with_vocal_cue
from music.theory import Key

NIGHT = vocals.Acapella(name="Night Calls", file="night.wav", key="A minor", bpm=124, bars=8,
                        license="test tone, made here")


def _vocal(**kw):
    return {**vocals.fit(NIGHT, "A minor", (118, 126), ["minor", "dorian"]), **kw}


def test_vocal_chops_play_blood_sports_phrases_in_the_dark_styles():
    for style in ("darkwave", "techno", "downtempo"):
        block = find_block(style, "vocal_chop")
        assert block.role == "vocal" and block.drums, style
        assert all("bsvocals:" in v for v in block.patterns["vox"])
    assert not find_block("lofi", "vocal_chop") and not find_block("trance", "vocal_chop")


def test_an_acapella_is_sung_in_the_nearest_key_it_reaches_at_its_own_tempo():
    # A minor -> C minor is three semitones up: version 6.
    f = vocals.fit(NIGHT, "C minor", (118, 126), ["minor"])
    assert f["shift"] == 3 and f["n"] == 6 and Key.parse(f["key"]) == Key.parse("C minor")
    assert f["bpm"] == 124 and f["cycles"] == 8 and f["bank"] == "vox_night_calls"
    # Six semitones away is out of reach: the nearest of the seven versions.
    assert abs(vocals.fit(NIGHT, "D# minor", (118, 126), ["minor"])["shift"]) == 3
    # Double time in a fast style, half time in a slow one (8 bars -> 16 / 4 cycles).
    fast = vocals.fit(NIGHT, "A minor", (240, 250), ["minor"])
    slow = vocals.fit(NIGHT, "A minor", (60, 64), ["minor"])
    assert (fast["bpm"], fast["cycles"]) == (248, 16) and (slow["bpm"], slow["cycles"]) == (62, 4)
    # No tempo in the range, or a mode the style doesn't play: no fit.
    assert vocals.fit(NIGHT, "A minor", (136, 140), ["minor"]) is None
    assert vocals.fit(NIGHT, "A major", (118, 126), ["major", "lydian"]) is None


def test_choosing_needs_a_prepared_fitting_acapella_and_leaves_the_dice_alone_without_one(tmp_path):
    rng = random.Random(5)
    state = rng.getstate()
    kw = dict(acapellas=[NIGHT], samples_dir=str(tmp_path), chance=1.0)
    assert vocals.choose(rng, "deephouse", "A minor", (118, 124), ["minor"], **kw) is None   # not prepared
    assert rng.getstate() == state
    os.makedirs(tmp_path / NIGHT.bank)
    for i in range(7):
        (tmp_path / NIGHT.bank / f"{i}.wav").write_bytes(b"")
    assert vocals.choose(rng, "deephouse", "A minor", (118, 124), ["minor"], **kw)["name"] == "Night Calls"
    assert vocals.choose(rng, "deephouse", "A minor", (118, 124), ["minor"], **{**kw, "chance": 0}) is None
    only_trance = vocals.Acapella(**{**NIGHT.__dict__, "styles": ["trance"]})
    assert vocals.choose(rng, "deephouse", "A minor", (118, 124), ["minor"],
                         **{**kw, "acapellas": [only_trance]}) is None


def test_the_manifest_needs_a_licence_for_every_acapella(tmp_path):
    entry = {"name": "Hook", "file": "hook.wav", "key": "F minor", "bpm": 128, "bars": 4}
    (tmp_path / "acapellas.json").write_text(json.dumps([entry]))
    with pytest.raises(ValueError, match="license"):
        vocals.load_manifest(str(tmp_path))
    (tmp_path / "acapellas.json").write_text(json.dumps([{**entry, "license": "Splice, royalty-free"}]))
    assert vocals.load_manifest(str(tmp_path))[0].bank == "vox_hook"
    assert vocals.load_manifest(str(tmp_path / "nothing here")) == []


def test_a_track_with_an_acapella_sings_it_instead_of_the_chops_and_keeps_its_tempo():
    track = Track("T", "techno", "A minor", 124, [0, 5, 2, 6], 2, vocal=_vocal())
    [block] = blocks_for("techno", "vocal", track)
    assert block.name == "acapella" and blocks_for("techno", "vocal")[0].name == "vocal_chop"
    line = track.make(block, random.Random(0))
    assert line.patterns["loop"] == "vox_night_calls:3"
    assert line.body.startswith("loopAt 8 $ chop 32 $ ")
    assert set(allowed_wrappers(line)) == {"whenmod 8 6 (# hpf 1500)", "every 2 (# room 0.5)"}
    program = apply_edit(Program(track), Edit("add", role="vocal", line=line))
    assert program.statement("vocal").startswith("d12 $ ")
    with pytest.raises(EditError):
        apply_edit(program, Edit("set_pattern", role="vocal", name="loop", value="vox_night_calls:4"))
    with pytest.raises(EditError, match="acapella"):
        apply_edit(program, Edit("tempo", value="126"))
    apply_edit(Program(Track("T", "techno", "A minor", 124, [0], 2)), Edit("tempo", value="126"))


def test_the_vocal_waits_for_the_breakdown():
    performer = Performer(random.Random(0), style="darkwave", log=lambda *_: None)
    program = Program(performer.new_track(None))
    assert any(c.get("part") == "vocal" for cs in performer.state.cues.values() for c in cs)
    for role in ("bass", "beat", "chords", "lead", "arp"):
        program = apply_edit(program, performer._add(program, role))
    performer.state.phase = "build"
    assert "vocal" not in performer._missing_roles(program)
    assert all(b.role != "vocal" for b in performer._addable(program))
    performer.state.phase = "breakdown"
    for role in ("beat", "bass"):
        program.lines[role].muted = True
        performer.state.muted_for_breakdown.append(role)
    edit = performer._builtin_edit(program)
    assert edit.op == "add" and edit.role == "vocal" and edit.line.block == "vocal_chop"


def test_the_vocal_cue_is_added_once():
    cues = with_vocal_cue({"breakdown": [{"do": "stop", "part": "beat", "say": "Drop the kick"}]})
    assert cues["breakdown"][-1] == VOCAL_CUE
    assert with_vocal_cue(cues) == cues
    assert with_vocal_cue({"groove": []})["groove2"] == [VOCAL_CUE]


def test_a_chosen_acapella_sets_the_tracks_key_and_tempo(monkeypatch):
    monkeypatch.setattr(vocals, "choose", lambda *a, **k: _vocal(key="C minor", shift=3, n=6))
    performer = Performer(random.Random(3), style="deephouse", log=lambda *_: None)
    track = performer.new_track(None)
    assert track.key == "C minor" and track.bpm == 124 and track.vocal["n"] == 6
    assert track.surprise not in ("tempo_lift", "key_lift")
    assert any(c.get("part") == "vocal" for cs in performer.state.cues.values() for c in cs)


@pytest.mark.skipif(not (shutil.which("sox") and shutil.which("ffmpeg")), reason="needs sox and ffmpeg")
def test_preparing_cuts_the_loop_to_its_bars_in_seven_keys(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # A 'voice': a vowel-ish tone, 0.5 s of silence before the downbeat.
    subprocess.run(["sox", "-n", "-r", "44100", "-c", "2", str(src / "night.wav"), "synth", "9",
                    "sine", "220", "sine", "440", "pad", "0.5", "0", "vol", "0.3"], check=True)
    acapella = vocals.Acapella(**{**NIGHT.__dict__, "start": 0.5, "bars": 4})
    folder = vocals.prepare(acapella, str(src), str(tmp_path / "samples"), log=lambda *_: None)
    assert sorted(os.listdir(folder)) == [f"{i}.wav" for i in range(7)]
    want = 4 * 240 / 124
    for i in (0, 3, 6):
        path = os.path.join(folder, f"{i}.wav")
        seconds = float(subprocess.run(["soxi", "-D", path], capture_output=True, text=True).stdout)
        assert abs(seconds - want) < 0.002
        assert abs(vocals._loudness(path) - vocals.FILE_LUFS) < 1.0
    assert vocals.is_prepared(acapella, str(tmp_path / "samples"))



def test_hardcore_has_vocal_chops_and_two_kinds_of_lead():
    assert find_block("hardcore", "vocal_chop").role == "vocal"
    for style in ("hardcore", "techno"):
        leads = {b.name for b in blocks_for(style, "lead")}
        assert {"glass_lead", "anthem_lead"} <= leads
    anthem = find_block("hardcore", "anthem_lead")
    assert "+ 12" in anthem.body and not anthem.wrappers          # an octave up, not pumping
