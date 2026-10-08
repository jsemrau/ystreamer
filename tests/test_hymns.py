"""Hymns: when the art director (the Director) thinks it fits, the hook is a
tune to sing along to, plainer and catchier, measured and written by the
hymn's rules."""

import random

import pytest

from music import hooks
from music.director import Director
from music.plan import choose_melody, plan_piece, progression_degrees, validate_piece

AM = [0, 5, 2, 6]                      # i-VI-III-VII, two bars each
# Eight bars, AABA: a note a beat, a dotted lilt, each two-bar line ending held.
HYMN = ("<[0 _ _ 1 2 _ 0 _] [2 _ _ _ _ _ ~ ~] [0 _ _ 1 2 _ 0 _] [2 _ _ _ _ _ ~ ~] "
        "[2 _ _ 3 4 _ _ 5] [6 _ _ _ _ _ ~ ~] [5 _ _ 4 3 _ 1 _] [1 _ _ _ _ _ ~ ~]>")
# A good hook, but busy and syncopated: not a hymn.
SYNCOPATED = ("<[~ 0 _ 2 _ 4 _ ~] [2 _ 4 _ 2 _ _ ~] [~ 0 _ 2 _ 4 _ ~] [4 _ 5 _ 4 _ _ ~] "
              "[~ 4 _ 6 _ 8 _ ~] [6 _ 4 _ 2 _ _ ~] [~ 1 _ 3 _ 5 _ ~] [3 _ 1 _ _ _ ~ ~]>")


def test_a_hymn_passes_the_hymns_rules():
    a = hooks.analyze(HYMN, AM, 2, melody="hymn")
    assert a["score"] == 1.0, a["problems"]
    assert {"on the beat", "lines rest", "returns"} <= set(a["checks"])
    assert "off the beat" not in a["checks"]              # (a hymn doesn't need the lilt off the beat)


def test_a_busy_syncopated_hook_is_not_a_hymn_and_is_told_why():
    a = hooks.analyze(SYNCOPATED, AM, 2, melody="hymn")
    assert a["score"] < hooks.analyze(SYNCOPATED, AM, 2)["score"]
    assert any("hymn" in p for p in a["problems"])
    # A first line that never comes back.
    bars = HYMN.strip("<>").split("] [")
    bars[2] = "2 _ 0 _ 2 _ _ _"
    b = hooks.analyze("<[" + "] [".join(b.strip("[]") for b in bars) + "]>", AM, 2, melody="hymn")
    assert not b["checks"]["returns"]


@pytest.mark.parametrize("degrees,bars", [(AM, 2), ([0, 3, 4, 0], 1), ([0, 5, 3, 4], 2), ([0, 3, 4], 1),
                                          ([5, 3, 0, 4], 2), ([0, 6, 5, 6], 1)])
def test_the_writer_writes_hymns_that_fit_and_sing(degrees, bars):
    for seed in range(4):
        tune = hooks.write_hook(random.Random(seed), degrees, bars, melody="hymn")
        assert hooks.key_harmony_problem(tune, degrees, bars) is None, tune
        a = hooks.analyze(tune, degrees, bars, melody="hymn")
        assert a["score"] >= hooks.GOOD, (tune, a["problems"])
        notes = [d for d in (int(t) for bar in hooks.parse(tune) for t in bar if t not in ("~", "_"))]
        assert max(notes) - min(notes) <= 7
        assert all(sum(t not in ("~", "_") for t in bar) <= 5 for bar in hooks.parse(tune))


def test_the_planner_writes_a_hymn_when_the_theme_asks_for_one():
    assert choose_melody("a choir at dawn", random.Random(0)) == "hymn"
    assert choose_melody("chill lofi to study to", random.Random(0)) == ""
    assert not any(choose_melody("Halloween trance party", random.Random(k)) for k in range(20))
    assert any(choose_melody("uplifting sunrise", random.Random(k)) for k in range(20))
    piece = plan_piece(random.Random(3), style="trance", theme="sacred anthem")
    assert piece.melody == "hymn" and piece.hook.get("relative") == "key"
    a = hooks.analyze(piece.hook["pattern"], progression_degrees(piece), piece.cycles_per_chord, melody="hymn")
    assert a["score"] >= hooks.GOOD, a["problems"]


def test_the_directors_melody_is_kept_and_its_hook_judged_as_a_hymn():
    fallback = plan_piece(random.Random(5), style="trance", theme="chill lofi to study to")
    assert fallback.melody == ""
    piece, notes = validate_piece({"melody": "hymn"}, fallback)
    assert piece.melody == "hymn"
    # (No hook given and the fallback's is a hook, not a hymn: a hymn is written.)
    assert hooks.analyze(piece.hook["pattern"], progression_degrees(piece), piece.cycles_per_chord,
                         melody="hymn")["score"] >= hooks.GOOD
    piece, notes = validate_piece({"melody": "polka"}, fallback)
    assert piece.melody == "" and any("polka" in n for n in notes)
    # Left out: a plain hook, whatever the fallback had.
    assert validate_piece({}, plan_piece(random.Random(3), style="trance", theme="sacred anthem"))[0].melody == ""

    director = Director(None, random.Random(0), log=lambda *a: None, style="trance")
    syncopated = type(fallback).from_dict({**fallback.to_dict(), "progression": "i-VI-III-VII", "key": "A minor",
                                           "cycles_per_chord": 2,
                                           "hook": {"role": "lead", "kind": "phrase", "relative": "key",
                                                    "pattern": SYNCOPATED}})
    hymn = type(syncopated).from_dict({**syncopated.to_dict(), "melody": "hymn"})
    assert director.weak_hook(syncopated) is None
    weak = director.weak_hook(hymn)
    assert weak is not None and any("hymn" in p for p in weak[1])
