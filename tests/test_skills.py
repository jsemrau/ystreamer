"""The art director's skills (prompts/skills): song design, the basics and
the advanced techniques, in her system prompt; the techniques she names are
remembered so the next tracks choose others."""
import random

import config
from music.director import Director, director_skills, technique_ids
from music.plan import plan_piece

AVOID = {"progressions": set(), "presets": set(), "sounds": set()}


def _director():
    return Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)


def test_the_skills_follow_the_persona_basics_first():
    system = _director().system
    basic, advanced = system.index("# Skill: song design, the basics"), system.index("# Skill: song design, advanced")
    assert system.index("Vesna Kade") < basic < advanced and system.endswith("Reply with JSON only.")


def test_every_technique_has_a_number_and_a_lever():
    ids = technique_ids()
    assert len(ids) >= 15 and ids["A1"] == "Tease, then give"
    text = director_skills()
    for n in ids:
        block = text.split(f"**{n} · ")[1].split("**A")[0]
        assert "Lever" in block, n


def test_the_techniques_named_are_remembered_and_shown_to_the_next_track():
    d = _director()
    fallback = plan_piece(random.Random(1), style="trance", target_minutes=5)
    d._recommend(fallback, {"recommendation": {"concept": "a dawn", "techniques": ["a5", "A11", "Z9", 3]}})
    assert list(d.recent_techniques) == ["A5", "A11"]
    prompt = d.piece_prompt(None, fallback, AVOID)
    assert "Techniques the last tracks used (`techniques_recent`: choose others): A5, A11" in prompt
    assert '"techniques": ["A.."]' in prompt


def test_no_skills_folder_no_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DIRECTOR_SKILLS", str(tmp_path / "none"))
    assert "# Skill" not in _director().system and technique_ids() == {}
