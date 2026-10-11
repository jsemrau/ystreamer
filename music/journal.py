"""
The art director's journal (runtime/director_journal.jsonl): what she made
and how it went, kept across tracks and restarts, so each track begins from
memory and not from nothing.

Three kinds of entry, each with the track's title and the time:
- "track": her plan as the track begins: her reasoning, concept, the
  techniques she used, the stance, key, tempo, subgenre, sounds, form;
- "kit": the drum kit and bass sound the performer took for it;
- "develop": a decision she made while it played (music/director.py,
  develop_section): which section, what she heard, what she changed.
How it went is read back from what the host did while it played: the thumbs
(ratings.jsonl) and requests (requests.jsonl) between its start and the
next track's.
"""

from __future__ import annotations

import json
import os
import time

import config

NAME = "director_journal.jsonl"


def _path(name: str = NAME) -> str:
    return os.path.join(config.RUNTIME_DIR, name)


def write(kind: str, title: str, **fields) -> None:
    rec = {"type": kind, "t": time.time(), "title": title, **fields}
    try:
        os.makedirs(config.RUNTIME_DIR, exist_ok=True)
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


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


def tracks(n: int = 8) -> list[dict]:
    """The last n tracks: each its plan, kit, decisions and how the host took it."""
    entries = _read(NAME)
    starts = [e for e in entries if e.get("type") == "track"]
    ratings, requests = _read("ratings.jsonl"), _read("requests.jsonl")
    out = []
    for i, start in enumerate(starts):
        t0 = float(start.get("t", 0))
        t1 = float(starts[i + 1].get("t", 0)) if i + 1 < len(starts) else float("inf")
        title = start.get("title", "")
        mine = [e for e in entries if e.get("title") == title and t0 <= float(e.get("t", 0)) < t1]
        thumbs = [int(r.get("rating", 0) or 0) for r in ratings if t0 <= float(r.get("t", 0) or 0) < t1]
        out.append({**start,
                    "kit": next((e for e in mine if e.get("type") == "kit"), {}),
                    "decisions": [e for e in mine if e.get("type") == "develop"],
                    "up": sum(1 for v in thumbs if v > 0), "down": sum(1 for v in thumbs if v < 0),
                    "asked": [str(r.get("text", "")) for r in requests
                              if t0 <= float(r.get("t", 0) or 0) < t1 and r.get("text")][:3]})
    return out[-n:]


def _line(tr: dict) -> str:
    sounds = tr.get("sounds") or {}
    kit = tr.get("kit") or {}
    frame = ", ".join(x for x in (tr.get("key"), f"{tr['bpm']:g} BPM" if tr.get("bpm") else "",
                                  tr.get("subgenre")) if x)
    voices = ", ".join(f"{r} {s.removeprefix('ys_')}" for r, s in sounds.items() if r in ("lead", "arp", "chords"))
    extra = ", ".join(x for x in (f"kit {kit['drums'].removeprefix('kit_')}" if kit.get("drums") else "",
                                  f"bass {kit['bass'].removeprefix('ys_')}" if kit.get("bass") else "",
                                  "techniques " + "+".join(tr["techniques"]) if tr.get("techniques") else "",
                                  tr.get("stance", "")) if x)
    reaction = (f"{tr['up']} 👍 {tr['down']} 👎" if tr["up"] or tr["down"] else "no thumbs")
    asked = ("; asked: " + " | ".join(f'"{a}"' for a in tr["asked"])) if tr["asked"] else ""
    developed = ""
    if tr["decisions"]:
        developed = "; developed: " + " / ".join(f"{d.get('phase')}: {str(d.get('decision', ''))[:70]}"
                                                 for d in tr["decisions"][-2:])
    concept = str(tr.get("concept") or "")[:90]
    return (f"- '{tr.get('title')}' ({frame}; {voices}{'; ' + extra if extra else ''}): {concept} "
            f"-> {reaction}{asked}{developed}")


def digest(n: int = 6) -> str:
    """For the Director's brief: the last tracks and how each went, and the
    best and worst received of all remembered; "" with nothing yet."""
    remembered = tracks(40)
    if not remembered:
        return ""
    lines = ["Your last tracks, oldest first, and how the host took them:"]
    lines += [_line(tr) for tr in remembered[-n:]]
    rated = [tr for tr in remembered if tr["up"] or tr["down"]]
    if rated:
        best = max(rated, key=lambda tr: tr["up"] - tr["down"])
        worst = min(rated, key=lambda tr: tr["up"] - tr["down"])
        if best["up"] - best["down"] > 0:
            lines.append(f"Best received of all you remember: '{best.get('title')}' ({best.get('concept', '')[:80]}; "
                         f"techniques {'+'.join(best.get('techniques') or []) or 'none named'}).")
        if worst is not best and worst["up"] - worst["down"] < 0:
            lines.append(f"Least liked: '{worst.get('title')}' ({worst.get('concept', '')[:80]}).")
    return "\n".join(lines)
