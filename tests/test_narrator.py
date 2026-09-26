import os
import random
import shutil
import stat
import time

import pytest

from music import narrator as N


@pytest.mark.parametrize("text,ok", [
    ("Stay a little longer. Let the night slow down with you.", True),
    ("The verse section at 0.5 energy calls for 3-5 layers", False),
    ("adding the epiano_lead introduces a gentle presence", False),
    ("x" * 300, False),
    (None, False),
])
def test_clean_narration(text, ok):
    assert bool(N.clean_narration(text)) == ok


def test_modes():
    n = N.Narrator(log=lambda m: None, mode="pieces", speak=False)
    assert n.wants("piece") and not n.wants("section")
    n = N.Narrator(log=lambda m: None, mode="off", speak=False)
    assert not n.wants("piece")


def test_missing_voice_explains_setup(monkeypatch):
    logs = []
    monkeypatch.delenv("YS_TTS_CMD", raising=False)
    n = N.Narrator(log=logs.append, mode="sections", voice="/nope/voice.onnx")
    assert not n.available
    assert "download_voices" in logs[0]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_speaks_through_tts_softens_plays_and_ducks(tmp_path, monkeypatch, isolated_runtime):
    # Stand-in TTS: writes a short tone as the "voice"; stand-in paplay records the call.
    played = tmp_path / "played.txt"
    paplay = tmp_path / "paplay"
    paplay.write_text(f'#!/usr/bin/env bash\necho "$@" >> {played}\n')
    paplay.chmod(paplay.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("YS_TTS_CMD", "ffmpeg -loglevel error -y -f lavfi -i sine=frequency=200:sample_rate=22050:duration=1 {out}")
    monkeypatch.setenv("YS_NARRATION_MIN_GAP", "0")
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    ducks, logs = [], []
    n = N.Narrator(log=logs.append, duck=ducks.append, mode="sections")
    assert n.available
    n.say("Here comes a soft little pulse. Just let it carry you.")
    deadline = time.time() + 30
    while time.time() < deadline and not played.exists():
        time.sleep(0.1)
    while time.time() < deadline and n._busy.is_set():
        time.sleep(0.1)
    assert played.exists(), logs
    assert played.read_text().strip().endswith("voice.wav")
    assert ducks == [True, False]
    assert any(line.startswith("🗣") for line in logs)
    assert "carry you" in (isolated_runtime / "thoughts.log").read_text()


def test_technical_text_is_not_spoken():
    logs = []
    n = N.Narrator(log=logs.append, mode="sections", speak=False)
    n.say("Adding the epiano_lead at 0.5 energy")
    assert logs == ['🗣  (not spoken: numbers or technical words) "Adding the epiano_lead at 0.5 energy"']


def test_dropped_lines_say_why(monkeypatch):
    logs = []
    n = N.Narrator(log=logs.append, mode="sections", speak=False)
    n.available = True                      # pretend a voice is set up, without starting the worker
    n._last_spoken = N.time.monotonic()     # a line was just spoken
    n.say("Here comes the rain again.", kind="section")
    assert "skipped: last line" in logs[-1]
    n.say("Now playing: Neon Rain.", kind="piece")   # track intros are never skipped for the gap
    assert logs[-1] == '🗣  "Now playing: Neon Rain."'


def test_relative_voice_path_resolves_against_the_repo(tmp_path, monkeypatch):
    import config

    monkeypatch.chdir(tmp_path)
    n = N.Narrator(log=lambda *_: None, mode="off", voice="voices/x.onnx", speak=False)
    assert n.voice == f"{config.REPO_DIR}/voices/x.onnx"


def test_env_values_expand_variables(tmp_path, monkeypatch):
    import os

    import config

    env = tmp_path / ".env"
    env.write_text("YS_TEST_VOICE=${HOME}/voices/v.onnx\nYS_TEST_EMPTY=\nYS_TEST_EMPTY=\n")
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("YS_TEST_EMPTY", "kept")
    monkeypatch.delenv("YS_TEST_VOICE", raising=False)
    config.load_env_file(str(env))
    assert os.environ["YS_TEST_VOICE"] == "/home/someone/voices/v.onnx"
    assert os.environ["YS_TEST_EMPTY"] == "kept"
    monkeypatch.delenv("YS_TEST_VOICE")


def test_engine_narrates_piece_start_and_sections(monkeypatch):
    from music.director import Director
    from music.engine import MusicEngine

    class Sim:
        t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    said = []

    class FakeNarrator:
        def say(self, text, kind="section"):
            said.append((kind, text))

    director = Director(None, random.Random(3))
    original = director.arrange_section

    def with_narration(piece, index, current):
        arrangement = original(piece, index, current)
        arrangement.narration = "Let it breathe for a moment."
        return arrangement

    director.arrange_section = with_narration
    sim = Sim()
    engine = MusicEngine(lambda lines: None, director, log=lambda m: None, now=sim.now, sleep=sim.sleep,
                         max_pieces=1, director_grace=5, narrator=FakeNarrator())
    engine.run()
    assert said[0][0] == "piece" and said[0][1].startswith("Now playing:")
    assert ("section", "Let it breathe for a moment.") in said


def test_ducker():
    sent = []
    duck = N.tidal_ducker(sent.append)
    duck(True)
    duck(False)
    assert sent == [["all (|* gain 0.85)"], ["all id"]]
