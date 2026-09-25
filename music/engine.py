"""
The music engine: turns Director decisions into Tidal code and schedules it
on musical boundaries, forever.

Timeline
--------
- Cycle = bar. The engine keeps its own copy of Tidal's cycle clock
  (reset with `resetCycles` at start, updated on every `setcps`).
- Sections are a whole number of progression periods long and start on a
  cycle that is a multiple of the period.
- A section's code is sent `lead` cycles before its start using
  `jumpMod <id> <period>`, so Tidal itself switches every layer exactly on
  the downbeat of the next progression loop. No mid-phrase cuts.
- The next section's arrangement (and the next piece's plan) is prepared in a
  background thread while the current section plays, so slow LLM calls never
  delay the music. If the Director isn't ready in time, the deterministic
  fallback is used.
"""

from __future__ import annotations

import concurrent.futures
import math
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable

import config

from . import presets as P
from . import runtime
from .director import Director
from .plan import Arrangement, Piece
from .templates import HarmonyContext, render_layer, statement, with_orbit
from .theory import PROGRESSIONS_BY_NAME, Key, progression_chords


class Clock:
    """Tracks Tidal's cycle position from wall-clock time and cps changes."""

    def __init__(self, cps: float, now: Callable[[], float] = time.time):
        self._now = now
        self.cps = cps
        self.ref_cycle = 0.0
        self.ref_time = now()

    def reset(self, cps: float | None = None) -> None:
        if cps:
            self.cps = cps
        self.ref_cycle = 0.0
        self.ref_time = self._now()

    def cycle(self) -> float:
        return self.ref_cycle + (self._now() - self.ref_time) * self.cps

    def set_cps(self, cps: float) -> None:
        self.ref_cycle = self.cycle()
        self.ref_time = self._now()
        self.cps = cps

    def seconds_until(self, cycle: float) -> float:
        return (cycle - self.cycle()) / self.cps


@dataclass
class RenderedSection:
    statements: dict[str, str] = field(default_factory=dict)  # role -> full statement
    expressions: dict[str, str] = field(default_factory=dict)  # role -> pattern expr
    silenced: list[str] = field(default_factory=list)


def stable_hash(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


class MusicEngine:
    def __init__(self, send: Callable[[list[str]], str | None], director: Director,
                 log: Callable[[str], None] = print, use_custom_synths: bool = True,
                 free_layer: bool = False, type_checker=None, lead_cycles: float = 1.0,
                 now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 max_pieces: int | None = None, director_grace: float = 0.0, narrator=None):
        self.send = send
        # Speaks the Director's listener-facing lines (music/narrator.py); optional.
        self.narrator = narrator
        self.director = director
        self.log = log
        self.use_custom_synths = use_custom_synths
        self.free_layer = free_layer
        self.type_checker = type_checker
        self.lead_cycles = lead_cycles
        self._sleep = sleep
        self.clock = Clock(0.3, now=now)
        self.stop_event = threading.Event()
        self.max_pieces = max_pieces
        # Extra real seconds to wait for the Director at a boundary (dry runs
        # use this because simulated time doesn't give the LLM time to answer).
        self.director_grace = director_grace
        self.active: dict[str, str] = {}  # role -> statement currently playing
        self.piece: Piece | None = None
        self.section_index = 0
        self.arrangement: Arrangement | None = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="director")

    # ------------------------------------------------------------------ render

    def harmony_for(self, piece: Piece, section_name: str) -> HarmonyContext:
        key = Key.parse(piece.key)
        chords = progression_chords(key, PROGRESSIONS_BY_NAME[piece.progression])
        # Same section name -> same seed -> recurring sections reuse their melody.
        seed = (piece.seed * 31 + stable_hash(section_name)) % 1_000_000
        return HarmonyContext(key=key, chords=chords, cycles_per_chord=piece.cycles_per_chord, seed=seed)

    def render_section(self, piece: Piece, index: int, arrangement: Arrangement,
                       previous_active: dict[str, str], transition: str = "jumpMod",
                       free_cycles: list[str] | None = None) -> RenderedSection:
        section = piece.sections[index]
        ctx = self.harmony_for(piece, section.name)
        out = RenderedSection()
        for role in P.ROLES:
            preset_name = arrangement.layers.get(role) if role != "free" else ("free_glass" if free_cycles else None)
            preset = P.get(role, preset_name)
            if preset is None:
                continue
            kwargs = {}
            if role == "melody" and arrangement.melody_character:
                kwargs["character"] = arrangement.melody_character
            if role == "free":
                kwargs["notes_by_cycle"] = free_cycles
            expr = render_layer(role, ctx, preset, section.energy, self.use_custom_synths, **kwargs)
            if expr is None:
                continue
            if self.type_checker is not None:
                error = self.type_checker.check(with_orbit(role, expr))
                if error:
                    self.log(f"[Engine] {role} failed type check, skipping:\n{error}")
                    continue
            out.expressions[role] = expr
            out.statements[role] = statement(role, expr, transition, piece.period)
        out.silenced = [role for role in previous_active if role not in out.statements]
        return out

    # ---------------------------------------------------------------- playback

    def _send(self, lines: list[str]) -> bool:
        error = self.send(lines)
        if error:
            self.log(f"[Engine] Tidal reported an error:\n{error}")
            return False
        return True

    def _apply(self, rendered: RenderedSection, period: int, transition: str) -> None:
        lines = list(rendered.statements.values())
        for role in rendered.silenced:
            ch = P.ROLE_CHANNELS[role]
            if transition == "jumpMod":
                lines.append(f"jumpMod {ch} {period} $ silence")
            else:
                lines.append(f"d{ch} $ silence")
        self._send(lines)
        self.active = dict(rendered.statements)

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

    def _publish(self, piece: Piece, index: int, arrangement: Arrangement, rendered: RenderedSection,
                 start_cycle: int) -> None:
        section = piece.sections[index]
        now_playing = (f"{piece.title}  ·  {piece.key}  ·  {piece.bpm:.0f} BPM  ·  "
                       f"{section.name} ({index + 1}/{len(piece.sections)})")
        runtime.write_overlay(now_playing, list(rendered.statements.values()))
        state = {
            "piece": piece.to_dict(),
            "section_index": index,
            "arrangement": arrangement.to_dict(),
            "active": self.active,
            "start_cycle": start_cycle,
            "saved_at": time.time(),
        }
        runtime.save_state(state)
        runtime.append_history({"piece": piece.to_dict(), "section_index": index,
                                "arrangement": arrangement.to_dict(),
                                "code": list(rendered.statements.values())})
        self.log(f"\n♪ {now_playing}")
        layers = ", ".join(f"{r}={arrangement.layers.get(r) or 'llm'}" for r in rendered.statements)
        self.log(f"   layers: {layers}")
        if self.narrator is not None:
            if index == 0:
                intro = f"Now playing: {piece.title}."
                self.narrator.say(f"{intro} {piece.narration}".strip(), kind="piece")
            elif arrangement.narration:
                self.narrator.say(arrangement.narration, kind="section")

    def _prepare(self, piece: Piece, index: int, current: Arrangement | None):
        """Director work for one section."""
        arrangement = self.director.arrange_section(piece, index, current)
        free = None
        if self.free_layer and piece.sections[index].energy >= 0.4 \
                and len(arrangement.active_roles()) < config.MAX_ACTIVE_LAYERS:
            free = self.director.compose_free_layer(piece, piece.sections[index])
        return arrangement, free

    def _prepare_next(self, piece: Piece, index: int, current: Arrangement | None):
        """Runs in the background thread: plan the next section (and piece)."""
        if index + 1 < len(piece.sections):
            next_piece, next_index = piece, index + 1
        else:
            next_piece, next_index = self.director.next_piece(piece), 0
        arrangement, free = self._prepare(next_piece, next_index, current)
        return next_piece, next_index, arrangement, free

    def _resolve(self, future, piece: Piece, index: int):
        try:
            return future.result(timeout=self.director_grace)
        except Exception as e:
            future.cancel()
            reason = "not ready in time" if isinstance(e, concurrent.futures.TimeoutError) else f"failed ({e})"
            self.log(f"[Engine] Director {reason}; using the deterministic plan")
            from .plan import arrange, plan_piece
            if index + 1 < len(piece.sections):
                next_piece, next_index = piece, index + 1
            else:
                next_piece, next_index = plan_piece(self.director.rng, piece, style=self.director.style,
                                                    target_minutes=self.director.target_minutes), 0
            arrangement = arrange(next_piece, next_piece.sections[next_index], self.director.rng,
                                  self.director.max_layers)
            return next_piece, next_index, arrangement, None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self, resume_state: dict | None = None) -> None:
        pieces_played = 0
        if resume_state:
            piece = Piece.from_dict(resume_state["piece"])
            index = int(resume_state.get("section_index", 0))
            arrangement = Arrangement.from_dict(resume_state["arrangement"])
            self.log(f"[Engine] Resuming '{piece.title}' at section {index + 1}")
            free = None
        else:
            piece = self.director.next_piece(None)
            index = 0
            arrangement, free = self._prepare(piece, 0, None)

        # First section starts immediately after a cycle reset.
        self.clock.reset(piece.cps)
        self._send(["hush", f"setcps {piece.cps:.6f}", "resetCycles"])
        self.clock.reset(piece.cps)
        rendered = self.render_section(piece, index, arrangement, {}, transition="now", free_cycles=free)
        self._apply(rendered, piece.period, "now")
        start = 0
        self.piece, self.section_index, self.arrangement = piece, index, arrangement
        self._publish(piece, index, arrangement, rendered, start)

        while not self.stop_event.is_set():
            section = piece.sections[index]
            end = start + section.bars

            # Work out what comes next and prepare it in the background, so a
            # slow LLM never delays the music.
            last_section = index + 1 >= len(piece.sections)
            if last_section:
                pieces_played += 1
                if self.max_pieces is not None and pieces_played >= self.max_pieces:
                    self._wait_until(end)
                    break
            future = self._pool.submit(self._prepare_next, piece, index, arrangement)

            self._wait_until(end - self.lead_cycles)
            if self.stop_event.is_set():
                break
            next_piece, next_index, next_arrangement, free = self._resolve(future, piece, index)
            next_start = int(math.ceil(end / next_piece.period) * next_piece.period)
            if next_start != end:
                # New piece has a longer period: let the outro run to its boundary.
                self._wait_until(next_start - self.lead_cycles)
                if self.stop_event.is_set():
                    break

            lines_before: list[str] = []
            if next_piece is not piece and abs(next_piece.cps - piece.cps) > 1e-9:
                lines_before.append(f"setcps {next_piece.cps:.6f}")
            if lines_before:
                self._send(lines_before)
                self.clock.set_cps(next_piece.cps)

            rendered = self.render_section(next_piece, next_index, next_arrangement, self.active,
                                           transition="jumpMod", free_cycles=free)
            self._apply(rendered, next_piece.period, "jumpMod")
            piece, index, arrangement, start = next_piece, next_index, next_arrangement, next_start
            self.piece, self.section_index, self.arrangement = piece, index, arrangement
            # Publish once the switch has actually happened.
            self._wait_until(start)
            self._publish(piece, index, arrangement, rendered, start)

        self._pool.shutdown(wait=False, cancel_futures=True)
