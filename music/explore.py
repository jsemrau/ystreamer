"""
Explore or exploit: the Director's creative choices as a bandit, rewarded by
the host's thumbs.

Each track the Director is offered one arm on each dimension (DIMENSIONS):
a touchstone from the host's favourites, a spark, a mode and a tempo band.
What the track used is kept in choices.jsonl; the thumbs that fell while it
played (ratings.jsonl) are its reward: ±1 on the whole track, ±0.5 on a
part, older thumbs counting less (HALF_LIFE_DAYS).

Each track takes a stance:
- explore: the least-tried arm on every dimension, and the bold option at
  every choice: the stream goes somewhere it hasn't been;
- exploit: Thompson sampling, each arm's chance of a 👍 drawn from
  Beta(1 + up, 1 + down), so what earned thumbs comes back, while untried
  arms, uncertain, still win sometimes.
The chance of exploring is config.EXPLORE, higher after a 👎 (that didn't
work: look elsewhere), lower after a 👍 (ride it), lower at a set's start
(open on solid ground).
"""

from __future__ import annotations

import json
import os
import random
import time

import config

DIMENSIONS = ("touchstone", "spark", "mode", "tempo")
HALF_LIFE_DAYS = 21.0
# A track's thumbs: until the next track began, at most this long.
MAX_TRACK_SECONDS = 20 * 60
TEMPO_BANDS = ("slow", "mid", "fast")


def _path(name: str) -> str:
    return os.path.join(config.RUNTIME_DIR, name)


def _read(name: str) -> list[dict]:
    out = []
    try:
        with open(_path(name), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except OSError:
        pass
    return out


def record(title: str, stance: str, arms: dict[str, str | None], t: float | None = None) -> None:
    """Keep what a track used, when it began."""
    rec = {"t": time.time() if t is None else t, "title": title, "stance": stance,
           "arms": {k: v for k, v in arms.items() if k in DIMENSIONS and v}}
    try:
        os.makedirs(config.RUNTIME_DIR, exist_ok=True)
        with open(_path("choices.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def tracks(now: float | None = None) -> list[dict]:
    """Every recorded track with its reward: {"t", "title", "stance", "arms",
    "up", "down", "weight"} (up/down the thumbs, weight the age discount)."""
    now = time.time() if now is None else now
    choices = sorted(_read("choices.jsonl"), key=lambda r: float(r.get("t", 0) or 0))
    ratings = _read("ratings.jsonl")
    out = []
    for i, c in enumerate(choices):
        start = float(c.get("t", 0) or 0)
        end = min(start + MAX_TRACK_SECONDS,
                  float(choices[i + 1].get("t", 0) or 0) if i + 1 < len(choices) else float("inf"))
        up = down = 0.0
        for r in ratings:
            t = float(r.get("t", 0) or 0)
            if start <= t < end:
                value = 0.5 if r.get("role") else 1.0
                if int(r.get("rating", 0) or 0) > 0:
                    up += value
                else:
                    down += value
        out.append({**c, "up": up, "down": down,
                    "weight": 0.5 ** (max(0.0, now - start) / 86400 / HALF_LIFE_DAYS)})
    return out


def arm_stats(dimension: str, history: list[dict] | None = None) -> dict[str, dict[str, float]]:
    """arm -> {"up", "down" (discounted thumbs), "plays" (discounted tracks)}."""
    stats: dict[str, dict[str, float]] = {}
    for tr in tracks() if history is None else history:
        arm = (tr.get("arms") or {}).get(dimension)
        if not arm:
            continue
        s = stats.setdefault(arm, {"up": 0.0, "down": 0.0, "plays": 0.0})
        s["up"] += tr["up"] * tr["weight"]
        s["down"] += tr["down"] * tr["weight"]
        s["plays"] += tr["weight"]
    return stats


def explore_chance(history: list[dict], track_in_set: int, base: float | None = None) -> float:
    """config.EXPLORE, moved by the last rated track and the set's start."""
    p = config.EXPLORE if base is None else base
    rated = [tr for tr in history if tr["up"] or tr["down"]]
    if rated and rated[-1] is history[-1]:            # the host reacted to the last track
        net = rated[-1]["up"] - rated[-1]["down"]
        p += 0.2 if net < 0 else -0.2 if net > 0 else 0.0
    if track_in_set == 0:
        p -= 0.15
    return min(0.9, max(0.1, p))


def choose_stance(rng: random.Random, history: list[dict], track_in_set: int) -> str:
    if config.EXPLORE <= 0:
        return "exploit"
    if config.EXPLORE >= 1:
        return "explore"
    return "explore" if rng.random() < explore_chance(history, track_in_set) else "exploit"


def pick(dimension: str, candidates: list[str], stance: str, rng: random.Random,
         history: list[dict] | None = None) -> str | None:
    """An arm among `candidates`: the least-played when exploring, the best
    Thompson draw when exploiting."""
    if not candidates:
        return None
    stats = arm_stats(dimension, history)
    zero = {"up": 0.0, "down": 0.0, "plays": 0.0}
    if stance == "explore":
        fewest = min(stats.get(c, zero)["plays"] for c in candidates)
        return rng.choice([c for c in candidates if stats.get(c, zero)["plays"] <= fewest + 0.25])
    draws = {c: rng.betavariate(1 + stats.get(c, zero)["up"], 1 + stats.get(c, zero)["down"]) for c in candidates}
    return max(candidates, key=lambda c: draws[c])


def tempo_band(bpm: float, lo: float, hi: float) -> str:
    third = (hi - lo) / 3 or 1
    return TEMPO_BANDS[min(2, max(0, int((bpm - lo) // third)))]


def band_range(band: str, lo: float, hi: float) -> tuple[int, int]:
    third = (hi - lo) / 3
    i = TEMPO_BANDS.index(band) if band in TEMPO_BANDS else 1
    return int(round(lo + i * third)), int(round(lo + (i + 1) * third))


def evidence(history: list[dict] | None = None, n: int = 4) -> list[str]:
    """What earned thumbs, best first: "the dorian mode (+2 👍)"."""
    history = tracks() if history is None else history
    found = []
    for dim in DIMENSIONS:
        for arm, s in arm_stats(dim, history).items():
            net = s["up"] - s["down"]
            if net >= 0.5:
                found.append((net, _name(dim, arm), s))
    found.sort(key=lambda x: -x[0])
    return [f"{name} (+{net:.1f} net 👍 over {s['plays']:.1f} tracks)" for net, name, s in found[:n]]


def misses(history: list[dict] | None = None, n: int = 3) -> list[str]:
    """What drew 👎, worst first."""
    history = tracks() if history is None else history
    found = []
    for dim in DIMENSIONS:
        for arm, s in arm_stats(dim, history).items():
            net = s["up"] - s["down"]
            if net <= -0.5:
                found.append((net, _name(dim, arm)))
    found.sort()
    return [f"{name} ({net:.1f})" for net, name in found[:n]]


def _name(dim: str, arm: str) -> str:
    return {"touchstone": f"the touchstone {arm}", "spark": f'the spark "{arm}"',
            "mode": f"the {arm} mode", "tempo": f"a {arm} tempo"}.get(dim, arm)


def brief(stance: str, history: list[dict] | None = None) -> str:
    """The stance for the Director's brief."""
    history = tracks() if history is None else history
    if stance == "explore":
        covered = []
        for dim in ("mode", "touchstone"):
            most = sorted(arm_stats(dim, history).items(), key=lambda kv: -kv[1]["plays"])[:3]
            if most:
                covered.append(f"{dim}s: " + ", ".join(a for a, _ in most))
        return ("**Explore.** This track goes where the stream hasn't been. The key, tempo, spark and "
                "touchstone below were chosen because they've been tried least: take them. At every "
                "choice take the bold option: the less expected lead voice, a form unlike the recent ones, "
                "a harmony or texture you haven't used, the surprise if one is allowed. A brave track that "
                "half-works teaches more than a safe one; but it must still be music: a clear hook, a "
                "clean mix."
                + (f" Most played so far ({'; '.join(covered)}): stay away from them." if covered else ""))
    good, bad = evidence(history), misses(history)
    if not good:
        return ("**Exploit.** Nothing has earned clear thumbs yet, so play to your strengths: a strong, "
                "clear, well-made track in the style, its hook the centre of it."
                + (f" These drew 👎: {'; '.join(bad)}." if bad else ""))
    return ("**Exploit.** Build on what has worked for the host: " + "; ".join(good) + ". The suggestions "
            "below lean that way. Keep the quality, never the notes: a new hook and progression with the "
            "same kind of appeal." + (f" These drew 👎, leave them: {'; '.join(bad)}." if bad else ""))
