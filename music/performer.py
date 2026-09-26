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
from dataclasses import dataclass, field

import config

from . import llm
from .blocks import STYLE_SETTINGS, Block, blocks_for, find_block
from .livecode import (ALL_WRAPPERS, BASS_WRAPPERS, CHOICE_SLOTS, DRUM_WRAPPERS, EDIT_OPS, FX_RANGES,
                       LOCKED_SLOTS, MELODIC_WRAPPERS, PF_RANGES, ROLE_CHANNELS, ROLE_FX, Edit, EditError,
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
                 theme: str = "", show_llm: bool = False):
        self.rng = rng
        # A locked style, or None to rotate between the chill styles per track.
        self.locked_style = style if style in STYLE_SETTINGS else None
        self.style = self.locked_style or "lofi"
        self.model_id = model_id
        self.log = log
        self.theme = theme
        self.show_llm = show_llm
        self.settings = STYLE_SETTINGS[self.style]
        self.state = SetState()
        self._llm_failures = 0
        self._skip_llm = 0

    # ------------------------------------------------------------- tracks

    def new_track(self, previous: Track | None) -> Track:
        if self.locked_style is None:
            rotation = ["lofi", "ambient", "downtempo", "piano"]
            if previous is None or self.rng.random() > 0.6:
                self.style = self.rng.choice(rotation)
            self.settings = STYLE_SETTINGS[self.style]
        prev_piece = None
        if previous is not None:
            prev_piece = plan_piece(random.Random(0), style=self.style)
            prev_piece.key, prev_piece.bpm = previous.key, previous.bpm
        piece = plan_piece(self.rng, prev_piece, style=self.style)
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
        if st.phase == "build" and len(active) >= st.target_lines:
            st.phase, st.edits_in_phase = "groove", 0
        elif st.phase == "groove" and st.edits_in_phase >= st.groove_edits:
            st.phase, st.edits_in_phase = "breakdown", 0
        elif st.phase == "breakdown" and st.edits_in_phase >= 4:
            st.phase, st.edits_in_phase = "rebuild", 0
        elif st.phase == "rebuild" and not any(program.lines.get(r) and program.lines[r].muted
                                               for r in st.muted_for_breakdown):
            st.phase, st.edits_in_phase = "groove2", 0
        elif st.phase == "groove2" and st.edits_in_phase >= st.groove_edits:
            st.phase, st.edits_in_phase = "outro", 0

    def track_finished(self, program: Program) -> bool:
        return self.state.phase == "outro" and len(program.active_roles()) <= 1 and self.state.edits_in_phase > 0

    # ---------------------------------------------------------------- edits

    def next_edit(self, program: Program) -> Edit:
        """Choose the next edit (LLM if available, built-in otherwise)."""
        self._advance_phase(program)
        edit = None
        if self.model_id and self._skip_llm <= 0:
            edit = self._llm_edit(program)
        elif self._skip_llm > 0:
            self._skip_llm -= 1
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
        numbers = [m for m in re.finditer(r"-?\d+", current)]
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

    def _llm_edit(self, program: Program) -> Edit | None:
        prompt = self._prompt(program)
        try:
            asks = (f"next edit for '{program.track.title}' ({self.state.phase}, "
                    f"{len(program.active_roles())} line{'' if len(program.active_roles()) == 1 else 's'} playing)")
            reply = llm.converse("Performer", self.model_id,
                                 "You are a live coder performing TidalCycles. Reply with JSON only.", prompt,
                                 log=self.log, asks=asks, show_full=self.show_llm, max_tokens=500)
            data = llm.extract_json(reply)
            edit = self._edit_from_json(data, program)
            apply_edit(program, edit, self.settings["max_lines"])
            self._llm_failures = 0
            edit.source = "LLM"
            return edit
        except (llm.LLMError, EditError, ValueError, TypeError, KeyError) as e:
            self._llm_failures += 1
            self.log(f"[Performer] LLM edit unusable ({e}); built-in performer takes this one")
            if self._llm_failures >= 3:
                self._skip_llm, self._llm_failures = 5, 0
                self.log("[Performer] too many LLM failures; the built-in performer plays the next 5 edits")
            return None

    def _edit_from_json(self, data: dict, program: Program) -> Edit:
        op = str(data.get("op", "")).strip()
        if op not in EDIT_OPS:
            raise EditError(f"unknown op {op!r}")
        role = str(data.get("role", "")).strip()
        edit = Edit(op, role=role, name=str(data.get("name", "")), value=str(data.get("value", "")),
                    reason=" ".join(str(data.get("reason", "")).split())[:160],
                    narration=clean_narration(data.get("narration")))
        if op == "add":
            block = find_block(self.style, str(data.get("block", "")))
            if block is None:
                raise EditError(f"unknown block {data.get('block')!r}")
            edit.role = block.role
            edit.line = block.make(self.rng)
        return edit

    def _prompt(self, program: Program) -> str:
        editable = {}
        for role, line in program.lines.items():
            editable[role] = {
                "channel": f"d{line.channel}", "muted": line.muted,
                "patterns": {k: v for k, v in line.patterns.items() if k not in LOCKED_SLOTS},
                "fx": dict((n, v) for n, v in line.fx), "controls": dict((n, v) for n, v in line.pfs),
                "wrappers": line.wrappers, "drums": line.drums,
            }
        addable = {b.name: b.role for b in blocks_for(self.style) if b.role not in program.lines}
        template = _load_prompt("performer.md")
        return template.format(
            theme=self.theme or "chill, hypnotic, evolving", style=self.style,
            track=json.dumps(program.track.to_dict()), phase=self.state.phase,
            phase_help=PHASE_HELP[self.state.phase], edits_in_phase=self.state.edits_in_phase,
            code="\n".join(program.code_lines()), lines=json.dumps(editable, indent=1),
            addable=json.dumps(addable), max_lines=self.settings["max_lines"],
            melodic_wrappers=json.dumps(MELODIC_WRAPPERS), drum_wrappers=json.dumps(DRUM_WRAPPERS),
            bass_wrappers=json.dumps(BASS_WRAPPERS), role_fx=json.dumps(ROLE_FX),
            fx=json.dumps({k: list(v) for k, v in FX_RANGES.items()}),
            controls=json.dumps({k: list(v) for k, v in PF_RANGES.items()}),
        )


PHASE_HELP = {
    "build": "Build the track up: add a line (or tweak one) - one idea at a time.",
    "groove": "The track is up. Revise it: change a pattern or a note, ride a filter or the acid knob, "
              "add or remove a wrapper. Keep it evolving, don't add everything.",
    "breakdown": "Breakdown: mute the beat and/or bass, and make what's left more spacious.",
    "rebuild": "Bring the muted lines back (unmute), building tension.",
    "groove2": "Second peak: revise and intensify, then start easing off.",
    "outro": "Outro: remove or mute lines one by one; the track is ending.",
}


def _load_prompt(name: str) -> str:
    with open(os.path.join(config.REPO_DIR, "prompts", name), encoding="utf-8") as f:
        return f.read()
