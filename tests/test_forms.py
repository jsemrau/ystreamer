"""Song forms from real songs (eval/song_forms.py -> music/forms.py), and the
live sections they bring: lift, tag, bridge, interlude, break."""
import random
import sys

import pytest

from eval.song_forms import learn, section_type
from music import arranger, forms
from music.livecode import Edit, EditError
from music.performer import PHASE_EXAMPLES, PHASE_HELP, PHASES, BOLDNESS, Performer
from music.plan import LIVE_PHASES, SECTION_TYPES, Piece, Section, live_plan


@pytest.mark.parametrize("label,bars,kind", [
    ("prechorus", 8, "lift"), ("postchorus2", 8, "tag"), ("instchorus", 8, "interlude"), ("gtr", 16, "interlude"),
    ("solo", 8, "interlude"), ("break", 4, "break"), ("break", 16, "breakdown"), ("chorus3", 8, "chorus"),
    ("altchorus", 8, "chorus"), ("verse1", 16, "verse"), ("bridge", 8, "bridge"), ("silence", 0, None),
    ("intro", 4, "intro"), ("outro", 8, "outro")])
def test_real_labels_fold_into_the_engines_section_types(label, bars, kind):
    assert section_type(label, bars) == kind
    assert kind is None or kind in SECTION_TYPES


def test_learning_counts_moves_lengths_and_shares():
    songs = [("dance", [("intro", 8), ("verse", 16), ("lift", 8), ("chorus", 16), ("tag", 8)]),
             ("dance", [("intro", 4), ("chorus", 16), ("bridge", 8), ("chorus", 16), ("outro", 8)])]
    d = learn(songs)["dance"]
    assert d["songs"] == 2 and d["moves"]["start"] == {"intro": 2}
    assert d["moves"]["chorus"] == {"tag": 1, "bridge": 1, "outro": 1}
    assert d["share_with"]["lift"] == 0.5 and d["share_with"]["bridge"] == 0.5
    assert d["lengths"]["chorus"] == {16: 3}


def test_the_data_file_has_every_family():
    assert {forms.family(s) for s in ("trance", "darkwave", "lofi", "ambient")} == {"dance", "rock", "groove", "pop"}


@pytest.mark.parametrize("style", ["trance", "techno", "hardcore", "deephouse", "darkwave", "lofi"])
def test_suggested_forms_keep_the_styles_frame(style):
    for seed in range(60):
        form = forms.suggest(style, random.Random(seed), 172, 8)
        names = [k for k, _, _ in form]
        assert names[0] == "intro" and "chorus" in names and len(form) <= 10
        assert all(b % 8 == 0 and b >= 8 for _, b, _ in form)
        assert all(b <= 16 for k, b, _ in form if k in forms.SHORT)            # a lift stays a lift
        assert all(names[i + 1] == "chorus" for i, k in enumerate(names) if k == "lift")
        assert all(i and names[i - 1] == "chorus" for i, k in enumerate(names) if k == "tag")
        if style in forms.BREAKDOWN_STYLES:
            assert "breakdown" in names


def test_suggestions_vary_and_avoid_the_recent_shapes():
    shapes = [forms.shape(forms.suggest("trance", random.Random(s), 172, 8)) for s in range(40)]
    assert len(set(shapes)) > 25
    recent = shapes[:5]
    again = [forms.shape(forms.suggest("trance", random.Random(s), 172, 8, avoid=recent)) for s in range(5)]
    assert not set(again) & set(recent)


def _piece(*sections):
    return Piece("t", "trance", "A minor", 138, "i-VI-III-VII", 2, "x", [Section(n, b, 0.5) for n, b in sections])


def test_the_live_plan_plays_each_section_its_own_way():
    plan = live_plan(_piece(("intro", 16), ("verse", 32), ("lift", 8), ("chorus", 32), ("tag", 8), ("bridge", 16),
                            ("breakdown", 16), ("lift", 8), ("chorus", 32), ("interlude", 16), ("break", 8),
                            ("chorus", 16)))
    assert [p for p, _ in plan] == ["build", "groove", "lift", "groove2", "tag", "bridge", "breakdown", "rebuild",
                                    "groove2", "interlude", "break", "groove2", "outro"]
    assert plan[-1] == ["outro", 8]                      # ends on its chorus: a short fade
    assert set(p for p, _ in plan) <= set(LIVE_PHASES)


def test_every_phase_has_its_guidance():
    assert set(PHASES) == set(LIVE_PHASES)
    for table in (PHASE_HELP, PHASE_EXAMPLES, BOLDNESS, Performer.HARMONY_PARTS, Performer.COMPLEXITY_SHARE):
        assert set(PHASES) <= set(table)


def test_the_arranger_marks_the_new_sections():
    lift = arranger.plan_for("trance", "groove", "lift")
    assert any("ys_riser" in s for s in lift["statements"]) and not lift["breath"]
    assert arranger.plan_for("trance", "lift", "groove2")["breath"]           # the lift drops into the peak
    assert arranger.plan_for("trance", "groove2", "break")["playing"] == {}
    assert arranger.plan_for("trance", "break", "tag")["impact"]               # back from the break with a hit
    assert not arranger.plan_for("trance", "groove2", "tag")["impact"]


def test_a_section_rests_its_parts_and_brings_them_back(tmp_path):
    sys.path.insert(0, "tests")
    from test_livecode import _SimClock

    from music.live_engine import LiveEngine

    logs, clock = [], _SimClock()
    performer = Performer(random.Random(2), style="deephouse", log=logs.append)
    LiveEngine(lambda lines: None, performer, log=logs.append, now=clock.now, sleep=clock.sleep, max_tracks=1).run()
    turns = [l for l in logs if l.startswith("🧱 Section")]
    assert any("rests" in l for l in turns) and any("back:" in l for l in turns)
    assert performer.state.held_for_section == []


def test_nothing_brings_a_resting_part_back_early():
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    performer.state.phase, performer.state.held_for_section = "bridge", ["lead"]
    sys.path.insert(0, "tests")
    from test_livecode import _program_with

    program = _program_with("trance", "trance_beat", "glass_lead")
    program.lines["lead"].muted = True
    with pytest.raises(EditError, match="rests for the bridge"):
        performer._edit_from_json({"op": "unmute", "role": "lead"}, program)


def test_the_director_is_offered_a_real_form_and_not_a_recent_one():
    from music.director import Director
    from music.plan import plan_piece

    director = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    fallback = plan_piece(random.Random(1), style="trance", target_minutes=5)
    prompt = director.piece_prompt(None, fallback, {"progressions": set(), "presets": set()})
    assert "Suggested form: **intro" in prompt and "a real dance song's sections" in prompt
    assert "`interlude`: instrumental" in prompt


def test_tabs_keep_their_section_markers():
    from eval.tabs import _sections

    assert _sections(["Intro", None, None, None, "Verse", None, "Chorus", None, None, None]) == \
        [("Intro", 4), ("Verse", 2), ("Chorus", 4)]
