"""Against "it sounds always the same": a session of trance tracks shared
its kick, bass, hats and chord block, its hook rhythm, its lead voice and
its concept. Each of those now moves from track to track."""
import random

from music.blocks import BLOCKS, SUBGENRES
from music.director import Director
from music.hooks import opening_rhythm, write_hook
from music.plan import plan_piece


def test_hooks_dont_open_on_a_recent_rhythm():
    rng, recent, openings = random.Random(1), [], []
    for _ in range(10):
        tune = write_hook(rng, [0, 5, 2, 6], 2, recent[-4:])
        assert opening_rhythm(tune) not in {opening_rhythm(r) for r in recent[-4:]}
        recent.append(tune)
        openings.append(opening_rhythm(tune))
    assert len(set(openings)) >= 6


def test_the_director_hears_a_hook_opening_like_a_recent_one():
    director = Director(None, random.Random(0), style="trance", log=lambda *_: None)
    director.recent_hooks.append("lead: <[0 _ _ 2 _ 4 _ ~] [2 _ 0 _ ~ ~ ~ ~] [5 _ _ 7 _ 9 _ ~]>")
    assert director.close_hook("<[3 _ _ 5 _ 7 _ ~] [5 _ 3 _ ~ ~ ~ ~] [2 _ 4 _ 6 _ _ ~]>")[1] == 1.0
    assert director.close_hook("<[~ ~ 4 _ 6 _ 4 _] [~ 6 7 _ 6 _ 7 _]>") is None


def test_no_lead_voice_twice_within_three_tracks():
    director = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    previous, leads = None, []
    for _ in range(10):
        previous = director.next_piece(previous)
        leads.append(previous.sounds.get("lead"))
    assert not any(leads[i] in leads[max(0, i - 2):i] for i in range(len(leads)))


def test_the_trance_rhythm_section_has_choices():
    blocks = {b.name: b for b in BLOCKS["trance"]}
    assert {"held_chords", "offbeat_sub"} <= set(blocks)
    assert len(set(blocks["trance_beat"].patterns["kick"])) >= 2
    assert all(len(sub["kit"]["rhythm"]) >= 4 for sub in SUBGENRES["trance"].values())


def test_the_brief_names_what_to_move_away_from():
    director = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    previous = None
    for _ in range(2):
        previous = director.next_piece(previous)
    director.recent_concepts.append("a long straight road at night")
    prompt = director.piece_prompt(previous, previous, {"progressions": set(), "presets": set()})
    assert "a long straight road at night" in prompt and "frames (mode, tempo, subgenre)" in prompt
    bpm = int(prompt.split("tempo **")[1].split(" BPM")[0])
    assert all(abs(bpm - b) >= 4 for b in director.recent_bpms)
    modes = {k.split()[1] for k in list(director.recent_keys)[-2:]}
    suggested = prompt.split("Suggested key: **")[1].split("**")[0]
    assert suggested.split()[1] not in modes or len(modes) == 3
