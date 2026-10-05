import json
import random

import pytest

from music import director as D
from music import feedback, llm
from music.plan import plan_piece
from music.theory import Key


def test_extract_json_variants():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json here")


def test_degrees_to_cycles_maps_into_safe_pentatonic():
    key = Key.parse("C minor")
    cycles = D.degrees_to_cycles(["0 _ _ ~ 2 _ ~ ~", "5 _ 4 _ ~ ~ -1 _"], key, 2, low=0)
    assert cycles[0] == "[0 _ _ ~ 5 _ ~ ~]"
    notes = [int(t) for c in cycles for t in c.strip("[]").split() if t not in "~_"]
    assert all(n % 12 in key.safe_melody_pcs() for n in notes)
    assert 12 in notes  # degree 5 = tonic an octave up


@pytest.mark.parametrize("bad", [
    ["0 1 2 3 4 0 1 2"],            # too busy
    ["0 1 2"],                      # wrong length
    ["0 ~ ~ ~ x ~ ~ ~"],            # bad token
    ["0 ~ ~ ~ 12 ~ ~ ~"],           # out of range (multi-digit)
    "0 ~ ~ ~ 2 ~ ~ ~",              # not a list
])
def test_degrees_to_cycles_rejects(bad):
    with pytest.raises(ValueError):
        D.degrees_to_cycles(bad, Key.parse("C minor"), 1)


def test_director_without_llm_is_deterministic():
    a = D.Director(None, random.Random(9)).next_piece(None)
    b = D.Director(None, random.Random(9)).next_piece(None)
    assert a == b


def test_director_uses_and_validates_llm(monkeypatch):
    replies = iter([
        json.dumps({"title": "Night Ferry", "style": "lofi", "key": "D minor", "bpm": 80,
                    "progression": "i-VI-III-VII", "mood": "late-night"}),
        "not json at all",
    ])
    monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
    director = D.Director("fake/model", random.Random(1), log=lambda m: None)
    piece = director.next_piece(None)
    assert piece.title == "Night Ferry" and piece.key == "D minor"
    # Second call gets garbage -> deterministic arrangement, no exception.
    arrangement = director.arrange_section(piece, 0, None)
    assert arrangement.active_roles()


def test_director_backs_off_after_repeated_failures(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise llm.LLMError("down")

    monkeypatch.setattr(llm, "complete", boom)
    director = D.Director("fake/model", random.Random(1), log=lambda m: None)
    piece = director.next_piece(None)
    for i in range(8):
        director.arrange_section(piece, 0, None)
    assert len(calls) < 9


def test_feedback_steers_away_from_disliked_progressions():
    rng = random.Random(0)
    piece = plan_piece(rng, style="lofi")
    for _ in range(3):
        feedback.append({"source": "human", "rating": -1, "piece": piece.to_dict(),
                         "arrangement": {"layers": {"pad": "warm_pad"}}})
    disliked = feedback.disliked()
    assert piece.progression in disliked["progressions"]
    assert "warm_pad" in disliked["presets"]
    director = D.Director(None, random.Random(0), style="lofi")
    for _ in range(10):
        assert director.next_piece(None).progression != piece.progression


def test_director_shows_its_reasons(monkeypatch, isolated_runtime):
    replies = iter([
        json.dumps({"title": "Night Ferry", "style": "lofi", "key": "D minor", "bpm": 80,
                    "progression": "i-VI-III-VII", "mood": "late-night",
                    "reason": "Relative minor of the last piece, a bit slower."}),
        json.dumps({"layers": {"pad": "warm_pad", "drums": "lofi_kit"}, "melody_character": "sparse",
                    "reason": "Drums enter to lift the verse."}),
    ])
    monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
    logs = []
    director = D.Director("fake/model", random.Random(1), log=logs.append, show_llm=True)
    piece = director.next_piece(None)
    director.arrange_section(piece, 1, None)
    text = "\n".join(logs)
    assert "raw reply" in text
    assert "🧠 Next piece: 'Night Ferry'" in text and "Relative minor" in text
    assert "🧠 Next section" in text and "Drums enter" in text
    thoughts = (isolated_runtime / "thoughts.log").read_text()
    assert "Night Ferry" in thoughts and "Drums enter" in thoughts


def test_planner_decisions_are_labelled(isolated_runtime):
    logs = []
    director = D.Director(None, random.Random(1), log=logs.append)
    piece = director.next_piece(None)
    director.arrange_section(piece, 0, None)
    assert any("built-in planner" in line for line in logs)


def test_director_keeps_listener_narration_only(monkeypatch):
    replies = iter([
        json.dumps({"title": "Night Ferry", "style": "lofi", "key": "D minor", "bpm": 80,
                    "progression": "i-VI-III-VII", "narration": "Stay a little longer with me."}),
        json.dumps({"layers": {"pad": "warm_pad"},
                    "narration": "The verse at 0.5 energy adds epiano_lead"}),
    ])
    monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
    director = D.Director("fake/model", random.Random(1), log=lambda m: None)
    piece = director.next_piece(None)
    assert piece.narration == "Stay a little longer with me."
    assert director.arrange_section(piece, 1, None).narration == ""


def test_the_director_sees_the_previous_shape_and_is_told_not_to_repeat_it(monkeypatch):
    prompts = []

    def complete(model_id, system, user, **kwargs):
        prompts.append(user)
        return json.dumps({"title": "Glass Harbour", "style": "trance", "key": "A minor", "bpm": 138})

    monkeypatch.setattr(llm, "complete", complete)
    director = D.Director("fake/model", random.Random(1), log=lambda m: None)
    previous = plan_piece(random.Random(3), style="trance")
    director.next_piece(previous)
    prompt = prompts[-1]
    shape = " → ".join(f"{s.name} {s.bars}" for s in previous.sections)
    assert f"Previous track's shape (if you write `sections`, make it different): {shape}" in prompt


def test_written_progressions_use_only_the_modes_chords():
    from music.theory import parse_progression, progression_named

    assert parse_progression("i-II-VI-vii", "phrygian").degrees == (0, 1, 5, 6)
    for bad in ("i-V-VI-VII",        # V isn't in natural minor
                "i-ii-VI-VII",       # ii is diminished in minor
                "i-VI-VII",          # three chords
                "i-VI-X-VII"):       # not a numeral
        with pytest.raises(ValueError):
            parse_progression(bad, "minor")
    assert progression_named("i-V-VI-VII", "minor") is None


def test_hooks_stay_on_chord_tones():
    from music.plan import validate_hook

    assert validate_hook({"role": "acid", "pattern": "0 7 0 4 9"}, "trance")[0]["kind"] == "cycle"
    assert validate_hook({"role": "lead", "pattern": "7 ~ 4 ~ 9 ~ 7 6"}, "trance")[0]["kind"] == "phrase"
    # Passing notes between the beats, in a two-bar phrase too...
    assert validate_hook({"role": "lead", "pattern": "<[4 3 2 ~ 0 ~ ~ ~] [2 ~ 4 5 4 ~ 2 ~]>"}, "trance")[0]
    # ...but not a leap to a note outside the chord and away again.
    assert not validate_hook({"role": "lead", "pattern": "7 ~ 4 ~ 9 ~ 7 5"}, "trance")[0]
    assert not validate_hook({"role": "acid", "pattern": "0 3 0 4"}, "trance")[0]       # 3 clashes
    assert not validate_hook({"role": "lead", "pattern": "1 ~ 4 ~ 9 ~ 7 ~"}, "trance")[0]  # strong step off
    assert not validate_hook({"role": "bass", "pattern": "0 7"}, "trance")[0]            # no hook part


def test_the_director_writes_its_own_chords_and_hook(monkeypatch):
    from music.blocks import find_block

    reply = {"title": "Glass Minaret", "key": "E phrygian", "bpm": 138, "progression": "i-II-VI-vii",
             "section_progressions": {"breakdown": "VI-vii-i-i", "chorus": "i-III-II-i"},
             "hook": {"role": "acid", "pattern": "0 7 0 4 9 4 2"}, "idea": "the acid climbs at the peak"}
    prompts = []

    def complete(model_id, system, user, **kwargs):
        prompts.append(user)
        return json.dumps(reply)

    monkeypatch.setattr(llm, "complete", complete)
    director = D.Director("fake/model", random.Random(1), log=lambda m: None, style="trance")
    piece = director.next_piece(None)
    assert (piece.key, piece.progression) == ("E phrygian", "i-II-VI-vii")
    assert piece.section_progressions["chorus"] == "i-III-II-i"
    assert piece.hook == {"role": "acid", "kind": "cycle", "pattern": "0 7 0 4 9 4 2"}
    assert "Suggested key:" in prompts[-1] and "chords_per_mode" in prompts[-1]

    # The acid line plays the hook when it comes in.
    line = find_block("trance", "acid").make(random.Random(0), {}, piece.hook)
    assert line.patterns["motif"] == "0 7 0 4 9 4 2"

    # The next track isn't in a key played recently.
    second = director.next_piece(piece)
    assert second.key != "E phrygian"
    assert "E phrygian" in prompts[-1] and "acid: 0 7 0 4 9 4 2" in prompts[-1]


def test_wrong_case_chords_become_the_modes_own():
    from music.plan import validate_piece

    fallback = plan_piece(random.Random(0), style="trance")
    piece, notes = validate_piece({"key": "B dorian", "progression": "i-VII-iv-VII",
                                   "section_progressions": {"chorus": "i-IV-iii-VII"}}, fallback)
    assert piece.progression == "i-VII-IV-VII"
    assert piece.section_progressions["chorus"] == "i-IV-III-VII"
    assert any("i-VII-iv-VII -> i-VII-IV-VII" in n for n in notes)


def test_sixteen_note_cycle_hooks_are_fine():
    from music.plan import validate_hook

    assert validate_hook({"role": "arp", "pattern": "0 ~ ~ 7 ~ 4 ~ 0 ~ 11 ~ ~ 7 ~ 4 ~"}, "trance")[0]


def test_the_hooks_part_comes_in_right_after_the_opener():
    from music.livecode import Program, Track, apply_edit
    from music.performer import Performer

    performer = Performer(random.Random(2), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    track.hook = {"role": "arp", "kind": "cycle", "pattern": "0 7 4 7 0 4 ~ 7"}
    program = Program(track)
    for _ in range(2):
        program = apply_edit(program, performer._builtin_edit(program), performer.settings["max_lines"])
    assert "arp" in program.lines and program.lines["arp"].patterns[
        next(iter(k for k in program.lines["arp"].patterns if k in ("motif", "arp")))] == "0 7 4 7 0 4 ~ 7"
    # The judge is offered only the hook's part to add until it's in.
    performer2 = Performer(random.Random(2), style="trance", log=lambda *_: None)
    performer2.new_track(None)
    fresh = Program(track)
    fresh = apply_edit(fresh, performer2._add(fresh, "beat"), 5)
    adds = [e.role for e in performer2._candidates(fresh) if e.op == "add"]
    assert adds == ["arp"]


def test_distilled_answers_are_kept_only_when_nothing_needed_fixing():
    from eval.distill_director import _situation, check

    from music.plan import subgenre_from_theme

    req = _situation(random.Random(5), 0)
    # (A trance scenario whose theme doesn't ask for a subgenre: "Ibiza" asks for balearic.)
    while req["style"] != "trance" or subgenre_from_theme("trance", req["theme"]):
        req = _situation(random.Random(len(req["prompt"])), 0)
    # (A subgenre without modes of its own: the fallback's may be balearic, dorian/minor only.)
    answer = {"title": "Glass Minaret", "subgenre": "acid", "key": "C# phrygian", "bpm": 138, "progression": "i-II-VI-vii",
              "section_progressions": {"breakdown": "VI-vii-i-i", "chorus": "i-III-II-i"},
              "hook": {"role": "acid", "pattern": "0 7 0 4 9 4 2"},
              "cues": {"build": ["Start the bass now"], "groove": ["Change the lead melody"],
                       "breakdown": ["Drop the kick"], "peak": ["Open the acid filter"]},
              "parts": {"bass": "[~ 0 0 0]*4", "chords": "~ t ~ t ~ t [~ t] ~",
                        "lead": "<[7 ~ 4 ~ 2 ~ 0 ~] [9 ~ 7 ~ 4 ~ 2 ~]>", "arp": "0 4 7 11"},
              "motion": {"acid": {"acid": [0.3, 0.85, 32]}, "chords": {"lpf": [1500, 4000, 16]}},
              "narration": "Hold on.", "mood": "tense"}
    # A phrygian key the scenario didn't play recently (its history is random).
    from music.theory import Key
    answer["key"] = next(Key(r, "phrygian").name for r in range(12)
                         if Key(r, "phrygian").name not in {Key.parse(k).name for k in req["recent_keys"]})
    assert check(req, json.dumps(answer))[0] is not None
    # Playable after a correction (V -> v) isn't good enough to learn from.
    fixed = dict(answer, key="C# minor", progression="i-V-VI-VII",
                 section_progressions={"breakdown": "VI-VII-i-i", "chorus": "i-VI-III-VII"},
                 hook={"role": "acid", "pattern": "0 7 0 4"})
    assert check(req, json.dumps(fixed))[0] is None
    assert check(req, json.dumps({k: v for k, v in answer.items() if k != "hook"}))[0] is None
    # Too thin for a Switch Angel build: one part, nothing moving.
    assert "parts sketched" in check(req, json.dumps(dict(answer, parts={"bass": "[~ 0 0 0]*4"})))[1]
    assert "moving knobs" in check(req, json.dumps(dict(answer, motion={})))[1]


def test_the_sketch_sets_each_parts_starting_pattern_and_moving_knobs():
    from music.blocks import find_block
    from music.plan import validate_motion, validate_parts

    parts, notes = validate_parts({"bass": "[~ 0 ~ 7]*4", "chords": "t ~ ~ t ~ ~ t ~", "arp": "0 3 7"}, "trance")
    assert set(parts) == {"bass", "chords"} and "arp" in notes[0]          # 3 isn't a chord tone
    motion, notes = validate_motion({"acid": {"acid": [0.3, 0.85, 32]}, "bass": {"room": [0, 0.5, 8]},
                                     "acid ": {"accent": [0, 1, 8]}}, "trance")
    assert motion == {"acid": {"acid": "(slow 32 $ range 0.3 0.85 sine)"}} and len(notes) == 2
    bass = find_block("trance", "rolling_bass").make(random.Random(0), {}, None, parts, motion)
    assert bass.patterns["rhythm"] == "[~ 0 ~ 7]*4"
    stabs = find_block("trance", "piano_stabs").make(random.Random(0), {}, None, parts, motion)
    assert stabs.patterns["stab"] == "t ~ ~ t ~ ~ t ~"
    acid = find_block("trance", "acid").make(random.Random(0), {}, None, parts, motion)
    assert dict(acid.pfs)["acid"] == "(slow 32 $ range 0.3 0.85 sine)"


def test_the_performer_is_told_how_complex_the_phase_should_be():
    from music.livecode import Program
    from music.performer import Performer

    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    performer.state.phase = "groove2"
    note = performer.complexity_note(Program(track))
    assert "about 17" in note and "too plain" in note


def test_the_directors_cues_are_carried_out_by_the_performer_one_per_move():
    from music.livecode import Program, apply_edit, line_synth
    from music.performer import Performer
    from music.plan import validate_cues

    cues, notes = validate_cues({"intro": ["Start the bass now"], "peak": [
        {"do": "sound", "part": "lead", "sound": "ys_fmlead", "say": "Lead to the FM lead"},
        {"do": "sound", "part": "lead", "sound": "ys_supersaw"}], "nowhere": ["x"]}, "trance")
    assert cues == {"build": [{"say": "Start the bass now"}],
                    "groove2": [{"do": "sound", "part": "lead", "sound": "ys_fmlead",
                                 "say": "The lead to the FM lead: DX7"}]}       # exact words
    assert any("supersaw" in n for n in notes) and any("nowhere" in n for n in notes)
    # A sound the named block can't play moves to a block of that part that can.
    cues, notes = validate_cues({"build": [{"do": "start", "part": "arp", "block": "high_acid", "sound": "ys_pluck"}]},
                                "trance")
    assert cues["build"][0]["block"] == "pluck_16" and "using pluck_16" in notes[0]

    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    program = Program(performer.new_track(None))
    performer.state.plan, performer.state.target_lines = [["build", 400], ["groove", 64]], 99   # a long build
    performer.state.cues = {"build": [
        "Start the bass now",                                              # plain: the Performer interprets
        {"do": "start", "part": "arp", "block": "pluck_16", "sound": "ys_glass", "pattern": "0 4 7 4 9",
         "say": "Start the glass arp, 0 4 7 4 9"},                          # precise: played exactly
        {"do": "knob", "part": "arp", "knob": "lpf", "to": [800, 4000, 16], "say": "Open the arp filter"},
        {"do": "sound", "part": "arp", "sound": "ys_fmlead", "say": "Arp to the FM arp"}]}
    performer.state.cue_queue = list(performer.state.cues["build"])
    first = performer.next_edit(program)
    assert first.request == "Start the bass now" and first.op == "add" and first.role == "bass"
    program = apply_edit(program, first)
    second = performer.next_edit(program)
    assert second.source == "director" and second.line.block == "pluck_16"
    assert line_synth(second.line) == "ys_glass" and second.line.patterns["arp"] == "0 4 7 4 9"
    program = apply_edit(program, second)
    third = performer.next_edit(program)
    assert (third.op, third.name, third.value) == ("set_fx", "lpf", "(slow 16 $ range 800 4000 sine)")
    program = apply_edit(program, third)
    fourth = performer.next_edit(program)
    program = apply_edit(program, fourth)
    assert line_synth(program.lines["arp"]) == "ys_fmlead" and not performer.state.cue_queue
    # A part of the arc brings its own instructions.
    performer.state.cues["groove"] = [{"do": "stop", "part": "bass", "say": "Drop the bass"}]
    performer.state.plan, performer.state.step = [["build", 1], ["groove", 64]], 0
    performer.state.phase, performer.state.phase_start = "build", -100
    fifth = performer.next_edit(program)
    assert performer.state.phase == "groove" and (fifth.op, fifth.role) == ("mute", "bass")


def test_cues_that_name_a_surprise_are_not_moves():
    import json as _json
    from music.plan import plan_piece as _plan_piece, resolve_surprise_cues, validate_cues

    raw = {"peak": [{"do": "wrap", "wrap": "stripped_peak", "say": "drop with only kick, bass, hats and the arp"},
                    {"do": "bring back", "part": "chords", "say": "chords return"},
                    {"do": "wrap", "wrap": "key_lift", "say": "everything lifts a whole step"}],
           "outro": [{"do": "surprise", "say": "a surprise"}]}
    cues, notes = validate_cues(raw, "trance")
    assert not notes                                           # recognised, not "trance has no ''"
    piece = _plan_piece(random.Random(0), style="trance")
    piece.cues, piece.surprise = cues, "key_lift"
    notes = resolve_surprise_cues(piece)
    assert [c.get("do") for c in piece.cues["groove2"]] == ["bring back"]   # the real move stays
    assert "outro" not in piece.cues                           # nothing left there
    assert len(notes) == 1 and "stripped_peak" in notes[0]     # the surprise that won't happen is noted
    assert "surprise" not in _json.dumps(piece.cues)


# The hooks of one hardcore session (2026-10-05): the last five are one tune.
SESSION_HOOKS = ["<[4 _ 6 ~ 4 2 0 ~] [6 _ 4 2 0 ~ -3 ~]>", "<[0 ~ 2 ~ 4 ~ 2 ~] [4 ~ 2 0 ~ -3 ~ 0]>",
                 "<[0 ~ 2 ~ -1 ~ 0 ~] [4 _ 2 0 ~ -1 ~ 0]>", "<[0 ~ -1 ~ 0 2 ~ ~] [4 _ 2 0 ~ -1 0 ~]>",
                 "<[0 ~ 2 ~ ~ 4 ~ ~] [2 ~ 0 ~ ~ -1 0 ~]>", "<[0 ~ -1 0 ~ 2 ~ ~] [4 3 2 0 ~ ~ -1 ~]>"]


def test_hook_similarity_hears_the_same_tune_and_tells_different_ones_apart():
    from music.plan import HOOK_SIMILAR, hook_similarity

    happy = "<0 ~ 4 ~ 7 _ 4 ~ 0 ~ 4 7 0 ~ ~ ~ [0 ~ 4 ~ 0 ~ 4 7 0 _ ~ ~ 4 ~ 0 ~]>"
    for a in SESSION_HOOKS[2:]:
        assert hook_similarity(happy, a) < HOOK_SIMILAR
        assert max(hook_similarity(a, b) for b in SESSION_HOOKS[1:] if b != a) >= HOOK_SIMILAR


def test_the_melody_brief_names_what_the_recent_tunes_share():
    from music.plan import melody_brief

    brief = melody_brief(SESSION_HOOKS[1:])
    assert "8 steps a bar" in brief and "between -3 and 4" in brief and "differ" in brief
    assert melody_brief(SESSION_HOOKS[:1]) == ""


def _director_with_hooks(monkeypatch, reply):
    director = D.Director("fake/model", random.Random(1), log=lambda m: None, style="hardcore")
    director.recent_hooks.extend(f"lead: {h}" for h in SESSION_HOOKS)
    calls = []
    monkeypatch.setattr(director, "_ask", lambda kind, *a, **k: calls.append((kind, a[1])) or reply)
    piece = plan_piece(random.Random(0), style="hardcore")
    piece.hook = {"role": "lead", "kind": "phrase", "pattern": "<[0 ~ 2 ~ -1 ~ 0 ~] [4 _ 2 0 ~ -1 0 ~]>"}
    return director, piece, calls


def test_a_hook_too_like_a_recent_one_is_written_again(monkeypatch):
    fresh = "<[7 _ _ ~ 9 7 ~ 4] [6 _ _ _ 4 ~ 2 ~]>"
    director, piece, calls = _director_with_hooks(monkeypatch, {"hook": {"role": "lead", "pattern": fresh}})
    director._freshen_hook(piece, "PROMPT")
    assert [k for k, _ in calls] == ["hook"] and calls[0][1].startswith("PROMPT")
    assert piece.hook["pattern"] == fresh


def test_the_old_hook_stays_when_the_new_one_is_no_fresher_or_breaks_the_rules(monkeypatch):
    old = "<[0 ~ 2 ~ -1 ~ 0 ~] [4 _ 2 0 ~ -1 0 ~]>"
    for reply in ({"hook": {"role": "lead", "pattern": "<[0 ~ 2 ~ -1 ~ 0 ~] [4 _ 2 0 ~ -1 ~ 0]>"}},
                  {"hook": {"role": "lead", "pattern": "1 1 1 1"}}, None):
        director, piece, _ = _director_with_hooks(monkeypatch, reply)
        director._freshen_hook(piece, "PROMPT")
        assert piece.hook["pattern"] == old


def test_a_fresh_hook_is_left_alone(monkeypatch):
    director, piece, calls = _director_with_hooks(monkeypatch, None)
    piece.hook["pattern"] = "<[7 _ _ ~ 9 7 ~ 4] [6 _ _ _ 4 ~ 2 ~]>"
    director._freshen_hook(piece, "PROMPT")
    assert calls == []
