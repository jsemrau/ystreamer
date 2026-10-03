"""The stream page's data (runtime/ui.json) and its server."""

import json
import os
import random
import subprocess
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

import config
from music import runtime


def test_ui_events_and_updates_land_in_ui_json(isolated_runtime):
    runtime.ui_reset()
    runtime.ui_update(track={"title": "Glass Harbour", "style": "darkwave"}, phase="groove")
    for i in range(50):
        runtime.ui_event("performer", edit=f"edit {i}")
    runtime.ui_thinking("Judge", "jevtypesafe")
    data = json.load(open(os.path.join(config.RUNTIME_DIR, "ui.json")))
    assert data["track"]["title"] == "Glass Harbour" and data["phase"] == "groove"
    assert len(data["events"]) == 40 and data["events"][-1]["edit"] == "edit 49"   # bounded feed
    assert data["events"][-1]["id"] > data["events"][0]["id"]
    assert data["thinking"]["Judge"]["model"] == "jevtypesafe"
    runtime.ui_thinking("Judge", None)
    assert json.load(open(os.path.join(config.RUNTIME_DIR, "ui.json")))["thinking"] == {}


def test_live_engine_publishes_code_events_and_clock(isolated_runtime):
    from music.live_engine import LiveEngine
    from music.performer import Performer

    class Clock:
        t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    runtime.ui_reset()
    clock = Clock()
    snapshots = []
    from music.director import Director

    director = Director(None, random.Random(1), style="darkwave", log=lambda *_: None)
    engine = LiveEngine(lambda lines: None, Performer(random.Random(2), style="darkwave", log=lambda *_: None,
                                                      director=director),
                        log=lambda *_: None, now=clock.now, sleep=clock.sleep, max_tracks=1)
    original = engine._publish_ui

    def spy(*a):
        original(*a)
        snapshots.append(json.load(open(os.path.join(config.RUNTIME_DIR, "ui.json"))))

    engine._publish_ui = spy
    engine.run()
    busiest = max(snapshots, key=lambda d: len(d["code"]))
    assert len(busiest["code"]) >= 4
    assert all(line["text"].startswith(f"d{line['channel']} $ ") for line in busiest["code"])
    assert busiest["track"]["style"] == "darkwave" and busiest["clock"]["cps"] > 0
    agents = {e["agent"] for s in snapshots for e in s["events"]}
    assert {"track", "director", "performer", "arc"} <= agents


def test_ui_server_serves_the_page_and_ui_json(isolated_runtime):
    from stream.ui_server import UI_DIR, Handler

    runtime.ui_reset()
    runtime.ui_update(phase="outro")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=UI_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/").read().decode()          # the stream's page: the deck
        assert 'id="editor"' in page and "deck.js" in page
        classic = urllib.request.urlopen(f"{base}/index.html").read().decode()
        assert "live.tidal" in classic and "app.js" in classic
        data = json.loads(urllib.request.urlopen(f"{base}/ui.json?t=1").read())
        assert data["phase"] == "outro"
        for asset in ("deck.js", "deck.css", "app.js", "dancer/wave.webm", "dancer/dance.webm", "dancer/groove.webm",
                      "dancer/hakken.webm", "dancer/darkwave.webm", "dancer/bow.webm", "dancer/wave2.webm",
                      "dancer/breakdown.webm", "dancer/breakdown2.webm", "dancer/rebuild.webm", "dancer/trance.webm"):
            assert urllib.request.urlopen(f"{base}/{asset}").status == 200
    finally:
        server.shutdown()


def test_stream_script_parses():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert subprocess.run(["bash", "-n", os.path.join(root, "stream", "stream.sh")]).returncode == 0


def test_tidal_tells_the_piano_rolls_what_each_line_plays():
    from music.blocks import find_block
    from music.livecode import Edit, Program, apply_edit
    from music.performer import Performer
    from music.validator import TidalTypeChecker

    checker = TidalTypeChecker()
    if not checker.available:
        pytest.skip("GHCi with Tidal isn't installed")
    try:
        performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
        program = Program(performer.new_track(None))
        program = apply_edit(program, Edit("add", role="beat", line=find_block("trance", "trance_beat").make(random.Random(0))))
        kicks = checker.events(program.expression("beat"), 4.0, 6.0)
        assert [e[0] for e in kicks] == [4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 5.75]
        assert all(e[2] is None and e[3] == "ys_kick909" for e in kicks)
        assert checker.events("this isn't tidal", 0.0, 1.0) is None
    finally:
        checker.close()
