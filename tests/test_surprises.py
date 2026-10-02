"""The art director's surprise menu: chosen sparingly, placed by code, and
each one actually happens in a played track."""

import json
import random

import pytest

import config
from music import surprises
from music.director import Director
from music.live_engine import LiveEngine
from music.performer import Performer
from music.sessionlog import SessionLog
from test_sessionlog import _SimClock, _SyncPool


class _Forced(Director):
    """A Director without a model that always picks one given surprise."""

    def __init__(self, name, **kw):
        # (Seed 2 plans a track with a breakdown, a rebuild and a peak.)
        super().__init__(None, random.Random(2), log=lambda *_: None, **kw)
        self.name = name

    def choose_surprise(self, piece, data, notes):
        super().choose_surprise(piece, {"surprise": self.name}, notes)


def _play(tmp_path, name, seed=2):
    log = SessionLog(directory=str(tmp_path), session_id="t")
    performer = Performer(random.Random(seed), style="trance", log=lambda *_: None)
    performer.director = _Forced(name, style="trance")
    performer.director.recent_surprises.clear()
    sent = []
    clock = _SimClock()
    engine = LiveEngine(sent.extend, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=1, session_log=log)
    engine._pool = _SyncPool()
    engine.run()
    log.close(edits=engine.edits)
    records = [json.loads(line) for line in open(log.path, encoding="utf-8")]
    return records, sent, performer


@pytest.mark.parametrize("name", ["fake_drop", "kickless_drop", "silent_bar", "key_lift", "solo_breakdown",
                                  "stripped_peak", "tempo_lift"])
def test_every_surprise_happens_once_in_a_played_track(tmp_path, name):
    records, sent, performer = _play(tmp_path, name)
    start = next(r for r in records if r["type"] == "track_start")
    assert start["program"]["track"]["surprise"] == name
    fired = [r for r in records if r["type"] == "surprise"]
    assert [r["surprise"] for r in fired] == [name]
    if name == "key_lift":
        keys = {r["key"] for r in records if r["type"] == "edit"}
        assert len(keys) == 2                                    # the last peak a whole step up
    if name == "tempo_lift":
        assert any(r["type"] == "edit" and r["op"] == "tempo" for r in records)
    if name == "fake_drop":
        arcs = [r["after"] for r in records if r["type"] == "arc"]
        assert arcs.index("groove2") < arcs.index("breakdown", arcs.index("groove2"))


def test_surprises_are_rare_and_not_repeated():
    director = Director(None, random.Random(1), style="trance", log=lambda *_: None)
    picked = [director.next_piece(None).surprise for _ in range(12)]
    every = config.SURPRISE_EVERY
    assert sum(bool(p) for p in picked) == len(picked) // every              # one every SURPRISE_EVERY tracks
    chosen = [p for p in picked if p]
    assert all(chosen[i] not in chosen[max(0, i - 4):i] for i in range(len(chosen)))


def test_a_surprise_the_track_cant_take_is_dropped():
    director = Director(None, random.Random(0), style="trance", log=lambda *_: None)
    from music.plan import plan_piece

    piece = plan_piece(random.Random(0), style="trance")
    piece.hook = {"role": "lead", "pattern": "0 1 2 4 5"}             # five different notes
    notes = []
    director.choose_surprise(piece, {"surprise": "three_note_hook"}, notes)
    assert piece.surprise == "" and "three different notes" in notes[0]
    assert surprises.hook_notes("0 2 4 7 9") == 3                         # 7 and 9 are 0 and 2 an octave up
    # One that needs a part of the arc the track doesn't have is refused too.
    assert "no breakdown" in surprises.problem("solo_breakdown", "trance", [["build", 16], ["groove2", 64]])
