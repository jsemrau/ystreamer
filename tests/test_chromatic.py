"""The chromatic colour: chords from outside the key sliding in parallel
("Everything In Its Right Place"), uneven lengths rolling across the 4/4
bars, every line playing relative to the chord sounding."""

import random
import sys

import pytest

from music import hooks
from music.director import Director
from music.livecode import Track
from music.performer import Performer
from music.plan import choose_chromatic, plan_piece, progression_degrees, validate_piece
from music.theory import CHROMATIC_COLOURS, parse_chromatic

RIGHT_PLACE = "I@4-bII@4-bIII@2-I@4-bII@4-iv@2"


def _bars(name: str, mode: str = "major") -> list:
    return [[[c.shift, c.scale, n] for c, n in bar] for bar in parse_chromatic(name, mode).bars()]


def test_numerals_off_the_key_become_chords_each_in_its_own_scale():
    p = parse_chromatic(RIGHT_PLACE, "major")
    assert [(c.numeral, c.shift, c.quality) for c in p.chords[:3]] == [("I", 0, "maj"), ("bII", 1, "maj"),
                                                                      ("bIII", 3, "maj")]
    assert p.chords[-1].quality == "min" and p.chords[-1].shift == 5
    # Each chord's scale shares most notes with the others: Db lydian (G, C), Eb mixolydian (Db).
    assert [c.scale for c in p.chords[:3]] == ["mixolydian", "lydian", "mixolydian"]
    assert parse_chromatic("I-bVI", "minor").name == "I@4-bVI@4"            # (a bar each by default)
    assert parse_chromatic("I@4-V@4", "major").chords[1].shift == -5        # (kept near the tonic)


def test_uneven_lengths_roll_across_the_bars_until_they_land_on_a_barline():
    bars = parse_chromatic(RIGHT_PLACE, "major").bars()
    assert len(bars) == 5                                                    # 20 beats
    assert [[(c.numeral, n) for c, n in bar] for bar in bars[2:]] == [
        [("bIII", 2), ("I", 2)], [("I", 2), ("bII", 2)], [("bII", 2), ("iv", 2)]]
    assert len(parse_chromatic("I@4-bII@4-bIII@2", "major").bars()) == 5     # 10 beats, twice round


@pytest.mark.parametrize("bad,why", [("I", "2 to 8"), ("I@4-I@4", "one chord"), ("Ib@4-II", "numeral"),
                                     ("IV@9-I", "1 to 8 beats"), ("I@1-bII@1-bIII@1-iv@4-I@4-bII@4-bIII@4-iv@4",
                                                                      "barline")])
def test_a_chromatic_progression_that_cant_play_says_why(bad, why):
    with pytest.raises(ValueError, match=why):
        parse_chromatic(bad, "major")


def test_every_line_plays_in_the_chord_sounding_and_strikes_again_when_it_changes():
    t = Track(title="x", style="trance", key="F minor", bpm=124, degrees=[0] * 8, chromatic=_bars(RIGHT_PLACE))
    h = t.harmony()
    assert h["mode"] == "<mixolydian lydian [mixolydian@2 mixolydian@2] [mixolydian@2 lydian@2] [lydian@2 minor@2]>"
    assert h["root"] == ' |+ "<0 1 [3@2 0@2] [0@2 1@2] [1@2 5@2]>" + 5'
    # (A chord held over the bar is struck again where the chord changes.)
    assert h["chords"].split()[2] == "[[0,2,4,6]@2" and h["roots"] == "0 0 [0@2 0@2] [0@2 0@2] [0@2 0@2]"
    # A change of harmony starts on the first chord at its cycle.
    t.start_cycle = 7
    assert t.harmony()["root"].startswith(' |+ "<[0@2 1@2] [1@2 5@2] 0 1')


def test_the_director_writes_one_and_its_hook_follows_the_chords():
    fallback = plan_piece(random.Random(5), style="trance", theme="chill lofi to study to")
    assert fallback.chromatic == ""
    hook = {"role": "lead", "pattern": "<[0 _ _ 2 4 _ 2 ~] [4 _ 2 _ 0 _ _ ~]>"}
    piece, notes = validate_piece({"chromatic": "I@4-bII@4-bIII@2", "hook": hook}, fallback)
    assert piece.chromatic == "I@4-bII@4-bIII@2" and set(progression_degrees(piece)) == {0}
    assert piece.hook["pattern"] == hook["pattern"] and piece.hook["relative"] == "key", notes
    # Off the chord on a beat (1 is no chord's tone, relative to it): rejected, a fitting one written.
    piece, notes = validate_piece({"chromatic": "I@4-bII@4-bIII@2", "hook": {"role": "lead",
                                   "pattern": "<[1 _ _ 1 3 _ 1 ~] [3 _ 1 _ 1 _ _ ~]>"}}, fallback)
    assert any("hook" in n for n in notes) and hooks.key_harmony_problem(
        piece.hook["pattern"], progression_degrees(piece), piece.cycles_per_chord) is None
    piece, notes = validate_piece({"chromatic": "I@4-Q@4"}, fallback)
    assert piece.chromatic == "" and any("chromatic" in n for n in notes)


def test_the_planner_plays_one_now_and_then_never_two_running():
    assert choose_chromatic("Radiohead at dawn", random.Random(0)) in CHROMATIC_COLOURS
    assert not any(choose_chromatic("warehouse rave", random.Random(k)) for k in range(30))
    assert any(choose_chromatic("chill music to relax and study", random.Random(k)) for k in range(60))
    previous = plan_piece(random.Random(1), style="trance", theme="drifting radiohead")
    assert previous.chromatic
    assert choose_chromatic("drifting radiohead", random.Random(0), previous) == ""
    director = Director(None, random.Random(0), log=lambda *a: None, style="trance")
    director._last_chromatic = True
    piece, notes = director.review_piece({"chromatic": RIGHT_PLACE}, plan_piece(random.Random(5), style="trance"),
                                         {"progressions": set(), "presets": set()})
    assert piece.chromatic == "" and any("last track was chromatic" in n for n in notes)


def test_a_chromatic_track_plays_through_the_engine():
    sys.path.insert(0, "tests")
    from test_livecode import _SimClock

    from music.live_engine import LiveEngine

    performer = Performer(random.Random(2), style="trance", theme="drifting radiohead", log=lambda *_: None)
    track = performer.new_track(None)
    assert track.chromatic and set(track.degrees) == {0} and list(track.progressions) == ["main"]
    assert hooks.key_harmony_problem(track.hook["pattern"], track.degrees, track.bars_per_chord) is None

    sent, logs, clock = [], [], _SimClock()
    performer = Performer(random.Random(2), style="trance", theme="drifting radiohead", log=logs.append)
    LiveEngine(sent.extend, performer, log=logs.append, now=clock.now, sleep=clock.sleep, max_tracks=1).run()
    assert any("🎼 Chromatic" in l for l in logs)
    pitched = [l for l in sent if "scale" in l]
    assert pitched and all('scale "<' in l and '|+ "<' in l for l in pitched)
