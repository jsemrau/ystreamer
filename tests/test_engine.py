import random
import re

from music import runtime
from music.director import Director
from music.engine import Clock, MusicEngine


class Sim:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def run_engine(pieces=2, seed=3, style=None, max_layers=6):
    sim = Sim()
    sent = []

    def send(lines):
        sent.append((sim.t, list(lines)))
        return None

    director = Director(None, random.Random(seed), style=style, max_layers=max_layers)
    engine = MusicEngine(send, director, log=lambda m: None, now=sim.now, sleep=sim.sleep,
                         max_pieces=pieces, director_grace=5)
    engine.run()
    return engine, sent, sim


def test_clock_tracks_cps_changes():
    sim = Sim()
    clock = Clock(0.5, now=sim.now)
    sim.t = 10
    assert clock.cycle() == 5
    clock.set_cps(1.0)
    sim.t = 12
    assert clock.cycle() == 7
    assert clock.seconds_until(9) == 2


def test_engine_starts_immediately_then_switches_on_period_boundaries():
    engine, sent, sim = run_engine()
    first_t, first = sent[0]
    assert first[:3][0] == "hush" and first[2] == "resetCycles"
    assert all(l.startswith("d") for l in sent[1][1])
    for t, lines in sent[2:]:
        for line in lines:
            if line.startswith("setcps"):
                continue
            m = re.match(r"jumpMod (\d+) (\d+) \$ ", line)
            assert m, line
            assert int(m.group(2)) in (4, 8)


def test_sections_start_on_multiples_of_the_period(isolated_runtime):
    engine, sent, sim = run_engine(pieces=3)
    import json
    history = [json.loads(l) for l in open(isolated_runtime / "history.jsonl")]
    assert len(history) >= 3 * 8
    state = runtime.load_state()
    assert state["start_cycle"] % (4 * state["piece"]["cycles_per_chord"]) == 0


def test_layer_cap_is_never_exceeded():
    engine, sent, sim = run_engine(pieces=2, max_layers=4)
    for _, lines in sent[1:]:
        playing = [l for l in lines if "silence" not in l and not l.startswith(("setcps", "hush", "resetCycles"))]
        assert len(playing) <= 4


def test_removed_layers_are_silenced():
    engine, sent, sim = run_engine(pieces=1)
    silences = [l for _, lines in sent for l in lines if l.endswith("$ silence")]
    assert silences  # breakdown/outro remove layers


def test_overlay_and_state_written(isolated_runtime):
    engine, sent, sim = run_engine(pieces=1)
    assert (isolated_runtime / "now_playing.txt").read_text().strip()
    assert "orbit" in (isolated_runtime / "code.txt").read_text()
    assert runtime.load_state()["piece"]["title"]


def test_resume_replays_saved_section():
    engine, sent, sim = run_engine(pieces=1)
    state = runtime.load_state()
    sim2 = Sim()
    sent2 = []
    engine2 = MusicEngine(lambda lines: sent2.append(lines), Director(None, random.Random(1)),
                          log=lambda m: None, now=sim2.now, sleep=sim2.sleep, max_pieces=1, director_grace=5)
    engine2.run(resume_state=state)
    assert sent2[0][0] == "hush"
    assert set(engine.active) == set(
        re.match(r"d(\d)", l) and {"1": "drums", "2": "bass", "3": "pad", "4": "keys", "5": "melody",
                                   "6": "arp", "7": "texture", "8": "free"}[l[1]] for l in sent2[1])


def test_free_layer_is_added_when_llm_composes(monkeypatch):
    from music import director as D

    monkeypatch.setattr(D.Director, "compose_free_layer",
                        lambda self, piece, section: ["[0 _ ~ ~ 2 _ ~ ~]"] * piece.period)
    sim = Sim()
    sent = []
    engine = MusicEngine(lambda lines: sent.append(lines) and None, Director(None, random.Random(2)),
                         log=lambda m: None, now=sim.now, sleep=sim.sleep, max_pieces=1,
                         free_layer=True, director_grace=5)
    engine.run()
    assert any(l.startswith("jumpMod 8 ") and "silence" not in l for lines in sent for l in lines)
