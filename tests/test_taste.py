"""The host's taste, remembered: thumbs and requests matched to what played,
read by the Director before it recommends a track's sounds and arc."""
import json
import os
import random
import time

import config
from music import taste
from music.director import Director
from music.plan import plan_piece

NOW = time.time()


def _write(name, rows):
    os.makedirs(config.RUNTIME_DIR, exist_ok=True)
    with open(os.path.join(config.RUNTIME_DIR, name), "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _playing(ts, **synths):
    lines = {role: {"role": role, "body": f'n "0" # s "{synth}"', "patterns": {}, "muted": False, "drums": False}
             for role, synth in synths.items()}
    lines["beat"] = {"role": "beat", "body": 's "bd*4"', "patterns": {}, "drums": True}
    return {"ts": ts, "program": {"lines": lines}}


def _history():
    _write("history.jsonl", [_playing(NOW - 300, lead="ys_flute", chords="ys_hypersaw", texture="ys_softpad"),
                             _playing(NOW - 100, lead="ys_vox", chords="ys_hypersaw")])


def test_thumbs_and_requests_rate_what_was_playing():
    _history()
    _write("ratings.jsonl", [{"rating": -1, "t": NOW - 250, "role": "lead"},
                             {"rating": 1, "t": NOW - 50, "role": "lead"},
                             {"rating": 1, "t": NOW - 40}])
    _write("requests.jsonl", [{"text": "remove the flute", "t": NOW - 240},
                              {"text": "strings would be nice", "t": NOW - 30},
                              {"text": "remove d5", "t": NOW - 20}])
    scores = taste.sound_scores()
    assert scores["ys_flute"][0] == -2
    # 👍 on the lead, 👍 on the track (half), then "remove d5" (the lead is on d5).
    assert scores["ys_vox"][0] == 0.5
    assert scores["ys_hypersaw"][0] == 0.5
    assert scores["ys_coldstrings"][0] == 1          # a wish: from the catalogue
    assert taste.disliked_sounds() == {"ys_flute"}


def test_a_complaint_about_a_sound_not_playing_is_only_a_guess():
    _history()
    _write("requests.jsonl", [{"text": "the piano is terrible", "t": NOW - 50},
                              {"text": "remove the piano", "t": NOW - 40}])
    scores = taste.sound_scores()
    assert scores["ys_housepiano"][0] == -1 and not taste.disliked_sounds()


def test_the_digest_names_likes_dislikes_and_standing_wishes():
    assert taste.digest() == ""
    _history()
    _write("requests.jsonl", [{"text": "remove the flute", "t": NOW - 240},
                              {"text": "remove d3", "t": NOW - 230},
                              {"text": "darker, more aggressive", "t": NOW - 30}])
    _write("ratings.jsonl", [{"rating": -1, "t": NOW - 250, "role": "lead"}])
    text = taste.digest()
    assert "ys_flute (-2" in text and '"darker, more aggressive"' in text
    assert "ys_hypersaw (-1" in text                  # "remove d3": the chords on d3
    assert '"remove d3"' not in text.split("asked for this session")[1]
    # A wish from before this session steers nothing.
    assert '"darker, more aggressive"' not in taste.digest(since=NOW - 10)


def test_the_built_in_planner_leaves_out_disliked_sounds():
    leads = {plan_piece(random.Random(s), style="lofi", disliked_sounds={"ys_keys", "ys_glass"}).sounds.get("lead")
             for s in range(20)}
    assert leads and not leads & {"ys_keys", "ys_glass"}


def test_the_director_hears_the_taste_and_never_plays_a_disliked_sound():
    _history()
    _write("requests.jsonl", [{"text": "remove the flute", "t": NOW - 240}])
    _write("ratings.jsonl", [{"rating": -1, "t": NOW - 250, "role": "lead"}])
    director = Director(None, random.Random(0), style="lofi", theme="rain at night", log=lambda *_: None)
    director.session_start = NOW - 3600                       # the requests were made this session
    fallback = plan_piece(random.Random(1), style="lofi", disliked_sounds={"ys_flute"})
    avoid = {"progressions": set(), "presets": set(), "sounds": taste.disliked_sounds()}
    prompt = director.piece_prompt(None, fallback, avoid)
    assert "ys_flute (-2" in prompt and "Then, your recommendation" in prompt
    piece, notes = director.review_piece({"sounds": {"lead": "ys_flute"}}, fallback, avoid)
    assert piece.sounds["lead"] != "ys_flute" and any("disliked" in n for n in notes)


def test_the_recommendation_is_shown_and_its_arc_reaches_the_performer():
    logs = []
    director = Director(None, random.Random(0), style="trance", log=logs.append)
    piece = plan_piece(random.Random(2), style="trance")
    piece.idea = ""
    director._recommend(piece, {"recommendation": {"sounds": "lead ys_sawlead: the host wants darker",
                                                   "arc": "the filter held shut until the peak",
                                                   "bold": "a false drop before the last chorus"}})
    assert piece.idea == "the filter held shut until the peak"
    assert "🎨 Recommendation" in logs[0] and "false drop" in logs[0]
    director._recommend(piece, {"recommendation": "just a string"})   # ignored
    assert len(logs) == 1
