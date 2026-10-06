"""Memorable melodies: the hook is measured, written for the progression,
kept as written, re-fitted to other chords and learned from the host."""

import json
import random
import sys

import pytest

from music import hooks
from music.blocks import find_block
from music.livecode import Edit, EditError, Program, Track, apply_edit, hook_kept_problem
from music.performer import Performer, builtin_cues
from music.plan import HOOK_SIMILAR, hook_similarity, plan_piece, progression_degrees, validate_hook

AM = [0, 5, 2, 6]                      # i-VI-III-VII, two bars each
GOOD_HOOK = "<[0 _ _ _ -1 0 _ ~] [2 _ _ _ 2 _ _ ~] [2 _ _ _ 0 2 _ ~] [4 _ _ 4 _ _ _ ~] " \
            "[4 _ _ _ 2 4 _ ~] [6 _ _ _ 4 _ _ ~] [5 _ _ _ 3 5 _ ~] [3 _ 1 _ _ _ ~ ~]>"


# Phase 2: what makes a hook memorable, measured.

def test_a_running_arpeggio_scores_low_and_says_why():
    a = hooks.analyze("<[0 _ 2 _ 4 _ 7 _] [9 _ 7 _ 4 _ 2 _]>")
    assert a["score"] < hooks.GOOD
    assert any("long note" in p for p in a["problems"]) and any("rest" in p for p in a["problems"])
    assert hooks.summary("<[0 _ 2 _ 4 _ 7 _] [9 _ 7 _ 4 _ 2 _]>").startswith("melody 0.")


def test_a_good_hook_passes_every_rule():
    a = hooks.analyze(GOOD_HOOK, AM, 2)
    assert a["score"] == 1.0, a["problems"]
    assert not hooks.analyze("<[2 _ 2 _ 2 _ 2 ~] [2 _ 2 _ 2 _ 2 ~]>")["checks"]["moves"]


# Phase 3: written against the key, bar by bar.

def test_on_the_beats_a_tone_of_that_bars_chord():
    assert hooks.key_harmony_problem(GOOD_HOOK, AM, 2) is None
    # Bar 1 is i (0 2 4): a 3 on the downbeat clashes; a 3 between the beats, stepping, passes.
    assert "bar 1" in hooks.key_harmony_problem(GOOD_HOOK.replace("[0 _ _ _ -1 0 _ ~]", "[3 _ _ _ -1 0 _ ~]", 1), AM, 2)
    assert hooks.key_harmony_problem("<[0 1 2 _ 4 _ _ ~] [2 _ _ _ 0 _ _ ~]>", [0, 0], 1) is None
    # A note held into a bar whose chord lacks it: no.
    assert "held" in hooks.key_harmony_problem("<[0 _ _ _ 4 _ _ _] [_ _ _ _ 3 _ _ ~]>", [0, 3], 1)
    assert "fit" in hooks.key_harmony_problem("<[0 _ _ ~] [2 _ _ ~] [4 _ _ ~]>", AM, 2)   # 3 bars in 8


def test_the_hook_is_re_fitted_to_other_chords_keeping_its_shape():
    breakdown = [5, 3, 0, 4]
    fitted = hooks.refit(GOOD_HOOK, breakdown, 2)
    assert fitted != GOOD_HOOK and hooks.key_harmony_problem(fitted, breakdown, 2) is None
    assert hook_similarity(GOOD_HOOK, fitted) >= 0.6 and len(hooks.parse(fitted)) == 8
    assert [len(b) for b in hooks.parse(fitted)] == [len(b) for b in hooks.parse(GOOD_HOOK)]
    assert hooks.refit(GOOD_HOOK, AM, 2) == GOOD_HOOK                # already fits: as written


# Phase 4: the hook writer.

@pytest.mark.parametrize("degrees,bars", [(AM, 2), ([0, 6, 5, 4], 1), ([0, 3], 2), ([5, 6, 0, 0], 1)])
def test_the_writer_writes_hooks_that_fit_and_are_memorable(degrees, bars):
    rng = random.Random(1)
    written = []
    for _ in range(4):
        hook = hooks.write_hook(rng, degrees, bars, recent=written)
        assert hooks.key_harmony_problem(hook, degrees, bars) is None
        assert hooks.score(hook, degrees, bars) >= hooks.GOOD
        assert all(hook_similarity(hook, w) < HOOK_SIMILAR for w in written)
        written.append(hook)


def test_the_built_in_planner_gives_every_track_a_hook_for_its_chords():
    for style in ("trance", "lofi", "deephouse", "darkwave", "ambient"):
        piece = plan_piece(random.Random(4), style=style)
        assert piece.hook["relative"] == "key" and piece.hook["role"] == "lead"
        assert hooks.key_harmony_problem(piece.hook["pattern"], progression_degrees(piece), piece.cycles_per_chord) is None


def test_the_directors_lead_hook_is_checked_against_the_key():
    piece = plan_piece(random.Random(1), style="trance")
    degrees = progression_degrees(piece)
    ok, why = validate_hook({"role": "lead", "pattern": piece.hook["pattern"]}, "trance", degrees,
                            piece.cycles_per_chord)
    assert not why and ok["relative"] == "key"
    chord, why = validate_hook({"role": "lead", "pattern": "<[0 _ 2 _ 4 _ ~ ~] [4 _ 2 _ 0 _ ~ ~]>",
                                "relative": "chord"}, "trance", degrees, piece.cycles_per_chord)
    assert not why and "relative" not in chord


# Phase 1: the hook is kept, heard, and brought back.

def _hook_track(**kw):
    return Track("T", "trance", "A minor", 132, AM, 2, start_cycle=3,
                 hook={"role": "lead", "kind": "phrase", "relative": "key", "pattern": GOOD_HOOK}, **kw)


def test_a_key_hook_line_plays_against_the_key_from_the_progressions_first_chord():
    track = _hook_track()
    line = track.make(find_block("trance", "cafe_lead"), random.Random(0))
    assert line.key_tune and line.hook == GOOD_HOOK and line.hook_slot == "melody"
    program = apply_edit(Program(track), Edit("add", role="lead", line=line))
    code = program.statement("lead")
    assert '(3 ~> "<[0 _ _ _ -1 0 _ ~]' in code and '|+ "<' not in code


def test_the_hook_stays_as_written_but_its_answer_may_vary():
    track = _hook_track()
    program = apply_edit(Program(track), Edit("add", role="lead", line=track.make(find_block("trance", "cafe_lead"),
                                                                                  random.Random(0))))
    answer = GOOD_HOOK.replace("[3 _ 1 _ _ _ ~ ~]", "[3 _ _ _ 1 _ ~ ~]")
    apply_edit(program, Edit("set_pattern", role="lead", name="melody", value=answer))
    with pytest.raises(EditError, match="the hook stays"):
        apply_edit(program, Edit("set_pattern", role="lead", name="melody",
                                 value=GOOD_HOOK.replace("[2 _ _ _ 2 _ _ ~]", "[2 _ 4 _ 2 _ _ ~]")))
    assert hook_kept_problem("<[0 _ 2 _ 4 _ ~ ~]>", "<[0 _ 2 _ 4 _ ~ ~] [4 _ 2 _ 0 _ ~ ~]>") is None   # an answer added


def test_the_built_in_cues_keep_the_hook_and_bring_it_back_an_octave_up():
    piece = plan_piece(random.Random(2), style="trance", theme="Chicane Saltwater")
    cues = builtin_cues(piece)
    said = " ".join(c.get("say", "") for cs in cues.values() for c in cs)
    assert "Change the" not in said and "vary only its last bar" in said
    peak = cues["groove2"]
    assert {"do": "pattern", "part": "lead", "pattern": piece.hook["pattern"],
            "say": "The hook returns, as it was"} in peak
    assert any(c.get("wrap") == "(|+ n 12)" for c in peak)
    assert any(c.get("do") == "sound" and c["part"] == "lead" for c in cues["breakdown"])


def test_a_track_with_a_key_hook_keeps_its_progression_at_the_peak_and_counts_the_hook():
    performer = Performer(random.Random(2), style="deephouse", log=lambda *_: None)
    track = performer.new_track(None)
    assert track.hook["relative"] == "key" and "peak" not in track.progressions
    performer.state.phase = "groove2"
    assert performer.harmony_for_phase(track)[0] == "main"
    program = apply_edit(Program(track), performer._add(Program(track), "lead"))
    bars = iter([0.0, 16.0, 40.0])
    performer.bar = lambda: next(bars)
    for _ in range(3):
        performer.count_hook(program)
    assert performer.stats["hook_bars"] == 40


def test_the_engine_re_fits_the_hook_for_the_breakdown_and_restores_it():
    sys.path.insert(0, "tests")
    from test_livecode import _SimClock

    from music.live_engine import LiveEngine

    logs, clock = [], _SimClock()
    performer = Performer(random.Random(2), style="deephouse", log=logs.append)
    LiveEngine(lambda lines: None, performer, log=logs.append, now=clock.now, sleep=clock.sleep, max_tracks=1).run()
    refits = [l for l in logs if "🎣 The hook" in l]
    assert any("re-fitted" in l for l in refits) and any("as written" in l for l in refits)
    assert any("bars as written" in l for l in logs)


# Phase 5: learning from the host.

def test_ratings_are_kept_with_the_hook_and_the_liked_ones_described_as_rules(isolated_runtime):
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    performer.note_rating(1, "")
    performer.note_rating(1, "", role="lead", line="d5 $ ...")
    performer.note_rating(-1, "", role="bass", line="d2 $ ...")         # not the hook's line: not counted
    entries = [json.loads(l) for l in open(isolated_runtime / "hook_ratings.jsonl")]
    assert len(entries) == 2 and all(e["hook"] == track.hook["pattern"] for e in entries)
    liked = hooks.liked()
    assert liked and "bars" in liked[0] and "rhythm" in liked[0] and track.hook["pattern"] not in liked[0]


def test_the_judge_sees_a_melodic_proposals_score():
    explained = Performer._explain(Edit("set_pattern", role="lead", name="melody",
                                        value="<[0 _ 2 _ 4 _ 7 _] [9 _ 7 _ 4 _ 2 _]>", reason="climb"))
    assert "[melody 0." in explained


def test_a_weak_hook_from_the_director_is_written_again_or_replaced(monkeypatch):
    from music import director as D

    director = D.Director("fake/model", random.Random(1), log=lambda m: None, style="trance")
    monkeypatch.setattr(director, "_ask", lambda *a, **k: None)          # the model can't do better
    piece = plan_piece(random.Random(0), style="trance")
    weak = {"role": "lead", "kind": "phrase", "relative": "key",
            "pattern": hooks.refit("<[0 _ 2 _ 4 _ 2 _] [0 _ 2 _ 4 _ 2 _]>", progression_degrees(piece),
                                   piece.cycles_per_chord)}
    piece.hook = dict(weak)
    assert director.weak_hook(piece) is not None
    director._freshen_hook(piece, "PROMPT")
    assert piece.hook["pattern"] != weak["pattern"] and director.weak_hook(piece) is None
