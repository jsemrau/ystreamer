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
