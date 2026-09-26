import re
import random

import pytest

import config

from music import llm
from music.blocks import BLOCKS, STYLE_SETTINGS, blocks_for, find_block
from music.live_engine import LiveEngine
from music.livecode import (Edit, EditError, LineSpec, Program, Track, allowed_wrappers, apply_edit,
                            changed_statements, check_pattern)
from music.performer import Performer


def _track(style="lofi", key="A minor", bpm=80):
    return Track("Test", style, key, bpm, [0, 5, 3, 4], 2)


def _program_with(style, *names, key="A minor"):
    program = Program(_track(style, key))
    rng = random.Random(0)
    for name in names:
        block = find_block(style, name)
        program = apply_edit(program, Edit("add", role=block.role, line=block.make(rng)))
    return program


# ------------------------------------------------------------------ rendering

def test_harmony_root_sign_and_chords():
    h = _track(key="A minor").harmony()
    assert h["mode"] == "minor"
    assert h["root"] == " - 3"
    assert "+ -" not in h["root"]
    assert _track(key="D major").harmony()["root"] == " + 2"
    assert _track(key="C major").harmony()["root"] == ""
    assert h["chords"].startswith("[0,2,4,6]!2")


@pytest.mark.parametrize("style", sorted(BLOCKS))
def test_every_block_renders(style):
    harmony = _track(style).harmony()
    for block in BLOCKS[style]:
        line = block.make(random.Random(1))
        code = line.render(harmony)
        assert "{" not in code and "}" not in code, (block.name, code)
        assert code.count("(") == code.count(")"), code
        assert code.count("[") == code.count("]"), code
        assert "+ -" not in code


def test_lofi_has_no_pad():
    lofi = BLOCKS["lofi"]
    assert all("ys_pad" not in b.body for b in lofi)
    assert all(b.role != "chords" for b in lofi)


def test_statement_and_mute():
    program = _program_with("lofi", "dusty_beat")
    assert program.statement("beat").startswith("d1 $ ")
    muted = apply_edit(program, Edit("mute", role="beat"))
    assert muted.statement("beat") == "d1 $ silence"
    assert any(line.strip().startswith("-- d1") for line in muted.code_lines())
    assert program.tempo_statement() == "setcps (80/60/4)"


def test_code_lines_highlight():
    program = _program_with("lofi", "dusty_beat", "sub_walk")
    lines = program.code_lines(highlight="bass")
    assert sum("▶" in line for line in lines) == 1


# ----------------------------------------------------------------- validation

def test_apply_edit_returns_copy():
    program = _program_with("lofi", "dusty_beat")
    new = apply_edit(program, Edit("set_fx", role="beat", name="lpf", value="3000"))
    assert new is not program
    assert ["lpf", "3000"] not in [list(p) for p in program.lines["beat"].fx]
    assert "# lpf 3000" in new.statement("beat")
    assert changed_statements(program, new) == [new.statement("beat")]


@pytest.mark.parametrize("edit", [
    Edit("set_fx", role="beat", name="lpf", value="99999"),
    Edit("set_fx", role="beat", name="lpf", value="1; hush"),
    Edit("set_fx", role="bass", name="room", value="0.5"),
    Edit("set_fx", role="beat", name="nonsense", value="1"),
    Edit("set_pattern", role="bass", name="rhythm", value="0 1 [2"),
    Edit("set_pattern", role="bass", name="rhythm", value="c e g"),
    Edit("wrap", role="bass", name="jux rev"),
    Edit("remove", role="lead"),
    Edit("tempo", value="140"),
    Edit("explode", role="beat"),
])
def test_invalid_edits_rejected(edit):
    program = _program_with("lofi", "dusty_beat", "sub_walk")
    with pytest.raises(EditError):
        apply_edit(program, edit)


def test_modulated_fx_allowed():
    program = _program_with("lofi", "dusty_beat")
    new = apply_edit(program, Edit("set_fx", role="beat", name="lpf", value="(slow 8 $ range 1500 4000 sine)"))
    assert "# lpf (slow 8 $ range 1500 4000 sine)" in new.statement("beat")


def test_max_lines_and_duplicate_add():
    program = _program_with("lofi", "dusty_beat")
    block = find_block("lofi", "dusty_beat")
    with pytest.raises(EditError):
        apply_edit(program, Edit("add", role="beat", line=block.make(random.Random(0))))
    other = find_block("lofi", "sub_walk")
    with pytest.raises(EditError):
        apply_edit(program, Edit("add", role="bass", line=other.make(random.Random(0))), max_lines=1)


def test_wrappers_by_role():
    program = _program_with("lofi", "dusty_beat", "vinyl", "rhodes_comp")
    assert allowed_wrappers(program.lines["texture"]) == []
    for role in ("beat", "keys"):
        wrapper = allowed_wrappers(program.lines[role])[0]
        new = apply_edit(program, Edit("wrap", role=role, name=wrapper))
        assert f"{wrapper} $ " in new.statement(role)
        back = apply_edit(new, Edit("unwrap", role=role, name=wrapper))
        assert back.statement(role) == program.statement(role)
    assert not any("stut" in w for w in allowed_wrappers(program.lines["beat"]))


def test_gate_choice_slot():
    program = _program_with("trance", "gated_saw", key="A minor")
    role = find_block("trance", "gated_saw").role
    assert apply_edit(program, Edit("set_pattern", role=role, name="gate", value="2"))
    with pytest.raises(EditError):
        apply_edit(program, Edit("set_pattern", role=role, name="gate", value="7"))


def test_check_pattern():
    assert check_pattern(" 0 ~ 2 4 ") == "0 ~ 2 4"
    with pytest.raises(EditError):
        check_pattern('0" # s "x')


def test_program_roundtrip():
    program = _program_with("trance", "acid")
    again = Program.from_dict(program.to_dict())
    assert again.statement("acid") == program.statement("acid")
    assert LineSpec.from_dict(program.lines["acid"].to_dict()).render(program.track.harmony())


# ------------------------------------------------------------------ performer

def test_settings_for_every_style():
    for style in BLOCKS:
        assert style in STYLE_SETTINGS
        assert blocks_for(style)


class _SimClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _run(style, seed=0, tracks=1, performer=None):
    clock = _SimClock()
    sent: list[str] = []
    performer = performer or Performer(random.Random(seed), style=style, log=lambda *_: None)
    engine = LiveEngine(lambda lines: sent.extend(lines), performer, log=lambda *_: None,
                        now=clock.now, sleep=clock.sleep, max_tracks=tracks)
    engine.run()
    return engine, sent, clock


@pytest.mark.parametrize("style", sorted(BLOCKS))
def test_builtin_set_builds_up_and_ends(style):
    engine, sent, clock = _run(style, seed=3)
    assert engine.edits >= 10
    statements = [s for s in sent if s.startswith("d") and not s.endswith("silence")]
    assert len({s.split(" ")[0] for s in statements}) >= 3   # at least three lines built up
    assert 60 < clock.t < 60 * 30
    assert all("ys_pad" not in s for s in sent) or style != "lofi"


def test_rotation_changes_tracks():
    performer = Performer(random.Random(9), style=None, log=lambda *_: None)
    engine, sent, _ = _run(None, tracks=2, performer=performer)
    assert sum(s.startswith("setcps") for s in sent) >= 2


def test_llm_edit_used_and_fallback(monkeypatch):
    replies = iter(['{"op": "add", "block": "dusty_beat", "reason": "start with drums"}',
                    "not json at all"])
    monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None)
    program = Program(_track())
    first = performer.next_edit(program)
    assert first.op == "add" and first.role == "beat" and first.reason == "start with drums"
    program = apply_edit(program, first)
    second = performer.next_edit(program)       # garbage reply -> built-in edit
    apply_edit(program, second)


def test_llm_invalid_edit_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: '{"op": "set_fx", "role": "beat", "name": "lpf", "value": "999999"}')
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None)
    program = _program_with("lofi", "dusty_beat")
    edit = performer.next_edit(program)
    apply_edit(program, edit)
    assert edit.value != "999999"


def test_resume_from_saved_state():
    from music import runtime

    engine, _, _ = _run("lofi", seed=1)
    state = runtime.load_state()
    assert state["mode"] == "live"
    clock = _SimClock()
    sent: list[str] = []
    performer = Performer(random.Random(2), style="lofi", log=lambda *_: None)
    resumed = LiveEngine(lambda lines: sent.extend(lines), performer, log=lambda *_: None,
                         now=clock.now, sleep=clock.sleep, max_tracks=1)
    resumed.run(resume_state=state)
    assert sent[0] == "hush"


# ------------------------------------------------------- local-model quality

def test_edit_schema_allows_legal_edits_and_rejects_made_up_names():
    jsonschema = pytest.importorskip("jsonschema")
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None)
    program = _program_with("lofi", "dusty_beat", "sub_walk")
    schema = performer._edit_schema(program)
    ok = [
        {"reason": "keys in", "op": "add", "block": "rhodes_comp"},
        {"reason": "darker", "op": "set_fx", "role": "beat", "name": "lpf", "value": "2400"},
        {"reason": "swing", "op": "wrap", "role": "bass", "name": "degradeBy 0.1"},
        {"reason": "drop", "op": "mute", "role": "beat", "narration": "The drums step aside."},
        {"reason": "faster", "op": "tempo", "value": "84"},
    ]
    bad = [
        {"reason": "x", "op": "add", "block": "laser_harp"},                          # no such block
        {"reason": "x", "op": "set_fx", "role": "bass", "name": "room", "value": "0.5"},  # no reverb on bass
        {"reason": "x", "op": "set_fx", "role": "lead", "name": "lpf", "value": "900"},   # lead isn't playing
        {"reason": "x", "op": "wrap", "role": "bass", "name": "jux rev"},              # not a bass wrapper
        {"op": "mute", "role": "beat"},                                                # no reason
        {"reason": "x", "op": "explode"},
    ]
    ok = [{"listening": "the groove is bare", **e} for e in ok]
    bad = [{"listening": "x", **e} for e in bad] + [{"reason": "no ear", "op": "mute", "role": "beat"}]
    for edit in ok:
        jsonschema.validate(edit, schema)
    for edit in bad:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(edit, schema)


def test_out_of_range_numbers_are_clamped_not_rejected():
    from music.performer import _clamp_value

    program = _program_with("lofi", "dusty_beat")
    assert _clamp_value("set_fx", "lpf", "12000", program) == "9000"
    assert _clamp_value("set_fx", "room", "-1", program) == "0"
    assert _clamp_value("set_pf", "acid", "1.4", program) == "0.95"
    assert _clamp_value("set_fx", "lpf", "(slow 8 $ range 100 20000 sine)", program) == \
        "(slow 8 $ range 200 9000 sine)"
    assert _clamp_value("tempo", "", "120", program) == "92"     # 80 BPM + at most 12
    assert _clamp_value("set_pattern", "melody", "0 2 4", program) == "0 2 4"


def test_rejected_edit_gets_one_corrected_retry(monkeypatch):
    replies = iter(['{"reason": "x", "op": "set_pattern", "role": "bass", "name": "rhythm", "value": "c e g"}',
                    '{"reason": "fixed", "op": "set_pattern", "role": "bass", "name": "rhythm", "value": "0 ~ 4 ~"}'])
    prompts = []

    def fake(agent, model_id, system, prompt, **kw):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(llm, "converse", fake)
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None)
    program = _program_with("lofi", "dusty_beat", "sub_walk")
    performer.state.phase = "groove"
    edit = performer.next_edit(program)
    assert edit.source == "LLM" and edit.value == "0 ~ 4 ~"
    assert "previous reply was rejected" in prompts[1]
    assert performer.stats["llm"] == 1


def test_slow_model_counts_as_late_and_falls_back(monkeypatch):
    def slow(*a, **k):
        raise llm.LLMError("local call timed out after 9s")

    monkeypatch.setattr(llm, "converse", slow)
    logs = []
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=logs.append)
    program = _program_with("lofi", "dusty_beat")
    edit = performer.next_edit(program)
    assert edit.source == "built-in"
    assert performer.stats["late"] == 1
    assert any("too slow" in line for line in logs)


def test_local_request_carries_schema_and_survives_servers_without_it(monkeypatch):
    from music import llm as L

    bodies = []

    def fake_post(model, api_base, key, system, user, timeout, max_tokens, temperature, extra=None):
        bodies.append(extra or {})
        if "response_format" in (extra or {}):
            raise L.LLMError("HTTP 400 from server: response_format not supported")
        return '{"op": "tempo", "value": "80"}'

    monkeypatch.setattr(L, "_openai_compatible", fake_post)
    monkeypatch.setattr(L, "_local_schema_ok", True)
    reply = L.complete("local/m", "s", "u", timeout=5, schema={"type": "object"})
    assert "tempo" in reply
    assert bodies[0]["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert "response_format" not in bodies[1]
    assert L._local_schema_ok is False


def test_prompt_teaches_tidal_and_remembers_recent_edits():
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None)
    program = _program_with("lofi", "dusty_beat", "sub_walk", "felt_melody")
    performer.applied(Edit("set_fx", role="beat", name="lpf", value="2400", reason="darker drums", source="LLM"))
    prompt = performer._prompt(program)
    assert "`jux rev`: a reversed copy in the other ear" in prompt       # wrapper glossary
    assert "`lpf` [200-9000]: low-pass cutoff" in prompt                  # effect glossary
    assert "`<a b>` alternates bar by bar" in prompt                       # mini-notation
    assert "- d1: # lpf -> 2400 (you, build): darker drums" in prompt      # its own recent edits
    assert "Good moves in this phase" in prompt and "{" not in prompt.split("## Reply")[0].split("```json")[0][-50:]


def test_every_wrapper_and_effect_is_explained():
    from music.livecode import ALL_WRAPPERS, FX_NOTES, FX_RANGES, PF_RANGES, WRAPPER_NOTES

    assert set(ALL_WRAPPERS) <= set(WRAPPER_NOTES)
    assert set(FX_RANGES) | set(PF_RANGES) <= set(FX_NOTES)


def test_listening_is_logged_before_the_edit(monkeypatch):
    reply = ('{"listening": "The beat is alone and bare.", "reason": "Give it a bass to lean on.", '
             '"op": "add", "block": "sub_walk"}')
    monkeypatch.setattr(llm, "converse", lambda *a, **k: reply)
    clock = _SimClock()
    logs = []
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=logs.append)
    engine = LiveEngine(lambda lines: None, performer, log=logs.append, now=clock.now, sleep=clock.sleep,
                        max_tracks=1)
    program = _program_with("lofi", "dusty_beat")
    engine.clock.reset(program.track.cps)
    edit = performer.next_edit(program)
    assert edit.listening == "The beat is alone and bare."


def test_jevtypesafe_chooses_among_legal_candidates(monkeypatch):
    import types

    import requests

    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        options = json["questions"]["edit"]["criteria"]
        pick = sorted(options)[1]
        return types.SimpleNamespace(status_code=200, text="{}",
                                     json=lambda: {"answers": {"edit": {"choice": pick,
                                                                        "reason": "the groove needs weight"}}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("YS_JEV_API_KEY", "test-key")
    monkeypatch.delenv("YS_JEV_API_BASE", raising=False)
    monkeypatch.delenv("YS_JEV_API_STYLE", raising=False)
    logs = []
    performer = Performer(random.Random(0), style="lofi", model_id="jevtypesafe", log=logs.append)
    program = _program_with("lofi", "dusty_beat")
    edit = performer.next_edit(program)
    assert sent["url"] == "https://jevtypesafeai.com/api/v1/decide"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    options = sent["body"]["questions"]["edit"]["criteria"]
    assert len(options) >= 3 and "The program playing now:" in sent["body"]["state"]
    assert edit.source == "jevtypesafe" and edit.listening == "the groove needs weight"
    apply_edit(program, edit)                      # every candidate is legal
    assert any("jevtypesafe/decide" in line and "←" in line for line in logs)


def test_jevtypesafe_without_key_falls_back(monkeypatch):
    monkeypatch.delenv("YS_JEV_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("YS_JEV_API_STYLE", raising=False)
    logs = []
    performer = Performer(random.Random(0), style="lofi", model_id="jevtypesafe", log=logs.append)
    edit = performer.next_edit(_program_with("lofi", "dusty_beat"))
    assert edit.source == "built-in"
    assert any("YS_JEV_API_KEY" in line for line in logs)


def test_director_plans_the_track_in_live_mode():
    from music.director import Director

    director = Director(None, random.Random(1), style="lofi", log=lambda *_: None)
    performer = Performer(random.Random(0), style="lofi", log=lambda *_: None, director=director)
    track = performer.new_track(None)
    assert track.title == performer._last_piece.title and track.style == "lofi"
    second = performer.new_track(track)
    assert second.title and performer._last_piece.title == second.title


def test_arc_moments_are_narrated_without_a_text_model():
    class Voice:
        def __init__(self):
            self.lines = []

        def wants(self, kind):
            return True

        def say(self, text, kind="section"):
            self.lines.append((kind, text))

    voice = Voice()
    engine, _, _ = None, None, None
    clock = _SimClock()
    performer = Performer(random.Random(3), style="lofi", log=lambda *_: None)
    engine = LiveEngine(lambda lines: None, performer, log=lambda *_: None, narrator=voice,
                        now=clock.now, sleep=clock.sleep, max_tracks=1)
    engine.run()
    engine._voice_pool.shutdown(wait=True)
    kinds = [k for k, _ in voice.lines]
    assert kinds[0] == "piece" and voice.lines[0][1].startswith("Now playing:")
    assert kinds.count("section") >= 3       # groove, breakdown, rebuild, ...


def test_edit_bars_override():
    performer = Performer(random.Random(0), style="lofi", log=lambda *_: None, edit_bars=12)
    assert performer.settings["edit_bars"] == 12
    performer.new_track(None)
    assert performer.settings["edit_bars"] == 12
    assert Performer(random.Random(0), style="lofi", log=lambda *_: None).settings["edit_bars"] == 8


# ------------------------------------------------------ generator / judge

_PROPOSALS = json_text = (
    '{"proposals": ['
    '{"listening": "bass is plain", "reason": "walk the bass", "op": "set_pattern", "role": "bass", '
    '"name": "rhythm", "value": "0 ~ 4 ~ 7 ~ 4 ~"},'
    '{"listening": "drums too bright", "reason": "darken the drums", "op": "set_fx", "role": "beat", '
    '"name": "lpf", "value": "2200"},'
    '{"listening": "x", "reason": "nonsense", "op": "set_fx", "role": "lead", "name": "lpf", "value": "900"}'
    ']}')


def _judge_setup(monkeypatch, pick):
    """pick(options) -> option id; returns (performer, program, calls)."""
    calls = {}
    monkeypatch.setattr(llm, "converse", lambda *a, **k: _PROPOSALS)

    def decide(agent, state, questions, **kw):
        options = questions["edit"]["criteria"]
        calls["options"] = options
        calls["agent"] = agent
        choice = pick(options)
        return {"edit": {"choice": choice, "reason": "it develops the groove", "confidence": 0.45,
                         "probabilities": {k: (0.6 if k == choice else 0.4 / (len(options) - 1)) for k in options}}}

    monkeypatch.setattr(llm, "decide", decide)
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=lambda *_: None,
                          judge="jevtypesafe")
    program = _program_with("lofi", "dusty_beat", "sub_walk")
    performer.state.phase = "groove"
    return performer, program, calls


def test_judge_picks_the_llm_idea(monkeypatch):
    performer, program, calls = _judge_setup(
        monkeypatch, lambda opts: next(k for k, v in opts.items() if "walk the bass" in v))
    edit = performer.next_edit(program)
    assert edit.source == "LLM, judged" and edit.value == "0 ~ 4 ~ 7 ~ 4 ~"
    assert edit.listening == "bass is plain"                 # the LLM's own explanation is kept
    assert calls["agent"] == "Judge"
    texts = list(calls["options"].values())
    assert len(texts) >= 4                                   # 2 valid LLM ideas + built-in candidates
    assert not any("lead" in t and "900" in t for t in texts)  # the invalid proposal was dropped
    assert not any("LLM" in t or "built-in" in t for t in texts)   # blind
    assert performer.stats["judge_llm"] == 1 and performer.stats["llm"] == 1
    assert len(performer.judge_mass) == 1 and 0.6 < performer.judge_mass[0] < 1.0
    logs = []
    performer.log = logs.append
    performer.report_stats()
    assert "gave them" in logs[-1] and "of its probability" in logs[-1]


def test_judge_picks_a_builtin_idea(monkeypatch):
    performer, program, calls = _judge_setup(
        monkeypatch, lambda opts: next(k for k, v in opts.items()
                                       if "walk the bass" not in v and "darken the drums" not in v))
    edit = performer.next_edit(program)
    assert edit.source == "built-in, judged"
    assert performer.stats["judge_builtin"] == 1 and performer.stats["llm"] == 0
    apply_edit(program, edit)


def test_judge_unavailable_plays_the_llms_idea(monkeypatch):
    performer, program, _ = _judge_setup(monkeypatch, lambda opts: "e1")

    def down(*a, **k):
        raise llm.LLMError("jevtypesafe HTTP 503")

    monkeypatch.setattr(llm, "decide", down)
    edit = performer.next_edit(program)
    assert edit.source == "LLM, judged"


def test_one_note_changes_never_touch_repeat_counts():
    performer = Performer(random.Random(0), style="darkwave", log=lambda *_: None)
    program = _program_with("darkwave", "sequencer_bass")
    line = program.lines["bass"]
    for seed in range(40):
        performer.rng = random.Random(seed)
        line.patterns["rhythm"] = "[0 0 7 0]*4"
        edit = performer._pattern_edit("bass", line)
        if edit and edit.name == "rhythm" and "*" in edit.value and edit.value.startswith("["):
            assert edit.value.endswith("]*4"), edit.value


def test_small_context_window_switches_to_compact_prompts(monkeypatch):
    prompts = []

    def fake(agent, model_id, system, prompt, **kw):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise llm.LLMError("HTTP 400: the prompt doesn't fit the model's context window (...)")
        return '{"listening": "x", "reason": "darker", "op": "set_fx", "role": "beat", "name": "lpf", "value": "2400"}'

    monkeypatch.setattr(llm, "converse", fake)
    logs = []
    performer = Performer(random.Random(0), style="lofi", model_id="local", log=logs.append)
    program = _program_with("lofi", "dusty_beat", "sub_walk", "felt_melody")
    performer.state.phase = "groove"
    edit = performer.next_edit(program)
    assert edit.source == "LLM"
    assert len(prompts[1]) < len(prompts[0]) - 1500
    assert "`jux rev`" in prompts[1] and "a reversed copy" not in prompts[1]   # names kept, notes dropped
    assert any("compact prompts" in line for line in logs)
    performer.next_edit(program)
    assert len(prompts[2]) < len(prompts[0]) - 1500                          # stays compact


def test_director_tempo_follows_the_locked_style():
    from music.director import Director
    from music import llm as L

    director = Director("local", random.Random(0), style="darkwave", log=lambda *_: None)
    reply = {"title": "Paper Lanterns", "style": "lofi", "key": "A minor", "bpm": 76,
             "progression": "i-VI-III-VII", "mood": "late-night", "reason": "x"}
    director._ask = lambda *a, **k: dict(reply)
    piece = director.next_piece(None)
    assert piece.style == "darkwave"
    assert 110 <= piece.bpm <= 128
    assert piece.title == "Paper Lanterns"


def test_gain_edits_stay_near_the_lines_level():
    program = _program_with("piano", "strings")
    line = program.lines["chords"]
    base = float(line.base_gain)
    ok = apply_edit(program, Edit("set_fx", role="chords", name="gain", value=f"{base * 1.05:.3f}"))
    assert ok.lines["chords"].fx
    with pytest.raises(EditError):
        apply_edit(program, Edit("set_fx", role="chords", name="gain", value="1.0"))   # +13 dB
    with pytest.raises(EditError):
        apply_edit(program, Edit("remove_fx", role="chords", name="gain"))
    performer = Performer(random.Random(0), style="piano", log=lambda *_: None)
    edit = performer._edit_from_json({"op": "set_fx", "role": "chords", "name": "gain", "value": "1.0"}, program)
    apply_edit(program, edit)                                                     # clamped, playable
    assert float(edit.value) <= base * 1.09 + 1e-3


def test_drum_lines_keep_their_kit():
    program = _program_with("trance", "trance_beat")
    ok = apply_edit(program, Edit("set_pattern", role="beat", name="hats", value="[~ 808oh]*4, hh*16"))
    assert "808oh" in ok.statement("beat")
    for foreign in ("[~ arpy]*4", "cp*4", "[~ superpiano]*4", "[~ 808oh:7]*4"):
        with pytest.raises(EditError, match="kit"):
            apply_edit(program, Edit("set_pattern", role="beat", name="hats", value=foreign))
    performer = Performer(random.Random(0), style="trance", model_id="local", log=lambda *_: None)
    assert '"sounds":{' in performer._prompt(program)


def test_no_random_pitching_of_drums():
    from music.livecode import DRUM_WRAPPERS

    assert not any("speed" in w for w in DRUM_WRAPPERS)


def test_every_melodic_variant_and_pool_is_in_harmony():
    from music.livecode import CHOICE_SLOTS, harmony_problem

    for style, blocks in BLOCKS.items():
        for block in blocks:
            if block.drums:
                continue
            for slot, variants in block.patterns.items():
                if slot in CHOICE_SLOTS or slot == "synth":
                    continue
                for v in variants:
                    assert harmony_problem(block.role, v) is None, (style, block.name, v)
            if block.role in ("bass", "acid"):
                assert all(d % 7 in (0, 2, 4, 6) for d in block.pool), (style, block.name, block.pool)


def test_clashing_notes_are_rejected():
    from music.livecode import harmony_problem

    program = _program_with("trance", "acid")
    with pytest.raises(EditError, match="not in the chord"):          # the edit from the log
        apply_edit(program, Edit("set_pattern", role="acid", name="motif", value="0 5 0 9 7 0 5 12"))
    assert apply_edit(program, Edit("set_pattern", role="acid", name="motif", value="0 4 0 9 7 11"))
    assert harmony_problem("lead", "0 1 2 3 4 5 7 ~") is None           # passing notes on weak steps
    assert harmony_problem("lead", "3 ~ 2 ~ 0 ~ ~ ~") is not None       # a 4th on the downbeat
    assert harmony_problem("lead", "[0 3]*2") is not None               # repeating: chord tones only
    assert harmony_problem("bass", "0 ~ -1 ~") is None                   # -1 = the 7th below


def test_a_late_edit_comes_in_on_the_next_bar_instead_of_being_dropped():
    import threading

    release, first = threading.Event(), []

    class SlowPerformer(Performer):
        def next_edit(self, program):
            if not first:
                first.append(True)
                release.wait(10)          # the model is still thinking
            return super().next_edit(program)

    clock, logs, sent = _SimClock(), [], []
    performer = SlowPerformer(random.Random(3), style="lofi", log=lambda *_: None)
    engine = LiveEngine(lambda lines: sent.extend(lines), performer, log=logs.append,
                        now=clock.now, sleep=clock.sleep, max_tracks=1)
    worker = threading.Thread(target=engine.run)
    worker.start()
    for _ in range(200):
        if any("still deciding" in line for line in logs):
            break
        threading.Event().wait(0.01)
    assert any("still deciding" in line for line in logs)
    assert not [line for line in sent if line.startswith("d")]   # the edit waits for its reply
    release.set()
    worker.join(30)
    assert engine.edits >= 10 and sent


def test_only_the_local_model_gets_a_longer_wait(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_PERFORMER_WAIT_SECONDS", 60.0)
    program = _program_with("techno", "techno_kick")
    window = STYLE_SETTINGS["techno"]["edit_bars"] / program.track.cps * 0.85
    local = Performer(random.Random(0), style="techno", model_id="local", log=lambda *_: None)
    claude = Performer(random.Random(0), style="techno", model_id="claude", log=lambda *_: None)
    local.state.phase = claude.state.phase = "groove"
    assert local._wait_seconds(program) == max(60.0, window)
    assert claude._wait_seconds(program) == pytest.approx(max(4.0, window))
    # The build doesn't wait for a slow model: lines keep coming in.
    local.state.phase = "build"
    assert local._wait_seconds(program) == pytest.approx(max(4.0, 4 / program.track.cps * 0.85))


def test_the_build_adds_lines_every_few_bars_then_the_groove_slows_down(monkeypatch):
    monkeypatch.setattr(config, "BUILD_BARS", 4)
    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    performer.state.phase = "build"
    assert performer.edit_bars_now() == 4
    performer.state.phase = "groove"
    assert performer.edit_bars_now() == STYLE_SETTINGS["trance"]["edit_bars"]


@pytest.mark.parametrize("style", ["trance", "techno", "darkwave"])
def test_club_styles_build_to_a_rich_moving_peak(style):
    peak = []
    performer = Performer(random.Random(1), style=style, log=lambda *_: None)
    original = performer.applied

    def applied(edit):
        original(edit)
        roles = engine.program.active_roles()
        if len(roles) > len(peak):
            peak[:] = [engine.program.statement(r) for r in roles]

    performer.applied = applied
    clock = _SimClock()
    engine = LiveEngine(lambda lines: None, performer, log=lambda *_: None,
                        now=clock.now, sleep=clock.sleep, max_tracks=1)
    engine.run()
    code = "\n".join(peak)
    assert len(peak) >= 6
    assert "*16" in code                          # a 16th-note acid or arp
    assert "sine" in code                         # something sweeps
