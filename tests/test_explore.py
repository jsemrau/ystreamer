"""Explore or exploit: the Director's suggestions as a bandit rewarded by the
host's thumbs (music/explore.py)."""
import json
import os
import random
import time

import config
from music import explore
from music.director import Director
from music.plan import plan_piece


def _thumbs(*ratings):
    os.makedirs(config.RUNTIME_DIR, exist_ok=True)
    with open(os.path.join(config.RUNTIME_DIR, "ratings.jsonl"), "a") as f:
        for t, rating, role in ratings:
            f.write(json.dumps({"t": t, "rating": rating, **({"role": role} if role else {})}) + "\n")


def test_a_tracks_thumbs_are_its_reward():
    now = time.time()
    explore.record("A", "explore", {"mode": "dorian", "tempo": "slow"}, t=now - 600)
    explore.record("B", "exploit", {"mode": "minor"}, t=now - 300)
    _thumbs((now - 500, 1, None), (now - 450, 1, "lead"), (now - 200, -1, None))
    a, b = explore.tracks(now)
    assert (a["up"], a["down"], b["up"], b["down"]) == (1.5, 0, 0, 1)
    stats = explore.arm_stats("mode", [a, b])
    assert stats["dorian"]["up"] > 1.4 and stats["minor"]["down"] > 0.9


def test_old_thumbs_count_less():
    now = time.time()
    explore.record("Old", "explore", {"mode": "lydian"}, t=now - 42 * 86400)
    _thumbs((now - 42 * 86400 + 10, 1, None))
    assert abs(explore.arm_stats("mode")["lydian"]["up"] - 0.25) < 0.01   # two half-lives


def test_exploring_takes_the_least_tried_arm():
    hist = [{"arms": {"mode": "minor"}, "up": 0, "down": 0, "weight": 1}] * 3 \
        + [{"arms": {"mode": "dorian"}, "up": 0, "down": 0, "weight": 1}]
    rng = random.Random(0)
    assert {explore.pick("mode", ["minor", "dorian", "phrygian"], "explore", rng, hist) for _ in range(20)} \
        == {"phrygian"}


def test_exploiting_mostly_takes_what_earned_thumbs_but_still_tries_others():
    hist = [{"arms": {"mode": "dorian"}, "up": 3, "down": 0, "weight": 1},
            {"arms": {"mode": "minor"}, "up": 0, "down": 2, "weight": 1}]
    rng = random.Random(0)
    picks = [explore.pick("mode", ["dorian", "minor", "lydian"], "exploit", rng, hist) for _ in range(400)]
    assert picks.count("dorian") > 200 and picks.count("minor") < picks.count("lydian") and "lydian" in picks


def test_the_chance_of_exploring_follows_the_host():
    track = lambda up, down: {"arms": {}, "up": up, "down": down, "weight": 1}
    assert explore.explore_chance([track(0, 1)], 3, base=0.5) == 0.7      # a 👎: look elsewhere
    assert explore.explore_chance([track(1, 0)], 3, base=0.5) == 0.3      # a 👍: ride it
    assert explore.explore_chance([track(1, 0), track(0, 0)], 3, base=0.5) == 0.5
    assert explore.explore_chance([], 0, base=0.5) == 0.35                # a set opens on solid ground


def test_the_brief_says_the_stance_and_what_worked():
    hist = [{"arms": {"mode": "dorian", "touchstone": "Children (Dream Version)"}, "up": 2, "down": 0, "weight": 1}]
    assert "**Explore.**" in explore.brief("explore", hist) and "dorian" in explore.brief("explore", hist)
    exploit = explore.brief("exploit", hist)
    assert "the dorian mode" in exploit and "Children" in exploit
    assert "Nothing has earned clear thumbs yet" in explore.brief("exploit", [])


def test_the_director_takes_a_stance_and_records_what_the_track_used(monkeypatch):
    monkeypatch.setattr(config, "EXPLORE", 1.0)
    d = Director(None, random.Random(0), style="trance", log=lambda *_: None, target_minutes=5)
    fallback = plan_piece(random.Random(1), style="trance", target_minutes=5)
    prompt = d.piece_prompt(None, fallback, {"progressions": set(), "presets": set()})
    assert d._stance == "explore" and "## Explore or exploit\n\n**Explore.**" in prompt
    assert f"Something to try: {d._spark}" in prompt
    d._record_choices(fallback)
    rec = explore.tracks()[-1]
    assert rec["stance"] == "explore" and rec["arms"]["spark"] == d._spark
    assert rec["arms"]["touchstone"] == d._touchstone["title"] and rec["arms"]["tempo"] in explore.TEMPO_BANDS
