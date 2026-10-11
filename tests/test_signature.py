"""Each track recognisable: the theme's subgenre is home, not a cage (an
exploring track may roam), the hook's voice is a signature no recent track
had, and plucked strings play in tune."""
import os
import random
import re

import config
from music.director import Director
from music.plan import plan_piece

THEME = "trance house London, Ethereal, Orbital"     # asks for ambient house
AVOID = {"progressions": set(), "presets": set(), "sounds": set()}


def _director(stance):
    d = Director(None, random.Random(0), style="trance", theme=THEME, log=lambda *_: None, target_minutes=5)
    d._stance = stance
    return d


def _fallback():
    return plan_piece(random.Random(1), style="trance", theme=THEME, target_minutes=5)


def test_exploiting_stays_home_exploring_may_roam():
    piece, notes = _director("exploit").review_piece({"subgenre": "psy"}, _fallback(), AVOID)
    assert piece.subgenre == "ambient house" and any("theme asks for" in n for n in notes)
    piece, notes = _director("explore").review_piece({"subgenre": "psy"}, _fallback(), AVOID)
    assert piece.subgenre == "psy" and not any("theme asks for" in n for n in notes)


def test_the_hook_voice_may_come_from_outside_the_subgenre():
    data = {"sounds": {"arp": "ys_nylon"}, "hook": {"role": "arp", "kind": "cycle", "pattern": "0 2 4 7"}}
    piece, _ = _director("exploit").review_piece(data, _fallback(), AVOID)
    if piece.hook.get("role") == "arp":
        assert piece.sounds["arp"] == "ys_nylon"          # not ambient house's list, kept: the signature


def test_no_signature_twice_in_a_row():
    d = _director("exploit")
    d.recent_signatures.extend(["ys_glass", "ys_pluck", "ys_fmlead"])
    piece, _ = d.review_piece({"sounds": {"lead": "ys_glass", "arp": "ys_pluck"}}, _fallback(), AVOID)
    role = piece.hook.get("role") or "lead"
    assert piece.sounds[role] not in d.recent_signatures


def test_exploit_suggests_only_the_home_modes(monkeypatch):
    monkeypatch.setattr(config, "EXPLORE", 0.0)
    d = _director("")
    modes = {d._choose_arms("trance", [])[0] for _ in range(30)}
    assert modes <= {"major", "lydian"} and d._stance == "exploit"


def test_the_brief_names_home_and_the_signature(monkeypatch):
    monkeypatch.setattr(config, "EXPLORE", 1.0)
    d = Director(None, random.Random(0), style="trance", theme=THEME, log=lambda *_: None, target_minutes=5)
    d.recent_signatures.append("ys_glass")
    prompt = d.piece_prompt(None, _fallback(), AVOID)
    assert "Home subgenre: ambient house" in prompt and "psy" in prompt
    assert "**Signature voice.**" in prompt and "(ys_glass)" in prompt
    monkeypatch.setattr(config, "EXPLORE", 0.0)
    prompt = d.piece_prompt(None, _fallback(), AVOID)
    assert "Subgenre: ambient house (the theme asks for it" in prompt


def test_every_plucked_string_is_tuned():
    with open(os.path.join(config.REPO_DIR, "tidal", "synthdefs.scd"), encoding="utf-8") as f:
        defs = f.read()
    plucks = re.findall(r"Pluck\.ar\(exc, Impulse\.ar\(0\), 1 / 30, ([^,]+(?:\([^)]*\))?),", defs)
    assert plucks and all(p.startswith("~ysKS.(") for p in plucks), plucks
