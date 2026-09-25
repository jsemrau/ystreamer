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
from collections import deque

import config

from . import feedback, llm
from . import presets as P
from .melody import STEPS_PER_CYCLE
from .plan import (STYLES, Arrangement, Piece, Section, arrange, describe_options, plan_piece,
                   validate_arrangement, validate_piece)
from .theory import PROGRESSIONS_BY_NAME, Key

PROMPTS_DIR = os.path.join(config.REPO_DIR, "prompts")
# Jev picks between options instead of writing plans: how many valid piece
# plans the deterministic planner proposes for it to choose from.
JEV_PIECE_CANDIDATES = 6


def _load_prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


class Director:
    def __init__(self, model_id: str | None, rng: random.Random, log=print,
                 theme: str = "chill music to relax, study and sleep to",
                 target_minutes: float = 12.0, max_layers: int = config.MAX_ACTIVE_LAYERS,
                 style: str | None = None):
        self.model_id = model_id
        self.uses_jev = bool(model_id) and llm.resolve(model_id)[0] == "jevtypesafe"
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

    def _ask(self, system: str, user: str) -> dict | None:
        return self._guarded(lambda: llm.extract_json(llm.complete(self.model_id, system, user)))

    def _decide(self, state, questions: dict) -> dict | None:
        return self._guarded(lambda: llm.decide(self.model_id, state, questions))

    def _guarded(self, call) -> dict | None:
        try:
            data = call()
            self.consecutive_failures = 0
            return data
        except llm.LLMError as e:
            self.consecutive_failures += 1
            self.log(f"[Director] LLM unavailable ({e}); using deterministic fallback")
            if self.consecutive_failures >= self.failure_backoff:
                self._skip_calls = 5
                self.consecutive_failures = 0
                self.log("[Director] too many LLM failures; skipping the next 5 LLM calls")
            return None

    # -- decisions -------------------------------------------------------------

    def next_piece(self, previous: Piece | None) -> Piece:
        avoid = feedback.disliked()
        fallback = plan_piece(self.rng, previous, style=self.style,
                              avoid_progressions=set(self.recent_progressions) | avoid["progressions"],
                              target_minutes=self.target_minutes)
        piece = fallback
        if self._llm_available():
            if self.uses_jev:
                data = self._jev_piece(previous, fallback, avoid)
            else:
                prompt = _load_prompt("director_piece.md").format(
                    target_minutes=int(self.target_minutes),
                    options=json.dumps(describe_options(), indent=1),
                    previous=json.dumps({k: v for k, v in previous.to_dict().items() if k != "sections"})
                    if previous else "none (this is the first piece)",
                    recent_progressions=", ".join(self.recent_progressions) or "none",
                    avoid=", ".join(sorted(avoid["progressions"] | avoid["presets"])) or "nothing",
                    theme=self.theme,
                )
                data = self._ask("You are a concise music director. Reply with JSON only.", prompt)
            if data:
                piece, notes = validate_piece(data, fallback, self.target_minutes)
                if self.style and piece.style != self.style:
                    notes.append(f"style locked to {self.style}")
                    piece.style = fallback.style
                    piece.cycles_per_chord = fallback.cycles_per_chord
                if piece.progression in avoid["progressions"]:
                    notes.append(f"progression {piece.progression} is disliked; using fallback")
                    piece.progression = fallback.progression
                    if Key.parse(piece.key).mode != Key.parse(fallback.key).mode:
                        piece.key = fallback.key
                if notes:
                    self.log(f"[Director] corrected LLM piece plan: {'; '.join(notes)}")
        self.recent_progressions.append(piece.progression)
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
            return fallback
        if self.uses_jev:
            data = self._jev_arrangement(piece, index, current, avoid)
        else:
            prompt = _load_prompt("director_section.md").format(
                piece=json.dumps({k: v for k, v in piece.to_dict().items() if k != "sections"}),
                section=section.name, energy=section.energy, bars=section.bars,
                index=index + 1, count=len(piece.sections),
                current=json.dumps(current.layers if current else {}),
                presets=json.dumps({r: P.names(r) for r in P.ENERGY_ORDER}),
                max_layers=self.max_layers,
                avoid=", ".join(sorted(avoid["presets"])) or "nothing",
            )
            data = self._ask("You are a concise music arranger. Reply with JSON only.", prompt)
        if not data:
            return fallback
        arrangement, notes = validate_arrangement(data, piece, fallback, self.max_layers)
        for role, name in arrangement.layers.items():
            if name in avoid["presets"]:
                arrangement.layers[role] = fallback.layers.get(role)
                notes.append(f"{name} is disliked")
        if notes:
            self.log(f"[Director] corrected LLM arrangement: {'; '.join(notes)}")
        arrangement.variation_seed = fallback.variation_seed
        return arrangement

    def compose_free_layer(self, piece: Piece, section: Section) -> list[str] | None:
        """Ask the LLM for a counter-melody as pentatonic degrees; map to notes."""
        if self.uses_jev or not self._llm_available():  # Jev picks options; it can't write melodies
            return None
        cycles = piece.period
        prompt = _load_prompt("free_layer.md").format(
            key=piece.key, progression=piece.progression, cycles=cycles, mood=piece.mood,
            section=section.name, energy=section.energy,
        )
        data = self._ask("You are a concise composer. Reply with JSON only.", prompt)
        if not data:
            return None
        try:
            return degrees_to_cycles(data.get("cycles"), Key.parse(piece.key), cycles,
                                     low=P.PRESETS["free"]["free_glass"].register[0])
        except ValueError as e:
            self.log(f"[Director] free layer rejected: {e}")
            return None

    # -- Jev (typed decisions) ----------------------------------------------------

    def _jev_piece(self, previous: Piece | None, fallback: Piece, avoid: dict) -> dict | None:
        """Jev can't write a plan, so it picks one of several valid candidate plans."""
        candidates = {"piece_1": fallback}
        seen = {(fallback.style, fallback.key, fallback.progression)}
        for _ in range(4 * JEV_PIECE_CANDIDATES):
            if len(candidates) == JEV_PIECE_CANDIDATES:
                break
            candidate = plan_piece(self.rng, previous, style=self.style,
                                   avoid_progressions=set(self.recent_progressions) | avoid["progressions"],
                                   target_minutes=self.target_minutes)
            if (candidate.style, candidate.key, candidate.progression) not in seen:
                seen.add((candidate.style, candidate.key, candidate.progression))
                candidates[f"piece_{len(candidates) + 1}"] = candidate
        options = {name: _describe_piece(c) for name, c in candidates.items()}
        state = {
            "stream_theme": self.theme,
            "previous_piece": _describe_piece(previous) if previous else "none (this is the first piece)",
            "recently_used_progressions": list(self.recent_progressions) or "none",
            "listeners_dislike": sorted(avoid["progressions"] | avoid["presets"]) or "nothing",
        }
        answers = self._decide(state, {"piece": {
            "type": "choice", "criteria": options,
            "instructions": "Which piece should play next on this endless chill-music live stream? It should "
                            "feel related to, but different from, the previous piece and fit the stream theme.",
        }})
        if not answers:
            return None
        pick = answers["piece"]
        self.log(f"[Director] Jev picked {options[pick['choice']]} (confidence {pick.get('confidence', 0):.2f})")
        return candidates[pick["choice"]].to_dict()

    def _jev_arrangement(self, piece: Piece, index: int, current: Arrangement | None, avoid: dict) -> dict | None:
        """One batched Jev call: a preset (or silence) for every role, plus the melody character."""
        section = piece.sections[index]
        questions = {}
        for role in P.ENERGY_ORDER:
            criteria = {name: preset.description for name, preset in P.PRESETS[role].items()
                        if name not in avoid["presets"]}
            criteria["silent"] = f"no {role} in this section"
            questions[role] = {"type": "choice", "criteria": criteria,
                               "instructions": f"Which {role} preset should play in the next section? "
                                               f"'silent' leaves the {role} out."}
        questions["melody_character"] = {
            "type": "choice", "instructions": "How should the melody move in the next section?",
            "criteria": {"sparse": "few notes, lots of space", "flowing": "smooth, connected phrases",
                         "lively": "busier and more rhythmic"},
        }
        state = {
            "piece": _describe_piece(piece),
            "next_section": f"{section.name}, energy {section.energy} on a 0-1 scale, {section.bars} bars, "
                            f"section {index + 1} of {len(piece.sections)}",
            "currently_playing": {r: p for r, p in (current.layers if current else {}).items() if p} or "nothing",
            f"presets_that_suit_{piece.style}": STYLES[piece.style]["presets"],
            "guidelines": [
                f"Energy 0.1-0.3: 1-3 layers (pad, texture, maybe bass); 0.4-0.6: 3-5 layers; "
                f"0.7-0.9: 5-{self.max_layers} layers. Never more than {self.max_layers}.",
                "A breakdown removes layers suddenly (contrast); an outro thins out.",
                "Prefer continuity: change at most 2 presets from what is playing, "
                "unless the section type changes dramatically.",
            ],
        }
        answers = self._decide(state, questions)
        if not answers:
            return None
        return {"layers": {role: None if answers[role]["choice"] == "silent" else answers[role]["choice"]
                           for role in P.ENERGY_ORDER},
                "melody_character": answers["melody_character"]["choice"]}


def _describe_piece(piece: Piece) -> str:
    progression = PROGRESSIONS_BY_NAME[piece.progression]
    return (f"{piece.style}, {piece.key}, {piece.bpm:.0f} BPM, progression {piece.progression} "
            f"({progression.mood}), {piece.mood} mood")


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
