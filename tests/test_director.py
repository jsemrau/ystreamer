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
    assert f"Previous piece's shape (don't repeat it): {shape}" in prompt
    assert "must NOT repeat the previous piece's" in prompt and "double breakdown" in prompt
