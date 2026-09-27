"""
The Director: makes musical decisions as validated JSON.

It asks an LLM when one is configured and falls back to the deterministic
planner/arranger whenever the LLM is off, slow, or returns something
invalid. The music never waits on, or breaks because of, the LLM.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from collections import deque

import config

from . import feedback, llm, runtime
from .narrator import clean_narration
from . import presets as P
from .melody import STEPS_PER_CYCLE
from .plan import (STYLES, Arrangement, Piece, Section, arrange, describe_options, plan_piece,
                   validate_arrangement, validate_piece)
from .theory import Key, progressions_for

PROMPTS_DIR = os.path.join(config.REPO_DIR, "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


# The Director's calls, as the agents they are: (name, what it is asked).
_AGENTS = {
    "piece": ("Director", "plan the next piece"),
    "section": ("Arranger", "arrange the next section"),
    "free": ("Composer", "write a counter-melody"),
}


class Director:
    def __init__(self, model_id: str | None, rng: random.Random, log=print,
                 theme: str = "chill music to relax, study and sleep to",
                 target_minutes: float = 12.0, max_layers: int = config.MAX_ACTIVE_LAYERS,
                 style: str | None = None, show_llm: bool = False):
        self.model_id = model_id
        # Also print the model's full raw reply (--show-llm).
        self.show_llm = show_llm
        self.rng = rng
        self.log = log
        self.theme = theme
        self.target_minutes = target_minutes
        self.max_layers = max_layers
        self.style = style
        self.recent_progressions: deque[str] = deque(maxlen=4)
        self.consecutive_failures = 0
        # After this many failures in a row, stop asking the LLM for a while.
        self.failure_backoff = 3
        self._skip_calls = 0

    # -- helpers -------------------------------------------------------------

    def _llm_available(self) -> bool:
        if not self.model_id:
            return False
        if self._skip_calls > 0:
            self._skip_calls -= 1
            return False
        return True

    def _ask(self, kind: str, system: str, user: str) -> dict | None:
        started = time.monotonic()
        try:
            agent, asks = _AGENTS.get(kind, ("Director", kind))
            reply = llm.converse(agent, self.model_id, system, user, log=self.log, asks=asks,
                                 show_full=self.show_llm)
            data = llm.extract_json(reply)
            self.consecutive_failures = 0
            data["_seconds"] = time.monotonic() - started
            return data
        except llm.LLMError as e:
            self.consecutive_failures += 1
            self.log(f"[Director] LLM unavailable ({e}); using deterministic fallback")
            if self.consecutive_failures >= self.failure_backoff:
                self._skip_calls = 5
                self.consecutive_failures = 0
                self.log("[Director] too many LLM failures; skipping the next 5 LLM calls")
            return None

    def _think(self, what: str, decision: str, data: dict) -> None:
        """Show what the model decided and, if it said, why."""
        reason = data.get("reason")
        reason = " ".join(str(reason).split())[:400] if reason else ""
        line = f"🧠 {what}: {decision}"
        if reason:
            line += f'\n   "{reason}"'
        line += f"  ({data.get('_seconds', 0):.1f}s)"
        self.log(line)
        runtime.append_thought(line.replace("\n   ", "  | "))
        model = llm.resolve(self.model_id)[1] if self.model_id else "built-in"
        runtime.ui_event("director", what=what, decision=decision, reason=reason, model=model,
                         seconds=round(float(data.get("_seconds", 0) or 0), 1))

    def _planner_note(self, piece: Piece, index: int, arrangement: Arrangement) -> None:
        section = piece.sections[index]
        layers = ", ".join(f"{r}={p}" for r, p in arrangement.layers.items() if p)
        line = (f"🎛  Next section ({section.name} {index + 1}/{len(piece.sections)}, energy {section.energy}) "
                f"by the built-in planner: {layers}")
        self.log(line)
        runtime.append_thought(line)

    # -- decisions -------------------------------------------------------------

    def next_piece(self, previous: Piece | None) -> Piece:
        avoid = feedback.disliked()
        fallback = plan_piece(self.rng, previous, style=self.style,
                              avoid_progressions=set(self.recent_progressions),
                              disliked=avoid["progressions"],
                              target_minutes=self.target_minutes)
        piece = fallback
        if self._llm_available():
            prompt = _load_prompt("director_piece.md").format(
                target_minutes=int(self.target_minutes),
                options=json.dumps(describe_options(), indent=1),
                previous=json.dumps({k: v for k, v in previous.to_dict().items() if k != "sections"})
                if previous else "none (this is the first piece)",
                previous_shape=" → ".join(f"{s.name} {s.bars}" for s in previous.sections)
                if previous and previous.sections else "none",
                recent_progressions=", ".join(self.recent_progressions) or "none",
                avoid=", ".join(sorted(avoid["progressions"] | avoid["presets"])) or "nothing",
                theme=self.theme,
            )
            data = self._ask("piece", "You are a concise music director. Reply with JSON only.", prompt)
            if data:
                piece, notes = validate_piece(data, fallback, self.target_minutes)
                if self.style and piece.style != self.style:
                    notes.append(f"style locked to {self.style}")
                    piece.style = fallback.style
                    piece.cycles_per_chord = fallback.cycles_per_chord
                # Tempo and key must suit the style that actually plays (the
                # LLM may have planned them for another style).
                lo, hi = STYLES[piece.style]["bpm"]
                if not lo <= piece.bpm <= hi:
                    notes.append(f"{piece.bpm:g} BPM is outside {piece.style}'s {lo}-{hi}; using {fallback.bpm:g}")
                    piece.bpm = fallback.bpm
                if Key.parse(piece.key).mode not in STYLES[piece.style]["modes"]:
                    notes.append(f"{piece.key} doesn't suit {piece.style}; using {fallback.key}")
                    piece.key, piece.progression = fallback.key, fallback.progression
                if piece.progression in avoid["progressions"]:
                    notes.append(f"progression {piece.progression} is disliked; using fallback")
                    piece.progression = fallback.progression
                    if Key.parse(piece.key).mode != Key.parse(fallback.key).mode:
                        piece.key = fallback.key
                if piece.progression in self.recent_progressions:
                    # Don't let every track sit on the same chords.
                    mode = Key.parse(piece.key).mode
                    fresh = [p.name for p in progressions_for(mode) if p.name not in self.recent_progressions]
                    if fresh:
                        choice = fallback.progression if fallback.progression in fresh else self.rng.choice(fresh)
                        notes.append(f"progression {piece.progression} was used in the last "
                                     f"{len(self.recent_progressions)} tracks; using {choice}")
                        piece.progression = choice
                _distinct_sections(piece, self.rng)
                if notes:
                    self.log(f"[Director] corrected LLM piece plan: {'; '.join(notes)}")
                piece.narration = clean_narration(data.get("narration"))
                self._think("Next piece", f"'{piece.title}', {piece.style}, {piece.key}, {piece.bpm:.0f} BPM, "
                                          f"{piece.progression}, {piece.mood}", data)
        self.recent_progressions.append(piece.progression)
        if piece is fallback:
            runtime.ui_event("director", what="Next piece", model="built-in",
                             decision=f"'{piece.title}', {piece.style}, {piece.key}, {piece.bpm:.0f} BPM, "
                                      f"{piece.progression}, {piece.mood}",
                             reason="planned by the built-in planner", seconds=0)
        return piece

    def arrange_section(self, piece: Piece, index: int, current: Arrangement | None) -> Arrangement:
        section = piece.sections[index]
        fallback = arrange(piece, section, self.rng, self.max_layers)
        avoid = feedback.disliked()
        # Swap disliked presets in the fallback for another preset in that role.
        for role, name in list(fallback.layers.items()):
            if name in avoid["presets"]:
                alternatives = [n for n in P.names(role) if n not in avoid["presets"]]
                fallback.layers[role] = alternatives[0] if alternatives else name
        if not self._llm_available():
            self._planner_note(piece, index, fallback)
            return fallback
        prompt = _load_prompt("director_section.md").format(
            piece=json.dumps({k: v for k, v in piece.to_dict().items() if k != "sections"}),
            section=section.name, energy=section.energy, bars=section.bars,
            index=index + 1, count=len(piece.sections),
            current=json.dumps(current.layers if current else {}),
            presets=json.dumps({r: P.names(r) for r in P.ENERGY_ORDER}),
            max_layers=self.max_layers,
            avoid=", ".join(sorted(avoid["presets"])) or "nothing",
            theme=self.theme,
        )
        data = self._ask("section", "You are a concise music arranger. Reply with JSON only.", prompt)
        if not data:
            self._planner_note(piece, index, fallback)
            return fallback
        arrangement, notes = validate_arrangement(data, piece, fallback, self.max_layers)
        for role, name in arrangement.layers.items():
            if name in avoid["presets"]:
                arrangement.layers[role] = fallback.layers.get(role)
                notes.append(f"{name} is disliked")
        if notes:
            self.log(f"[Director] corrected LLM arrangement: {'; '.join(notes)}")
        arrangement.variation_seed = fallback.variation_seed
        arrangement.narration = clean_narration(data.get("narration"))
        layers = ", ".join(f"{r}={p}" for r, p in arrangement.layers.items() if p) or "silence"
        character = f", melody {arrangement.melody_character}" if arrangement.melody_character else ""
        self._think(f"Next section ({section.name} {index + 1}/{len(piece.sections)}, energy {section.energy})",
                    layers + character, data)
        return arrangement

    def compose_free_layer(self, piece: Piece, section: Section) -> list[str] | None:
        """Ask the LLM for a counter-melody as pentatonic degrees; map to notes."""
        if not self._llm_available():
            return None
        cycles = piece.period
        prompt = _load_prompt("free_layer.md").format(
            key=piece.key, progression=piece.progression, cycles=cycles, mood=piece.mood,
            section=section.name, energy=section.energy,
        )
        data = self._ask("free", "You are a concise composer. Reply with JSON only.", prompt)
        if not data:
            return None
        try:
            result = degrees_to_cycles(data.get("cycles"), Key.parse(piece.key), cycles,
                                       low=P.PRESETS["free"]["free_glass"].register[0])
            self._think(f"Counter-melody ({section.name})", " | ".join(data["cycles"]), data)
            return result
        except ValueError as e:
            self.log(f"[Director] free layer rejected: {e}")
            return None


_TOKEN_RE = re.compile(r"^(~|_|-?\d)$")


def degrees_to_cycles(raw, key: Key, cycles: int, low: int = 0) -> list[str]:
    """Validate LLM degree strings and convert them to note-number cycles."""
    if not isinstance(raw, list) or len(raw) != cycles:
        raise ValueError(f"expected a list of {cycles} cycle strings")
    safe = sorted(key.safe_melody_pcs(), key=lambda pc: (pc - key.root) % 12)
    tonic = next(n for n in range(low, low + 12) if n % 12 == key.root)

    def degree_note(d: int) -> int:
        octs, idx = divmod(d, len(safe))
        interval = (safe[idx] - key.root) % 12
        return tonic + interval + 12 * octs

    out = []
    for text in raw:
        tokens = str(text).split()
        if len(tokens) != STEPS_PER_CYCLE:
            raise ValueError(f"cycle {text!r} must have {STEPS_PER_CYCLE} tokens")
        if not all(_TOKEN_RE.match(t) for t in tokens):
            raise ValueError(f"cycle {text!r} has invalid tokens")
        onsets = sum(1 for t in tokens if t not in ("~", "_"))
        if onsets > 4:
            raise ValueError(f"cycle {text!r} is too busy")
        rendered = []
        for t in tokens:
            if t == "~":
                rendered.append("~")
            elif t == "_":
                rendered.append("_" if rendered and rendered[-1] != "~" else "~")
            else:
                d = int(t)
                if not -3 <= d <= 9:
                    raise ValueError(f"degree {d} out of range")
                rendered.append(str(degree_note(d)))
        out.append("[" + " ".join(rendered) + "]")
    return out


def _distinct_sections(piece: Piece, rng) -> None:
    """The breakdown and the chorus (the peak) get progressions of their own,
    different from the main one, so the harmony moves with the arc."""
    mode = Key.parse(piece.key).mode
    others = [p.name for p in progressions_for(mode) if p.name != piece.progression]
    rng.shuffle(others)
    for part in ("breakdown", "chorus"):
        if piece.section_progressions.get(part) in (None, piece.progression) and others:
            piece.section_progressions[part] = others.pop()
