"""
The host's taste, remembered across tracks and restarts: the sounds they
liked or asked for, the ones they pushed away, and what they keep asking
the music to be ("darker", "more space", "long drawn out strings").

Nothing new is recorded for it: the thumbs (ratings.jsonl) and requests
(requests.jsonl) are matched to what was playing at that moment, which
history.jsonl keeps with every edit. A thumb on a line rates its sound; a
thumb on the whole track rates its melodic sounds by half. A request that
names a sound ("remove the flute", "the piano is terrible", "remove d5",
"strings would be nice") rates that sound, the one playing if it is, else
the catalogue's.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import time

import config

# Strong enough to keep a sound off the Director's list.
DISLIKED = -2
_NEGATIVE_RE = re.compile(r"\b(remove|stop|drop|mute|kill|no more|terrible|awful|annoying|bad|hate|crap|ugly|"
                          r"shrill|shrieky|harsh|boring|not good|too much)\b", re.I)
_POSITIVE_RE = re.compile(r"\b(love|nice|great|like|more|add|bring|beautiful|good|want)\b", re.I)
_CHANNEL_RE = re.compile(r"\bd(\d{1,2})\b", re.I)
_ROLE_WORDS = {"lead": "lead", "melody": "lead", "arp": "arp", "chords": "chords", "keys": "keys", "acid": "acid",
               "pad": "texture", "texture": "texture", "bass": "bass", "vocal": "vocal"}
# Words in the sounds' descriptions that say nothing about which sound it is.
_STOP = {"a", "an", "the", "and", "of", "on", "in", "into", "with", "lead", "arp", "arpeggio", "melody", "chords",
         "sound", "soft", "bright", "warm", "real", "style", "stabs", "bed", "far", "background", "behind", "long",
         "short", "plucked", "trance", "synth", "wave", "line", "voice", "voices"}
MELODIC = ("lead", "arp", "keys", "acid", "chords", "texture", "vocal")


def _path(name: str) -> str:
    return os.path.join(config.RUNTIME_DIR, name)


def _records(name: str, since: float) -> list[dict]:
    out = []
    try:
        with open(_path(name), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if float(r.get("t", r.get("ts", 0)) or 0) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def _snapshots(since: float) -> tuple[list[float], list[dict[str, str]]]:
    """(times, role -> synth of each line playing) after every edit since `since`."""
    from .livecode import LineSpec, line_synth

    times, playing = [], []
    try:
        with open(_path("history.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ts = float(r.get("ts", 0) or 0)
                lines = (r.get("program") or {}).get("lines")
                if ts < since or not isinstance(lines, dict):
                    continue
                now = {}
                for role, spec in lines.items():
                    if isinstance(spec, dict) and not spec.get("muted") and not spec.get("drums"):
                        synth = line_synth(LineSpec.from_dict(spec))
                        if synth:
                            now[role] = synth
                times.append(ts)
                playing.append(now)
    except OSError:
        pass
    return times, playing


def _aliases() -> dict[str, set[str]]:
    """synth -> the words a host would call it by ("flute", "piano", "strings")."""
    from .blocks import LIVE_SOUNDS

    out: dict[str, set[str]] = {}
    for palette in LIVE_SOUNDS.values():
        for synth, about in palette.items():
            head = re.split(r"[:(]", about)[0].lower()
            words = {w for w in re.findall(r"[a-z]+", head) if w not in _STOP and len(w) > 2}
            words.add(synth.removeprefix("ys_"))
            out.setdefault(synth, set()).update(words)
    return out


def _named(text: str, playing: dict[str, str], aliases: dict[str, set[str]]) -> tuple[list[str], bool]:
    """The sounds a request names: a channel (d5), a part (the lead) or a sound's
    name, the one playing first, else from the catalogue; and whether they
    were playing (a complaint about a piano that wasn't on is a guess)."""
    from .livecode import ROLE_CHANNELS

    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    channels = {v: k for k, v in ROLE_CHANNELS.items()}
    found = [playing[channels[int(n)]] for n in _CHANNEL_RE.findall(text)
             if int(n) in channels and channels[int(n)] in playing]
    found += [playing[role] for word, role in _ROLE_WORDS.items() if word in words and role in playing]
    words |= {w.removesuffix("s") for w in words}
    named = {s for s, names in aliases.items() if names & words}
    found += [s for s in playing.values() if s in named]
    if found:
        return list(dict.fromkeys(found)), True
    return sorted(named), False


def sound_scores(days: float = 30.0, requests_since: float | None = None) -> dict[str, tuple[float, list[str]]]:
    """synth -> (net score, why): the host's thumbs (the last `days`) and
    requests about it (only this session's, from `requests_since`)."""
    since = time.time() - days * 86400
    times, playing = _snapshots(since)

    def at(t: float) -> dict[str, str]:
        i = bisect.bisect_right(times, t) - 1
        return playing[i] if i >= 0 else {}

    scores: dict[str, list] = {}

    def add(synth: str, value: float, why: str) -> None:
        entry = scores.setdefault(synth, [0.0, []])
        entry[0] += value
        if why not in entry[1]:
            entry[1].append(why)

    for r in _records("ratings.jsonl", since):
        value, now = (1 if int(r.get("rating", 0)) > 0 else -1), at(float(r.get("t", 0)))
        thumb = "👍" if value > 0 else "👎"
        if r.get("role"):
            if r["role"] in now:
                add(now[r["role"]], value, f"{thumb} on the {r['role']}")
        else:
            for role in MELODIC:
                if role in now:
                    add(now[role], value / 2, f"{thumb} on the track")
    aliases = _aliases()
    for r in _records("requests.jsonl", since if requests_since is None else requests_since):
        text = str(r.get("text", ""))
        negative, positive = _NEGATIVE_RE.search(text), _POSITIVE_RE.search(text)
        if not (negative or positive):
            continue
        synths, playing_then = _named(text, at(float(r.get("t", 0))), aliases)
        for synth in synths:
            add(synth, 1 if not negative else -1 if playing_then else -0.5, f'"{text}"')
    return {s: (round(v, 1), why) for s, (v, why) in scores.items()}


def disliked_sounds(days: float = 30.0) -> set[str]:
    return {s for s, (v, _) in sound_scores(days).items() if v <= DISLIKED}


def requests(n: int = 10, days: float = 30.0, since: float | None = None) -> list[str]:
    """The host's last requests that say what the music should be (not the
    ones that only name a channel: "remove d5"), from `since` if given."""
    out = []
    for r in _records("requests.jsonl", time.time() - days * 86400 if since is None else since):
        text = " ".join(str(r.get("text", "")).split())
        if text and not re.fullmatch(r"(remove|stop|mute|simplify|in)\s+d\d+.*", text, re.I) and text not in out:
            out.append(text)
    return out[-n:]


def digest(days: float = 30.0, since: float | None = None) -> str:
    """The host's taste for the Director's brief, or "" with nothing to go on:
    their thumbs over `days`, and only this session's requests (from `since`):
    an old wish never steers a new set."""
    scores = sound_scores(days, requests_since=since)
    liked = sorted(((v, s, w) for s, (v, w) in scores.items() if v > 0), reverse=True)[:6]
    disliked = sorted((v, s, w) for s, (v, w) in scores.items() if v < 0)[:8]
    lines = []
    if liked:
        lines.append("- Sounds the host liked: " + "; ".join(f"{s} (+{v:g}: {', '.join(w[:2])})" for v, s, w in liked))
    if disliked:
        lines.append("- Sounds the host pushed away"
                     + f" (at {DISLIKED} or below they are off the list): "
                     + "; ".join(f"{s} ({v:g}: {', '.join(w[:2])})" for v, s, w in disliked))
    asked = requests(since=since)
    if asked:
        lines.append("- What the host has asked for this session (oldest first): " + " | ".join(f'"{t}"' for t in asked)
                     + ". Serve these wishes across the set, not on every track: answer one in about every "
                     "second or third track, each time in a new way, and never with the same sound twice running.")
    return "\n".join(lines)


def reactions_since(since: float, n: int = 3) -> tuple[int, int, list[str]]:
    """The host's thumbs since `since`: (👍 count, 👎 count, the last n, each
    with the part and sound it was on), for the Director's progress."""
    ratings = _records("ratings.jsonl", since)
    if not ratings:
        return 0, 0, []
    times, playing = _snapshots(since - 3600)
    up = sum(1 for r in ratings if int(r.get("rating", 0)) > 0)
    last = []
    for r in ratings[-n:]:
        thumb = "👍" if int(r.get("rating", 0)) > 0 else "👎"
        i = bisect.bisect_right(times, float(r.get("t", 0))) - 1
        now = playing[i] if i >= 0 else {}
        role = r.get("role")
        last.append(f"{thumb} on the {role} ({now[role]})" if role and role in now
                    else f"{thumb} on the {role}" if role else f"{thumb} on the whole track")
    return up, len(ratings) - up, last
