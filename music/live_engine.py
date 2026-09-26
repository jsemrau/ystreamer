"""
The live-coding engine: performs the program forever.

Every `edit_bars` bars (on the bar boundary) the performer's next edit is
applied to the program and only the lines it changed are sent to Tidal, the
way a live coder re-evaluates one line. The screen shows the program with the
edited line marked and the performer's reason as a code comment.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Callable

from . import runtime
from .engine import Clock
from .livecode import ROLE_CHANNELS, EditError, Program, apply_edit, changed_statements
from .performer import Performer, SetState


class LiveEngine:
    def __init__(self, send: Callable[[list[str]], str | None], performer: Performer,
                 log: Callable[[str], None] = print, narrator=None, type_checker=None,
                 now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 max_tracks: int | None = None):
        self.send = send
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
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="performer")
        # Narration lines are written in the background so the music never waits.
        self._voice_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="narrator")
        self._last_reason = ""
        performer.on_arc = self._on_arc

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

    def _publish(self, program: Program, highlight: str | None, comment: str) -> None:
        track, st = program.track, self.performer.state
        now_playing = f"{track.title}  ·  {track.key}  ·  {track.bpm:g} BPM  ·  {st.phase}"
        lines = program.code_lines(highlight=highlight)
        if comment:
            lines.append("")
            lines.append(f"-- {comment}")
        runtime.write_overlay(now_playing, lines, width=120, max_lines=26)
        runtime.save_state({"mode": "live", "program": program.to_dict(), "performer": st.to_dict(),
                            "style": self.performer.style, "saved_at": time.time()})

    def _announce(self, program: Program) -> None:
        track = program.track
        self.log(f"\n♪ New track: {track.title}  ·  {track.key}  ·  {track.bpm:g} BPM  ·  {track.style}")
        runtime.append_thought(f"♪ {track.title} · {track.key} · {track.bpm:g} BPM · {track.style}")
        if self.narrator is not None:
            line = self.performer.track_narration
            self.narrator.say(f"Now playing: {track.title}. {line}".strip(), kind="piece")

    def _on_arc(self, before: str, after: str, why: str) -> None:
        """The arc moved on (e.g. groove -> breakdown): have the Narrator
        say something about the moment, written in the background."""
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
        self.clock.reset(program.track.cps)
        self._send(["hush", program.tempo_statement(), "resetCycles"])
        self.clock.reset(program.track.cps)
        self._send([program.statement(r) for r in program.lines])
        self.program = program
        self._announce(program)
        self._publish(program, None, "")

        tracks = 0
        edit_bars = self.performer.settings["edit_bars"]
        next_cycle = 1 if not program.lines else edit_bars
        while not self.stop_event.is_set():
            future = self._pool.submit(self.performer.next_edit, program)
            self._wait_until(next_cycle)
            if self.stop_event.is_set():
                break
            try:
                edit = future.result()
            except Exception as e:  # never let the performer stop the music
                self.log(f"[Live] performer failed ({e}); skipping this edit")
                next_cycle += edit_bars
                continue

            try:
                new = apply_edit(program, edit, self.performer.settings["max_lines"])
            except EditError as e:
                self.log(f"[Live] skipped invalid edit ({edit.describe()}): {e}")
                next_cycle += edit_bars
                continue
            statements = changed_statements(program, new)
            if not self._type_ok(statements) or not self._send(statements):
                next_cycle += edit_bars
                continue
            if new.track.bpm != program.track.bpm:
                self.clock.set_cps(new.track.cps)
            program = self.program = new
            self.edits += 1
            highlight = edit.role if edit.role in program.lines else None
            tag = f"[{edit.source}] " if edit.source else ""
            if edit.listening:
                self.log(f"👂 hears: {edit.listening}")
                runtime.append_thought(f"👂 hears: {edit.listening}")
            self.log(f"✎ {tag}{edit.describe()}" + (f"   -- {edit.reason}" if edit.reason else ""))
            runtime.append_thought(f"✎ {tag}{edit.describe()}  -- {edit.reason}")
            self.performer.applied(edit)
            runtime.append_history({"edit": edit.describe(), "source": edit.source, "listening": edit.listening,
                                    "op": edit.op, "role": edit.role,
                                    "name": edit.name, "value": edit.value, "reason": edit.reason,
                                    "program": program.to_dict()})
            self._publish(program, highlight, edit.reason)
            self._last_reason = edit.reason
            if edit.narration and self.narrator is not None:
                self.narrator.say(edit.narration, kind="section")

            if self.performer.track_finished(program):
                tracks += 1
                if self.max_tracks is not None and tracks >= self.max_tracks:
                    self._wait_until(next_cycle + edit_bars)
                    break
                previous = program.track
                track = self.performer.new_track(previous)
                silence = [f"d{ROLE_CHANNELS[r]} $ silence" for r in program.lines]
                program = self.program = Program(track)
                self._send(silence + [program.tempo_statement()])
                self.clock.set_cps(track.cps)
                edit_bars = self.performer.settings["edit_bars"]
                self._announce(program)
                self._publish(program, None, "")
            next_cycle += edit_bars
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
