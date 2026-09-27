"""The Director plans each track's arc before it starts: its sections, in its
order, become the live phases with their bars, and the live engine moves on
by those bars, not by counting edits."""

import random

from music.live_engine import LiveEngine
from music.livecode import Program, apply_edit
from music.performer import Performer, SetState
from music.plan import MIN_BUILD_BARS, Section, arc_summary, live_plan, plan_piece


def _piece(sections):
    piece = plan_piece(random.Random(0), style="trance")
    piece.sections = [Section(name, bars, energy) for name, bars, energy in sections]
    return piece


def test_the_directors_sections_become_the_arc_in_its_order():
    plan = live_plan(_piece([("intro", 16, 0.2), ("verse", 32, 0.5), ("chorus", 32, 0.8), ("breakdown", 16, 0.3),
                             ("breakdown", 8, 0.2), ("build", 16, 0.6), ("chorus", 64, 0.9), ("outro", 24, 0.2)]))
    assert plan == [["build", 16], ["groove", 32], ["groove2", 32], ["breakdown", 24], ["rebuild", 16],
                    ["groove2", 64], ["outro", 24]]
    assert arc_summary(plan) == ("build ≤16 · groove 32 · peak 32 · breakdown 24 · rebuild ≤16 · peak 64 · "
                                 "outro 24 bars")


def test_every_breakdown_is_followed_by_a_rebuild_and_the_arc_ends_with_an_outro():
    plan = live_plan(_piece([("intro", 16, 0.2), ("verse", 32, 0.5), ("breakdown", 16, 0.3), ("chorus", 32, 0.9),
                             ("breakdown", 16, 0.3), ("chorus", 32, 0.9)]))
    phases = [p for p, _ in plan]
    assert phases == ["build", "groove", "breakdown", "rebuild", "groove2", "breakdown", "rebuild", "groove2",
                      "outro"]
    assert all(bars >= MIN_BUILD_BARS for p, bars in plan if p in ("build", "rebuild"))


def test_plans_differ_with_the_directors_sections():
    a = live_plan(_piece([("intro", 16, .2), ("verse", 64, .5), ("breakdown", 32, .3), ("build", 16, .6),
                          ("chorus", 64, .9), ("outro", 16, .2)]))
    b = live_plan(_piece([("intro", 32, .2), ("chorus", 32, .8), ("bridge", 32, .5), ("chorus", 32, .9),
                          ("outro", 32, .2)]))
    assert [p for p, _ in a] != [p for p, _ in b]
    assert "breakdown" not in [p for p, _ in b]                  # a slow burn without a breakdown is fine


class _Bars:
    def __init__(self):
        self.bar = 0.0

    def __call__(self):
        return self.bar


PLAN = [["build", 16], ["groove", 64], ["breakdown", 32], ["rebuild", 16], ["groove2", 64], ["outro", 16]]


def _performer_at(phase, bars):
    performer = Performer(random.Random(0), style="lofi", log=lambda *_: None)
    performer.bar_now = bars
    program = Program(performer.new_track(None))
    performer.state.plan = [list(s) for s in PLAN]
    performer.state.step = [p for p, _ in PLAN].index(phase)
    performer.state.phase = phase
    performer.state.phase_start = bars.bar
    return performer, program


def test_the_groove_lasts_its_planned_bars_however_many_edits():
    bars = _Bars()
    performer, program = _performer_at("groove", bars)
    performer.state.edits_in_phase = 50               # many edits: no longer what ends a phase
    bars.bar = 63
    performer.advance_phase(program)
    assert performer.state.phase == "groove"
    bars.bar = 64
    performer.advance_phase(program)
    assert performer.state.phase == "breakdown" and performer.state.step == 2 and performer.state.phase_start == 64


def test_build_ends_when_its_lines_are_in_or_its_bars_are_over():
    bars = _Bars()
    performer, program = _performer_at("build", bars)
    performer.state.target_lines = 9                  # more than will come in
    bars.bar = 8
    performer.advance_phase(program)
    assert performer.state.phase == "build"
    bars.bar = 16
    performer.advance_phase(program)
    assert performer.state.phase == "groove"


def test_the_outro_ends_on_its_planned_bars_even_with_lines_playing():
    bars = _Bars()
    performer, program = _performer_at("build", bars)      # build up first, then play the outro
    for _ in range(3):
        program = apply_edit(program, performer._builtin_edit(program))
    performer.state.phase, performer.state.step, performer.state.phase_start = "outro", 5, 0.0
    performer.state.edits_in_phase = 1
    assert len(program.active_roles()) > 1
    bars.bar = 10
    assert not performer.track_finished(program)
    bars.bar = 16
    assert performer.track_finished(program)


def test_a_state_saved_with_the_earlier_arc_still_loads():
    state = SetState.from_dict({"phase": "breakdown", "arc": {"build": 32, "groove": 64, "breakdown": 32,
                                                              "rebuild": 32, "groove2": 64, "outro": 32}})
    assert state.plan[state.step] == ["breakdown", 32]


class _SimClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_a_track_follows_its_plan():
    """Each timed step moves on within one edit of its planned bars, and
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
    plan, edit_bars = performer.state.plan, performer.settings["edit_bars"]
    assert [p for p, _ in moves] == [p for p, _ in plan[1:]]
    for i in range(1, len(moves)):
        phase, bars = plan[i]
        took = moves[i][1] - moves[i - 1][1]
        if phase not in ("build", "rebuild"):
            assert bars <= took <= bars + edit_bars + 1, (phase, took, plan)
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
