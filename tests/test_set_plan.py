"""A set with an end time (stream/live.sh --hours): the Director assumes a
length for every track to come, shaped like a club night, so the set ends on
time, and re-plans before each track from the time actually left."""
import random
import time

import pytest

import config
from music.director import Director
from music.plan import TRACK_MINUTES, live_plan, fit_live_plan, set_plan


def test_a_set_plan_breathes_and_ends_on_time():
    plan = set_plan(60, 0, 6.5)
    assert abs(sum(plan) - 60) < 1.0 and len(plan) == 9
    assert plan[0] < max(plan) and plan[-1] < max(plan)          # a shorter opener and closer, peak time longer
    assert all(TRACK_MINUTES[0] <= m <= TRACK_MINUTES[1] for m in plan)
    assert set_plan(2, 58, 6.5) == [TRACK_MINUTES[0]]           # the last moments: one short track


def test_an_assumption_is_refitted_to_the_time_left():
    assert abs(sum(set_plan(30, 30, 6.5, [7, 7, 6, 5])) - 30) < 1.0
    shorter = set_plan(12, 48, 6.5, [7, 7, 6, 5])                 # running late: tracks drop out
    assert len(shorter) < 4 and abs(sum(shorter) - 12) < 1.0


def _director(monkeypatch, minutes_left=60.0, elapsed=0.0):
    now = time.time()
    monkeypatch.setattr(config, "SESSION_START", now - elapsed * 60)
    monkeypatch.setattr(config, "SESSION_END", now + minutes_left * 60)
    return Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)


def test_each_track_gets_its_planned_length(monkeypatch):
    director = _director(monkeypatch)
    piece = director.next_piece(None)
    assert director.set_plan and piece.minutes == director.set_plan[0]
    seconds = sum(s.bars for s in piece.sections) / piece.cps
    assert abs(seconds / 60 - piece.minutes) < 0.35 * piece.minutes
    plan = fit_live_plan(live_plan(piece), piece.cps, piece.minutes)
    assert sum(b for _, b in plan) / piece.cps / 60 < 1.2 * piece.minutes
    assert director.assumed == director.set_plan[1:]


def test_the_brief_states_the_sets_time(monkeypatch):
    director = _director(monkeypatch, minutes_left=45, elapsed=15)
    director.plan_set()
    line = director._set_line()
    assert "This set runs 60 minutes: 15 played, 45 left." in line and "`set_plan`" in line


def test_the_directors_choice_is_kept_when_it_fits(monkeypatch):
    director = _director(monkeypatch, minutes_left=30)
    director.plan_set()
    notes = []
    assert director._chosen_minutes({"minutes": 8, "set_plan": [8, 7, 7]}, notes) == 8
    assert director.assumed == [8, 7, 7] and not notes
    director.plan_set()                                    # the next track starts from that assumption
    assert director.set_plan[0] == 8


def test_a_choice_that_doesnt_fit_keeps_the_working_assumption(monkeypatch):
    director = _director(monkeypatch, minutes_left=30)
    director.plan_set()
    notes = []
    director._chosen_minutes({"minutes": 9, "set_plan": [9, 9, 9, 9, 9]}, notes)
    assert notes and director.assumed == director.set_plan[1:]
    assert director._chosen_minutes({"minutes": 99}, []) == TRACK_MINUTES[1]


def test_without_an_end_time_tracks_keep_their_usual_length(monkeypatch):
    monkeypatch.setattr(config, "SESSION_END", 0.0)
    director = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    piece = director.next_piece(None)
    assert piece.minutes == 5 and "no end time" in director._set_line()
