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
from .llm import CACHE_BREAK
from .narrator import clean_narration
from . import presets as P
from .melody import STEPS_PER_CYCLE
from .blocks import hook_kinds
from .plan import (HOOK_SIMILAR, STYLES, Arrangement, Piece, Section, arrange, director_vocabulary, hook_similarity,
                   melody_brief, plan_piece, resolve_surprise_cues, subgenre_from_theme, validate_arrangement,
                   validate_hook, validate_piece)
from .theory import Key, progressions_for

PROMPTS_DIR = os.path.join(config.REPO_DIR, "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


# The Director's calls, as the agents they are: (name, what it is asked).
_AGENTS = {
    "piece": ("Director", "plan the next piece"),
    "hook": ("Director", "write a fresher hook"),
    "section": ("Arranger", "arrange the next section"),
    "free": ("Composer", "write a counter-melody"),
}


# One of these goes into each piece prompt, so tracks don't all start from
# the same habits (an LLM left alone writes A minor and the same four chords).
SPARKS = [
    "a hook built from only two or three different notes, all in the rhythm",
    "start the progression away from the tonic, so it only comes home on the last chord",
    "a hook that climbs across the loop and falls back at the end",
    "a hook with more rests than notes: syncopated, full of space",
    "lean on the mode's most characteristic chord (the II in phrygian, the IV in dorian, the VI in minor)",
    "a breakdown progression darker than the main one, and the brightest chords saved for the peak",
    "a hook that answers itself: a question in its first half, the answer in its second",
    "harmony that barely moves (two chords, repeated) so the hook does all the moving",
    "a hook built around the fifth and the seventh rather than the root",
    "a tempo at the edge of the style's range",
    "a key far from the last track's: a new colour straight away",
    "an odd-length acid or arp cycle (5 or 7 notes) rolling against the 4/4",
]


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
        self.recent_keys: deque[str] = deque(maxlen=4)
        self.recent_hooks: deque[str] = deque(maxlen=8)   # the hook and the lead/keys melodies
        self.recent_surprises: deque[str] = deque(maxlen=4)
        # The last tracks' sounds (part -> synth): no track repeats one of these sets.
        self.recent_sounds: deque[dict] = deque(maxlen=3)
        self.recent_titles: deque[str] = deque(maxlen=12)
        self._since_surprise = config.SURPRISE_EVERY      # the first track may have one
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

    def _ask(self, kind: str, system: str, user: str, cache_ttl: str = "") -> dict | None:
        started = time.monotonic()
        try:
            agent, asks = _AGENTS.get(kind, ("Director", kind))
            reply = llm.converse(agent, self.model_id, system, user, log=self.log, asks=asks,
                                 show_full=self.show_llm, cache_ttl=cache_ttl)
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

    def _suggest_key(self, style: str, previous: Piece | None) -> Key:
        """A random key for the style that wasn't played recently: half the
        time a neighbour of the previous track's key, otherwise any root."""
        modes = STYLES[style]["modes"]
        fresh = [Key(r, m) for r in range(12) for m in modes if Key(r, m).name not in self.recent_keys]
        if previous is not None and self.rng.random() < 0.5:
            near = [k for k in Key.parse(previous.key).neighbours() if k in fresh]
            if near:
                return self.rng.choice(near)
        return self.rng.choice(fresh or [Key(r, m) for r in range(12) for m in modes])

    # -- decisions -------------------------------------------------------------

    PIECE_SYSTEM = "You are a concise music director. Reply with JSON only."

    def surprise_due(self) -> bool:
        return self._since_surprise >= config.SURPRISE_EVERY - 1

    def _surprise_line(self) -> str:
        from .surprises import SURPRISES

        if not self.surprise_due():
            return ("No surprise in this track (the stream keeps them rare): leave `surprise` out and let the "
                    "music flow.")
        recent = ", ".join(self.recent_surprises) or "none"
        menu = "\n".join(f"  - `{n}`: {about} ({when})" for n, (_, about, when) in SURPRISES.items())
        return ("This track may have ONE surprise, a rule-break the listener notices, chosen from the menu "
                f"below to suit the track (or \"none\" if none fits). Played recently, don't repeat: {recent}. "
                "Name it in `surprise` only: it plays itself at its moment, so no cue for it, and only one.\n"
                + menu)

    def choose_surprise(self, piece: Piece, data: dict | None, notes: list[str]) -> None:
        """Keep the Director's surprise if it's allowed; when one is due and the
        Director didn't decide (no model), the built-in planner picks one."""
        from .plan import live_plan
        from .surprises import SURPRISES, problem

        plan = live_plan(piece)
        asked = str((data or {}).get("surprise") or "").strip().lower().replace(" ", "_").replace("-", "_")
        piece.surprise = ""
        if asked and asked != "none":
            why = ("not this track (they're kept rare)" if not self.surprise_due()
                   else "played recently" if asked in self.recent_surprises
                   else problem(asked, piece.style, plan, piece.hook))
            if why:
                notes.append(f"surprise {asked!r} dropped: {why}")
            else:
                piece.surprise = asked
        elif not asked and data is None and self.surprise_due():
            options = [n for n in SURPRISES if n not in self.recent_surprises
                       and not problem(n, piece.style, plan, piece.hook)]
            piece.surprise = self.rng.choice(options) if options else ""
        if piece.surprise:
            self.recent_surprises.append(piece.surprise)
            self._since_surprise = 0
        else:
            self._since_surprise += 1

    def piece_prompt(self, previous: Piece | None, fallback: Piece, avoid: dict) -> str:
        """The prompt asking for the next track and its cues (also used to build
        training data, eval/distill_director.py)."""
        style = self.style if self.style in STYLES else None
        cfg = STYLES[style] if style else None
        return _load_prompt("director_piece.md").format(
            theme=self.theme, target_minutes=round(self.target_minutes),
            style_line=(f"Style: {style} ({cfg['bpm'][0]}-{cfg['bpm'][1]} BPM, modes: {', '.join(cfg['modes'])})."
                        + (f" Subgenre: {sub} (the theme asks for it: every track is one)."
                           if (sub := subgenre_from_theme(style, self.theme)) else "")
                        if cfg else "Choose `style` from `styles` and include it."),
            options=json.dumps(director_vocabulary(style)),
            previous=json.dumps({k: previous.to_dict()[k] for k in
                                 ("title", "style", "subgenre", "key", "bpm", "progression", "mood", "hook")})
            if previous else "none (this is the first track)",
            previous_shape=" → ".join(f"{s.name} {s.bars}" for s in previous.sections)
            if previous and previous.sections else "none",
            recent_keys=", ".join(self.recent_keys) or "none",
            recent_hooks=", ".join(self.recent_hooks) or "none",
            melody_brief=(f"- {brief}\n" if (brief := melody_brief([h.split(": ", 1)[-1] for h in self.recent_hooks]))
                          else ""),
            recent_progressions=", ".join(self.recent_progressions) or "none",
            recent_sounds="; ".join(", ".join(f"{r} {s}" for r, s in sorted(snd.items())) for snd in self.recent_sounds)
            or "none",
            avoid=", ".join(sorted(avoid["progressions"] | avoid["presets"])) or "nothing",
            suggested_key=self._suggest_key(style or fallback.style, previous).name,
            surprise_line=self._surprise_line(),
            spark=self.rng.choice(SPARKS),
            # Above it the brief, the same for every track: Claude caches it.
            cache_break=CACHE_BREAK,
        )

    def review_piece(self, data: dict, fallback: Piece, avoid: dict) -> tuple[Piece, list[str]]:
        """The model's plan made playable: validated, with every correction
        noted (an empty list means it was right as it was)."""
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
            piece.section_progressions = dict(fallback.section_progressions)
        # The subgenre the theme asks for ("Saltwater" -> balearic), at its tempo.
        from .blocks import SUBGENRES as _SUBGENRES

        wanted = subgenre_from_theme(piece.style, self.theme)
        if wanted in _SUBGENRES.get(piece.style, {}) and piece.subgenre != wanted:
            notes.append(f"the theme asks for {wanted} {piece.style}, not {piece.subgenre or 'none'}")
            piece.subgenre = wanted
            lo, hi = _SUBGENRES[piece.style][wanted]["bpm"]
            piece.bpm = float(min(hi, max(lo, piece.bpm)))
        if wanted in _SUBGENRES.get(piece.style, {}):
            # ...and its sounds (Saltwater's lead is the pluck or the nylon guitar, not any lead).
            from .blocks import subgenre_sounds as _subgenre_sounds

            for role, options in _subgenre_sounds(piece.style, wanted).items():
                if piece.sounds.get(role) and piece.sounds[role] not in options:
                    notes.append(f"{role} {piece.sounds[role]} isn't a {wanted} sound; using {options[0]}")
                    piece.sounds[role] = options[0]
        # A subgenre with its own modes (happy hardcore: major).
        from .blocks import subgenre as _subgenre
        from .plan import fit_mode

        modes = _subgenre(piece.style, piece.subgenre).get("modes")
        if modes and Key.parse(piece.key).mode not in modes:
            new = fit_mode(Key.parse(piece.key), modes).name
            notes.append(f"{piece.key} isn't in {piece.subgenre}'s modes ({'/'.join(modes)}); using {new}")
            piece.key = new
            # Its chords as written may not exist in the new mode: the fallback's
            # (planned in the subgenre's modes) or the mode's most common ones.
            mode = Key.parse(new).mode
            if Key.parse(fallback.key).mode == mode:
                piece.progression = fallback.progression
                piece.section_progressions = dict(fallback.section_progressions)
            else:
                common = [p.name for p in progressions_for(mode)]
                piece.progression = common[0]
                piece.section_progressions = {"chorus": common[1 % len(common)], "breakdown": common[2 % len(common)],
                                              "bridge": common[2 % len(common)]}
        if piece.key in self.recent_keys:
            # Same mode, another root: the Roman numerals still fit.
            key = Key.parse(piece.key)
            roots = [r for r in range(12) if Key(r, key.mode).name not in self.recent_keys]
            if roots:
                new = Key(self.rng.choice(roots), key.mode).name
                notes.append(f"{piece.key} was played in the last {len(self.recent_keys)} tracks; using {new}")
                piece.key = new
        if piece.hook and hook_kinds(piece.style).get(piece.hook.get("role")) != piece.hook.get("kind"):
            notes.append(f"hook on {piece.hook.get('role')} doesn't fit {piece.style}")
            piece.hook = {}
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
        # The theme may give a lead or arp another style's voice, never the chords:
        # they play this style's chord rhythm (a slow string swell smears trance stabs).
        from .blocks import subgenre_sounds
        from .plan import STYLE_SOUNDS

        from .blocks import SUBGENRES

        frame = set(STYLE_SOUNDS.get(piece.style, {}).get("chords", [])).union(
            *(subgenre_sounds(piece.style, n).get("chords", []) for n in SUBGENRES.get(piece.style, {})))
        if frame and piece.sounds.get("chords") and piece.sounds["chords"] not in frame:
            new = fallback.sounds.get("chords") if fallback.sounds.get("chords") in frame else sorted(frame)[0]
            notes.append(f"chords {piece.sounds['chords']} isn't one of {piece.style}'s chord sounds; using {new}")
            piece.sounds["chords"] = new
        if piece.sounds and any(piece.sounds == recent for recent in self.recent_sounds):
            # The same set as a recent track: the fallback's (themed, fresh) sound for one part.
            for role in ("lead", "arp", "keys", "chords"):
                if fallback.sounds.get(role) and fallback.sounds[role] != piece.sounds.get(role):
                    notes.append(f"sounds repeat a recent track's; {role} -> {fallback.sounds[role]}")
                    piece.sounds[role] = fallback.sounds[role]
                    break
        _distinct_sections(piece, self.rng)
        piece.narration = clean_narration(data.get("narration"))
        return piece, notes

    def close_hook(self, pattern: str) -> tuple[str, float] | None:
        """The recent tune this one is too close to (and how close), or None."""
        scored = [(h, hook_similarity(pattern, h.split(": ", 1)[-1])) for h in self.recent_hooks]
        best = max(scored, key=lambda x: x[1], default=None)
        return best if best and best[1] >= HOOK_SIMILAR else None

    def _freshen_hook(self, piece: Piece, prompt: str) -> None:
        """A hook that is a recent one again (Claude, shown its last tunes,
        still tends to write their sibling): ask once more, for the hook
        alone, with the plan as it stands; keep the old one if the new one
        isn't fresher or doesn't fit."""
        if not piece.hook:
            return
        close = self.close_hook(piece.hook["pattern"])
        if close is None:
            return
        role, old = piece.hook["role"], piece.hook["pattern"]
        self.log(f"[Director] the hook {old} is {close[1]:.0%} like a recent one ({close[0]}); asking for another")
        ask = (f"{prompt}\n\n## One change to your plan\n\nYou planned '{piece.title}' in {piece.key} on "
               f"{piece.progression}, the hook on the {role}: {old}. It is too close to a tune played recently "
               f"({close[0]}). Keep the rest of the plan; write only a new hook for the {role}, by the same rules, "
               f"that differs from every recent tune in its rhythm, its register and its ending. Reply with JSON "
               f'only: {{"hook": {{"role": "{role}", "pattern": "..."}}, "reason": "what is new about it"}}')
        data = self._ask("hook", self.PIECE_SYSTEM, ask, cache_ttl="1h") if self._llm_available() else None
        hook, why = validate_hook((data or {}).get("hook"), piece.style)
        if not hook or hook["role"] != role:
            self.log(f"[Director] kept the hook: the new one didn't fit ({why or 'another part'})")
            return
        from .surprises import hook_notes

        if piece.surprise == "three_note_hook" and hook_notes(hook["pattern"]) > 3:
            self.log("[Director] kept the hook: the new one has more than three notes (three-note-hook surprise)")
            return
        again = self.close_hook(hook["pattern"])
        if again and again[1] >= close[1]:
            self.log(f"[Director] kept the hook: the new one {hook['pattern']} is no fresher")
            return
        self.log(f"🎣 Fresher hook: {hook['pattern']}")
        piece.hook = hook

    def next_piece(self, previous: Piece | None) -> Piece:
        avoid = feedback.disliked()
        fallback = plan_piece(self.rng, previous, style=self.style,
                              avoid_progressions=set(self.recent_progressions),
                              disliked=avoid["progressions"],
                              target_minutes=self.target_minutes,
                              theme=self.theme, recent_sounds=list(self.recent_sounds))
        piece = fallback
        data = None
        if self._llm_available():
            prompt = self.piece_prompt(previous, fallback, avoid)
            # A track is ~5 minutes: the 5-minute cache would just miss it.
            data = self._ask("piece", self.PIECE_SYSTEM, prompt, cache_ttl="1h")
            if data:
                piece, notes = self.review_piece(data, fallback, avoid)
                self.choose_surprise(piece, data, notes)
                self._freshen_hook(piece, prompt)
                notes.extend(resolve_surprise_cues(piece))
                if notes:
                    self.log(f"[Director] corrected LLM piece plan: {'; '.join(notes)}")
                self._think("Next piece", f"'{piece.title}', {piece.style}, {piece.key}, {piece.bpm:.0f} BPM, "
                                          f"{piece.progression}, {piece.mood}", data)
        if not data:
            self.choose_surprise(piece, None, [])
            # The built-in titles come from a few words: not one of the last few.
            from .plan import _title, theme_moods
            for _ in range(8):
                if piece.title not in self.recent_titles:
                    break
                piece.title = _title(self.rng, theme_moods(self.theme))
        self.recent_progressions.append(piece.progression)
        self.recent_keys.append(piece.key)
        self.recent_titles.append(piece.title)
        if piece.sounds:
            self.recent_sounds.append(dict(piece.sounds))
        if piece.hook:
            self.recent_hooks.append(f"{piece.hook['role']}: {piece.hook['pattern']}")
        # The melodies the lead and keys were given (sketch and cues), so the
        # next tracks write new ones rather than reuse a good one forever.
        for role in ("lead", "keys"):
            tunes = [(piece.parts or {}).get(role, {}).get("pattern")]
            tunes += [c.get("pattern") for cues in (piece.cues or {}).values() for c in cues
                      if c.get("part") == role and c.get("pattern")]
            for tune in dict.fromkeys(t for t in tunes if t):
                entry = f"{role}: {tune}"
                if entry not in self.recent_hooks and not (piece.hook and piece.hook.get("pattern") == tune):
                    self.recent_hooks.append(entry)
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
