"""The Director plans each track's arc before it starts (bars per phase), and
the live engine moves the arc on by those bars, not by counting edits."""

import random

from music.live_engine import LiveEngine
from music.livecode import Program, apply_edit
from music.performer import Performer
from music.plan import MIN_BUILD_BARS, Section, arc_summary, live_arc, plan_piece


def _piece(sections):
    piece = plan_piece(random.Random(0), style="trance")
    piece.sections = [Section(name, bars, energy) for name, bars, energy in sections]
    return piece


def test_the_directors_sections_become_the_live_arc():
    piece = _piece([("intro", 16, 0.2), ("verse", 32, 0.5), ("chorus", 32, 0.8), ("breakdown", 16, 0.3),
                    ("breakdown", 8, 0.2), ("build", 16, 0.6), ("chorus", 64, 0.9), ("outro", 24, 0.2)])
    assert live_arc(piece) == {"build": 16, "groove": 64, "breakdown": 24, "rebuild": 16,
                               "groove2": 64, "outro": 24}
    assert arc_summary(live_arc(piece)) == "build ≤16 · groove 64 · breakdown 24 · rebuild ≤16 · peak 64 · outro 24 bars"


def test_a_plan_without_a_breakdown_uses_the_styles_arc():
    arc = live_arc(_piece([("verse", 32, 0.5), ("chorus", 64, 0.8), ("outro", 16, 0.2)]))
    assert set(arc) == {"build", "groove", "breakdown", "rebuild", "groove2", "outro"}
    assert all(bars >= 8 for bars in arc.values()) and arc["build"] >= MIN_BUILD_BARS


class _Bars:
    def __init__(self):
        self.bar = 0.0

    def __call__(self):
        return self.bar


def _performer_in(phase, arc, bars):
    performer = Performer(random.Random(0), style="lofi", log=lambda *_: None)
    performer.bar_now = bars
    performer.state.arc = dict(arc)
    performer.state.phase = phase
    performer.state.phase_start = bars.bar
    return performer


ARC = {"build": 16, "groove": 64, "breakdown": 32, "rebuild": 16, "groove2": 64, "outro": 16}


def test_the_groove_lasts_its_planned_bars_however_many_edits():
    bars = _Bars()
    performer = _performer_in("groove", ARC, bars)
    program = Program(performer.new_track(None))
    performer.state.arc, performer.state.phase, performer.state.phase_start = dict(ARC), "groove", 0.0
    performer.state.edits_in_phase = 50               # many edits: no longer what ends a phase
    bars.bar = 63
    performer.advance_phase(program)
    assert performer.state.phase == "groove"
    bars.bar = 64
    performer.advance_phase(program)
    assert performer.state.phase == "breakdown" and performer.state.phase_start == 64


def test_build_ends_when_its_lines_are_in_or_its_bars_are_over():
    bars = _Bars()
    performer = _performer_in("build", ARC, bars)
    program = Program(performer.new_track(None))
    performer.state.arc, performer.state.phase_start = dict(ARC), 0.0
    performer.state.target_lines = 9                  # more than will come in
    bars.bar = 8
    performer.advance_phase(program)
    assert performer.state.phase == "build"
    bars.bar = 16
    performer.advance_phase(program)
    assert performer.state.phase == "groove"


def test_the_outro_ends_on_its_planned_bars_even_with_lines_playing():
    bars = _Bars()
    performer = _performer_in("outro", ARC, bars)
    program = Program(performer.new_track(None))
    for _ in range(3):
        program = apply_edit(program, performer._builtin_edit(program))
    performer.state.arc, performer.state.phase, performer.state.phase_start = dict(ARC), "outro", 0.0
    performer.state.edits_in_phase = 1
    assert len(program.active_roles()) > 1
    bars.bar = 10
    assert not performer.track_finished(program)
    bars.bar = 16
    assert performer.track_finished(program)


class _SimClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_a_track_follows_its_plan():
    """Each timed phase moves on within one edit of its planned bars, and
    what still plays when the outro ends fades out."""
    clock, sent, moves = _SimClock(), [], []
    engine = None

    def log(message):
        if message.startswith("🧭 Arc:"):
            moves.append((message.split("(")[0].split("→")[1].strip(), engine.clock.cycle()))

    performer = Performer(random.Random(5), style="trance", log=log)
    engine = LiveEngine(sent.extend, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=1)
    engine.run()
    arc, edit_bars = performer.state.arc, performer.settings["edit_bars"]
    starts = dict(moves)
    assert [p for p, _ in moves] == ["groove", "breakdown", "rebuild", "groove2", "outro"]
    for before, after in (("groove", "breakdown"), ("breakdown", "rebuild"), ("groove2", "outro")):
        took = starts[after] - starts[before]
        assert arc[before] <= took <= arc[before] + edit_bars + 1, (before, took, arc)
    assert any(s.startswith("xfadeIn ") and s.endswith("$ silence") for s in sent)


def test_resume_keeps_how_far_into_the_phase_it_was():
    from music import runtime

    clock = _SimClock()
    performer = Performer(random.Random(1), style="lofi", log=lambda *_: None)
    LiveEngine(lambda lines: None, performer, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
               max_tracks=1).run()
    state = runtime.load_state()
    state["phase_elapsed"] = 12.0
    clock = _SimClock()
    resumed = Performer(random.Random(2), style="lofi", log=lambda *_: None)
    engine = LiveEngine(lambda lines: None, resumed, log=lambda *_: None, now=clock.now, sleep=clock.sleep,
                        max_tracks=0)
    seen = []
    resumed.next_edit = lambda program: (seen.append(resumed.phase_bars()), engine.stop_event.set())[1]
    engine.run(resume_state=state)
    assert seen and 12.0 <= seen[0] < 13.0
