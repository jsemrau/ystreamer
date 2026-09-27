import json
import random

from eval import export_edits
from music import sessionlog
from music.live_engine import LiveEngine
from music.performer import Performer


class _SimClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _session(tmp_path, tracks=2):
    log = sessionlog.SessionLog(directory=str(tmp_path), session_id="test")
    log.start(theme="test", style="trance")
    clock = _SimClock()
    performer = Performer(random.Random(4), style="trance", log=lambda *_: None)
    engine = LiveEngine(lambda lines: None, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=tracks, session_log=log)
    engine.run()
    log.close(edits=engine.edits)
    return engine, log


def test_every_played_edit_is_kept_with_the_artefact_before_and_after(tmp_path):
    engine, log = _session(tmp_path)
    records = [json.loads(line) for line in open(log.path, encoding="utf-8")]
    kinds = [r["type"] for r in records]
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    assert kinds.count("track_start") == 2 and kinds.count("track_end") == 2
    edits = [r for r in records if r["type"] == "edit"]
    assert len(edits) == engine.edits
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    first = edits[0]
    assert first["op"] == "add" and first["line_before"] is None and first["line_after"].startswith("d")
    for e in edits:
        assert e["source"] and e["phase"] and e["describe"] and isinstance(e["code"], list)
    # The artefact after the last edit of a track is the one the track ends on.
    end = next(r for r in records if r["type"] == "track_end")
    last = [e for e in edits if e["track"] == end["track"]][-1]
    assert last["code"] == end["code"]
    assert any(r["type"] == "arc" for r in records)


def test_the_log_is_append_only_across_sessions(tmp_path):
    _session(tmp_path, tracks=1)
    size = len(open(tmp_path / "test.jsonl", encoding="utf-8").readlines())
    _session(tmp_path, tracks=1)            # same id: appended, never overwritten
    assert len(open(tmp_path / "test.jsonl", encoding="utf-8").readlines()) > size


def test_export_gives_state_move_and_reason(tmp_path):
    _session(tmp_path, tracks=1)
    examples = list(export_edits.examples(str(tmp_path)))
    assert examples and examples[0]["program_before"] == ""        # the track starts empty
    assert examples[1]["program_before"].startswith("d")          # then the first line plays
    assert all(e["edit"]["op"] and e["describe"] for e in examples)
    assert "sessions" in export_edits.summary(str(tmp_path))
    assert list(sessionlog.iter_edits(str(tmp_path), style="trance"))
    assert not list(sessionlog.iter_edits(str(tmp_path), style="lofi"))


def test_complexity_is_logged(tmp_path):
    engine, log = _session(tmp_path, tracks=1)
    edits = [json.loads(line) for line in open(log.path, encoding="utf-8") if '"type": "edit"' in line]
    assert max(e["complexity_after"] for e in edits) >= 6


def test_the_first_groove_edit_waits_the_grooves_interval_not_the_builds(tmp_path):
    from music.blocks import STYLE_SETTINGS

    _, log = _session(tmp_path, tracks=1)
    edits = [json.loads(line) for line in open(log.path, encoding="utf-8") if '"type": "edit"' in line]
    i = next(i for i, e in enumerate(edits) if e["phase"] == "groove")
    gap = edits[i]["cycle"] - edits[i - 1]["cycle"]
    # The groove's interval (16), not the build's (4-5). Under the simulated
    # clock the performer's thread can finish just after the bar, and the
    # engine then (correctly) lands the edit one bar later.
    assert STYLE_SETTINGS["trance"]["edit_bars"] <= round(gap) <= STYLE_SETTINGS["trance"]["edit_bars"] + 1
    assert 4 <= round(edits[1]["cycle"] - edits[0]["cycle"]) <= 5  # the build itself stays quick (same +1)


def test_a_full_track_offers_no_blocks_to_add():
    import random as _r

    performer = Performer(_r.Random(0), style="hardcore", log=lambda *_: None)
    from test_livecode import _program_with

    program = _program_with("hardcore", "gabber_kick", "hats", "rumble", "acid", "stab", "hoover")
    assert len(program.active_roles()) == performer.settings["max_lines"]
    prompt = performer._prompt(program)
    assert "none right now: 6 lines play" in prompt
    assert '"pluck_seq"' not in prompt


def test_the_harmony_moves_with_the_arc(tmp_path):
    _, log = _session(tmp_path, tracks=1)
    records = [json.loads(line) for line in open(log.path, encoding="utf-8")]
    changes = [r for r in records if r["type"] == "harmony"]
    parts = [c["part"] for c in changes]
    assert "breakdown" in parts and "peak" in parts                 # its own chords for each
    start = next(r for r in records if r["type"] == "track_start")
    names = {start["program"]["track"]["progressions"][p]["name"] for p in ("main", "breakdown", "peak")}
    assert len(names) == 3                                          # three different progressions
    for c in changes:                                               # each begins on its first chord
        assert c["start_cycle"] == round(c["start_cycle"]) and c["start_cycle"] > 0


def test_consecutive_tracks_dont_repeat_progressions():
    import random as _r

    performer = Performer(_r.Random(3), style="trance", log=lambda *_: None)
    from music.director import Director

    performer.director = Director(None, _r.Random(3), style="trance", log=lambda *_: None)
    track, seen = None, []
    for _ in range(5):
        track = performer.new_track(track)
        seen.append(track.progressions["main"]["name"])
    assert len(set(seen)) == 5
