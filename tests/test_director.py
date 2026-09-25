import json
import random

import pytest

from music import director as D
from music import feedback, llm
from music import presets as P
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


def fake_jev(monkeypatch, pick):
    """Answer every Jev question with pick(name, question); text completion must not be used."""
    calls = []

    def decide(model_id, state, questions, timeout=None):
        calls.append(questions)
        return {name: {"type": "choice", "choice": pick(name, q), "confidence": 0.9} for name, q in questions.items()}

    def no_text(*a, **k):
        raise AssertionError("Jev must not be asked for free text")

    monkeypatch.setattr(llm, "decide", decide)
    monkeypatch.setattr(llm, "complete", no_text)
    return calls


def test_jev_picks_one_of_the_candidate_pieces(monkeypatch):
    calls = fake_jev(monkeypatch, lambda name, q: list(q["criteria"])[-1])
    director = D.Director("jevtypesafe", random.Random(3), log=lambda m: None)
    piece = director.next_piece(None)
    options = calls[0]["piece"]["criteria"]
    assert len(options) > 1
    assert D._describe_piece(piece) == options[list(options)[-1]]


def test_jev_arranges_with_one_batched_call(monkeypatch):
    def pick(name, q):
        if name == "melody_character":
            return "sparse"
        return list(q["criteria"])[0] if name in ("pad", "bass") else "silent"

    calls = fake_jev(monkeypatch, pick)
    director = D.Director("jevtypesafe", random.Random(3), log=lambda m: None)
    arrangement = director.arrange_section(plan_piece(random.Random(3), style="lofi"), 0, None)
    assert len(calls) == 1 and set(calls[0]) == set(P.ENERGY_ORDER) | {"melody_character"}
    assert arrangement.active_roles() == ["pad", "bass"]
    assert arrangement.melody_character == "sparse"


def test_jev_arrangement_respects_layer_cap(monkeypatch):
    fake_jev(monkeypatch, lambda name, q: list(q["criteria"])[0])
    director = D.Director("jevtypesafe", random.Random(3), log=lambda m: None, max_layers=3)
    arrangement = director.arrange_section(plan_piece(random.Random(3), style="lofi"), 3, None)
    assert len(arrangement.active_roles()) == 3


def test_jev_failure_falls_back_to_deterministic_plan(monkeypatch):
    def down(*a, **k):
        raise llm.LLMError("Jev HTTP 402")

    monkeypatch.setattr(llm, "decide", down)
    director = D.Director("jevtypesafe", random.Random(9), log=lambda m: None)
    piece = director.next_piece(None)
    assert piece == D.Director(None, random.Random(9)).next_piece(None)
    assert director.compose_free_layer(piece, piece.sections[1]) is None


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
