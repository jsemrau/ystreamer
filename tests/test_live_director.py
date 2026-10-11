"""The art director reasons as each track begins, from her journal (music/
journal.py), and develops the track live, section by section
(Director.develop_section, LiveEngine._develop_next)."""
import json
import os
import random
import time

import config
from music import journal
from music.director import Director
from music.plan import plan_piece

AVOID = {"progressions": set(), "presets": set(), "sounds": set()}


def _director():
    return Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)


def test_the_journal_remembers_each_track_and_how_the_host_took_it():
    journal.write("track", "Dawn Signal", concept="a sunrise", techniques=["A5"], key="Bb lydian", bpm=128,
                  subgenre="ambient house", sounds={"lead": "ys_nylon"}, stance="exploit")
    journal.write("kit", "Dawn Signal", drums="kit_808", bass="ys_sub")
    journal.write("develop", "Dawn Signal", phase="breakdown", decision="the voice alone over the pads")
    os.makedirs(config.RUNTIME_DIR, exist_ok=True)
    with open(os.path.join(config.RUNTIME_DIR, "ratings.jsonl"), "a") as f:
        f.write(json.dumps({"t": time.time(), "rating": 1}) + "\n")
    with open(os.path.join(config.RUNTIME_DIR, "requests.jsonl"), "a") as f:
        f.write(json.dumps({"t": time.time(), "text": "more strings"}) + "\n")
    (tr,) = journal.tracks()
    assert (tr["up"], tr["down"], tr["asked"], tr["kit"]["drums"]) == (1, 0, ["more strings"], "kit_808")
    text = journal.digest()
    assert "'Dawn Signal' (Bb lydian, 128 BPM, ambient house; lead nylon" in text and "kit 808" in text
    assert "1 👍 0 👎" in text and '"more strings"' in text and "breakdown: the voice alone" in text
    assert "Best received" in text


def test_the_brief_asks_her_to_reason_from_memory_first():
    journal.write("track", "Old One", concept="rain on glass")
    prompt = _director().piece_prompt(None, plan_piece(random.Random(1), style="trance", target_minutes=5), AVOID)
    assert prompt.index("## First, reason") < prompt.index("## Then, your recommendation")
    assert '{"reasoning": "...",' in prompt and "'Old One'" in prompt and "## Your memory" in prompt


def test_her_reasoning_is_shown_and_journaled():
    logs = []
    d = Director(None, random.Random(0), style="trance", log=logs.append, target_minutes=5)
    piece = plan_piece(random.Random(1), style="trance", target_minutes=5)
    d._remember(piece, {"reasoning": "The host liked the strings; tonight wants a slow climb.",
                        "recommendation": {"concept": "a slow climb", "techniques": ["A14", "X1"]}})
    assert any(l.startswith("🧠 Reasoning: The host liked") for l in logs)
    assert d._plan["techniques"] == ["A14"] and journal.tracks()[-1]["concept"] == "a slow climb"


def test_she_develops_the_next_section_from_what_plays(monkeypatch):
    d = _director()
    piece = plan_piece(random.Random(1), style="trance", target_minutes=5)
    d._remember(piece, {"reasoning": "r", "recommendation": {"concept": "c"}})
    asked = {}

    def fake_ask(kind, system, user, cache_ttl=""):
        asked.update(kind=kind, user=user, system=system)
        return {"listening": "The arp is crowding the hook.", "decision": "I strip to the hook alone.",
                "cues": [{"do": "stop", "part": "arp", "say": "Arp out"}, {"do": "fly", "part": "x"}]}

    monkeypatch.setattr(d, "_ask", fake_ask)
    monkeypatch.setattr(d, "_llm_available", lambda: True)
    plan = [("build", 16), ("groove", 16), ("breakdown", 24), ("groove2", 24), ("outro", 8)]
    out = d.develop_section(piece, plan, 1, ['d9 $ n "0 2 4" # s "ys_pluck"'], ["add d9 (arp) (LLM)"],
                            [{"do": "stop", "part": "beat", "say": "Kick out"}])
    assert asked["kind"] == "develop" and "▶ groove 16 → → breakdown 24" in asked["user"]
    assert 'd9 $ n "0 2 4"' in asked["user"] and "Kick out" in asked["user"] and "# Skill" in asked["system"]
    assert out["phase"] == "breakdown" and out["decision"] == "I strip to the hook alone."
    assert [c["part"] for c in out["cues"]] == ["arp"]                      # the bad cue was dropped
    assert journal.tracks()[-1]["decisions"][-1]["phase"] == "breakdown"
    # Keeping the plan, or the last step, or no model: nothing replaced.
    monkeypatch.setattr(d, "_ask", lambda *a, **k: {"decision": "as planned", "keep": True})
    assert d.develop_section(piece, plan, 1, [], [], [])["cues"] is None
    assert d.develop_section(piece, plan, 4, [], [], []) is None
    monkeypatch.setattr(config, "DIRECTOR_DEVELOP", False)
    assert d.develop_section(piece, plan, 1, [], [], []) is None


def test_the_engine_hands_her_new_cues_to_the_performer_a_section_ahead():
    from music.live_engine import LiveEngine
    from music.livecode import Program
    from music.performer import Performer

    class Her:
        def __init__(self, cues):
            self.cues, self.calls = cues, []

        def develop_section(self, piece, plan, step, code, edits, planned):
            self.calls.append((step, plan[step + 1][0], planned))
            return {"phase": plan[step + 1][0], "decision": "the voice alone", "cues": self.cues}

    performer = Performer(random.Random(0), style="trance", log=lambda *_: None)
    track = performer.new_track(None)
    engine = LiveEngine(lambda lines: None, performer, log=lambda *_: None)
    engine.program = Program(track)
    st = performer.state
    st.plan, st.step, st.phase = [("build", 16), ("groove", 16), ("breakdown", 24)], 1, "groove"
    st.cues = {"breakdown": [{"do": "stop", "part": "beat", "say": "Kick out"}]}
    new = [{"do": "stop", "part": "arp", "say": "Arp out"}]
    performer.director = Her(new)
    engine._develop_next()
    engine._develop_pool.shutdown(wait=True)
    assert performer.director.calls == [(1, "breakdown", [{"do": "stop", "part": "beat", "say": "Kick out"}])]
    assert st.cues["breakdown"] == new and engine._decisions["breakdown"] == "the voice alone"
    # She answers after the section began: its remaining instructions are hers.
    engine._develop_pool = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=1)
    st.step, st.cues = 1, {}
    performer.director = Her(new)

    def late(piece, plan, step, *rest):
        st.step, st.phase, st.cue_queue = 2, "breakdown", [{"do": "stop", "part": "beat", "say": "old"}]
        return {"phase": "breakdown", "decision": "", "cues": new}

    performer.director.develop_section = late
    engine._develop_next()
    engine._develop_pool.shutdown(wait=True)
    assert st.cue_queue == new
