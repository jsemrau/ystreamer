"""The art director's persona: a character with an ambition and a goal, in
the Director's system prompt, with its progress through the set in each
track's brief."""
import json
import os
import random
import time

import config
from music.director import Director
from music.plan import plan_piece


def _director(**kw):
    return Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5, **kw)


def test_the_director_speaks_as_its_persona():
    system = _director().system
    assert "Vesna Kade" in system and "**Your ambition.**" in system and "**Your goal for every set:**" in system
    assert system.endswith("Reply with JSON only.")


def test_another_persona_or_none(tmp_path, monkeypatch):
    other = tmp_path / "persona.md"
    other.write_text("You are Ilse, a minimal techno purist. Your goal: one idea per track, pushed far.")
    monkeypatch.setattr(config, "DIRECTOR_PERSONA", str(other))
    assert _director().system.startswith("You are Ilse")
    monkeypatch.setattr(config, "DIRECTOR_PERSONA", str(tmp_path / "missing.md"))
    assert _director().system == Director.PIECE_SYSTEM


def test_the_brief_shows_the_sets_progress():
    director = _director()
    fallback = plan_piece(random.Random(1), style="trance", target_minutes=5)
    avoid = {"progressions": set(), "presets": set()}
    assert "- Track 1 of this set." in director.piece_prompt(None, fallback, avoid)
    os.makedirs(config.RUNTIME_DIR, exist_ok=True)
    with open(os.path.join(config.RUNTIME_DIR, "ratings.jsonl"), "w") as f:
        f.write(json.dumps({"rating": 1, "t": time.time(), "role": "lead"}) + "\n")
        f.write(json.dumps({"rating": -1, "t": time.time() - 99999}) + "\n")      # before the set: not counted
    director.next_piece(None)
    prompt = director.piece_prompt(None, fallback, avoid)
    assert "- Track 2 of this set." in prompt and "1 👍, 0 👎" in prompt and "👍 on the lead" in prompt


def test_the_concept_leads_the_recommendation():
    logs = []
    director = Director(None, random.Random(0), style="trance", log=logs.append)
    piece = plan_piece(random.Random(2), style="trance")
    director._recommend(piece, {"recommendation": {"concept": "a night drive that never reaches the city",
                                                   "arc": "the filter held shut until the peak"}})
    assert "concept: a night drive" in logs[0]
