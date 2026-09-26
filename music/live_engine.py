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
from typing import Callable

from . import runtime
from .engine import Clock
from .livecode import ROLE_CHANNELS, Edit, EditError, Program, apply_edit, changed_statements, complexity
from .performer import Performer, SetState


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
        self.groove_complexity: list[float] = []   # complexity after each groove edit, per track
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="performer")
        # Narration lines are written in the background so the music never waits.
        self._voice_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="narrator")
        self._last_reason = ""
        performer.on_arc = self._on_arc

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

    def _wait_until(self, cycle: float) -> None:
        last_beat = 0.0
        while not self.stop_event.is_set():
            remaining = self.clock.seconds_until(cycle)
            if remaining <= 0:
                return
            if time.monotonic() - last_beat > 5:
                runtime.heartbeat()
                last_beat = time.monotonic()
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
        runtime.save_state({"mode": "live", "program": program.to_dict(), "performer": st.to_dict(),
                            "style": self.performer.style, "saved_at": time.time()})

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
        stats = dict(perf.stats)
        judged = stats.get("judge_llm", 0) + stats.get("judge_builtin", 0)
        models = {}
        if perf.model_id:
            models["performer"] = llm.resolve(perf.model_id)[1]
        if perf.judge:
            models["judge"] = perf.judge
        runtime.ui_update(
            channel="sleepless composer",
            track={"title": track.title, "style": track.style, "key": track.key, "bpm": track.bpm,
                   "mood": perf.track_mood},
            phase=perf.state.phase, phases=["build", "groove", "breakdown", "rebuild", "groove2", "outro"],
            clock={"ref_time": self.clock.ref_time, "ref_cycle": self.clock.ref_cycle, "cps": self.clock.cps,
                   "now": self.clock._now()},
            tempo_line=program.tempo_statement(), code=code, comment=comment,
            models=models,
            stats={"edits": sum(v for k, v in stats.items() if k not in ("judge_llm", "judge_builtin")),
                   "llm": stats.get("llm", 0), "judged": judged, "judge_llm": stats.get("judge_llm", 0),
                   "judge_mass": (sum(perf.judge_mass) / len(perf.judge_mass)) if perf.judge_mass else None},
        )

    def _announce(self, program: Program) -> None:
        track = program.track
        self.log(f"\n♪ New track: {track.title}  ·  {track.key}  ·  {track.bpm:g} BPM  ·  {track.style}")
        runtime.append_thought(f"♪ {track.title} · {track.key} · {track.bpm:g} BPM · {track.style}")
        runtime.ui_event("track", title=track.title, style=track.style, key=track.key, bpm=track.bpm,
                         mood=self.performer.track_mood)
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

    # --------------------------------------------------------------------- run

    def run(self, resume_state: dict | None = None) -> None:
        if resume_state and "program" in resume_state:
            program = Program.from_dict(resume_state["program"])
            self.performer.style = resume_state.get("style", program.track.style)
            self.performer.state = SetState.from_dict(resume_state.get("performer", {}))
            self.log(f"[Live] Resuming '{program.track.title}' ({len(program.lines)} lines)")
        else:
            program = Program(self.performer.new_track(None))
        program.track.start_cycle = 0          # resetCycles below: the progression restarts too
        self._record_track_start(program, resumed=bool(resume_state and "program" in resume_state))
        self.clock.reset(program.track.cps)
        self._send(["hush", program.tempo_statement(), "resetCycles"])
        self.clock.reset(program.track.cps)
        self._send([program.statement(r) for r in program.lines])
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
                silence = [f"d{ROLE_CHANNELS[r]} $ silence" for r in program.lines]
                track.start_cycle = math.floor(self.clock.cycle() + 0.25)   # first chord on this bar
                program = self.program = Program(track)
                self._send(silence + [program.tempo_statement()])
                self.clock.set_cps(track.cps)
                self._record_track_start(program)
                self._announce(program)
                self._publish(program, None, "")
            # Move the arc on now (e.g. build -> groove once the last line is
            # in), so the wait until the next edit already uses the new phase's
            # pace, the same one the performer's deadline uses.
            self.performer.advance_phase(program)
            program = self.program = self._follow_harmony(program)
            next_cycle += self.performer.edit_bars_now()
        self.performer.report_stats()
        self._pool.shutdown(wait=False, cancel_futures=True)
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
