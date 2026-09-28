import json
import random
import urllib.request

import pytest

import config
from eval import export_edits
from music import arranger, host_requests
from music.host_requests import RatingQueue
from music.live_engine import LiveEngine
from music.performer import Performer
from music.sessionlog import SessionLog
from test_sessionlog import _SimClock, _SyncPool


def _run(tmp_path, style, seed=4, ratings=None, on_edit=None):
    log = SessionLog(directory=str(tmp_path / "s"), session_id="t")
    clock, sent = _SimClock(), []
    performer = Performer(random.Random(seed), style=style, log=lambda *_: None)
    engine = LiveEngine(sent.extend, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=1, session_log=log)
    engine._pool = _SyncPool()
    engine.ratings = ratings
    if on_edit:
        original = performer.applied

        def applied(edit):
            original(edit)
            on_edit(performer)

        performer.applied = applied
    engine.run()
    records = [json.loads(line) for line in open(log.path, encoding="utf-8")]
    return records, sent


def test_trance_gets_tension_a_breath_and_a_drop(tmp_path):
    records, sent = _run(tmp_path, "trance")
    moves = {r["after"]: r for r in records if r["type"] == "arrangement"}
    assert "rebuild" in moves and any("ys_riser" in s for s in moves["rebuild"]["statements"])
    assert any("sn*32" in s for s in moves["rebuild"]["statements"])                 # the roll speeds up
    drop = moves["groove2"]
    assert drop["breath"] and any("ys_impact" in s for s in drop["statements"])
    # The breath: the kick goes silent, then comes back with the drop.
    i = sent.index("d1 $ silence")
    assert any(s.startswith("d1 $ ") and s != "d1 $ silence" for s in sent[i + 1:])
    assert any("whenmod 16 15" in s for s in drop["statements"])                     # phrase fills


def test_calm_styles_keep_their_own_shape(tmp_path):
    records, sent = _run(tmp_path, "piano")
    assert not [r for r in records if r["type"] == "arrangement"]
    assert not any("ys_riser" in s or "ys_impact" in s for s in sent)


def test_every_transition_statement_is_well_formed():
    for style in arranger.CLUB_STYLES:
        for before, after in (("build", "groove"), ("groove", "breakdown"), ("breakdown", "rebuild"),
                              ("rebuild", "groove2"), ("groove2", "outro")):
            plan = arranger.plan_for(style, before, after)
            for s in plan["statements"]:
                assert s.startswith(("d10 $ ", "d11 $ ")) and s.count("(") == s.count(")")
                assert s.count('"') % 2 == 0


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "rt"))
    return tmp_path


def test_a_rating_is_kept_with_what_played_and_told_to_the_performer(runtime_dir):
    queue = RatingQueue()
    noted = []

    def on_edit(performer):
        if performer.state.phase == "groove" and not noted:
            noted.append(host_requests.submit_rating(-1))

    records, _ = _run(runtime_dir, "trance", ratings=queue, on_edit=on_edit)
    rated = [r for r in records if r["type"] == "rating"]
    assert rated and rated[0]["rating"] == -1 and rated[0]["last_edit"] and rated[0]["code"]
    assert list(export_edits.ratings(str(runtime_dir / "s")))[0]["rating"] == -1
    assert "👎 1" in export_edits.summary(str(runtime_dir / "s"))


def test_the_performer_remembers_ratings():
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    performer.note_rating(1, "d6: pF \"acid\" -> 0.56")
    assert 'the host 👍 liked what played after: d6: pF "acid" -> 0.56' in performer.recent[-1]


def test_the_page_server_takes_ratings(runtime_dir):
    import threading
    from functools import partial
    from http.server import ThreadingHTTPServer

    from stream.ui_server import UI_DIR, Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=UI_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        queue = RatingQueue()
        url = f"http://127.0.0.1:{server.server_address[1]}/rate"
        req = urllib.request.Request(url, data=b'{"rating": 1}', headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["ok"] is True
        assert queue.take()["rating"] == 1
    finally:
        server.shutdown()
