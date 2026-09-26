import json
import random
import threading
import urllib.request

import pytest

import config
from music import host_requests, llm
from music.host_requests import RequestQueue
from music.live_engine import LiveEngine
from music.livecode import gain_range
from music.performer import Performer
from test_livecode import _program_with


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_the_queue_sees_only_new_requests(runtime_dir):
    host_requests.submit("an old one, before the engine started")
    queue = RequestQueue()
    assert not queue.pending()
    host_requests.submit("more bass\nplease")                     # control characters are cleaned
    host_requests.submit("x" * 500)                               # and long text cut
    assert queue.take()["text"] == "more bass please"
    assert len(queue.take()["text"]) == host_requests.MAX_LENGTH
    assert queue.take() is None
    assert host_requests.submit("   ") is None


def _performer(runtime_dir, **kw):
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None, **kw)
    performer.requests = RequestQueue()
    return performer


def test_built_in_rules_answer_simple_requests(runtime_dir):
    performer = _performer(runtime_dir)
    performer.state.phase = "groove"
    program = _program_with("trance", "trance_beat", "acid")
    host_requests.submit("more bass")
    edit = performer.next_edit(program)
    assert (edit.op, edit.role, edit.request) == ("add", "bass", "more bass") and edit.review

    program = _program_with("trance", "trance_beat", "acid", "rolling_bass")
    host_requests.submit("MORE BASS!")
    edit = performer.next_edit(program)
    assert (edit.op, edit.role, edit.name) == ("set_fx", "bass", "gain")
    assert float(edit.value) == gain_range(program.lines["bass"])[1]        # +3 dB at most

    host_requests.submit("drop the beat")
    edit = performer.next_edit(program)
    assert (edit.op, edit.role) == ("mute", "beat")
    host_requests.submit("make it darker")
    assert performer.next_edit(program).name == "lpf"
    host_requests.submit("play some jazz saxophone")
    edit = performer.next_edit(program)
    assert "couldn't do that" in edit.review and edit.request == "play some jazz saxophone"


def test_the_model_reviews_the_request(runtime_dir, monkeypatch):
    seen = {}

    def fake_converse(agent, model, system, prompt, **kw):
        seen["prompt"], seen["schema"] = prompt, kw.get("schema")
        return json.dumps({"review": "Yes: the bass comes forward, within its level window.",
                           "listening": "The bass sits under the kick.", "reason": "More weight.",
                           "op": "set_fx", "role": "bass", "name": "gain", "value": "1.5"})

    monkeypatch.setattr(llm, "converse", fake_converse)
    performer = _performer(runtime_dir, model_id="local")
    performer.state.phase = "groove"
    program = _program_with("trance", "trance_beat", "rolling_bass")
    host_requests.submit("more bass")
    edit = performer.next_edit(program)
    assert 'The host is listening and asked: "more bass"' in seen["prompt"]
    assert all("review" in v["required"] for v in seen["schema"]["anyOf"])
    assert edit.source == "LLM, request" and edit.review.startswith("Yes: the bass")
    assert float(edit.value) == gain_range(program.lines["bass"])[1]        # 1.5 clamped to the window


def test_a_request_reaches_the_music_and_the_session_log(runtime_dir, tmp_path):
    from music.sessionlog import SessionLog
    from test_sessionlog import _SimClock

    log = SessionLog(directory=str(tmp_path / "s"), session_id="t")
    performer = _performer(runtime_dir)
    clock = _SimClock()
    sent = []
    engine = LiveEngine(sent.extend, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=1, session_log=log)
    original = performer.next_edit
    asked = []

    def next_edit(program):          # the host types during the groove
        if performer.state.phase == "groove" and not asked:
            asked.append(host_requests.submit("drop the beat"))
        return original(program)

    performer.next_edit = next_edit
    engine.run()
    edits = [json.loads(line) for line in open(log.path, encoding="utf-8") if '"type": "edit"' in line]
    answered = [e for e in edits if e.get("request") == "drop the beat"]
    assert answered and answered[0]["op"] == "mute" and answered[0]["review"]


def test_the_page_server_queues_requests(runtime_dir):
    from functools import partial
    from http.server import ThreadingHTTPServer

    from stream.ui_server import UI_DIR, Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=UI_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/request"
        queue = RequestQueue()
        req = urllib.request.Request(url, data=json.dumps({"text": "brighter"}).encode(),
                                     headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["ok"] is True
        assert queue.take()["text"] == "brighter"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(url, data=b'{"text": ""}'))
    finally:
        server.shutdown()
