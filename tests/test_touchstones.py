"""The host's favourite songs as touchstones: one in each track's brief, in
the track's style, none of the last few."""
import random

from music import touchstones
from music.director import Director
from music.plan import STYLES, plan_piece


def test_every_song_is_described_and_fits_a_style():
    songs = touchstones.songs()
    assert len(songs) == 14
    for s in songs:
        assert s["styles"] and set(s["styles"]) <= set(STYLES), s["title"]
        for field in ("artist", "sounds", "signature", "borrow"):
            assert s.get(field), (s["title"], field)


def test_every_style_has_a_touchstone():
    for style in STYLES:
        assert style in touchstones.pick(style, random.Random(0))["styles"]


def test_pick_avoids_the_recent_ones_while_others_are_left():
    rng = random.Random(1)
    darkwave = [s["title"] for s in touchstones.songs() if "darkwave" in s["styles"]]
    for _ in range(20):
        assert touchstones.pick("darkwave", rng, avoid=darkwave[:-1])["title"] == darkwave[-1]
    assert touchstones.pick("darkwave", rng, avoid=darkwave)["title"] in darkwave


def test_the_brief_carries_one_and_the_director_moves_on():
    d = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    fallback = plan_piece(random.Random(1), style="trance", target_minutes=5)
    seen = []
    for _ in range(3):
        prompt = d.piece_prompt(None, fallback, {"progressions": set(), "presets": set()})
        assert "## A touchstone from the host's favourites" in prompt and "never\ncopy its melody" in prompt
        title = d._touchstone["title"]
        assert f"**{title}**" in prompt and "trance" in d._touchstone["styles"]
        assert title not in seen
        seen.append(title)
        d.recent_touchstones.append(title)
