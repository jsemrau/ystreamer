"""
The performer: plays a live-coding set by editing the program, one edit at a
time. Each track goes build -> groove -> breakdown -> rebuild -> groove ->
outro, then the next track starts in a related key.

With an LLM, the model chooses each edit (as validated JSON) within that
frame, with a reason shown on screen as a code comment. The built-in
performer makes musically sensible edits whenever the LLM is off, slow, or
answers with something invalid.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import config

from . import llm, runtime
from .blocks import STYLE_SETTINGS, Block, blocks_for, find_block
from .livecode import (ALL_WRAPPERS, BASS_WRAPPERS, CHOICE_SLOTS, DRUM_WRAPPERS, EDIT_OPS, FX_RANGES,
                       FX_NOTES, LOCKED_SLOTS, MELODIC_WRAPPERS, PF_RANGES, ROLE_CHANNELS, ROLE_FX,
                       WRAPPER_NOTES, Edit, EditError,
                       Program, Track, allowed_wrappers, apply_edit)
from .narrator import clean_narration
from .plan import plan_piece

# Which roles come in first, per style (after the opener).
ROLE_ORDER = {
    "lofi": ["beat", "keys", "bass", "lead", "texture", "perc", "acid"],
    "deephouse": ["beat", "bass", "keys", "perc", "lead", "chords", "texture"],
    "trance": ["acid", "beat", "bass", "chords", "lead", "texture"],
    "ambient": ["chords", "texture", "keys", "lead", "bass", "beat"],
    "piano": ["keys", "lead", "bass", "chords", "texture"],
    "downtempo": ["beat", "keys", "bass", "lead", "texture"],
    "darkwave": ["bass", "beat", "chords", "lead", "keys", "texture"],
    "techno": ["beat", "perc", "bass", "acid", "chords", "texture", "lead"],
    "hardcore": ["beat", "perc", "acid", "lead", "chords", "texture"],
}

PHASES = ("build", "groove", "breakdown", "rebuild", "groove2", "outro")


@dataclass
class SetState:
    """Where the performer is in the current track."""
    phase: str = "build"
    edits_in_phase: int = 0
    edits_in_track: int = 0
    tracks_played: int = 0
    target_lines: int = 5
    groove_edits: int = 8
    muted_for_breakdown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "SetState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Performer:
    def __init__(self, rng: random.Random, style: str = "lofi", model_id: str | None = None, log=print,
                 theme: str = "", show_llm: bool = False, edit_bars: int | None = None, director=None,
                 judge: str | None = None):
        self.rng = rng
        # A locked style, or None to rotate between the chill styles per track.
        self.locked_style = style if style in STYLE_SETTINGS else None
        self.style = self.locked_style or "lofi"
        self.model_id = model_id
        self.log = log
        self.theme = theme
        self.show_llm = show_llm
        # Bars between edits: the style's default unless set (--edit-bars).
        self.edit_bars = edit_bars
        # The Director agent plans each track (key, tempo, progression, mood,
        # title); without one, the built-in planner does.
        self.director = director
        # Generator/judge mode: the LLM proposes edits, a decision model
        # ("jevtypesafe") chooses blind among them and built-in candidates.
        self.judge = judge
        self._last_piece = None
        self.track_narration = ""   # the Director's spoken line for the current track
        self.track_mood = ""
        # Called as on_arc(before, after, why) when the arc moves on (the
        # engine narrates these moments).
        self.on_arc = None
        self.settings = self._settings_for(self.style)
        self.state = SetState()
        self._llm_failures = 0
        self._skip_llm = 0
        # Per track: how many edits the LLM made, and why the built-in performer
        # had to step in ("late", "invalid", "error", "resting").
        self.stats: Counter[str] = Counter()
        # Compact prompts (no glossary or example moves) once the model's
        # context window turned out to be too small for the full one.
        self._compact = False
        # The judge's last answer details (probabilities per option, confidence)
        # and, per track, the share of its probability given to the LLM's ideas.
        self.last_judgement: dict = {}
        self.judge_mass: list[float] = []
        # The last edits that were actually played this track, for the prompt.
        self.recent: deque[str] = deque(maxlen=6)

    # ------------------------------------------------------------- tracks

    def report_stats(self) -> None:
        if not self.model_id or not self.stats:
            return
        total = sum(self.stats.values())
        fallbacks = ", ".join(f"{n} {why}" for why, n in self.stats.most_common() if why != "llm")
        fallbacks = ", ".join(f"{n} {why}" for why, n in self.stats.most_common()
                              if why not in ("llm", "judge_llm", "judge_builtin"))
        total = sum(n for why, n in self.stats.items() if why not in ("judge_llm", "judge_builtin"))
        self.log(f"[Performer] last track: {self.stats['llm']}/{total} edits by the LLM"
                 + (f" (built-in: {fallbacks})" if fallbacks else ""))
        judged = self.stats["judge_llm"] + self.stats["judge_builtin"]
        if judged:
            mass = (f"; on average it gave them {100 * sum(self.judge_mass) / len(self.judge_mass):.0f}% of "
                    "its probability" if self.judge_mass else "")
            self.log(f"[Performer] the judge preferred the LLM's idea {self.stats['judge_llm']}/{judged} times "
                     f"({100 * self.stats['judge_llm'] / judged:.0f}%) over the built-in candidates{mass}")
        self.judge_mass.clear()
        self.stats.clear()

    def _settings_for(self, style: str) -> dict:
        settings = dict(STYLE_SETTINGS[style])
        if self.edit_bars:
            settings["edit_bars"] = self.edit_bars
        return settings

    def applied(self, edit: Edit) -> None:
        """Called by the engine once an edit is actually playing."""
        who = "you" if edit.source in ("LLM", "jevtypesafe") else "the built-in performer"
        self.recent.append(f"- {edit.describe()} ({who}, {self.state.phase})"
                           + (f": {edit.reason}" if edit.reason else ""))

    def new_track(self, previous: Track | None) -> Track:
        self.recent.clear()
        if previous is not None:
            self.report_stats()
        if self.locked_style is None:
            rotation = ["lofi", "ambient", "downtempo", "piano"]
            if previous is None or self.rng.random() > 0.6:
                self.style = self.rng.choice(rotation)
            self.settings = self._settings_for(self.style)
        prev_piece = self._last_piece
        if previous is not None and prev_piece is None:
            prev_piece = plan_piece(random.Random(0), style=self.style)
            prev_piece.key, prev_piece.bpm = previous.key, previous.bpm
        if self.director is not None:
            self.director.style = self.style
            piece = self.director.next_piece(prev_piece)
        else:
            piece = plan_piece(self.rng, prev_piece, style=self.style)
        self._last_piece = piece
        self.track_narration = getattr(piece, "narration", "") or ""
        self.track_mood = piece.mood
        from .theory import PROGRESSIONS_BY_NAME

        degrees = list(PROGRESSIONS_BY_NAME[piece.progression].degrees)
        self.state = SetState(tracks_played=self.state.tracks_played + (1 if previous else 0),
                              target_lines=self.rng.randint(max(3, self.settings["max_lines"] - 2),
                                                            self.settings["max_lines"]),
                              groove_edits=self.rng.randint(6, 10))
        return Track(title=piece.title, style=self.style, key=piece.key, bpm=piece.bpm,
                     degrees=degrees, bars_per_chord=piece.cycles_per_chord)

    # -------------------------------------------------------------- phases

    def _advance_phase(self, program: Program) -> None:
        st = self.state
        active = program.active_roles()
        before = st.phase
        why = ""
        if st.phase == "build" and len(active) >= st.target_lines:
            st.phase, why = "groove", f"{len(active)} lines playing, the target for this track"
        elif st.phase == "groove" and st.edits_in_phase >= st.groove_edits:
            st.phase, why = "breakdown", f"{st.edits_in_phase} edits of groove, time for contrast"
        elif st.phase == "breakdown" and st.edits_in_phase >= 4:
            st.phase, why = "rebuild", "the breakdown has had 4 edits"
        elif st.phase == "rebuild" and not any(program.lines.get(r) and program.lines[r].muted
                                               for r in st.muted_for_breakdown):
            st.phase, why = "groove2", "everything muted for the breakdown is back"
        elif st.phase == "groove2" and st.edits_in_phase >= st.groove_edits:
            st.phase, why = "outro", f"{st.edits_in_phase} edits of the second peak"
        if st.phase != before:
            st.edits_in_phase = 0
            self.log(f"🧭 Arc: {before} → {st.phase} ({why})")
            runtime.ui_event("arc", before=before, after=st.phase, why=why)
            if self.on_arc is not None:
                self.on_arc(before, st.phase, why)

    def track_finished(self, program: Program) -> bool:
        return self.state.phase == "outro" and len(program.active_roles()) <= 1 and self.state.edits_in_phase > 0

    # ---------------------------------------------------------------- edits

    def next_edit(self, program: Program) -> Edit:
        """Choose the next edit (LLM if available, built-in otherwise)."""
        self._advance_phase(program)
        edit = None
        if self.model_id and self._skip_llm <= 0:
            if llm.resolve(self.model_id)[0] == "jevtypesafe" and llm.jev_style() == "decide":
                edit = self._jev_edit(program)
            elif self.judge:
                edit = self._judged_edit(program)
            else:
                edit = self._llm_edit(program)
        elif self._skip_llm > 0:
            self._skip_llm -= 1
            self.stats["resting"] += 1
        if edit is not None and not edit.source.startswith("built-in"):
            self.stats["llm"] += 1
        if edit is None:
            edit = self._builtin_edit(program)
            edit.source = "built-in"
        self.state.edits_in_phase += 1
        self.state.edits_in_track += 1
        if edit.op == "mute" and self.state.phase == "breakdown":
            self.state.muted_for_breakdown.append(edit.role)
        return edit

    def _block_for(self, role: str) -> Block:
        options = blocks_for(self.style, role)
        return self.rng.choice(options)

    def _add(self, program: Program, role: str) -> Edit:
        block = self._block_for(role)
        return Edit("add", role=role, line=block.make(self.rng), reason=f"bring in the {block.name.replace('_', ' ')}")

    def _missing_roles(self, program: Program) -> list[str]:
        available = {b.role for b in blocks_for(self.style)}
        order = [r for r in ROLE_ORDER.get(self.style, list(ROLE_CHANNELS)) if r in available]
        return [r for r in order if r not in program.lines]

    def _builtin_edit(self, program: Program) -> Edit:
        st, rng = self.state, self.rng
        active = program.active_roles()
        if st.phase == "build":
            if not program.lines:
                opener = rng.choice(self.settings["openers"])
                return self._add(program, opener[0])
            missing = self._missing_roles(program)
            if missing and (len(active) < 2 or rng.random() < 0.6):
                return self._add(program, missing[0])
            return self._tweak(program)
        if st.phase == "breakdown":
            for role in ("beat", "bass"):
                if role in active and len(active) > 1 and role not in st.muted_for_breakdown:
                    return Edit("mute", role=role, reason=f"drop the {role} for a breakdown")
            return self._tweak(program, calm=True)
        if st.phase == "rebuild":
            for role in ("bass", "beat"):
                if role in st.muted_for_breakdown and program.lines.get(role) and program.lines[role].muted:
                    return Edit("unmute", role=role, reason=f"bring the {role} back in")
            return self._tweak(program)
        if st.phase == "outro":
            removable = [r for r in active if r not in ("texture",)] or active
            if len(active) > 1:
                role = removable[-1] if rng.random() < 0.5 else rng.choice(removable)
                return Edit("remove", role=role, reason=f"take out the {role}")
            return self._tweak(program, calm=True)
        # groove / groove2
        missing = self._missing_roles(program)
        if missing and len(active) < self.settings["max_lines"] and rng.random() < 0.12:
            return self._add(program, missing[0])
        if rng.random() < 0.05:
            delta = rng.choice([-2, -1, 1, 2])
            return Edit("tempo", value=str(program.track.bpm + delta), reason="nudge the tempo")
        return self._tweak(program)

    def _tweak(self, program: Program, calm: bool = False) -> Edit:
        """One small revision to a playing line."""
        rng = self.rng
        roles = program.active_roles() or list(program.lines)
        for _ in range(12):
            role = rng.choice(roles)
            line = program.lines[role]
            kind = rng.choice(["pattern", "pattern", "fx", "fx", "pf", "wrap"])
            edit = None
            if kind == "pattern" and line.patterns:
                edit = self._pattern_edit(role, line)
            elif kind == "fx":
                edit = self._fx_edit(role, line, calm)
            elif kind == "pf" and line.pfs:
                edit = self._pf_edit(role, line, calm)
            elif kind == "wrap":
                edit = self._wrap_edit(role, line)
            if edit is None:
                continue
            try:
                apply_edit(program, edit, self.settings["max_lines"])
                return edit
            except EditError:
                continue
        return Edit("set_fx", role=roles[0], name="room",
                    value=f"{rng.choice([0.2, 0.3, 0.4])}", reason="open up the space a little")

    def _pattern_edit(self, role: str, line) -> Edit | None:
        rng = self.rng
        block = find_block(self.style, line.block)
        slots = [s for s in line.patterns if s not in LOCKED_SLOTS]
        if not slots:
            return None
        slot = rng.choice(slots)
        current = line.patterns[slot]
        if slot in CHOICE_SLOTS:
            value = rng.choice([v for v in CHOICE_SLOTS[slot] if v != current])
            return Edit("set_pattern", role=role, name=slot, value=value, reason=f"switch the {slot} pattern")
        variants = [v for v in (block.patterns.get(slot, []) if block else []) if v != current]
        # Notes only: not the counts after * ! / @ (e.g. the 16 in "0*16").
        numbers = [m for m in re.finditer(r"(?<![*!/@%\d.])-?\d+", current)]
        if not line.drums and numbers and block and block.pool and (not variants or rng.random() < 0.6):
            m = rng.choice(numbers)
            new_num = str(rng.choice([n for n in block.pool if str(n) != m.group()] or block.pool))
            value = current[:m.start()] + new_num + current[m.end():]
            return Edit("set_pattern", role=role, name=slot, value=value,
                        reason=f"change one note of the {role} {slot}")
        if variants:
            return Edit("set_pattern", role=role, name=slot, value=rng.choice(variants),
                        reason=f"new {slot} pattern on the {role}")
        return None

    def _fx_edit(self, role: str, line, calm: bool) -> Edit | None:
        rng = self.rng
        fx = dict((n, v) for n, v in line.fx)
        choices = [n for n in ROLE_FX.get(role, ["lpf", "room"]) if n not in ("gain", "legato")]
        if not choices:
            return None
        name = rng.choice(choices)
        lo, hi = FX_RANGES[name]
        current = fx.get(name)
        if name == "lpf":
            base = float(current) if current and re.match(r"^[\d.]+$", current) else 3000.0
            if rng.random() < 0.35:
                a, b = round(max(lo, base * 0.4)), round(min(hi, base * 1.1))
                value = f"(slow {rng.choice([4, 8, 16])} $ range {a} {b} sine)"
                return Edit("set_fx", role=role, name="lpf", value=value, reason=f"let the {role} filter breathe")
            factor = rng.uniform(0.55, 0.9) if calm else rng.uniform(0.7, 1.4)
            value = f"{round(min(hi, max(lo, base * factor)))}"
            return Edit("set_fx", role=role, name="lpf", value=value,
                        reason=f"{'darken' if factor < 1 else 'brighten'} the {role}")
        if name == "crush":
            if current:
                return Edit("remove_fx", role=role, name="crush", reason="clean the beat up again")
            return Edit("set_fx", role=role, name="crush", value=str(rng.choice([6, 8, 10])),
                        reason="dust the beat with some bitcrush")
        base = float(current) if current and re.match(r"^[\d.]+$", current) else (lo + hi) / 4
        value = round(min(hi * 0.8, max(lo, base + rng.choice([-0.15, -0.1, 0.1, 0.15]))), 2)
        return Edit("set_fx", role=role, name=name, value=f"{value:g}", reason=f"adjust the {role} {name}")

    def _pf_edit(self, role: str, line, calm: bool) -> Edit | None:
        name, current = self.rng.choice(line.pfs)
        lo, hi = PF_RANGES[name]
        base = float(current) if re.match(r"^[\d.]+$", current) else (lo + hi) / 2
        if name == "acid":
            step = self.rng.uniform(0.05, 0.14) * (-1 if calm or (base > 0.8 and self.rng.random() < 0.7) else 1)
            if not calm and self.rng.random() < 0.2:
                a, b = round(max(lo, base - 0.2), 2), round(min(hi, base + 0.15), 2)
                return Edit("set_pf", role=role, name=name, value=f"(slow 8 $ range {a:g} {b:g} sine)",
                            reason="ride the acid knob")
            value = round(min(hi, max(lo, base + step)), 3)
            return Edit("set_pf", role=role, name=name, value=f"{value:g}",
                        reason=f"{'close' if step < 0 else 'open'} the acid a little")
        value = round(min(hi, max(lo, base + self.rng.choice([-0.15, 0.1]))), 2)
        return Edit("set_pf", role=role, name=name, value=f"{value:g}", reason=f"adjust {name}")

    def _wrap_edit(self, role: str, line) -> Edit | None:
        own = [w for w in line.wrappers if w in ALL_WRAPPERS]
        if own and self.rng.random() < 0.5:
            w = self.rng.choice(own)
            return Edit("unwrap", role=role, name=w, reason=f"take off `{w}`")
        options = [w for w in allowed_wrappers(line) if w not in line.wrappers]
        if not options:
            return None
        w = self.rng.choice(options)
        return Edit("wrap", role=role, name=w, reason=f"try `{w}` on the {role}")

    # ------------------------------------------------------------------ LLM

    def _wait_seconds(self, program: Program) -> float:
        """How long to wait for the model: the edit window at the current
        tempo (with a little margin); a local model gets at least
        YS_LOCAL_PERFORMER_WAIT, and the engine keeps playing and applies
        its late edit on the next bar."""
        window = max(4.0, self.settings["edit_bars"] / program.track.cps * 0.85)
        if llm.resolve(self.model_id)[0] == "local":
            return max(window, config.LOCAL_PERFORMER_WAIT_SECONDS)
        return window

    def _llm_edit(self, program: Program) -> Edit | None:
        deadline = time.monotonic() + self._wait_seconds(program)
        extra = ""   # a correction request, added after a rejected reply
        schema = self._edit_schema(program)
        asks = (f"next edit for '{program.track.title}' ({self.state.phase}, "
                f"{len(program.active_roles())} line{'' if len(program.active_roles()) == 1 else 's'} playing)")
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            try:
                reply = self._ask_fitting(lambda: self._prompt(program) + extra,
                                          asks=asks if attempt == 1 else "corrected edit", show_full=self.show_llm,
                                          max_tokens=400, temperature=0.7, timeout=max(2.0, remaining),
                                          schema=schema)
                if time.monotonic() > deadline + 1:
                    raise llm.LLMError("reply came after the edit was due")
                edit = self._edit_from_json(llm.extract_json(reply), program)
                apply_edit(program, edit, self.settings["max_lines"])
                self._llm_failures = 0
                edit.source = "LLM"
                return edit
            except (EditError, ValueError, TypeError, KeyError) as e:
                # A usable-looking reply that broke a rule: if there's time,
                # say what was wrong and let the model correct itself once.
                if attempt == 1 and deadline - time.monotonic() > 3.0:
                    self.log(f"[Performer] edit rejected ({e}); asking the model to correct it")
                    extra = (f"\n\n## Your previous reply was rejected\n\n{reply.strip()[:400]}\n\n"
                             f"Reason: {e}. Reply with a corrected edit (one JSON object).")
                    continue
                return self._llm_failed(f"edit unusable ({e})", "invalid")
            except llm.LLMError as e:
                late = "timed out" in str(e) or "after the edit was due" in str(e)
                return self._llm_failed(f"LLM {'too slow' if late else 'failed'} ({e})",
                                        "late" if late else "error")
        return None

    # ------------------------------------------------ jevtypesafe decisions

    def _candidates(self, program: Program, limit: int = 8) -> list[Edit]:
        """Distinct edits that are legal now and fit the phase, for a
        decision model to choose from."""
        st = self.state
        out: list[Edit] = []
        seen: set[str] = set()

        def offer(edit: Edit | None) -> None:
            if edit is None or len(out) >= limit or edit.describe() in seen:
                return
            try:
                apply_edit(program, edit, self.settings["max_lines"])
            except EditError:
                return
            seen.add(edit.describe())
            out.append(edit)

        active = program.active_roles()
        if st.phase == "build" or (st.phase in ("groove", "groove2") and len(active) < self.settings["max_lines"]):
            for role in self._missing_roles(program)[:3 if st.phase == "build" else 1]:
                offer(self._add(program, role))
        if st.phase == "breakdown":
            for role in active:
                if len(active) > 1:
                    offer(Edit("mute", role=role, reason=f"drop the {role} for the breakdown"))
        if st.phase == "rebuild":
            for role, line in program.lines.items():
                if line.muted:
                    offer(Edit("unmute", role=role, reason=f"bring the {role} back in"))
        if st.phase == "outro":
            for role in active:
                if role != "texture" and len(active) > 1:
                    offer(Edit("remove", role=role, reason=f"take out the {role}"))
        offer(self._builtin_edit(program) if program.lines else None)
        calm = st.phase in ("breakdown", "outro")
        for _ in range(40):
            if len(out) >= limit or not program.lines:
                break
            offer(self._tweak(program, calm=calm))
        return out

    @staticmethod
    def _explain(edit: Edit) -> str:
        text = f"{edit.describe()}: {edit.reason}"
        if edit.op in ("wrap", "unwrap") and edit.name in WRAPPER_NOTES:
            text += f" ({WRAPPER_NOTES[edit.name]})"
        elif edit.op in ("set_fx", "set_pf", "remove_fx") and edit.name in FX_NOTES:
            text += f" ({FX_NOTES[edit.name].split(':')[0]})"
        return text[:240]

    def _judge(self, program: Program, candidates: list[Edit], timeout: float) -> tuple[int, str]:
        """Ask jevtypesafe to choose one of `candidates` (it isn't told who
        proposed which). Returns (index, its explanation or ""). Raises
        LLMError or EditError."""
        options = {f"e{i + 1}": self._explain(e) for i, e in enumerate(candidates)}
        state = "\n".join([
            f"A live coder is performing a {self.style} set in TidalCycles for a 24/7 stream "
            f"(theme: {self.theme or 'chill, hypnotic, evolving'}).",
            f"Track: {program.track.title}, {program.track.key}, {program.track.bpm:g} BPM.",
            f"Phase: {self.state.phase} ({self.state.edits_in_phase} edits so far). {PHASE_HELP[self.state.phase]}",
            "The program playing now:", *program.code_lines(),
            "Recent edits (oldest first):", *(self.recent or ["(none yet)"]),
        ])
        questions = {
            "edit": {"type": "choice",
                     "instructions": "Which edit should the live coder make next? Choose the one that best "
                                     "develops the track at this point of its arc and fits the theme: a change "
                                     "a listener will notice, not a jolt, and not a repeat of a recent move.",
                     "criteria": options},
        }
        asks = (f"choose the next edit for '{program.track.title}' ({self.state.phase}) "
                f"from {len(candidates)} options")
        agent = "Judge" if self.judge else "Performer"
        answers = llm.decide(agent, state, questions, log=self.log, asks=asks, show_full=self.show_llm,
                             timeout=timeout)
        answer = answers.get("edit") or {}
        choice = answer.get("choice") if isinstance(answer, dict) else answer
        if choice not in options:
            raise EditError(f"chose {choice!r}, not one of {sorted(options)}")
        why = ""
        info: dict = {}
        if isinstance(answer, dict):
            why = next((str(answer[k]) for k in ("reason", "rationale", "explanation", "why") if answer.get(k)), "")
            probs = answer.get("probabilities")
            if isinstance(probs, dict):
                info["probabilities"] = [float(probs.get(f"e{i + 1}", 0) or 0) for i in range(len(candidates))]
            if isinstance(answer.get("confidence"), (int, float)):
                info["confidence"] = float(answer["confidence"])
        self.last_judgement = info
        return int(choice[1:]) - 1, " ".join(why.split())[:200]

    def _jev_edit(self, program: Program) -> Edit | None:
        """jevtypesafe's decision API chooses the next edit from legal candidates."""
        candidates = self._candidates(program)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        window = self.settings["edit_bars"] / program.track.cps
        try:
            index, why = self._judge(program, candidates, max(4.0, window * 0.85))
        except llm.LLMError as e:
            late = "timed out" in str(e)
            return self._llm_failed(f"jevtypesafe {'too slow' if late else 'failed'} ({e})",
                                    "late" if late else "error")
        except EditError as e:
            return self._llm_failed(f"jevtypesafe answer unusable ({e})", "invalid")
        edit = candidates[index]
        edit.listening = why        # keep any explanation, so the choice can be understood
        edit.source = "jevtypesafe"
        self._llm_failures = 0
        return edit

    def _proposals(self, program: Program, timeout: float) -> list[Edit]:
        """The LLM proposes up to 3 different legal edits (one call)."""
        edit_schema = self._edit_schema(program)
        schema = {"type": "object", "properties": {"proposals": {"type": "array", "items": edit_schema,
                                                                 "minItems": 1, "maxItems": 3}},
                  "required": ["proposals"], "additionalProperties": False}
        asks = f"3 ideas for '{program.track.title}' ({self.state.phase})"
        reply = self._ask_fitting(lambda: self._prompt(program, proposals=3), asks=asks,
                                  show_full=self.show_llm, max_tokens=900, temperature=0.8, timeout=timeout,
                                  schema=schema)
        data = llm.extract_json(reply)
        items = data.get("proposals") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = [data]            # a model that answered with a single edit
        out, seen = [], set()
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            try:
                edit = self._edit_from_json(item, program)
                apply_edit(program, edit, self.settings["max_lines"])
            except (EditError, ValueError, TypeError, KeyError) as e:
                self.log(f"[Performer] proposal dropped ({e})")
                continue
            if edit.describe() not in seen:
                seen.add(edit.describe())
                edit.source = "LLM"
                out.append(edit)
        return out

    def _judged_edit(self, program: Program) -> Edit | None:
        """Generator/judge: the LLM proposes, jevtypesafe chooses blind among
        its proposals and a few built-in candidates."""
        deadline = time.monotonic() + max(6.0, self._wait_seconds(program))
        proposals: list[Edit] = []
        llm_problem = ""
        try:
            # Most of the time goes to the proposals; the judge answers in seconds.
            remaining = deadline - time.monotonic()
            proposals = self._proposals(program, timeout=max(3.0, remaining - min(10.0, remaining * 0.3)))
            if not proposals:
                llm_problem = "invalid"
        except llm.LLMError as e:
            self.log(f"[Performer] no proposals from the LLM ({e}); the judge chooses among built-in ideas")
            llm_problem = "late" if "timed out" in str(e) else "error"
        seen = {e.describe() for e in proposals}
        builtin = [e for e in self._candidates(program, limit=6) if e.describe() not in seen][:3]
        for e in builtin:
            e.source = "built-in"
        pool = proposals + builtin
        if not pool:
            return None
        self.rng.shuffle(pool)          # blind: order says nothing about who proposed what
        if len(pool) == 1:
            chosen, why = pool[0], ""
        else:
            try:
                index, why = self._judge(program, pool, max(3.0, deadline - time.monotonic()))
                chosen = pool[index]
            except (llm.LLMError, EditError) as e:
                self.log(f"[Performer] judge unavailable ({e}); playing the LLM's first idea")
                chosen, why = (proposals or builtin)[0], ""
        from_llm = chosen.source == "LLM"
        info = self.last_judgement if len(pool) > 1 else {}
        self.last_judgement = {}
        detail = []
        if "probabilities" in info:
            detail.append(f"p {info['probabilities'][pool.index(chosen)]:.2f}")
        if "confidence" in info:
            detail.append(f"confidence {info['confidence']:.2f}")
        if llm_problem:
            # Nothing from the LLM to compare against: not a judgement.
            self.stats[llm_problem] += 1
            chosen.source = "built-in, judged"
            return chosen
        compared = bool(proposals) and bool(builtin)
        if compared:
            # Only a real comparison (LLM ideas against built-in ones) counts.
            self.stats["judge_llm" if from_llm else "judge_builtin"] += 1
            if "probabilities" in info:
                self.judge_mass.append(sum(p for e, p in zip(pool, info["probabilities"]) if e.source == "LLM"))
        probs = info.get("probabilities") or []
        runtime.ui_event("judge", compared=compared, confidence=info.get("confidence"),
                         chosen=pool.index(chosen), chosen_from="ai" if from_llm else "random",
                         options=[{"text": e.describe(), "from": "ai" if e.source == "LLM" else "random",
                                   "p": probs[i] if i < len(probs) else None} for i, e in enumerate(pool)])
        self.log(f"⚖️  Judge chose {'the LLM' if from_llm else 'a built-in'} idea "
                 f"({', '.join(detail + [f'from {len(proposals)} LLM + {len(builtin)} built-in'])}"
                 + ("" if compared else "; not a comparison") + ")" + (f": {why}" if why else ""))
        if not from_llm:
            self.stats["judge picked built-in"] += 1
            chosen.source = "built-in, judged"
            return chosen               # counted as a built-in edit (not "llm")
        chosen.source = "LLM, judged"
        self._llm_failures = 0
        return chosen

    def _ask_fitting(self, build_prompt, **kwargs) -> str:
        """converse() with the prompt from build_prompt(); if the model's
        context window is too small for it, switch to compact prompts for
        the rest of the run and retry once."""
        try:
            return llm.converse("Performer", self.model_id, self._SYSTEM, build_prompt(), log=self.log, **kwargs)
        except llm.LLMError as e:
            if self._compact or "context window" not in str(e):
                raise
            self._compact = True
            self.log("[Performer] the prompt doesn't fit the model's context window; switching to compact "
                     "prompts (no glossary or example moves). Raise LLAMA_CTX_SIZE for the full prompt.")
            return llm.converse("Performer", self.model_id, self._SYSTEM, build_prompt(), log=self.log, **kwargs)

    _SYSTEM = "You are a live coder performing TidalCycles. Reply with JSON only."

    def _llm_failed(self, why: str, kind: str) -> None:
        self.stats[kind] += 1
        self._llm_failures += 1
        self.log(f"[Performer] {why}; built-in performer takes this one")
        if self._llm_failures >= 3:
            self._skip_llm, self._llm_failures = 5, 0
            self.log("[Performer] too many LLM failures; the built-in performer plays the next 5 edits")
        return None

    def _edit_schema(self, program: Program) -> dict:
        """JSON schema of every edit that is legal right now. A local server
        decodes under it, so only real block, role, slot, effect and wrapper
        names can come out; numbers are still range-checked afterwards."""
        text = {"type": "string", "maxLength": 80}
        note = {"type": "string", "maxLength": 160}

        def variant(op: str, **props) -> dict:
            # What it hears, then why, then the move: the model explains
            # itself before it commits.
            properties = {"listening": note, "reason": note, "op": {"const": op}, **props,
                          "narration": {"type": "string", "maxLength": 160}}
            return {"type": "object", "properties": properties,
                    "required": ["listening", "reason", "op", *props], "additionalProperties": False}

        options = []
        addable = [b.name for b in blocks_for(self.style) if b.role not in program.lines]
        if addable and len(program.active_roles()) < self.settings["max_lines"]:
            options.append(variant("add", block={"enum": addable}))
        active = program.active_roles()
        muted = [r for r, line in program.lines.items() if line.muted]
        if program.lines:
            options.append(variant("remove", role={"enum": list(program.lines)}))
        if len(active) > 1:
            options.append(variant("mute", role={"enum": active}))
        if muted:
            options.append(variant("unmute", role={"enum": muted}))
        for role, line in program.lines.items():
            slots = [k for k in line.patterns if k not in LOCKED_SLOTS]
            for slot in slots:
                value = {"enum": CHOICE_SLOTS[slot]} if slot in CHOICE_SLOTS else {"type": "string", "maxLength": 60}
                options.append(variant("set_pattern", role={"const": role}, name={"const": slot}, value=value))
            fx = [n for n in ROLE_FX.get(role, list(FX_RANGES)) if n in FX_RANGES]
            if fx:
                options.append(variant("set_fx", role={"const": role}, name={"enum": fx}, value=text))
            if line.pfs:
                options.append(variant("set_pf", role={"const": role}, name={"enum": [n for n, _ in line.pfs]},
                                       value=text))
            wraps = [w for w in allowed_wrappers(line) if w not in line.wrappers]
            if wraps and len(line.wrappers) < 3:
                options.append(variant("wrap", role={"const": role}, name={"enum": wraps}))
            if line.wrappers:
                options.append(variant("unwrap", role={"const": role}, name={"enum": list(line.wrappers)}))
        options.append(variant("tempo", value=text))
        return {"anyOf": options}

    def _edit_from_json(self, data: dict, program: Program) -> Edit:
        op = str(data.get("op", "")).strip()
        if op not in EDIT_OPS:
            raise EditError(f"unknown op {op!r}")
        role = str(data.get("role", "")).strip()
        name = str(data.get("name", "")).strip()
        value = _clamp_value(op, name, str(data.get("value", "")).strip(), program)
        edit = Edit(op, role=role, name=name, value=value,
                    reason=" ".join(str(data.get("reason", "")).split())[:200],
                    narration=clean_narration(data.get("narration")),
                    listening=" ".join(str(data.get("listening", "")).split())[:200])
        if op == "set_fx" and name == "gain" and role in program.lines:
            # Pull a gain edit into the line's +/-3 dB window instead of rejecting it.
            from .livecode import gain_range

            lo, hi = gain_range(program.lines[role])
            if _NUM_RE.match(edit.value):
                edit.value = f"{max(lo, min(hi, float(edit.value))):g}"
        if op == "add":
            block = find_block(self.style, str(data.get("block", "")))
            if block is None:
                raise EditError(f"unknown block {data.get('block')!r}")
            edit.role = block.role
            edit.line = block.make(self.rng)
        return edit

    def _prompt(self, program: Program, proposals: int = 0) -> str:
        # Only what applies to the lines playing now: a smaller prompt fits
        # small local context windows and keeps the model focused.
        editable = {}
        used_wrappers: set[str] = set()
        wrap_kinds: set[str] = set()
        used_fx: set[str] = set()
        for role, line in program.lines.items():
            fx_ok = [n for n in ROLE_FX.get(role, list(FX_RANGES)) if n in FX_RANGES]
            wraps_ok = [w for w in allowed_wrappers(line) if w not in line.wrappers]
            entry = {"line": f"d{line.channel}", "patterns": {k: v for k, v in line.patterns.items()
                                                               if k not in LOCKED_SLOTS}}
            if line.muted:
                entry["muted"] = True
            if line.drums:
                from .livecode import kit_sounds

                entry["sounds"] = {slot: sorted(kit_sounds(self.style, line.block, slot)) for slot in line.patterns}
            entry["fx"] = dict((n, v) for n, v in line.fx)
            if line.pfs:
                entry["controls"] = dict((n, v) for n, v in line.pfs)
            entry["can_set_fx"] = fx_ok
            if line.wrappers:
                entry["wrappers"] = line.wrappers
            if wraps_ok:
                entry["wrap_with"] = _wrapper_kind(line)
                wrap_kinds.add(_wrapper_kind(line))
            editable[role] = entry
            used_wrappers.update(line.wrappers)
            used_fx.update(fx_ok + [n for n, _ in line.pfs])
        addable = {b.name: b.role for b in blocks_for(self.style) if b.role not in program.lines}
        template = _load_prompt("performer.md")
        return template.format(
            theme=self.theme or "chill, hypnotic, evolving", style=self.style,
            track=json.dumps(program.track.to_dict()), phase=self.state.phase,
            phase_help=PHASE_HELP[self.state.phase], edits_in_phase=self.state.edits_in_phase,
            code="\n".join(program.code_lines()),
            lines="\n".join(f'"{r}": {json.dumps(e, separators=(",", ":"))}' for r, e in editable.items())
            or "(no lines yet)",
            addable=json.dumps(addable), max_lines=self.settings["max_lines"],
            examples=("(compact prompt)" if self._compact
                      else "\n".join(f"- {e}" for e in PHASE_EXAMPLES[self.state.phase])),
            recent="\n".join(self.recent) or "(none yet: this is the start of the track)",
            wrapper_notes=_wrapper_notes(wrap_kinds, used_wrappers, names_only=self._compact),
            fx_notes="\n".join(f"- `{n}` [{lo:g}-{hi:g}]: {FX_NOTES[n]}"
                                for n, (lo, hi) in {**FX_RANGES, **PF_RANGES}.items() if n in used_fx)
            if not self._compact else
            "Ranges: " + ", ".join(f"{n} {lo:g}-{hi:g}" for n, (lo, hi) in {**FX_RANGES, **PF_RANGES}.items()
                                   if n in used_fx),
            reply=REPLY_PROPOSALS.replace("N", str(proposals)) if proposals else REPLY_SINGLE,
        )


_WRAPPER_KINDS = {"drum wrappers": DRUM_WRAPPERS, "bass wrappers": BASS_WRAPPERS,
                  "melodic wrappers": MELODIC_WRAPPERS}


def _wrapper_kind(line) -> str:
    return "drum wrappers" if line.drums else "bass wrappers" if line.role == "bass" else "melodic wrappers"


def _wrapper_notes(kinds: set[str], extra: set[str], names_only: bool = False) -> str:
    """Each wrapper once, grouped by the kind of line it can go on."""
    out = []
    for kind, names in _WRAPPER_KINDS.items():
        if kind in kinds:
            if names_only:
                out.append(f"{kind[0].upper()}{kind[1:]}: " + "; ".join(f"`{w}`" for w in names))
                continue
            out.append(f"{kind[0].upper()}{kind[1:]}:")
            out.extend(f"- `{w}`: {WRAPPER_NOTES[w]}" for w in names)
    shown = {w for k in kinds for w in _WRAPPER_KINDS[k]}
    out.extend(f"- `{w}`: {WRAPPER_NOTES[w]}" for w in sorted(extra - shown) if w in WRAPPER_NOTES)
    return "\n".join(out)


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_RANGE_RE = re.compile(r"^\(slow (\d+) \$ range (-?[\d.]+) (-?[\d.]+) (sine|saw|tri)\)$")


def _clamp_value(op: str, name: str, value: str, program: Program) -> str:
    """Pull a slightly-out-of-range number back into range instead of
    throwing the whole edit away (e.g. lpf 12000 -> 9000, a tempo jump of
    +20 -> +12). Anything that isn't a number is left for the validator."""
    if op == "tempo" and _NUM_RE.match(value):
        bpm = program.track.bpm
        return f"{max(50.0, min(200.0, bpm - 12 if float(value) < bpm - 12 else min(bpm + 12, float(value)))):g}"
    ranges = FX_RANGES if op == "set_fx" else PF_RANGES if op == "set_pf" else None
    if not ranges or name not in ranges:
        return value
    lo, hi = ranges[name]
    if _NUM_RE.match(value):
        return f"{max(lo, min(hi, float(value))):g}"
    m = _RANGE_RE.match(value)
    if m:
        a, b = (max(lo, min(hi, float(x))) for x in (m.group(2), m.group(3)))
        return f"(slow {max(1, min(64, int(m.group(1))))} $ range {a:g} {b:g} {m.group(4)})"
    return value


PHASE_HELP = {
    "build": "Build the track up: add a line (or tweak one) - one idea at a time.",
    "groove": "The track is up. Revise it: change a pattern or a note, ride a filter or the acid knob, "
              "add or remove a wrapper. Keep it evolving, don't add everything.",
    "breakdown": "Breakdown: mute the beat and/or bass, and make what's left more spacious.",
    "rebuild": "Bring the muted lines back (unmute), building tension.",
    "groove2": "Second peak: revise and intensify, then start easing off.",
    "outro": "Outro: remove or mute lines one by one; the track is ending.",
}


REPLY_SINGLE = """## Reply

ONE JSON object with, in this order:

- `"listening"`: what you hear in the program right now that matters for
  the next move (one sentence, concrete: which lines, what's too much or
  missing).
- `"reason"`: why this move, and what the listener should hear change
  (one sentence).
- the operation fields.
- optionally `"narration"`: one warm sentence for listeners, no numbers or
  jargon, only for bigger moments.

```json
{"listening": "The acid line has sat at the same brightness for 16 bars and the groove feels static.",
  "reason": "Open the acid slowly so the line starts to scream towards the next phrase.",
  "op": "set_pf", "role": "acid", "name": "acid", "value": "(slow 8 $ range 0.4 0.75 sine)"}
```
"""

REPLY_PROPOSALS = """## Reply: propose N different edits

A judge will pick one of your ideas (or someone else's), so propose N
genuinely DIFFERENT moves: e.g. one on the melody, one on the sound, one
structural. Each is a full edit with, in this order, `"listening"` (what you
hear that matters), `"reason"` (why this move, what should change), the
operation fields, and optionally `"narration"`.

```json
{"proposals": [
  {"listening": "The melody repeats the same four notes.", "reason": "Answer the phrase a step lower.",
   "op": "set_pattern", "role": "lead", "name": "melody", "value": "4 ~ 3 ~ 0 ~ ~ ~"},
  {"listening": "The keys are bright and flat.", "reason": "Let the keys breathe with a slow filter sweep.",
   "op": "set_fx", "role": "keys", "name": "lpf", "value": "(slow 16 $ range 1200 4000 sine)"},
  {"listening": "Everything plays all the time.", "reason": "Drop the hats for air before the next phrase.",
   "op": "mute", "role": "perc"}
]}
```
"""

# A few example moves per phase: local models copy good examples far more
# reliably than they follow rules.
PHASE_EXAMPLES = {
    "build": [
        "Start with the most characteristic line of the style alone for a while (add the beat, or the acid).",
        "Add a line in a free register: bass under drums, then keys or chords, the melody last.",
        "Before adding the next line, give the new one a tweak so it sits (e.g. lpf on keys to 2500).",
    ],
    "groove": [
        "Change one or two notes of the melody so the phrase answers itself (\"0 ~ 2 4\" -> \"0 ~ 2 5\").",
        "Let a filter breathe: set_fx lpf to (slow 16 $ range 1200 4000 sine) on the keys.",
        "Wrap the melody in off 0.25 (|+ n 12) for a call-and-answer echo, then take it off a few edits later.",
        "Open the acid knob step by step across several edits to build tension.",
    ],
    "breakdown": [
        "Mute the beat first; the space it leaves is the point.",
        "Make what remains bigger: more room on the keys or chords, slower filter sweeps.",
        "Thin the melody (more ~ rests) so the breakdown breathes.",
    ],
    "rebuild": [
        "Bring the bass back first, then the beat, so the drop lands.",
        "Close a filter slightly before the beat returns, then open it with the beat.",
    ],
    "groove2": [
        "Push further than the first groove: a busier hi-hat pattern, a higher melody, the acid more open.",
        "Add a wrapper that creates motion (every 4 (fast 2) on the drums) for the peak.",
        "Near the end of this phase start easing off: darken filters, sparser patterns.",
    ],
    "outro": [
        "Remove the busiest lines first; keep the texture and one melodic line to the end.",
        "Darken what's left with a lower lpf as it fades out.",
    ],
}


def _load_prompt(name: str) -> str:
    with open(os.path.join(config.REPO_DIR, "prompts", name), encoding="utf-8") as f:
        return f.read()
