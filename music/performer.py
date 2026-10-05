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
import math
import os
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import config

from . import llm, runtime
from .llm import CACHE_BREAK
from .blocks import STYLE_SETTINGS, Block, blocks_for, find_block, subgenre, subgenre_blocks, track_blocks
from .livecode import (ALL_WRAPPERS, BASS_WRAPPERS, CHOICE_SLOTS, CLUB_DRUM_WRAPPERS, CLUB_STYLES, DRUM_WRAPPERS,
                       EDIT_OPS, FX_RANGES,
                       FX_NOTES, LEAD_WRAPPERS, LOCKED_SLOTS, MELODIC_WRAPPERS, PF_RANGES, ROLE_CHANNELS, ROLE_FX,
                       NOTE_BUDGET, VOCAL_WRAPPERS, WRAPPER_NOTES, notes_per_bar, Edit, EditError,
                       Program, Track, allowed_wrappers, apply_edit, complexity, line_controls)
from .narrator import clean_narration
from .plan import (DEFAULT_LIVE_PLAN, LIVE_PIECE_MINUTES, arc_summary, fit_live_plan, live_plan, plan_piece,
                   sounds_summary)

# Which roles come in first, per style (after the opener).
ROLE_ORDER = {
    "lofi": ["beat", "keys", "bass", "lead", "texture", "perc", "acid", "arp"],
    "deephouse": ["beat", "bass", "keys", "perc", "lead", "chords", "arp", "texture"],
    "trance": ["acid", "beat", "bass", "perc", "chords", "lead", "arp", "texture"],
    "ambient": ["chords", "texture", "keys", "lead", "arp", "bass", "beat"],
    "piano": ["keys", "lead", "bass", "chords", "arp", "texture"],
    "downtempo": ["beat", "keys", "bass", "lead", "arp", "texture"],
    "darkwave": ["bass", "beat", "chords", "lead", "arp", "keys", "texture", "perc"],
    "techno": ["beat", "perc", "bass", "acid", "chords", "arp", "texture", "lead"],
    "hardcore": ["beat", "perc", "bass", "acid", "lead", "arp", "chords", "texture"],
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
    muted_for_breakdown: list[str] = field(default_factory=list)
    # The Director's plan for this track, decided before it starts: the
    # phases in order with their bars (plan.live_plan), and which step plays
    # now. Build and rebuild end once their lines are in; the others last
    # their bars.
    plan: list = field(default_factory=list)
    step: int = 0
    phase_start: float = 0.0   # the bar (Tidal cycle) the current phase began
    # The Director's instructions per phase (plan.Piece.cues) and the ones
    # still to carry out in the phase playing now.
    cues: dict = field(default_factory=dict)
    cue_queue: list = field(default_factory=list)
    # The track's surprise (music/surprises.py): which have fired, and for a
    # fake drop the plan step where it falls back.
    surprise_done: list = field(default_factory=list)
    fake_step: int = -1

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "SetState":
        state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if not state.plan and isinstance(data.get("arc"), dict):     # saved with the earlier bars-per-phase arc
            state.plan = [[p, data["arc"][p]] for p, _ in DEFAULT_LIVE_PLAN if p in data["arc"]]
            state.step = next((i for i, (p, _) in enumerate(state.plan) if p == state.phase), 0)
        return state




PHASE_LABELS = {"groove2": "peak"}

# The vocal (chops or an acapella) is the payoff: it comes in for the
# breakdown, a voice in the space the kick leaves, and sings through the peak.
VOCAL_PHASES = ("breakdown", "rebuild", "groove2")
VOCAL_CUE = {"do": "start", "part": "vocal", "say": "In comes the vocal"}


def with_vocal_cue(cues: dict) -> dict:
    """The track's cues, with the vocal brought in for the breakdown (or the
    peak, if there's no breakdown) unless a cue already brings it in."""
    if any(isinstance(c, dict) and c.get("part") == "vocal" for cs in cues.values() for c in cs):
        return cues
    phase = "breakdown" if "breakdown" in cues else "groove2"
    return {**cues, phase: list(cues.get(phase, [])) + [dict(VOCAL_CUE)]}


def builtin_cues(piece) -> dict[str, list[str]]:
    """Plain instructions for a track when no Director wrote any: the turns a
    live set takes, named after the parts the track has."""
    hook = (piece.hook or {}).get("role") or "lead"
    return {
        "groove": [{"say": f"Change the {hook} pattern"}, {"do": "knob", "part": "chords", "knob": "lpf",
                                                          "to": [1200, 4500, 16], "say": "Open the chord filter slowly"}],
        "breakdown": [{"do": "stop", "part": "beat", "say": "Drop the kick"},
                      {"do": "stop", "part": "bass", "say": "Drop the bass"}],
        "rebuild": [{"do": "bring back", "part": "bass", "say": "Bring the bass back"},
                    {"do": "bring back", "part": "beat", "say": "Bring the kick back"}],
        "groove2": [{"say": f"Change the {hook} melody"},
                    {"do": "knob", "part": hook, "knob": "lpf", "to": 6000, "say": f"Brighter: open the {hook} filter"}],
        "outro": [{"do": "stop", "part": hook, "say": f"Take out the {hook}"},
                  {"do": "stop", "part": "bass", "say": "Drop the bass"}],
    }

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
        self._recent_vocals: deque[str] = deque(maxlen=3)   # the last acapellas sung
        self.track_narration = ""   # the Director's spoken line for the current track
        self.track_mood = ""
        # Called as on_surprise(name) when one of the track's surprises fires
        # (the engine records it).
        self.on_surprise = None
        self._track_surprise = ""
        # Called as on_arc(before, after, why) when the arc moves on (the
        # engine narrates these moments).
        self.on_arc = None
        # The current bar (Tidal cycle); the engine connects its clock. Without
        # one, bars are counted from the edits.
        self.bar_now = None
        self.settings = self._settings_for(self.style)
        self.state = SetState()
        self._llm_failures = 0
        self._skip_llm = 0
        # Per track: how many edits the LLM made, and why the built-in performer
        # had to step in ("late", "invalid", "error", "resting").
        self.stats: Counter[str] = Counter()
        # How long the model takes to decide (seconds, smoothed), so the
        # build's quick pace still leaves it time to make the moves.
        self.latency: float | None = None
        self._bar_seconds = 0.0
        # Knob rides: (role, control) -> direction (+1 up, -1 down). A ride
        # keeps going for several edits, like a hand on a knob, then turns.
        self._rides: dict[tuple[str, str], int] = {}
        self._last_ride: str | None = None
        # Requests typed into the stream page (music/host_requests.py); the
        # engine connects the queue.
        self.requests = None
        # Compact prompts (no glossary or example moves) once the model's
        # context window turned out to be too small for the full one.
        self._compact = False
        self._complexity_now: float | None = None   # measured at each edit
        self.line_ratings: dict[str, int] = {}       # the host's 👍 (1) / 👎 (-1) per line, this track
        self._knob_streak = 0                         # knob-only edits in a row (see KNOB_OPS)
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

    # Edits that only turn a knob. After MAX_KNOB_STREAK of them in a row the
    # next move must change the music itself (notes, rhythm, lines): a long
    # run of filter and drive nudges sounds like nothing is happening.
    KNOB_OPS = ("set_fx", "set_pf", "remove_fx")
    MAX_KNOB_STREAK = 2

    def must_change_music(self) -> bool:
        return self._knob_streak >= self.MAX_KNOB_STREAK

    def applied(self, edit: Edit) -> None:
        """Called by the engine once an edit is actually playing."""
        self._knob_streak = self._knob_streak + 1 if edit.op in self.KNOB_OPS else 0
        who = "you" if edit.source in ("LLM", "jevtypesafe") else "the built-in performer"
        self.recent.append(f"- {edit.describe()} ({who}, {self.state.phase})"
                           + (f": {edit.reason}" if edit.reason else ""))

    def new_track(self, previous: Track | None) -> Track:
        self.recent.clear()
        self.line_ratings = {}
        self._knob_streak = 0
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
            piece = plan_piece(self.rng, prev_piece, style=self.style, target_minutes=LIVE_PIECE_MINUTES,
                               theme=self.theme)
        self._last_piece = piece
        vocal = self._choose_acapella(piece)
        self.track_narration = getattr(piece, "narration", "") or ""
        self.track_mood = piece.mood
        from .theory import Key as _Key, progression_named, progressions_for

        mode = _Key.parse(piece.key).mode
        main = progression_named(piece.progression, mode) or progressions_for(mode)[0]
        degrees = list(main.degrees)
        # The harmony moves with the arc: the main progression, its own for
        # the breakdown, another for the peak (from the Director's plan).
        progressions = {"main": {"name": main.name, "degrees": degrees}}
        for part, section in (("breakdown", "breakdown"), ("peak", "chorus")):
            name = piece.section_progressions.get(section)
            other = progression_named(name, mode) if name and name != main.name else None
            if other is not None:
                progressions[part] = {"name": other.name, "degrees": list(other.degrees)}
        # (A cue naming a surprise isn't a move: the surprise plays itself.)
        cues = {p: [c for c in cs if not (isinstance(c, dict) and "surprise" in c)]
                for p, cs in (piece.cues or {}).items()}
        cues = {p: cs for p, cs in cues.items() if cs} or builtin_cues(piece)
        if vocal or any(b.role == "vocal" for b in blocks_for(self.style)):
            cues = with_vocal_cue(cues)
        plan = fit_live_plan(live_plan(piece), piece.cps, getattr(self.director, "target_minutes", LIVE_PIECE_MINUTES))
        fake_step = -1
        if piece.surprise == "fake_drop":
            # 8 bars of peak, back into a short breakdown, then the real drop.
            at = next((i for i, (p, _) in enumerate(plan) if p == "groove2"), None)
            if at is not None:
                plan[at:at] = [["groove2", 8], ["breakdown", 8], ["rebuild", 16]]
                fake_step = at + 1
        if piece.surprise == "tempo_lift":
            cues["rebuild"] = [{"do": "tempo", "to": piece.bpm + 2, "say": "Faster: +2 BPM"},
                               {"do": "tempo", "to": piece.bpm + 4, "say": "Faster still: +4 BPM"}] + cues.get("rebuild", [])
        self.state = SetState(tracks_played=self.state.tracks_played + (1 if previous else 0),
                              target_lines=self.rng.randint(max(3, self.settings["max_lines"] - 1),
                                                            self.settings["max_lines"]),
                              plan=plan, phase_start=self.bar(), fake_step=fake_step,
                              cues=cues, cue_queue=list(cues.get("build", [])))
        self.log("🧭 Arc plan: " + arc_summary(self.state.plan))
        if piece.subgenre:
            self.log(f"🎛  Subgenre: {piece.subgenre} {self.style} ({subgenre(self.style, piece.subgenre).get('about', '')})")
        if piece.sounds:
            self.log("🎹 Sounds: " + sounds_summary(piece.sounds))
        if piece.hook:
            self.log(f"🎣 Hook ({piece.hook['role']}): {piece.hook['pattern']}")
        if piece.cues:
            self.log("🎬 Cues: " + " | ".join(f"{PHASE_LABELS.get(p, p)}: " + "; ".join(
                c.get("say", "") if isinstance(c, dict) else str(c) for c in cues) for p, cues in piece.cues.items()))
        if piece.parts:
            self.log("✏️  Sketch: " + " · ".join(f"{r} {p['pattern']}" for r, p in piece.parts.items()))
        if piece.motion:
            self.log("🎛  Moving: " + " · ".join(f"{r}.{c} {v}" for r, cs in piece.motion.items() for c, v in cs.items()))
        from .livecode import DEFAULT_VOICING, TRANCE_VOICINGS
        from .plan import VOICED_STYLES

        voicing = DEFAULT_VOICING
        if self.style in VOICED_STYLES:
            # The Director's choice, or another of the trance shapes than last time.
            name = piece.voicing if piece.voicing in TRANCE_VOICINGS else self.rng.choice(
                [n for n in TRANCE_VOICINGS if TRANCE_VOICINGS[n] != getattr(previous, "voicing", "")]
                or list(TRANCE_VOICINGS))
            voicing = TRANCE_VOICINGS[name]
            self.log(f"🎹 Voicing: {name} ({voicing})")
        self._track_surprise = piece.surprise
        return Track(title=piece.title, style=self.style, key=piece.key, bpm=piece.bpm, voicing=voicing,
                     degrees=degrees, bars_per_chord=piece.cycles_per_chord, progressions=progressions,
                     sounds=dict(piece.sounds), hook=dict(piece.hook), idea=piece.idea,
                     parts=dict(piece.parts), motion=dict(piece.motion), subgenre=piece.subgenre,
                     kit=dict(subgenre(self.style, piece.subgenre).get("kit", {})), surprise=piece.surprise,
                     vocal=vocal or {})

    def _choose_acapella(self, piece) -> dict | None:
        """Now and then a licensed acapella (music/vocals.py) for the track:
        the track takes its tempo and the nearest key it can be sung in, and
        loses a surprise that would move either."""
        from . import vocals
        from .plan import STYLES

        sub = subgenre(self.style, piece.subgenre)
        style_cfg = STYLES.get(self.style, {})
        vocal = vocals.choose(self.rng, self.style, piece.key, tuple(sub.get("bpm") or style_cfg.get("bpm", (60, 180))),
                              list(sub.get("modes") or style_cfg.get("modes", [])), list(self._recent_vocals))
        if vocal is None:
            return None
        self._recent_vocals.append(vocal["name"])
        moved = [f"{piece.key} -> {vocal['key']}"] if vocal["key"] != piece.key else []
        moved += [f"{piece.bpm:g} -> {vocal['bpm']:g} BPM"] if vocal["bpm"] != piece.bpm else []
        piece.key, piece.bpm = vocal["key"], vocal["bpm"]
        if piece.surprise in ("tempo_lift", "key_lift"):
            self.log(f"[Performer] no {piece.surprise} this track: the acapella keeps its key and tempo")
            piece.surprise = ""
        self.log(f"🎤 Acapella: {vocal['name']} ({vocal['key']}, {vocal['bpm']:g} BPM, {vocal['cycles']} bars)"
                 + (f"; the track moves {', '.join(moved)}" if moved else ""))
        return vocal

    # Which progression plays in each phase of the arc.
    HARMONY_PARTS = {"build": "main", "groove": "main", "breakdown": "breakdown", "rebuild": "breakdown",
                     "groove2": "peak", "outro": "main"}

    # Share of the style's peak complexity each phase aims for.
    COMPLEXITY_SHARE = {"build": 0.5, "groove": 0.75, "breakdown": 0.4, "rebuild": 0.6, "groove2": 1.0, "outro": 0.3}

    def complexity_target(self) -> float:
        return self.settings.get("peak_complexity", 16) * self.COMPLEXITY_SHARE.get(self.state.phase, 0.75)

    def complexity_note(self, program: Program) -> str:
        """Where the music is against the target for this phase, and what to do about it."""
        now, target = complexity(program), self.complexity_target()
        if self.state.phase in ("breakdown", "outro"):
            advice = "strip back: fewer lines and variations" if now > target + 1 else "hold it sparse and moving"
        elif now < target - 2:
            advice = ("too plain for this phase: add variation to what plays - a wrapper, a knob that moves on "
                      "its own ((slow 16 $ range a b sine)), a phrase that changes bar to bar (<[...] [...]>), "
                      "an odd-length 16th cycle against the beat" + ("" if self.state.phase != "build"
                                                                     else ", or the next line"))
        elif now > target + 2:
            advice = "plenty going on: refine what plays rather than adding"
        else:
            advice = "about right: keep it moving"
        note = f"Complexity now {now:.1f}, about {target:.0f} suits this phase: {advice}."
        if self.must_change_music():
            note += (f"\nYour last {self._knob_streak} moves only turned knobs: this move must change the music "
                     "itself - notes or rhythm (set_pattern), a line in or out (add, mute, unmute, remove), or "
                     "a wrapper. Knob moves are refused this time.")
        return note

    def harmony_for_phase(self, track: Track) -> tuple[str, dict] | None:
        """(part, {"name", "degrees"}) for the current phase, or None when the
        track has no separate progression for it."""
        part = self.HARMONY_PARTS.get(self.state.phase, "main")
        chosen = track.progressions.get(part) or track.progressions.get("main")
        return (part, chosen) if chosen else None

    # -------------------------------------------------------------- phases

    def advance_phase(self, program: Program) -> None:
        self._advance_phase(program)

    def bar(self) -> float:
        """The current bar (Tidal cycle)."""
        if self.bar_now is not None:
            return self.bar_now()
        return float(self.state.edits_in_track * self.settings["edit_bars"])

    def phase_bars(self) -> float:
        """Bars played in the current phase."""
        return max(0.0, self.bar() - self.state.phase_start)

    def phase_bars_left(self) -> float:
        return max(0.0, self.step_bars() - self.phase_bars())

    def arc_plan(self) -> list:
        """The plan being played ([[phase, bars], ...]); a track without one
        (resumed from an old save, or a performer used on its own) gets the
        default arc."""
        if not self.state.plan:
            self.state.plan = [list(s) for s in DEFAULT_LIVE_PLAN]
            self.state.step = next((i for i, (p, _) in enumerate(self.state.plan) if p == self.state.phase), 0)
        return self.state.plan

    def step_bars(self) -> int:
        """Bars planned for the current step."""
        plan = self.arc_plan()
        return int(plan[min(self.state.step, len(plan) - 1)][1])

    def _advance_phase(self, program: Program) -> None:
        """Move to the plan's next step when this one is done: build once its
        lines are in (or its bars are over), rebuild once what the breakdown
        muted is back (or its bars are over), everything else after its
        bars. The outro is the last step."""
        st = self.state
        plan = self.arc_plan()
        if st.step + 1 >= len(plan):
            return
        before, bars = st.phase, self.step_bars()
        active = program.active_roles()
        over = self.phase_bars() >= bars
        names = {"groove2": "peak"}
        why = ""
        if before == "build" and len(active) >= st.target_lines:
            why = f"{len(active)} lines playing, the target for this track"
        elif before == "rebuild" and st.muted_for_breakdown and not any(
                program.lines.get(r) and program.lines[r].muted for r in st.muted_for_breakdown):
            why = "everything muted for the breakdown is back"
        elif over:
            why = f"{bars} bars of {names.get(before, before)}, as planned"
        if not why:
            return
        st.step += 1
        st.phase = plan[st.step][0]
        st.edits_in_phase = 0
        st.phase_start = self.bar()
        if st.phase == "breakdown":
            st.muted_for_breakdown = []          # each breakdown brings back what it muted
        st.cue_queue = list(st.cues.get(st.phase, []))      # the Director's instructions for this part
        if st.fake_step >= 0 and st.step == st.fake_step - 1:
            st.cue_queue = []                                # the fake peak: the real one's cues wait
        if st.step == st.fake_step and "fake_drop" not in st.surprise_done:
            self.surprise_fired("fake_drop")
        if st.phase == "rebuild" and self.surprise_due("tempo_lift"):
            self.surprise_fired("tempo_lift")
        if st.phase == "groove" and self.surprise_due("three_note_hook"):
            self.surprise_fired("three_note_hook")
        self.log(f"🧭 Arc: {before} → {st.phase} ({why})")
        runtime.ui_event("arc", before=before, after=st.phase, why=why)
        if self.on_arc is not None:
            self.on_arc(before, st.phase, why)

    def track_finished(self, program: Program) -> bool:
        """The outro is down to one line, or its planned bars are over (the
        engine fades out whatever still plays)."""
        st = self.state
        return st.phase == "outro" and st.edits_in_phase > 0 and (
            len(program.active_roles()) <= 1 or self.phase_bars() >= self.step_bars())

    # ---------------------------------------------------------------- edits

    def next_edit(self, program: Program) -> Edit:
        """Choose the next edit (LLM if available, built-in otherwise)."""
        self._advance_phase(program)
        self._bar_seconds = 1 / program.track.cps
        self._complexity_now = complexity(program)
        request = self.requests.take() if self.requests is not None else None
        if request is not None:
            return self._answer_request(program, request["text"])
        if self.state.cue_queue:
            # The Director's next instruction for this part of the track.
            return self._carry_out(program, self.state.cue_queue.pop(0))
        edit = None
        if self.model_id and self._skip_llm <= 0:
            started, late_before = time.monotonic(), self.stats["late"]
            if llm.resolve(self.model_id)[0] == "jevtypesafe" and llm.jev_style() == "decide":
                edit = self._jev_edit(program)
            elif self.judge:
                edit = self._judged_edit(program)
            else:
                edit = self._llm_edit(program)
            self._note_latency(time.monotonic() - started, late=self.stats["late"] > late_before,
                               answered=edit is not None and edit.source.startswith(("LLM", "jevtypesafe")))
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

    # ------------------------------------------------------------ requests

    def note_rating(self, rating: int, edit: str, role: str | None = None, line: str = "") -> None:
        """The host rated what is playing, or one line of it: keep it with the
        recent edits, so the model builds on what was liked and steers away
        from what wasn't. A 👎 line is the built-in performer's next one to
        change; a 👍 line is left alone while others need work."""
        thumb = "👍 liked" if rating > 0 else "👎 disliked"
        if role:
            self.line_ratings[role] = rating
            code = line if len(line) <= 160 else line[:157] + "..."
            advice = "keep it as it is" if rating > 0 else "change it (or take it out) at your next move"
            self.recent.append(f"- the host {thumb} the {role} line ({code}): {advice}")
        else:
            self.recent.append(f"- the host {thumb} what played after: {edit}" if edit
                               else f"- the host {thumb} what is playing")

    def request_waiting(self) -> bool:
        return self.requests is not None and self.requests.pending()

    def _answer_request(self, program: Program, text: str, who: str = "host") -> Edit:
        """The host asked for something, or the Director gave an instruction
        (who="director"): turn it into the move that best carries it out (the
        model if there is one, else simple rules)."""
        director = who == "director"
        self.log(f"🎬 Director: {text}" if director else f"📨 Request from the host: {text!r}")
        runtime.append_thought(f"🎬 director: {text}" if director else f"📨 request: {text}")
        edit = None
        if self.model_id and llm.resolve(self.model_id)[0] != "jevtypesafe":
            started, late_before = time.monotonic(), self.stats["late"]
            edit = self._llm_edit(program, request=text, who=who)
            self._note_latency(time.monotonic() - started, late=self.stats["late"] > late_before,
                               answered=edit is not None)
        if edit is None:
            edit = self._builtin_request(program, text)
        edit.request = text
        if edit.source.startswith("LLM"):
            self.stats["llm"] += 1
            edit.source = "LLM, director" if director else "LLM, request"
        self.state.edits_in_phase += 1
        self.state.edits_in_track += 1
        if edit.op == "mute" and self.state.phase == "breakdown":
            self.state.muted_for_breakdown.append(edit.role)
        self.log(f"{'🎬' if director else '📨'} Performer: {edit.review or edit.reason}")
        runtime.ui_event("cue" if director else "request", text=text, review=edit.review or edit.reason,
                         edit=edit.describe(), source=edit.source)
        return edit

    def _carry_out(self, program: Program, cue) -> Edit:
        """The Director's instruction as code. A precise cue (which part,
        block, sound, pattern, knob value) is played exactly as given; what
        it leaves open (a new melody "busier, in 16ths") and a plain sentence
        go to the Performer (the model, or simple rules) to write."""
        cue = cue if isinstance(cue, dict) else {"say": str(cue)}
        edit = self._cue_edit(program, cue)
        if edit is None:
            text = cue.get("say", "")
            if cue.get("part") and cue.get("do"):
                text += f" (the {cue['part']} line)"
            return self._answer_request(program, text, who="director")
        edit.source, edit.request, edit.review = "director", cue["say"], cue["say"]
        self.state.edits_in_phase += 1
        self.state.edits_in_track += 1
        if edit.op == "mute" and self.state.phase == "breakdown":
            self.state.muted_for_breakdown.append(edit.role)
        self.log(f"🎬 Director: {cue['say']}")
        runtime.append_thought(f"🎬 director: {cue['say']}")
        runtime.ui_event("cue", text=cue["say"], review="", edit=edit.describe(), source="director")
        return edit

    def _cue_edit(self, program: Program, cue: dict) -> Edit | None:
        """The edit a precise cue asks for, if it can be played as it stands;
        None leaves it to the Performer."""
        do, role = cue.get("do"), cue.get("part")
        line = program.lines.get(role) if role else None
        edit = None
        if do == "start" and line is not None and line.muted or do == "bring back" and line is not None and line.muted:
            edit = Edit("unmute", role=role)
        elif do == "start" and line is None:
            block = find_block(self.style, cue.get("block", ""), program.track) or next(
                iter(track_blocks(self.style, program.track.subgenre, program.track.sounds, role, program.track)), None)
            if block is None or self.melodic_problem(program, role) \
                    or len(program.active_roles()) >= self.settings["max_lines"]:
                return None
            track = program.track
            sounds = {**track.sounds, role: cue["sound"]} if cue.get("sound") else track.sounds
            new = block.make(self.rng, sounds, track.hook, track.parts, track.motion, voicing=track.voicing)
            slot = cue.get("slot") or (block.sketch_slot() or (None,))[0]
            if cue.get("pattern") and slot in new.patterns:
                new.patterns[slot] = cue["pattern"]
            edit = Edit("add", role=role, line=new)
        elif do == "stop" and line is not None and not line.muted and len(program.active_roles()) > 1:
            edit = Edit("mute", role=role)
        elif do == "pattern" and line is not None and cue.get("pattern"):
            block = find_block(self.style, line.block, program.track)
            slot = cue.get("slot") or ((block.sketch_slot() or (None,))[0] if block else None)
            if slot in line.patterns:
                edit = Edit("set_pattern", role=role, name=slot, value=cue["pattern"])
        elif do == "knob" and line is not None:
            to = cue["to"]
            value = f"(slow {to[2]} $ range {to[0]:g} {to[1]:g} sine)" if isinstance(to, list) else f"{to:g}"
            op = "set_fx" if cue["knob"] in FX_RANGES and cue["knob"] in ROLE_FX.get(role, ()) else "set_pf"
            edit = Edit(op, role=role, name=cue["knob"], value=value)
        elif do == "sound" and line is not None:
            edit = Edit("set_sound", role=role, value=cue["sound"])
        elif do == "wrap" and line is not None:
            edit = Edit("wrap", role=role, name=cue["wrap"])
        elif do == "tempo":
            edit = Edit("tempo", value=f"{cue['to']:g}")
        if edit is None:
            return None
        try:
            apply_edit(program, edit, self.settings["max_lines"])
        except EditError as e:
            self.log(f"[Performer] the Director's cue {cue['say']!r} can't be played as given ({e})")
            return None
        return edit

    def _builtin_request(self, program: Program, text: str) -> Edit:
        """Keyword rules for when there's no model: more/less/drop/bring back
        a part, darker/brighter, faster/slower. Anything else: say so."""
        words = text.lower()
        roles = {"bass": "bass", "sub": "bass", "kick": "beat", "beat": "beat", "drum": "beat",
                 "hat": "perc", "perc": "perc", "clap": "perc", "lead": "lead", "melod": "lead",
                 "hook": "lead", "chord": "chords", "pad": "chords", "string": "chords", "keys": "keys",
                 "piano": "keys", "arp": "arp", "acid": "acid", "303": "acid", "texture": "texture",
                 "noise": "texture", "vinyl": "texture", "vocal": "vocal", "vox": "vocal", "voice": "vocal",
                 "sing": "vocal", "acapella": "vocal"}
        role = next((r for w, r in roles.items() if w in words), None)
        if role == "perc" and "perc" not in program.lines and "beat" in program.lines and "clap" in words:
            role = "beat"                         # styles that keep the clap on the beat line
        active = program.active_roles()
        line = program.lines.get(role) if role else None
        edit = None
        starts = ("start", "bring in", "introduce", "add ", "enter", "let the")
        changes = ("change", "new ", "vary", "switch", "different", "another")
        if role and line is not None and line.muted and ("back" in words or any(w in words for w in starts)):
            edit = Edit("unmute", role=role, review=f"bringing the {role} back in")
        elif role and line is None and any(w in words for w in starts):
            if any(b.role == role for b in blocks_for(self.style, track=program.track)) \
                    and len(active) < self.settings["max_lines"] \
                    and not self.melodic_problem(program, role):
                edit = self._add(program, role)
                edit.review = f"starting the {role}"
        elif role and line is not None and not line.muted and any(w in words for w in changes):
            edit = self._pattern_edit(role, line)
            if edit is not None:
                edit.review = f"a new {edit.name} for the {role}: {edit.value}"
        elif role and line is not None and not line.muted and "take out" in words and len(active) > 1:
            edit = Edit("mute", role=role, review=f"taking the {role} out")
        if edit is not None:
            pass
        elif any(w in words for w in ("faster", "speed up", "tempo up", "more energy")):
            edit = Edit("tempo", value=str(program.track.bpm + 4), review="a little faster, +4 BPM")
        elif any(w in words for w in ("slower", "slow down", "tempo down", "calmer")):
            edit = Edit("tempo", value=str(program.track.bpm - 4), review="a little slower, -4 BPM")
        elif role and any(w in words for w in ("bring back", "unmute")) and line is not None and line.muted:
            edit = Edit("unmute", role=role, review=f"bringing the {role} back in")
        elif role and any(w in words for w in ("drop", "mute", "stop", "no ", "without", "remove")):
            if line is not None and not line.muted and len(active) > 1:
                edit = Edit("mute", role=role, review=f"dropping the {role} for now")
        elif any(w in words for w in ("darker", "warmer", "muffled", "brighter", "open", "sharper")):
            target = role if role in program.lines else next((r for r in ("lead", "acid", "arp", "chords")
                                                               if r in active), None)
            if target:
                fx = dict(program.lines[target].fx)
                base = float(fx["lpf"]) if _NUM_RE.match(fx.get("lpf", "")) else 3000.0
                darker = any(w in words for w in ("darker", "warmer", "muffled"))
                value = max(300, min(8000, round(base * (0.6 if darker else 1.5))))
                edit = Edit("set_fx", role=target, name="lpf", value=str(value),
                            review=f"{'darker' if darker else 'brighter'}: the {target} filter to {value} Hz")
        elif role and any(w in words for w in ("more", "louder", "up", "bigger", "add", "need")):
            if line is None or line.muted:
                blocks = blocks_for(self.style, role, program.track)
                if blocks and len(active) < self.settings["max_lines"]:
                    edit = self._add(program, role)
                    edit.review = f"bringing in the {role}"
                elif line is not None and line.muted and len(active) < self.settings["max_lines"]:
                    edit = Edit("unmute", role=role, review=f"bringing the {role} back in")
            else:
                from .livecode import gain_range

                lo, hi = gain_range(line)
                edit = Edit("set_fx", role=role, name="gain", value=f"{hi:g}",
                            review=f"the {role} up to the top of its level window (+3 dB at most, to keep the mix)")
        elif role and any(w in words for w in ("less", "quieter", "softer", "down", "lower")) and line is not None:
            from .livecode import gain_range

            lo, hi = gain_range(line)
            edit = Edit("set_fx", role=role, name="gain", value=f"{lo:g}", review=f"the {role} down a little")
        if edit is not None:
            try:
                apply_edit(program, edit, self.settings["max_lines"])
                edit.reason = edit.reason or edit.review
                edit.source = "built-in"
                return edit
            except EditError as e:
                why = str(e)
        else:
            why = "the built-in performer only understands simple requests (more/less/drop a part, darker, faster)"
        fallback = self._builtin_edit(program)
        fallback.source = "built-in"
        fallback.review = f"couldn't do that as asked ({why}); making a regular move instead"
        return fallback

    def _addable(self, program: Program) -> list[Block]:
        """Blocks for the parts not in the program yet (the subgenre's own, where it has them);
        the vocal only from the breakdown on."""
        roles = dict.fromkeys(b.role for b in blocks_for(self.style, track=program.track)
                              if b.role not in program.lines
                              and (b.role != "vocal" or self.state.phase in VOCAL_PHASES))
        return [b for r in roles for b in subgenre_blocks(self.style, program.track.subgenre, r, program.track)]

    def _block_for(self, role: str, track=None) -> Block:
        return self.rng.choice(track_blocks(self.style, track.subgenre if track else "",
                                            track.sounds if track else None, role, track))

    def _add(self, program: Program, role: str) -> Edit:
        block = self._block_for(role, program.track)
        return Edit("add", role=role, line=program.track.make(block, self.rng),
                    reason=f"bring in the {block.name.replace('_', ' ')}")

    def surprise_fired(self, name: str, detail: str = "") -> None:
        """A surprise just happened: once per track, logged and shown."""
        from .surprises import SURPRISES

        self.state.surprise_done.append(name)
        label, about, _ = SURPRISES[name]
        self.log(f"✨ Surprise: {label} ({about}){' ' + detail if detail else ''}")
        runtime.append_thought(f"✨ surprise: {label}")
        runtime.ui_event("surprise", name=name, label=label, about=about)
        if self.on_surprise is not None:
            self.on_surprise(name)

    def surprise_due(self, name: str) -> bool:
        return self.state.surprise_done.count(name) == 0 and getattr(self, "_track_surprise", "") == name

    MELODIC_ROLES = ("acid", "arp", "lead", "keys")

    def melodic_problem(self, program: Program, role: str) -> str | None:
        """Why another melodic line (acid, arp, lead, keys) can't come in: the
        style allows only so many above the bass and chords at once."""
        limit = self.settings.get("max_melodic")
        if not limit or role not in self.MELODIC_ROLES:
            return None
        playing = [r for r in program.active_roles() if r in self.MELODIC_ROLES and r != role]
        if len(playing) >= limit:
            return (f"{', '.join(playing)} already carry the melody ({limit} at most in {self.style}): "
                    f"mute or remove one first")
        return None

    def _missing_roles(self, program: Program) -> list[str]:
        """Roles not playing yet, in the order a track builds up; the part
        carrying the Director's hook comes first, so the hook is always heard.
        Melodic roles over the style's limit are left out."""
        available = [b.role for b in blocks_for(self.style, track=program.track)
                     if b.role != "vocal" or self.state.phase in VOCAL_PHASES]
        order = [r for r in ROLE_ORDER.get(self.style, list(ROLE_CHANNELS)) if r in available]
        # Every part the style has blocks for can come in, even one the order
        # forgot (hardcore once had no bass in it, so no track ever had one).
        order += [r for r in dict.fromkeys(available) if r not in order]
        # The style's order, but no decoration (texture) before a part the
        # Director sketched, so a full track never leaves a sketched part out;
        # the hook's part first of all.
        sketched = set(program.track.parts or {})
        if sketched & set(order) and "texture" in order and "texture" not in sketched:
            order.remove("texture")
            last = max(i for i, r in enumerate(order) if r in sketched)
            order.insert(last + 1, "texture")
        hook_role = (program.track.hook or {}).get("role")
        if hook_role in order:
            order.remove(hook_role)
            order.insert(0, hook_role)
        return [r for r in order if r not in program.lines and not self.melodic_problem(program, r)]

    def _builtin_edit(self, program: Program) -> Edit:
        st, rng = self.state, self.rng
        active = program.active_roles()
        if st.phase == "build":
            if not program.lines:
                opener = rng.choice(self.settings["openers"])
                return self._add(program, opener[0])
            missing = self._missing_roles(program)
            # Build up briskly: the track should reach its full, layered
            # groove within a few edits, not after ten minutes of tweaks.
            if missing and (len(active) < 3 or rng.random() < 0.85):
                return self._add(program, missing[0])
            return self._tweak(program)
        if st.phase == "breakdown":
            for role in ("beat", "bass"):
                if role in active and len(active) > 1 and role not in st.muted_for_breakdown:
                    return Edit("mute", role=role, reason=f"drop the {role} for a breakdown")
            if "vocal" not in program.lines and blocks_for(self.style, "vocal", program.track) \
                    and len(active) < self.settings["max_lines"]:
                return self._add(program, "vocal")
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
        if missing and len(active) < self.settings["max_lines"] and rng.random() < 0.35:
            return self._add(program, missing[0])
        if rng.random() < 0.05 and not program.track.vocal:
            delta = rng.choice([-2, -1, 1, 2])
            return Edit("tempo", value=str(program.track.bpm + delta), reason="nudge the tempo")
        return self._tweak(program)

    def _tweak(self, program: Program, calm: bool = False) -> Edit:
        """One small revision to a playing line."""
        rng = self.rng
        roles = program.active_roles() or list(program.lines)
        # The host's thumbs: a 👎 line is the one to change now (once);
        # 👍 lines are left alone while there are others to work on.
        disliked = [r for r in roles if self.line_ratings.get(r, 0) < 0]
        if disliked:
            role = disliked[0]
            self.line_ratings.pop(role)
            line = program.lines[role]
            for make in (lambda: self._pattern_edit(role, line) if line.patterns else None,
                         lambda: self._fx_edit(role, line, calm), lambda: self._wrap_edit(role, line)):
                edit = make()
                if edit is None:
                    continue
                try:
                    apply_edit(program, edit, self.settings["max_lines"])
                    edit.reason = f"the host didn't like the {role}: {edit.reason}" if edit.reason else edit.reason
                    return edit
                except EditError:
                    continue
        roles = [r for r in roles if self.line_ratings.get(r, 0) <= 0] or roles
        if self.must_change_music():
            # Enough knob turns: change notes or rhythm, or add a variation.
            for _ in range(20):
                role = rng.choice(roles)
                line = program.lines[role]
                edit = (self._pattern_edit(role, line) if line.patterns and rng.random() < 0.75
                        else self._wrap_edit(role, line))
                if edit is None:
                    continue
                try:
                    apply_edit(program, edit, self.settings["max_lines"])
                    return edit
                except EditError:
                    continue
        # Stay with the knob being ridden about half the time, so a ride
        # builds over several edits instead of scattering across lines.
        last = self._last_ride
        if last and last in program.active_roles() and rng.random() < 0.5:
            edit = self._ride_edit(last, program.lines[last], calm)
            if edit is not None:
                try:
                    apply_edit(program, edit, self.settings["max_lines"])
                    return edit
                except EditError:
                    pass
        for _ in range(12):
            role = rng.choice(roles)
            line = program.lines[role]
            # Small, audible, cumulative: one note or step, or a knob ridden a
            # little further; a new wrapper now and then.
            kind = rng.choice(["pattern", "pattern", "pattern", "pattern", "ride", "ride", "ride", "wrap", "fx"])
            if not calm and complexity(program) < self.complexity_target() - 2 and rng.random() < 0.85:
                # Too plain for this phase: layer variation onto what plays.
                kind = rng.choice(["wrap", "motion"])
            edit = None
            if kind == "motion":
                edit = self._motion_edit(role, line)
            elif kind == "ride":
                edit = self._ride_edit(role, line, calm)
            elif kind == "pattern" and line.patterns:
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
        # Small first: one step on or off (drums), one note or rest (melodies);
        # a whole new pattern only now and then.
        if line.drums:
            small = self._drum_step(current)
            if small and (not variants or rng.random() < 0.9):
                return Edit("set_pattern", role=role, name=slot, value=small,
                            reason=f"one {'hit more' if small.count('~') < current.count('~') else 'hit less'} "
                                   f"in the {role} {slot}")
        elif block and block.pool and rng.random() < 0.3:
            # (No note more once the line is at its budget: less is more.)
            budget = line.max_notes or NOTE_BUDGET.get(role, 0)
            full = bool(budget) and notes_per_bar(line.body, slot, current) >= budget
            small = self._melodic_step(current, block.pool, add=not full)
            if small:
                return Edit("set_pattern", role=role, name=slot, value=small,
                            reason=f"{'a rest' if small.count('~') > current.count('~') else 'one more note'} "
                                   f"in the {role}" + ("" if slot == role else f" {slot}"))
        # Notes only: not the counts after * ! / @ (e.g. the 16 in "0*16").
        numbers = [m for m in re.finditer(r"(?<![*!/@%\d.])-?\d+", current)]
        if not line.drums and numbers and block and block.pool and (not variants or rng.random() < 0.9):
            m = rng.choice(numbers)
            new_num = str(rng.choice([n for n in block.pool if str(n) != m.group()] or block.pool))
            value = current[:m.start()] + new_num + current[m.end():]
            return Edit("set_pattern", role=role, name=slot, value=value,
                        reason=f"change one note of the {role}" + ("" if slot == role else f" {slot}"))
        if variants:
            return Edit("set_pattern", role=role, name=slot, value=rng.choice(variants),
                        reason=f"new {slot} pattern on the {role}")
        return None

    def _drum_step(self, pattern: str) -> str | None:
        """Toggle one step of a flat drum layer: a rest becomes a hit (with a
        sound already in that pattern) or a hit a rest."""
        layers = pattern.split(",")
        k = self.rng.randrange(len(layers))
        tokens = layers[k].split()
        simple = [i for i, t in enumerate(tokens) if re.fullmatch(r"~|[a-z0-9]+(:\d+)?", t)]
        sounds = [t for t in tokens if t != "~" and re.fullmatch(r"[a-z0-9]+(:\d+)?", t)]
        if len(tokens) < 4 or not simple or not sounds:
            return None
        i = self.rng.choice(simple)
        tokens[i] = self.rng.choice(sounds) if tokens[i] == "~" else "~"
        if all(t == "~" for t in tokens):
            return None
        layers[k] = (" " if layers[k].startswith(" ") else "") + " ".join(tokens)
        return ",".join(layers)

    def _melodic_step(self, pattern: str, pool: list[int], add: bool = True) -> str | None:
        """In a flat melody: one note becomes a rest, or a rest a note (a
        chord tone, so it stays in harmony); only a rest when `add` is False."""
        tokens = pattern.split()
        if len(tokens) < 4 or not all(re.fullmatch(r"~|-?\d+", t) for t in tokens):
            return None
        rests = [i for i, t in enumerate(tokens) if t == "~"]
        notes = [i for i, t in enumerate(tokens) if t != "~"]
        chord_tones = [n for n in pool if n % 7 in (0, 2, 4, 6)] or [0]
        if add and rests and (len(notes) <= 2 or self.rng.random() < 0.5):
            tokens[self.rng.choice(rests)] = str(self.rng.choice(chord_tones))
        elif len(notes) > 2:
            tokens[self.rng.choice(notes)] = "~"
        else:
            return None
        return " ".join(tokens)

    def _ride_edit(self, role: str, line, calm: bool) -> Edit | None:
        """Ride a knob a little further: the acid, else the line's filter.
        The direction holds across edits (like a hand on the knob) and turns
        at the end of its range."""
        pfs = dict(line.pfs)
        if "acid" in pfs:
            name, lo, hi, current = "acid", 0.2, 0.9, pfs["acid"]
            step = self.rng.uniform(0.03, 0.06)
        else:
            fx = dict(line.fx)
            if "lpf" not in ROLE_FX.get(role, ["lpf"]):
                return None
            name, lo, hi, current = "lpf", 400.0, 8000.0, fx.get("lpf", "")
            step = None
        base = float(current) if _NUM_RE.match(current or "") else (lo + hi) / 2
        key = (role, name)
        direction = self._rides.get(key) or self.rng.choice([-1, 1])
        if calm:
            direction = -1
        if (direction > 0 and base >= hi * 0.97) or (direction < 0 and base <= lo * 1.03):
            direction = -direction                      # the end of the range: turn
        self._rides[key] = direction
        self._last_ride = role
        if name == "acid":
            value = round(min(hi, max(lo, base + direction * step)), 3)
            return Edit("set_pf", role=role, name="acid", value=f"{value:g}",
                        reason=f"ride the acid {'open' if direction > 0 else 'closed'} a little")
        factor = self.rng.uniform(1.06, 1.15)
        value = round(min(hi, max(lo, base * factor if direction > 0 else base / factor)))
        return Edit("set_fx", role=role, name="lpf", value=str(value),
                    reason=f"ride the {role} filter {'open' if direction > 0 else 'down'} a little")

    def _motion_edit(self, role: str, line) -> Edit | None:
        """Set a knob moving on its own: a slow sine around where it is (the
        filter by default), so the line keeps changing between edits."""
        rng = self.rng
        static = [(n, v, "set_pf") for n, v in line.pfs if n in PF_RANGES and re.match(r"^[\d.]+$", str(v))]
        static += [(n, v, "set_fx") for n, v in line.fx
                   if n in ROLE_FX.get(role, ()) and n not in ("gain", "legato", "pan")
                   and re.match(r"^[\d.]+$", str(v))]
        if not static and "lpf" in ROLE_FX.get(role, ()) and not any(n == "lpf" for n, _ in line.fx):
            static = [("lpf", "3000", "set_fx")]
        if not static:
            return None
        name, value, op = rng.choice(static)
        lo, hi = PF_RANGES.get(name) if op == "set_pf" else FX_RANGES[name]
        base, span = float(value), (hi - lo) * 0.2
        if name in ("lpf", "hpf"):
            a, b = max(lo, base * 0.5), min(hi, base * 1.4)
        else:
            a, b = max(lo, base - span), min(hi, base + span)
        digits = 0 if hi > 100 else 2
        value = f"(slow {rng.choice([8, 16, 32])} $ range {round(a, digits):g} {round(b, digits):g} sine)"
        return Edit(op, role=role, name=name, value=value, reason=f"set the {role} {name} moving on its own")

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
            if rng.random() < 0.1:
                a, b = round(max(lo, base * 0.4)), round(min(hi, base * 1.1))
                value = f"(slow {rng.choice([4, 8, 16])} $ range {a} {b} sine)"
                return Edit("set_fx", role=role, name="lpf", value=value, reason=f"let the {role} filter breathe")
            factor = rng.uniform(0.75, 0.92) if calm else rng.choice([rng.uniform(0.85, 0.94), rng.uniform(1.06, 1.18)])
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
        # Knobs only: a control holding a rhythm (the acid's accent pattern)
        # isn't a knob to nudge.
        knobs = [(n, v) for n, v in line.pfs if not v.startswith('"')]
        if not knobs:
            return None
        name, current = self.rng.choice(knobs)
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
        # Keep variations: take one off mostly when a line already carries two.
        if own and self.rng.random() < (0.6 if len(own) >= 2 else 0.2):
            w = self.rng.choice(own)
            return Edit("unwrap", role=role, name=w, reason=f"take off `{w}`")
        options = [w for w in allowed_wrappers(line, self.style) if w not in line.wrappers]
        if not options:
            return None
        w = self.rng.choice(options)
        return Edit("wrap", role=role, name=w, reason=f"try `{w}` on the {role}")

    # ------------------------------------------------------------------ LLM

    def _note_latency(self, seconds: float, late: bool, answered: bool) -> None:
        """Keep a smoothed estimate of how long the model takes. A late
        reply only says it takes longer than the time it had, so the
        estimate grows until the build leaves it enough."""
        if late:
            self.latency = max(self.latency or 0.0, seconds * 1.5)
        elif answered:
            self.latency = seconds if self.latency is None else 0.7 * self.latency + 0.3 * seconds

    def edit_bars_now(self) -> int:
        """Bars until the next edit: short while the track builds up (a new
        line every YS_BUILD_BARS bars, or as many more as the model needs to
        answer in time, so the build stays the AI's), then the style's edit
        interval."""
        if self.state.phase == "build" or self.request_waiting():
            # (A host's request is answered at the same quick pace.)
            bars = config.BUILD_BARS
            if self.model_id and self.latency and self._bar_seconds:
                # The model's reply must fit the deadline (85% of the window);
                # with a judge, the ideas get about 70% of that.
                need = self.latency / (0.85 * (0.7 if self.judge else 1.0)) + 0.5
                bars = max(bars, math.ceil(need / self._bar_seconds))
            return max(1, min(self.settings["edit_bars"], bars))
        if (self.state.phase in ("groove", "groove2") and self._complexity_now is not None
                and self._complexity_now < self.complexity_target() - 4):
            # Far from the density this phase wants: edit twice as often, as
            # a live coder does while building a section up, but never faster
            # than the model can answer.
            bars = max(1, self.settings["edit_bars"] // 2)
            if self.model_id and self.latency and self._bar_seconds:
                bars = max(bars, math.ceil((self.latency / 0.85 + 0.5) / self._bar_seconds))
            return min(self.settings["edit_bars"], bars)
        return self.settings["edit_bars"]

    def _wait_seconds(self, program: Program) -> float:
        """How long to wait for the model: the edit window at the current
        tempo (with a little margin); outside the build, a local model gets
        at least YS_LOCAL_PERFORMER_WAIT, and the engine keeps playing and
        applies its late edit on the next bar. The build doesn't wait: a
        late reply gives way to the built-in performer, so lines keep
        coming in."""
        window = max(4.0, self.edit_bars_now() / program.track.cps * 0.85)
        if llm.resolve(self.model_id)[0] == "local" and self.state.phase != "build":
            return max(window, config.LOCAL_PERFORMER_WAIT_SECONDS)
        return window

    def _llm_edit(self, program: Program, request: str = "", who: str = "host") -> Edit | None:
        deadline = time.monotonic() + self._wait_seconds(program)
        # A host's request, then (after a rejected reply) a correction.
        extra = ((DIRECTOR_SECTION if who == "director" else REQUEST_SECTION).format(request=request)
                 if request else "")
        schema = self._edit_schema(program, review=bool(request))
        asks = (f"next edit for '{program.track.title}' ({self.state.phase}, "
                f"{len(program.active_roles())} line{'' if len(program.active_roles()) == 1 else 's'} playing)")
        if request:
            asks = f"the Director's instruction {request!r}" if who == "director" else f"the host's request {request!r}"
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            try:
                reply = self._ask_fitting(lambda: self._prompt(program) + extra,
                                          asks=asks if attempt == 1 else "corrected edit", show_full=self.show_llm,
                                          max_tokens=400, temperature=0.7, timeout=max(2.0, remaining),
                                          schema=schema)
                if time.monotonic() > deadline + 1:
                    raise llm.LLMError("reply came after the edit was due")
                data = llm.extract_json(reply)
                edit = self._edit_from_json(data, program)
                apply_edit(program, edit, self.settings["max_lines"])
                self._llm_failures = 0
                edit.source = "LLM"
                if request:
                    edit.request = request
                    edit.review = " ".join(str(data.get("review", "")).split())[:200] or edit.reason
                return edit
            except (EditError, ValueError, TypeError, KeyError) as e:
                # A usable-looking reply that broke a rule: if there's time,
                # say what was wrong and let the model correct itself once.
                if attempt == 1 and deadline - time.monotonic() > 3.0:
                    self.log(f"[Performer] edit rejected ({e}); asking the model to correct it")
                    extra += (f"\n\n## Your previous reply was rejected\n\n{reply.strip()[:400]}\n\n"
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
            if edit.op in ("add", "unmute") and self.melodic_problem(program, edit.role):
                return
            if edit.op in self.KNOB_OPS and self.must_change_music():
                return
            try:
                apply_edit(program, edit, self.settings["max_lines"])
            except EditError:
                return
            seen.add(edit.describe())
            out.append(edit)

        active = program.active_roles()
        if st.phase == "build" or (st.phase in ("groove", "groove2") and len(active) < self.settings["max_lines"]):
            missing = self._missing_roles(program)
            hook_role = (program.track.hook or {}).get("role")
            # Until the hook's part is in, it's the only part offered to add.
            count = 1 if hook_role in missing[:1] or st.phase != "build" else 3
            for role in missing[:count]:
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
            f"Phase: {self.state.phase} ({self.state.edits_in_phase} edits so far, about "
            f"{self.phase_bars_left():.0f} of its planned {self.step_bars()} bars left). "
            f"{PHASE_HELP[self.state.phase]}",
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
        reply = self._ask_fitting(lambda: self._prompt(program, proposals=3), proposals=3, asks=asks,
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
        options = [{"text": e.describe(), "from": "ai" if e.source == "LLM" else "random",
                    "p": probs[i] if i < len(probs) else None} for i, e in enumerate(pool)]
        runtime.ui_event("judge", compared=compared, confidence=info.get("confidence"),
                         chosen=pool.index(chosen), chosen_from="ai" if from_llm else "random", options=options)
        chosen.judgement = {"options": options, "chosen": pool.index(chosen), "compared": compared,
                            "confidence": info.get("confidence"), "why": why}
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

    def _ask_fitting(self, build_prompt, proposals: int = 0, **kwargs) -> str:
        """converse() with the fixed rules as the system prompt and the prompt
        from build_prompt(); if the model's context window is too small for
        it, switch to compact prompts for the rest of the run and retry once."""
        system = self.system_prompt(proposals)
        try:
            return llm.converse("Performer", self.model_id, system, build_prompt(), log=self.log, **kwargs)
        except llm.LLMError as e:
            if self._compact or "context window" not in str(e):
                raise
            self._compact = True
            self.log("[Performer] the prompt doesn't fit the model's context window; switching to compact "
                     "prompts (no glossary or example moves). Raise LLAMA_CTX_SIZE for the full prompt.")
            return llm.converse("Performer", self.model_id, self.system_prompt(proposals), build_prompt(),
                                log=self.log, **kwargs)

    _SYSTEM = "You are a live coder performing TidalCycles. Reply with JSON only."

    def _llm_failed(self, why: str, kind: str) -> None:
        self.stats[kind] += 1
        self.log(f"[Performer] {why}; built-in performer takes this one")
        if kind == "late" and self.state.phase == "build":
            return None     # the build's short window isn't the model's fault
        self._llm_failures += 1
        if self._llm_failures >= 3:
            self._skip_llm, self._llm_failures = 5, 0
            self.log("[Performer] too many LLM failures; the built-in performer plays the next 5 edits")
        return None

    def _edit_schema(self, program: Program, review: bool = False) -> dict:
        """JSON schema of every edit that is legal right now. A local server
        decodes under it, so only real block, role, slot, effect and wrapper
        names can come out; numbers are still range-checked afterwards."""
        text = {"type": "string", "maxLength": 80}
        note = {"type": "string", "maxLength": 160}

        def variant(op: str, **props) -> dict:
            # What it hears, then why, then the move: the model explains
            # itself before it commits.
            # Answering a host's request, the review comes first.
            head = {"review": note} if review else {}
            properties = {**head, "listening": note, "reason": note, "op": {"const": op}, **props,
                          "narration": {"type": "string", "maxLength": 160}}
            return {"type": "object", "properties": properties,
                    "required": [*head, "listening", "reason", "op", *props], "additionalProperties": False}

        options = []
        addable = [b.name for b in self._addable(program)]
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
            knobs_ok = not self.must_change_music()
            fx = [n for n in ROLE_FX.get(role, list(FX_RANGES)) if n in FX_RANGES]
            if fx and knobs_ok:
                options.append(variant("set_fx", role={"const": role}, name={"enum": fx}, value=text))
            controls = line_controls(line)
            if controls and knobs_ok:
                options.append(variant("set_pf", role={"const": role}, name={"enum": controls}, value=text))
            wraps = [w for w in allowed_wrappers(line, self.style) if w not in line.wrappers]
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
        if op == "set_fx" and name in PF_RANGES and name not in FX_RANGES:
            op = "set_pf"            # a synth control sent as an effect (e.g. acidres)
        if op in self.KNOB_OPS and self.must_change_music():
            raise EditError(f"{self._knob_streak} knob turns in a row: change notes, rhythm or lines now")
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
        if op == "unmute" and self.melodic_problem(program, role):
            raise EditError(self.melodic_problem(program, role))
        if op == "add":
            block = find_block(self.style, str(data.get("block", "")), program.track)
            if block is None:
                raise EditError(f"unknown block {data.get('block')!r}")
            edit.role = block.role
            edit.line = program.track.make(block, self.rng)
            problem = self.melodic_problem(program, edit.role)
            if problem:
                raise EditError(problem)
            hook_role = (program.track.hook or {}).get("role")
            if (hook_role and hook_role not in program.lines and edit.role != hook_role
                    and len(program.active_roles()) + 1 >= self.settings["max_lines"]):
                raise EditError(f"the last place is kept for the {hook_role}, which plays the track's hook")
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
            wraps_ok = [w for w in allowed_wrappers(line, self.style) if w not in line.wrappers]
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
            extra = [c for c in line_controls(line) if c not in dict(line.pfs)]
            if extra:
                entry["can_add_controls"] = extra     # set_pf adds them
            entry["can_set_fx"] = fx_ok
            if line.wrappers:
                entry["wrappers"] = line.wrappers
            if wraps_ok:
                entry["wrap_with"] = _wrapper_kind(line)
                wrap_kinds.add(_wrapper_kind(line))
            editable[role] = entry
            used_wrappers.update(line.wrappers)
            used_fx.update(fx_ok + line_controls(line))
        addable = {b.name: b.role for b in self._addable(program)}
        full = len(program.active_roles()) >= self.settings["max_lines"]
        # A full track can't take another line: say so, rather than offer blocks
        # a model (one that doesn't decode under the schema) will try to add.
        addable_text = (f"none right now: {len(program.active_roles())} lines play, the most this style allows "
                        "(remove or mute one first)") if full else json.dumps(addable)
        template = _load_prompt("performer.md")
        return template.format(
            # The system rules and the track (the same for a whole track) come
            # first: Claude caches them up to here.
            track=json.dumps(program.track.to_dict()), cache_break=CACHE_BREAK, phase=self.state.phase,
            phase_help=PHASE_HELP[self.state.phase], edits_in_phase=self.state.edits_in_phase,
            complexity=self.complexity_note(program),
            bars_left=round(self.phase_bars_left()), phase_bars=self.step_bars(),
            code="\n".join(program.code_lines()),
            lines="\n".join(f'"{r}": {json.dumps(e, separators=(",", ":"))}' for r, e in editable.items())
            or "(no lines yet)",
            addable=addable_text, max_lines=self.settings["max_lines"],
            examples=("(compact prompt)" if self._compact
                      else "\n".join(f"- {e}" for e in PHASE_EXAMPLES[self.state.phase])),
            recent="\n".join(self.recent) or "(none yet: this is the start of the track)",
            # The full glossary is in the fixed rules; a compact prompt (small
            # context windows) gets just the names and ranges in play here.
            wrapper_notes="" if not self._compact else (
                "\nWrappers: " + _wrapper_notes(wrap_kinds, used_wrappers, names_only=True, style=self.style).replace("\n", " ")
                + "\nRanges: " + ", ".join(f"{n} {lo:g}-{hi:g}" for n, (lo, hi) in {**FX_RANGES, **PF_RANGES}.items()
                                            if n in used_fx)),
        )

    def system_prompt(self, proposals: int = 0) -> str:
        """The fixed part of the Performer's prompt: the role, how to read the
        code, the operations, the taste and the reply format. It is the same
        from call to call (it changes only with the style or the reply mode),
        so it goes first, as the system prompt: a local server reuses it from
        the previous call instead of reading it again, and Claude caches it."""
        if self._compact:
            glossary = "(compact prompt: the wrappers and ranges in play are listed with the lines)"
        else:
            groups = _wrapper_kinds(self.style)
            offered = {w for ws in groups.values() for w in ws}
            glossary = ("Wrappers (a line takes those of its group, see `wrap_with`):\n"
                        + "\n".join(f"- {kind}: " + ", ".join(f"`{w}`" for w in ws) for kind, ws in groups.items())
                        + "\n\n" + "\n".join(f"- `{w}`: {note}" for w, note in sorted(WRAPPER_NOTES.items())
                                               if w in offered)
                        + "\n\nEffects and controls (a line takes those in its `can_set_fx` / `controls`):\n"
                        + "\n".join(f"- `{n}` [{lo:g}-{hi:g}]: {FX_NOTES[n]}"
                                     for n, (lo, hi) in {**FX_RANGES, **PF_RANGES}.items() if n in FX_NOTES))
        rules = _load_prompt("performer_rules.md").format(
            theme=self.theme or "chill, hypnotic, evolving", style=self.style,
            max_lines=self.settings["max_lines"], glossary=glossary,
            reply=REPLY_PROPOSALS.replace("N", str(proposals)) if proposals else REPLY_SINGLE)
        return f"{self._SYSTEM}\n\n{rules}"


def _wrapper_kinds(style: str | None) -> dict[str, list[str]]:
    """The wrapper groups for a style (club drums stay straight)."""
    return {"drum wrappers": CLUB_DRUM_WRAPPERS if style in CLUB_STYLES else DRUM_WRAPPERS,
            "bass wrappers": BASS_WRAPPERS, "melodic wrappers": MELODIC_WRAPPERS, "lead wrappers": LEAD_WRAPPERS,
            "vocal wrappers": VOCAL_WRAPPERS}


def _wrapper_kind(line) -> str:
    if line.role == "vocal":
        return "vocal wrappers"
    if line.drums:
        return "drum wrappers"
    return {"bass": "bass wrappers", "lead": "lead wrappers"}.get(line.role, "melodic wrappers")


def _wrapper_notes(kinds: set[str], extra: set[str], names_only: bool = False, style: str | None = None) -> str:
    """Each wrapper once, grouped by the kind of line it can go on."""
    out = []
    groups = _wrapper_kinds(style)
    for kind, names in groups.items():
        if kind in kinds:
            if names_only:
                out.append(f"{kind[0].upper()}{kind[1:]}: " + "; ".join(f"`{w}`" for w in names))
                continue
            out.append(f"{kind[0].upper()}{kind[1:]}:")
            out.extend(f"- `{w}`: {WRAPPER_NOTES[w]}" for w in names)
    shown = {w for k in kinds for w in groups[k]}
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
    "build": "Build the track up: add the next line almost every edit, until the full groove plays.",
    "groove": "The track is up: a full, layered groove. Keep it moving with small steps, one per "
              "edit: change one note, add or drop one hit, ride the acid knob or a filter a little "
              "further in the same direction. Now and then a bigger touch (a second bar with "
              "`<[...] [...]>`, an octave layer). Keep every line in harmony.",
    "breakdown": "Breakdown: mute the beat and/or bass, and make what's left more spacious. "
                 "If there's a vocal, bring it in now: a voice in the space the kick leaves.",
    "rebuild": "Bring the muted lines back (unmute), building tension.",
    "groove2": "Second peak: the richest part of the track. Bring in what was missing, intensify, "
               "then start easing off.",
    "outro": "Outro: remove or mute lines one by one; the track is ending.",
}


REQUEST_SECTION = """

## A request from the host

The host is listening and asked: "{request}"

Review it before you play it. Make the ONE edit that best gives the host
what they asked for, within the rules above (harmony, the kit, the gain
window: +3 dB at most) and the track's style. If it can't be done as asked,
or would hurt the music, make the closest good move instead and say so.
Start your reply with `"review"`: one sentence to the host, what you'll do
and why (or why not exactly as asked)."""


DIRECTOR_SECTION = """

## The Director's instruction

The Director, who plans this track, says: "{request}"

Carry it out: make the ONE edit that does what the Director says, in code,
within the rules above (harmony, the kit, the gain window: +3 dB at most).
If it can't be done exactly (the part isn't there, or it would break the
harmony), make the closest move that serves it and say so. Start your reply
with `"review"`: one short sentence, what you're doing in the code."""


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
        "Change ONE note of the melody so the phrase answers itself (\"0 ~ 2 4\" -> \"0 ~ 2 6\").",
        "Add or remove one hit in the drums (\"~ cp ~ cp\" -> \"~ cp ~ [cp cp]\").",
        "Ride the acid knob a little further (0.52 -> 0.56), and keep riding it the same way next time.",
        "Open a filter a touch (lpf 2400 -> 2700); a few edits later, a touch more.",
    ],
    "breakdown": [
        "Mute the beat first; the space it leaves is the point.",
        "Then bring in the vocal: the voice over the breakdown is the moment people remember.",
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
