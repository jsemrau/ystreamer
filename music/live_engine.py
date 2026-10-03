"""
The live-coding engine: performs the program forever.

Every `edit_bars` bars (on the bar boundary) the performer's next edit is
applied to the program and only the lines it changed are sent to Tidal, the
way a live coder re-evaluates one line. The screen shows the program with the
edited line marked and the performer's reason as a code comment.
"""

from __future__ import annotations

import concurrent.futures
import copy
import math
import threading
import time
from collections import deque
from typing import Callable

import config

from . import runtime
from .engine import Clock
from .livecode import (FX_RANGES, PF_RANGES, ROLE_CHANNELS, Edit, EditError, Program, apply_edit,
                       changed_statements, complexity)
from .mix import FADE_OUT_BARS
from .performer import Performer, SetState
from .plan import arc_summary, sounds_summary


class LiveEngine:
    def __init__(self, send: Callable[[list[str]], str | None], performer: Performer,
                 log: Callable[[str], None] = print, narrator=None, type_checker=None,
                 now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 max_tracks: int | None = None, session_log=None):
        self.send = send
        # Every change to the artefact, kept per session (music/sessionlog.py); optional.
        self.session_log = session_log
        self.performer = performer
        self.log = log
        self.narrator = narrator
        self.type_checker = type_checker
        self._sleep = sleep
        self.clock = Clock(0.35, now=now)
        self.stop_event = threading.Event()
        self.max_tracks = max_tracks
        self.program: Program | None = None
        self.edits = 0
        # The arranger's transitions (music/arranger.py): the phase they were
        # set for, and the device lines playing now (channel -> statement).
        self._arranged_phase: str | None = None
        self._devices: dict[int, str] = {}
        # The host's 👍 / 👎 from the stream page (host_requests.RatingQueue;
        # the orchestrator connects it), matched to the last change before it.
        self.ratings = None
        self._recent_edits: deque = deque(maxlen=32)      # (time, describe, source)
        self.groove_complexity: list[float] = []   # complexity after each groove edit, per track
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="performer")
        # The piano rolls on the stream page: what each line plays over the
        # next ROLL_CYCLES bars, asked of Tidal itself (type_checker.events),
        # in the background so the music never waits on it.
        self._roll_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rolls")
        self._rolls_until = 0.0
        self._rolls_pending = None
        # Narration lines are written in the background so the music never waits.
        self._voice_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="narrator")
        self._last_reason = ""
        performer.on_arc = self._on_arc
        performer.on_surprise = lambda name: self.program and self._record(
            "surprise", **self._track_info(self.program), surprise=name, phase=performer.state.phase)
        performer.bar_now = self.clock.cycle   # the arc moves on by bars

    # ------------------------------------------------------------ session log

    def _record(self, type_: str, **data) -> None:
        if self.session_log is not None:
            self.session_log.write(type_, **data)

    def _track_info(self, program: Program) -> dict:
        t = program.track
        return {"track": t.title, "style": t.style, "key": t.key, "bpm": t.bpm}

    def _record_track_start(self, program: Program, resumed: bool = False) -> None:
        self._stats_at_start = dict(self.performer.stats)
        self._record("track_start", **self._track_info(program), degrees=list(program.track.degrees),
                     bars_per_chord=program.track.bars_per_chord, mood=self.performer.track_mood,
                     director=self.performer.track_narration, resumed=resumed,
                     program=program.to_dict(), code=[program.statement(r) for r in program.lines])

    def _record_edit(self, before: Program, after: Program, edit: Edit) -> None:
        role = edit.role
        self._record("edit", **self._track_info(after), cycle=round(self.clock.cycle(), 2),
                     phase=self.performer.state.phase, edits_in_phase=self.performer.state.edits_in_phase,
                     source=edit.source or "built-in", op=edit.op, role=role, name=edit.name, value=edit.value,
                     block=edit.line.block if edit.line is not None else "",
                     describe=edit.describe(), listening=edit.listening, reason=edit.reason,
                     narration=edit.narration, judgement=edit.judgement or None,
                     request=edit.request or None, review=edit.review or None,
                     line_before=before.statement(role) if role in before.lines else None,
                     line_after=after.statement(role) if role in after.lines else None,
                     complexity_before=complexity(before), complexity_after=complexity(after),
                     lines_playing=len(after.active_roles()),
                     code=[after.statement(r) for r in after.lines])

    def _record_rejected(self, program: Program, edit: Edit, why: str) -> None:
        self._record("rejected", **self._track_info(program), phase=self.performer.state.phase,
                     source=edit.source or "built-in", describe=edit.describe(), op=edit.op, role=edit.role,
                     name=edit.name, value=edit.value, reason=edit.reason, listening=edit.listening, why=why)

    def _record_track_end(self, program: Program) -> None:
        values = self.groove_complexity
        start = getattr(self, "_stats_at_start", {})
        edits_by = {k: v - start.get(k, 0) for k, v in self.performer.stats.items() if v - start.get(k, 0)}
        self._record("track_end", **self._track_info(program), edits_by=edits_by,
                     groove_complexity=round(sum(values) / len(values), 2) if values else None,
                     peak_complexity=max(values) if values else None,
                     code=[program.statement(r) for r in program.lines], program=program.to_dict())

    def _surprise_turn(self, program: Program) -> Program:
        """The surprises that happen as the arc turns: the last peak a whole
        step up (key_lift), the breakdown down to one line (solo_breakdown)."""
        perf, phase = self.performer, self.performer.state.phase
        if phase == self._arranged_phase:
            return program
        new = None
        if phase == "groove2" and perf.surprise_due("key_lift"):
            last = max(i for i, (p, _) in enumerate(perf.arc_plan()) if p == "groove2")
            if perf.state.step == last:
                from .theory import Key, transpose_key

                new = copy.deepcopy(program)
                new.track.key = transpose_key(Key.parse(new.track.key), 2).name
                fired = ("key_lift", f"{program.track.key} -> {new.track.key}")
        elif phase == "breakdown" and perf.surprise_due("solo_breakdown"):
            hook = (program.track.hook or {}).get("role")
            solo = next((r for r in ("acid", hook) if r and r in program.active_roles()), None)
            if solo:
                new = copy.deepcopy(program)
                for role in new.active_roles():
                    if role != solo:
                        new.lines[role].muted = True
                        perf.state.muted_for_breakdown.append(role)
                fired = ("solo_breakdown", f"the {solo} alone")
        if new is None or not self._send([new.statement(r) for r in new.lines]):
            return program
        perf.surprise_fired(*fired)
        self._publish(new, None, "")
        return new

    def _fill_for_the_drop(self, program: Program) -> tuple[Program, list[str]]:
        """When the groove lands (club styles), the stack lands with it:
        parts still missing (the Director's sketched parts first) come in and
        everything muted comes back at once, instead of one part per edit. The
        first groove fills to two short of the style's limit, leaving room to
        grow; the drop into the peak fills it."""
        from . import arranger

        phase = self.performer.state.phase
        if (phase not in ("groove", "groove2") or phase == self._arranged_phase
                or program.track.style not in arranger.CLUB_STYLES):
            return program, []
        new = copy.deepcopy(program)
        limit = self.performer.settings["max_lines"] - (2 if phase == "groove" else 0)
        landing = []
        perf = self.performer
        # Surprises at the drop: what is held back, and the cues that bring it in.
        held: set[str] = set()
        if phase == "groove2" and perf.surprise_due("kickless_drop") and "beat" in new.lines:
            held = {"beat"}
        elif phase == "groove2" and perf.surprise_due("stripped_peak"):
            keep = {"beat", "bass", "perc", (new.track.hook or {}).get("role")}
            held = {r for r in new.lines if r not in keep}
        for role in held:
            if role in new.lines and not new.lines[role].muted:
                new.lines[role].muted = True
                landing.append(role)
        for role, line in new.lines.items():
            if (line.muted and role not in held and len(new.active_roles()) < limit
                    and not self.performer.melodic_problem(new, role)):
                line.muted = False
                landing.append(role)
        sketched = set(new.track.parts) | {(new.track.hook or {}).get("role")}
        missing = sorted(self.performer._missing_roles(new), key=lambda r: r not in sketched)
        for role in missing:
            if len(new.active_roles()) >= limit or held:
                break
            new.lines[role] = new.track.make(self.performer._block_for(role, new.track), self.performer.rng)
            landing.append(role)
        if not landing or not self._type_ok([new.statement(r) for r in landing]):
            return program, []
        if held:
            name = "kickless_drop" if held == {"beat"} else "stripped_peak"
            # They come back one move at a time, before anything else.
            back = [{"do": "bring back", "part": r, "say": f"Bring the {r} back"} for r in sorted(held)]
            perf.state.cue_queue[:0] = back
            perf.surprise_fired(name, f"(held back: {', '.join(sorted(held))})")
        self.log(f"🎚 The drop brings in: {', '.join(landing)}")
        return new, landing

    def _arrange(self, program: Program, landing: list[str] | None = None) -> None:
        """The arc moved on: play the transition (music/arranger.py). For a
        drop, one bar without kick and bass first, then it all lands, with
        the parts in `landing` (see _fill_for_the_drop)."""
        from . import arranger

        phase = self.performer.state.phase
        if phase == self._arranged_phase:
            return
        before, self._arranged_phase = self._arranged_phase, phase
        plan = arranger.plan_for(program.track.style, before, phase)
        statements = list(plan["statements"]) + ([arranger.impact()] if plan["impact"] else [])
        # Everything playing is sent again when parts land: with more lines,
        # the beds step back in the mix (program.expression).
        landed = [program.statement(r) for r in program.active_roles()] if landing else []
        if not self._type_ok(statements):
            self.log("[Arranger] the transition didn't type check; skipped")
            if landed:
                self._send(landed)
            return
        breath = [r for r in arranger.BREATH_ROLES if r in program.active_roles()] if plan["breath"] else []
        if breath and self.performer.surprise_due("silent_bar"):
            breath = list(program.active_roles())          # a whole bar of silence, every line
            self.performer.surprise_fired("silent_bar")
        if breath:
            self._send([arranger.silence(ROLE_CHANNELS[r]) for r in breath] +
                       [s for s in plan["statements"] if s.startswith(f"d{arranger.RISER} ")] +
                       [arranger.silence(arranger.FILL)])
            self._wait_until(math.floor(self.clock.cycle()) + 1)          # one bar's breath
            statements = [program.statement(r) for r in breath] + statements
        statements = [s for s in landed if not s.startswith(tuple(f"d{ROLE_CHANNELS[r]} " for r in breath))] \
            + statements
        if not self._send(statements):
            return
        self._devices = dict(plan["playing"])
        if plan["note"]:
            self.log(f"🎚 Arranger: {plan['note']}")
            runtime.append_thought(f"🎚 {plan['note']}")
            runtime.ui_event("arranger", text=plan["note"], phase=phase,
                             devices=[s for s in statements if not s.startswith(tuple(
                                 f"d{ROLE_CHANNELS[r]} " for r in breath))])
            self._record("arrangement", **self._track_info(program), before=before, after=phase,
                         note=plan["note"], breath=bool(breath), statements=statements)
        self._publish(program, None, plan["note"])

    def _follow_harmony(self, program: Program) -> Program:
        """Change the progression when the arc reaches a part with its own
        (e.g. the breakdown), starting on its first chord at this bar."""
        wanted = self.performer.harmony_for_phase(program.track)
        if not wanted or wanted[1]["degrees"] == program.track.degrees:
            return program
        part, prog = wanted
        new = copy.deepcopy(program)
        new.track.degrees = list(prog["degrees"])
        new.track.start_cycle = math.floor(self.clock.cycle() + 0.25)
        if not self._send([new.statement(r) for r in new.lines]):
            return program
        label = {"main": "back to the main progression", "breakdown": "the breakdown's own progression",
                 "peak": "a lift for the peak"}.get(part, part)
        self.log(f"🎼 Harmony: {prog['name']} ({label})")
        runtime.append_thought(f"🎼 harmony: {prog['name']} ({label})")
        runtime.ui_event("director", what="Harmony", model="plan", decision=prog["name"],
                         reason=f"{label}, from the Director's plan for this track")
        self._record("harmony", **self._track_info(new), part=part, progression=prog["name"],
                     degrees=new.track.degrees, start_cycle=new.track.start_cycle)
        self._publish(new, None, f"harmony: {prog['name']}")
        return new

    def _report_complexity(self) -> None:
        values, self.groove_complexity = self.groove_complexity, []
        if values:
            self.log(f"[Live] complexity in the groove: {sum(values) / len(values):.1f} on average, "
                     f"{max(values):.1f} at the peak (livecode.complexity)")

    def stop(self) -> None:
        self.stop_event.set()

    # ----------------------------------------------------------------- helpers

    def _check_ratings(self) -> None:
        if self.ratings is None or self.program is None:
            return
        while True:
            rating = self.ratings.take()
            if rating is None:
                return
            program, t = self.program, rating["t"]
            role = rating.get("role")
            if role and role not in program.lines:
                role = None                       # the line went before the thumb arrived
            # The last change before the thumb: to that line, when one line is rated.
            tag = f"d{ROLE_CHANNELS[role]}" if role else ""
            before = [e for e in self._recent_edits if e[0] <= t and (not role or tag in e[1] or f"({role})" in e[1])]
            last = before[-1] if before else None
            edit = last[1] if last else ""
            thumb = "👍" if rating["rating"] > 0 else "👎"
            what = f"the {role} ({tag})" if role else "this"
            self.log(f"{thumb} The host {'likes' if rating['rating'] > 0 else 'dislikes'} {what}"
                     + (f" (the last change: {edit})" if edit else ""))
            runtime.append_thought(f"{thumb} host rating" + (f" on the {role}" if role else "")
                                   + (f" after: {edit}" if edit else ""))
            runtime.ui_event("rating", rating=rating["rating"], edit=edit, role=role)
            line = program.statement(role) if role else ""
            self.performer.note_rating(rating["rating"], edit, role=role, line=line)
            self._record("rating", **self._track_info(program), rating=rating["rating"], rated_at=t,
                         phase=self.performer.state.phase, last_edit=edit, role=role, line=line or None,
                         last_edit_source=last[2] if last else None,
                         seconds_since_edit=round(t - last[0], 1) if last else None,
                         code=[program.statement(r) for r in program.lines])

    def _wait_until(self, cycle: float) -> None:
        last_beat = 0.0
        while not self.stop_event.is_set():
            self._check_ratings()
            remaining = self.clock.seconds_until(cycle)
            if remaining <= 0:
                return
            if time.monotonic() - last_beat > 5:
                runtime.heartbeat()
                last_beat = time.monotonic()
                if self._rolls_until and self.clock.cycle() > self._rolls_until - 3:
                    self._refresh_rolls()
            self._sleep(min(remaining, 1.0))

    def _wait_for(self, future) -> None:
        last_beat = 0.0
        while not self.stop_event.is_set() and not future.done():
            if time.monotonic() - last_beat > 5:
                runtime.heartbeat()
                last_beat = time.monotonic()
            concurrent.futures.wait([future], timeout=1.0)

    def _send(self, lines: list[str]) -> bool:
        if not lines:
            return True
        error = self.send(lines)
        if error:
            self.log(f"[Live] Tidal reported an error:\n{error}")
            return False
        return True

    def _type_ok(self, statements: list[str]) -> bool:
        if self.type_checker is None:
            return True
        for stmt in statements:
            if stmt.startswith("d") and " $ " in stmt and not stmt.endswith("$ silence"):
                ch = int(stmt[1:stmt.index(" ")])
                error = self.type_checker.check(f"({stmt.split(' $ ', 1)[1]}) # orbit {ch - 1}")
                if error:
                    self.log(f"[Live] edit rejected by the type checker:\n{error}")
                    return False
        return True

    def _with_fades(self, statements: list[str], program: Program, edit) -> list[str]:
        """Lines that enter fade in over a few bars and lines that leave
        fade out (Tidal's xfadeIn), instead of switching on and off."""
        from .mix import FADE_IN_BARS, FADE_OUT_BARS

        role = edit.role
        if edit.op not in ("add", "unmute", "remove", "mute") or role not in ROLE_CHANNELS:
            return statements
        ch = ROLE_CHANNELS[role]
        if edit.op in ("add", "unmute") and role in program.lines:
            faded = f"xfadeIn {ch} {FADE_IN_BARS} $ ({program.expression(role)}) |< orbit {ch - 1}"
        else:
            faded = f"xfadeIn {ch} {FADE_OUT_BARS} $ silence"
        return [faded if stmt.startswith(f"d{ch} $ ") else stmt for stmt in statements]

    def _publish(self, program: Program, highlight: str | None, comment: str) -> None:
        track, st = program.track, self.performer.state
        now_playing = f"{track.title}  ·  {track.key}  ·  {track.bpm:g} BPM  ·  {st.phase}"
        lines = program.code_lines(highlight=highlight)
        if comment:
            lines.append("")
            lines.append(f"-- {comment}")
        runtime.write_overlay(now_playing, lines, width=120, max_lines=26)
        self._publish_ui(program, highlight, comment)
        self._refresh_rolls(program)
        runtime.save_state({"mode": "live", "program": program.to_dict(), "performer": st.to_dict(),
                            "phase_elapsed": self.performer.phase_bars(),
                            "style": self.performer.style, "saved_at": time.time()})

    ROLL_CYCLES = 8

    def _refresh_rolls(self, program: Program | None = None) -> None:
        """Ask Tidal (in the background) what every playing line does over the
        next ROLL_CYCLES bars, for the page's piano rolls."""
        checker = self.type_checker
        if checker is None or not getattr(checker, "available", False) or not hasattr(checker, "events"):
            return
        program = copy.deepcopy(program or self.program)
        if program is None:
            return
        if self._rolls_pending is not None and not self._rolls_pending.done():
            return
        start = float(math.floor(self.clock.cycle()))
        end = start + self.ROLL_CYCLES
        self._rolls_until = end

        def work():
            lines = {}
            for role in program.active_roles():
                events = checker.events(program.expression(role), start, end)
                if events:
                    lines[str(ROLE_CHANNELS[role])] = sorted(events)
            runtime.ui_update(rolls={"from": start, "to": end, "lines": lines})

        self._rolls_pending = self._roll_pool.submit(work)

    def _publish_ui(self, program: Program, highlight: str | None, comment: str) -> None:
        """The snapshot the stream page draws (stream/ui)."""
        from . import llm

        track, perf = program.track, self.performer
        code = []
        for role in sorted(program.lines, key=lambda r: ROLE_CHANNELS[r]):
            line = program.lines[role]
            code.append({"channel": line.channel, "role": role, "muted": line.muted,
                         "text": f"d{line.channel} $ " + (program.expression(role) if not line.muted
                                                         else line.render(track.harmony())),
                         "edited": role == highlight})
        for channel, statement in sorted(self._devices.items()):
            code.append({"channel": channel, "role": "arranger", "muted": False, "text": statement,
                         "edited": False})
        stats = dict(perf.stats)
        judged = stats.get("judge_llm", 0) + stats.get("judge_builtin", 0)
        models = {}
        if perf.model_id:
            models["performer"] = llm.resolve(perf.model_id)[1]
        if perf.judge:
            models["judge"] = perf.judge
        runtime.ui_update(
            channel=config.CHANNEL_NAME,
            track={"title": track.title, "style": track.style, "key": track.key, "bpm": track.bpm,
                   "mood": perf.track_mood, "subgenre": track.subgenre},
            phase=perf.state.phase, plan=[{"phase": p, "bars": b} for p, b in perf.arc_plan()],
            step=perf.state.step, phase_start=perf.state.phase_start,
            clock={"ref_time": self.clock.ref_time, "ref_cycle": self.clock.ref_cycle, "cps": self.clock.cps,
                   "now": self.clock._now()},
            tempo_line=program.tempo_statement(), code=code, comment=comment,
            models=models, ranges={**FX_RANGES, **PF_RANGES},      # the page's inline sliders
            stats={"edits": sum(v for k, v in stats.items() if k not in ("judge_llm", "judge_builtin")),
                   "llm": stats.get("llm", 0), "judged": judged, "judge_llm": stats.get("judge_llm", 0),
                   "judge_mass": (sum(perf.judge_mass) / len(perf.judge_mass)) if perf.judge_mass else None},
        )

    def _announce(self, program: Program) -> None:
        track = program.track
        self.log(f"\n♪ New track: {track.title}  ·  {track.key}  ·  {track.bpm:g} BPM  ·  {track.style}")
        runtime.append_thought(f"♪ {track.title} · {track.key} · {track.bpm:g} BPM · {track.style}")
        runtime.ui_event("track", title=track.title, style=track.style, key=track.key, bpm=track.bpm,
                         mood=self.performer.track_mood, arc=arc_summary(self.performer.arc_plan()),
                         sounds=sounds_summary(track.sounds))
        if self.narrator is not None:
            from .narrator import clean_narration

            # The Director's line is checked on its own, so a line it can't
            # speak (e.g. with a number in it) never takes the title with it.
            line = clean_narration(self.performer.track_narration)
            self.narrator.say(f"Now playing: {track.title}. {line}".strip(), kind="piece")

    def _on_arc(self, before: str, after: str, why: str) -> None:
        """The arc moved on (e.g. groove -> breakdown): have the Narrator
        say something about the moment, written in the background."""
        if self.program is not None:
            self._record("arc", **self._track_info(self.program), before=before, after=after, why=why)
        if self.narrator is None or not self.narrator.wants("section"):
            return
        program = self.program
        self._voice_pool.submit(self._narrate_moment, after, why, program, self._last_reason)

    def _narrate_moment(self, phase: str, why: str, program: Program | None, reason: str) -> None:
        from . import llm
        from .narrator import clean_narration

        line = ""
        model_id = self.performer.model_id
        text_model = model_id and not (llm.resolve(model_id)[0] == "jevtypesafe" and llm.jev_style() == "decide")
        if text_model and program is not None:
            prompt = (
                "You are the warm, intimate late-night voice of a 24/7 live-coded music stream. "
                f"The track '{program.track.title}' ({program.track.style}, mood: {self.performer.track_mood or 'calm'}) "
                f"is moving into its {phase.replace('groove2', 'second peak')} ({why}). "
                + (f"The last change: {reason}. " if reason else "")
                + "Say ONE sentence (under 20 words) to the listeners about this moment. No numbers, no technical "
                  "words, no quotes. Reply with the sentence only."
            )
            try:
                reply = llm.converse("Narrator", model_id, "You write one spoken sentence. No JSON.", prompt,
                                     log=self.log, asks=f"a line for the {phase}", max_tokens=80, timeout=20)
                line = clean_narration(reply.strip().splitlines()[0] if reply.strip() else "")
            except llm.LLMError:
                line = ""
        if not line:
            options = _ARC_LINES.get(phase, [])
            line = options[hash((program.track.title if program else "", phase)) % len(options)] if options else ""
        if line:
            self.narrator.say(line, kind="section")

    def _without_unwanted_sounds(self, program: Program) -> Program:
        """A track saved before a sound was taken out (saws, organ) still has
        its lines: swap each for the style's block in the same role, or drop
        it when the style has none."""
        import random

        from .blocks import blocks_for
        from .mix import synth_of
        from .plan import UNWANTED_SYNTHS

        for role, line in list(program.lines.items()):
            played = {synth_of(line.body), *line.patterns.values()}
            if not played & UNWANTED_SYNTHS:
                continue
            fresh = [b for b in blocks_for(program.track.style, role)
                     if not ({synth_of(b.body)} | {v for vs in b.patterns.values() for v in vs}) & UNWANTED_SYNTHS]
            if fresh:
                new = program.track.make(fresh[0], random.Random(0))
                new.muted = line.muted
                program.lines[role] = new
                self.log(f"[Live] d{line.channel}: {line.block or role} used a sound that's out; now {new.block}")
            else:
                del program.lines[role]
                self.log(f"[Live] d{line.channel}: {line.block or role} used a sound that's out; dropped")
        return program

    # --------------------------------------------------------------------- run

    def run(self, resume_state: dict | None = None) -> None:
        if resume_state and "program" in resume_state:
            program = Program.from_dict(resume_state["program"])
            self.performer.style = resume_state.get("style", program.track.style)
            self.performer.state = SetState.from_dict(resume_state.get("performer", {}))
            program = self._without_unwanted_sounds(program)
            self.log(f"[Live] Resuming '{program.track.title}' ({len(program.lines)} lines)")
        else:
            program = Program(self.performer.new_track(None))
        program.track.start_cycle = 0          # resetCycles below: the progression restarts too
        self._record_track_start(program, resumed=bool(resume_state and "program" in resume_state))
        self.clock.reset(program.track.cps)
        self._send(["hush", program.tempo_statement(), "resetCycles"])
        self.clock.reset(program.track.cps)
        self._send([program.statement(r) for r in program.lines])
        # The clock restarted at bar 0; a resumed track keeps how far into
        # its phase it was.
        self.performer.state.phase_start = -float((resume_state or {}).get("phase_elapsed", 0.0)) \
            if resume_state and "program" in resume_state else 0.0
        self.program = program
        self._announce(program)
        self._publish(program, None, "")

        tracks = 0
        next_cycle = 1 if not program.lines else self.performer.edit_bars_now()
        while not self.stop_event.is_set():
            future = self._pool.submit(self.performer.next_edit, program)
            self._wait_until(next_cycle)
            if not future.done():
                # A slow model: keep playing and bring the edit in on the
                # bar after it arrives, rather than dropping it.
                self.log("[Live] the performer is still deciding; the music plays on")
                self._wait_for(future)
                next_cycle = math.floor(self.clock.cycle()) + 1
                self._wait_until(next_cycle)
            if self.stop_event.is_set():
                break
            try:
                edit = future.result()
            except Exception as e:  # never let the performer stop the music
                self.log(f"[Live] performer failed ({e}); skipping this edit")
                next_cycle += self.performer.edit_bars_now()
                continue

            try:
                new = apply_edit(program, edit, self.performer.settings["max_lines"])
            except EditError as e:
                self.log(f"[Live] skipped invalid edit ({edit.describe()}): {e}")
                self._record_rejected(program, edit, f"invalid: {e}")
                next_cycle += self.performer.edit_bars_now()
                continue
            statements = changed_statements(program, new)
            if not self._type_ok(statements):
                self._record_rejected(program, edit, "did not type check")
                next_cycle += self.performer.edit_bars_now()
                continue
            if not self._send(self._with_fades(statements, new, edit)):
                self._record_rejected(program, edit, "Tidal refused it")
                next_cycle += self.performer.edit_bars_now()
                continue
            if new.track.bpm != program.track.bpm:
                self.clock.set_cps(new.track.cps)
            self._record_edit(program, new, edit)
            self._recent_edits.append((self.clock._now(), edit.describe(), edit.source or "built-in"))
            program = self.program = new
            self.edits += 1
            highlight = edit.role if edit.role in program.lines else None
            tag = f"[{edit.source}] " if edit.source else ""
            if edit.listening:
                self.log(f"👂 hears: {edit.listening}")
                runtime.append_thought(f"👂 hears: {edit.listening}")
            self.log(f"✎ {tag}{edit.describe()}" + (f"   -- {edit.reason}" if edit.reason else ""))
            runtime.append_thought(f"✎ {tag}{edit.describe()}  -- {edit.reason}")
            runtime.ui_event("performer", source=edit.source or "built-in", listening=edit.listening,
                             edit=edit.describe(), reason=edit.reason, channel=ROLE_CHANNELS.get(edit.role),
                             role=edit.role, op=edit.op)
            self.performer.applied(edit)
            if self.performer.state.phase in ("groove", "groove2"):
                self.groove_complexity.append(complexity(program))
            runtime.append_history({"edit": edit.describe(), "source": edit.source, "listening": edit.listening,
                                    "op": edit.op, "role": edit.role,
                                    "name": edit.name, "value": edit.value, "reason": edit.reason,
                                    "program": program.to_dict()})
            self._publish(program, highlight, edit.reason)
            self._last_reason = edit.reason
            if edit.narration and self.narrator is not None:
                self.narrator.say(edit.narration, kind="section")

            if self.performer.track_finished(program):
                self._record_track_end(program)
                self._report_complexity()
                tracks += 1
                if self.max_tracks is not None and tracks >= self.max_tracks:
                    self._wait_until(next_cycle + self.performer.edit_bars_now())
                    break
                previous = program.track
                track = self.performer.new_track(previous)
                # Whatever still plays when the planned outro ends fades out.
                silence = [f"xfadeIn {ROLE_CHANNELS[r]} {FADE_OUT_BARS} $ silence" for r in program.lines]
                track.start_cycle = math.floor(self.clock.cycle() + 0.25)   # first chord on this bar
                program = self.program = Program(track)
                self._send(silence + [program.tempo_statement()])
                self.clock.set_cps(track.cps)
                self._record_track_start(program)
                self._arrange(program)
                self._announce(program)
                self._publish(program, None, "")
            # Move the arc on now (e.g. build -> groove once the last line is
            # in), so the wait until the next edit already uses the new phase's
            # pace, the same one the performer's deadline uses.
            self.performer.advance_phase(program)
            program = self.program = self._follow_harmony(program)
            program = self.program = self._surprise_turn(program)
            program, landing = self._fill_for_the_drop(program)
            self.program = program
            self._arrange(program, landing)
            next_cycle += self.performer.edit_bars_now()
        self.performer.report_stats()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._roll_pool.shutdown(wait=False, cancel_futures=True)
        self._voice_pool.shutdown(wait=False, cancel_futures=True)


# Spoken at the arc's turning points when no text model writes the line.
_ARC_LINES = {
    "groove": ["Everything's in now. Let it settle and carry you.",
               "There it is. Stay with the groove for a while."],
    "breakdown": ["The floor drops away for a moment. Just breathe.",
                  "Let's take the weight off and float for a bit."],
    "rebuild": ["Here it comes back, piece by piece.",
                "Feel it gathering again. Almost there."],
    "groove2": ["And we're back, a little brighter this time.",
                "All together again. This is the peak of it."],
    "outro": ["The track starts to let go now, slowly.",
              "Let it fade out gently. Something new is coming."],
}
