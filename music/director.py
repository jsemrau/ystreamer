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

from . import feedback, journal, llm, runtime, taste
from .llm import CACHE_BREAK
from .narrator import clean_narration
from . import presets as P
from .melody import STEPS_PER_CYCLE
from .blocks import hook_kinds
from .hooks import GOOD as HOOK_GOOD
from .hooks import analyze as analyze_hook
from .hooks import key_harmony_problem
from .hooks import liked as liked_hooks
from .plan import (HOOK_SIMILAR, STYLES, Arrangement, Piece, Section, arrange, director_vocabulary, hook_similarity,
                   melody_brief, plan_piece, progression_degrees, resolve_surprise_cues, subgenre_from_theme,
                   validate_arrangement, validate_hook, validate_piece, write_piece_hook)
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
    "develop": ("Director", "develop the next section"),
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


def director_skills() -> str:
    """The art director's skills (config.DIRECTOR_SKILLS: song design, the
    basics first, then the advanced techniques), or "" without them."""
    folder = config.DIRECTOR_SKILLS
    try:
        names = sorted((n for n in os.listdir(folder) if n.endswith(".md")),
                       key=lambda n: (("advanced" in n), n))
    except OSError:
        return ""
    texts = []
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                texts.append(f.read().strip())
        except OSError:
            pass
    return "\n\n".join(t for t in texts if t)


def technique_ids() -> dict[str, str]:
    """The advanced techniques by number: {"A1": "Tease, then give", ...}."""
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"\*\*(A\d+) · ([^*.]+)\.?\*\*", director_skills())}


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
        # The last tracks' forms ("intro verse lift chorus ..."): the next suggestion is none of them.
        self.recent_forms: deque[str] = deque(maxlen=6)
        # The Director's own concepts and the tracks' frames, so it doesn't circle one idea.
        self.recent_concepts: deque[str] = deque(maxlen=6)
        # The advanced techniques (prompts/skills/song_design_advanced.md) the last tracks used.
        self.recent_techniques: deque[str] = deque(maxlen=6)
        # This track's plan as it began (her reasoning, concept, arc,
        # techniques) and when it began: what she develops it from, live.
        self._plan: dict = {}
        self._track_t0 = 0.0
        self.recent_frames: deque[str] = deque(maxlen=4)
        self.recent_bpms: deque[float] = deque(maxlen=3)
        # The host's favourite songs offered lately (music/touchstones.py), and this track's.
        self.recent_touchstones: deque[str] = deque(maxlen=5)
        self._touchstone: dict | None = None
        # Explore or exploit (music/explore.py): this track's stance and the spark it was offered.
        self.recent_sparks: deque[str] = deque(maxlen=4)
        # The hook's voice of the last tracks: each track's signature is a new one.
        self.recent_signatures: deque[str] = deque(maxlen=4)
        self._stance = ""
        self._spark = ""
        # The set so far, for the persona's goal (its progress in each track's brief).
        self.set_started = time.time()
        self.tracks_this_set = 0
        # A set with an end time (stream/live.sh --hours): each track's length
        # comes from the set plan, the Director's assumption for every track
        # still to come, refitted to the time actually left before each one.
        self.session_start = config.SESSION_START or self.set_started
        self.session_end = config.SESSION_END or 0.0
        self.track_minutes = target_minutes
        self.assumed: list[float] = []          # the tracks after this one, as last planned
        self.set_plan: list[float] = []
        self._since_surprise = config.SURPRISE_EVERY      # the first track may have one
        self._last_chromatic = False                       # (never two chromatic tracks running)
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

    def _fresh_modes(self, style: str) -> list[str]:
        """The style's modes but the last two tracks', where it has others."""
        modes = STYLES[style]["modes"]
        recent_modes = {k.split(" ", 1)[1] for k in list(self.recent_keys)[-2:] if " " in k}
        return [m for m in modes if m not in recent_modes] or modes

    def _suggest_key(self, style: str, previous: Piece | None, mode: str | None = None) -> Key:
        """A random key for the style that wasn't played recently (in `mode`
        if given): half the time a neighbour of the previous track's key,
        otherwise any root."""
        modes = [mode] if mode else self._fresh_modes(style)
        fresh = [Key(r, m) for r in range(12) for m in modes if Key(r, m).name not in self.recent_keys]
        if previous is not None and self.rng.random() < 0.5:
            near = [k for k in Key.parse(previous.key).neighbours() if k in fresh]
            if near:
                return self.rng.choice(near)
        return self.rng.choice(fresh or [Key(r, m) for r in range(12) for m in modes])

    def _suggest_bpm(self, style: str, band: str | None = None) -> int:
        """A tempo in the style's range (its third `band`, if given) at least 4
        BPM from the last tracks'."""
        lo, hi = STYLES[style]["bpm"]
        if band:
            from .explore import band_range

            lo, hi = band_range(band, lo, hi)
        options = [b for b in range(int(lo), int(hi) + 1) if all(abs(b - r) >= 4 for r in self.recent_bpms)]
        return self.rng.choice(options or list(range(int(lo), int(hi) + 1)))

    # -- decisions -------------------------------------------------------------

    PIECE_SYSTEM = "You are a concise music director. Reply with JSON only."

    @property
    def system(self) -> str:
        """The art director's persona (prompts/director_persona.md, or
        YS_DIRECTOR_PERSONA): its character, ambition and goal, then the
        reply format. Without the file, the plain director."""
        try:
            with open(config.DIRECTOR_PERSONA, encoding="utf-8") as f:
                persona = f.read().strip()
        except OSError:
            return self.PIECE_SYSTEM
        if not persona:
            return self.PIECE_SYSTEM
        skills = director_skills()
        return f"{persona}\n\n" + (f"{skills}\n\n" if skills else "") + "Be concise. Reply with JSON only."

    def _progress(self) -> str:
        """How the set is going against the persona's goal: tracks so far, the
        host's thumbs and requests since the set began."""
        up, down, last = taste.reactions_since(self.set_started)
        asked = [t for t in taste.requests(n=3, since=self.session_start) if t] if up + down or self.tracks_this_set else []
        lines = [f"- Track {self.tracks_this_set + 1} of this set."]
        if up or down:
            lines.append(f"- The host's thumbs this set: {up} 👍, {down} 👎; the last: {'; '.join(last)}.")
        else:
            lines.append("- No thumbs from the host yet this set.")
        if asked:
            lines.append("- The host's latest requests: " + " | ".join(f'"{t}"' for t in asked))
        lines.append("- What moves the set toward your goal now: what should this track do that the last "
                     "ones didn't?")
        return "\n".join(lines)

    def _suggest_form(self, style: str, fallback: Piece) -> str:
        """A real song's form for the style's genre (music/forms.py), scaled to
        the track, none of the recent tracks' shapes."""
        from . import forms
        from .blocks import STYLE_SETTINGS

        target = self.track_minutes * 60 * fallback.cps
        edit_bars = STYLE_SETTINGS.get(style, {}).get("edit_bars", 16)
        form = forms.suggest(style, self.rng, target, fallback.period,
                             max_sections=max(5, min(10, int(target // edit_bars))), avoid=list(self.recent_forms))
        if not form:
            return "none for this style: shape it yourself."
        fam = forms.family(style)
        return (f"**{forms.describe(form)}** (a real {fam} song's sections, scaled to this track; "
                f"energies: {', '.join(f'{k} {e:g}' for k, _, e in form)})")

    def _touchstone_line(self, style: str | None, history: list[dict] | None = None) -> str:
        """One of the host's favourite songs in the style, none of the last few:
        the least tried when exploring, a Thompson draw when exploiting."""
        from . import explore, touchstones

        pool = [s for s in touchstones.songs() if style in s.get("styles", [])] if style else []
        pool = pool or touchstones.songs()
        fresh = [s["title"] for s in pool if s["title"] not in self.recent_touchstones] or [s["title"] for s in pool]
        title = explore.pick("touchstone", fresh, self._stance or "exploit", self.rng, history)
        self._touchstone = next((s for s in pool if s["title"] == title), None)
        return touchstones.describe(self._touchstone)

    def _subgenre_line(self, style: str) -> str:
        """The theme's subgenre: where every track stays, but an exploring one."""
        from .blocks import SUBGENRES as _SUBGENRES
        from .blocks import subgenre as _subgenre

        sub = subgenre_from_theme(style, self.theme)
        if not sub:
            return ""
        modes = "/".join(_subgenre(style, sub).get("modes") or STYLES[style]["modes"])
        if self._stance == "explore":
            others = ", ".join(n for n in _SUBGENRES.get(style, {}) if n != sub)
            return (f" Home subgenre: {sub} (the theme's; its modes {modes}). This track explores: stay home, or "
                    f"take another {style} subgenre ({others}) if your concept wants it, and write `subgenre`.")
        return f" Subgenre: {sub} (the theme asks for it: this track is one; its modes {modes})."

    def _signature_line(self) -> str:
        recent = ", ".join(self.recent_signatures) or "none yet"
        return ("**Signature voice.** The hook's sound is what makes this track recognisable in its first "
                f"minute: choose one not heard as a hook lately ({recent}), from the whole palette, even outside "
                "the subgenre's list, and build the track around its character (a sitar wants a different riff "
                "than a glass bell). The real recordings (the sounds described as \"real\": a grand piano, a "
                "string section, an oboe, a harmonica, an mbira, a woman's voice, vocal chops ...) are the most distinctive: prefer one. Write "
                "for the instrument: a bowed or blown line breathes in long notes, a plucked or struck one can "
                "move fast. The other parts can stay in the subgenre's palette.")

    def _choose_arms(self, style: str, history: list[dict]) -> tuple[str, str]:
        """This track's stance, spark, mode and tempo band (music/explore.py)."""
        from . import explore

        self._stance = explore.choose_stance(self.rng, history, self.tracks_this_set)
        sparks = [sp for sp in SPARKS if sp not in self.recent_sparks] or SPARKS
        self._spark = explore.pick("spark", sparks, self._stance, self.rng, history)
        modes = self._fresh_modes(style)
        if self._stance != "explore":
            # Staying in the theme's subgenre: only its modes (ambient house: major/lydian).
            from .blocks import subgenre as _subgenre

            home = _subgenre(style, subgenre_from_theme(style, self.theme)).get("modes")
            if home:
                modes = [m for m in modes if m in home] or list(home)
        mode = explore.pick("mode", modes, self._stance, self.rng, history)
        band = explore.pick("tempo", list(explore.TEMPO_BANDS), self._stance, self.rng, history)
        return mode, band

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
        from . import explore

        style = self.style if self.style in STYLES else None
        cfg = STYLES[style] if style else None
        history = explore.tracks()
        mode, band = self._choose_arms(style or fallback.style, history)
        return _load_prompt("director_piece.md").format(
            theme=self.theme, target_minutes=round(self.track_minutes, 1),
            style_line=(f"Style: {style} ({cfg['bpm'][0]}-{cfg['bpm'][1]} BPM, modes: {', '.join(cfg['modes'])})."
                        + self._subgenre_line(style)
                        if cfg else "Choose `style` from `styles` and include it."),
            options=json.dumps(director_vocabulary(style)),
            previous=json.dumps({k: previous.to_dict()[k] for k in
                                 ("title", "style", "subgenre", "key", "bpm", "progression", "mood", "hook")})
            if previous else "none (this is the first track)",
            previous_shape=" → ".join(f"{s.name} {s.bars}" for s in previous.sections)
            if previous and previous.sections else "none",
            bars_per_chord_line=(
                f"In {style}, each chord lasts {cfg['cycles_per_chord']} bar"
                + (f"s: with four chords the hook has {4 * cfg['cycles_per_chord']} bars, bars 1-{cfg['cycles_per_chord']} "
                   f"over the first chord, bars {cfg['cycles_per_chord'] + 1}-{2 * cfg['cycles_per_chord']} over the "
                   "second, and so on." if cfg["cycles_per_chord"] > 1 else ": one bar per chord.")
                if cfg else "Each chord lasts the style's `cycles_per_chord` bars."),
            recent_keys=", ".join(self.recent_keys) or "none",
            recent_hooks=", ".join(self.recent_hooks) or "none",
            melody_brief=(f"- {brief}\n" if (brief := melody_brief([h.split(": ", 1)[-1] for h in self.recent_hooks]))
                          else ""),
            # What the host liked (music/hooks.py), as rules to learn from, not tunes to copy.
            liked_hooks=("- Hooks the host liked (what they had; write something new with that quality): "
                         + "; ".join(liked) + "\n" if (liked := liked_hooks()) else ""),
            recent_progressions=", ".join(self.recent_progressions) or "none",
            recent_sounds="; ".join(", ".join(f"{r} {s}" for r, s in sorted(snd.items())) for snd in self.recent_sounds)
            or "none",
            avoid=", ".join(sorted(avoid["progressions"] | avoid["presets"])) or "nothing",
            # The host's thumbs and requests, matched to what played (music/taste.py).
            taste=taste.digest(since=self.session_start) or "Nothing remembered yet: go by the theme and the style.",
            progress=self._progress(),
            set_line=self._set_line(),
            suggested_key=self._suggest_key(style or fallback.style, previous, mode).name,
            suggested_bpm=self._suggest_bpm(style or fallback.style, band),
            recent_concepts=" | ".join(self.recent_concepts) or "none yet",
            recent_techniques=", ".join(self.recent_techniques) or "none yet",
            memory=journal.digest() or "Nothing yet: this is the first track you remember.",
            recent_frames="; ".join(self.recent_frames) or "none yet",
            surprise_line=self._surprise_line(),
            chromatic_line=("Not this track: the last one was chromatic. Leave `chromatic` out."
                            if self._last_chromatic else
                            "This track may be chromatic, if the theme and your plan want that colour (most "
                            "tracks don't): write `chromatic`, or leave it out."),
            spark=self._spark,
            suggested_form=self._suggest_form(style or fallback.style, fallback),
            touchstone=self._touchstone_line(style, history),
            stance=explore.brief(self._stance, history) + "\n\n" + self._signature_line(),
            # Above it the brief, the same for every track: Claude caches it.
            cache_break=CACHE_BREAK,
        )

    def _recommend(self, piece: Piece, data: dict) -> None:
        """Show the Director's recommendation (its sounds, tension arc and bold
        choice); the arc becomes the track's idea, which the Performer reads."""
        rec = data.get("recommendation")
        if not isinstance(rec, dict):
            return
        parts = {k: " ".join(str(rec.get(k) or "").split())[:300] for k in ("concept", "sounds", "arc", "bold")}
        if not isinstance(rec.get("techniques"), list):
            rec["techniques"] = []
        if not any(parts.values()):
            return
        if parts["concept"]:
            self.recent_concepts.append(parts["concept"][:160])
        # The advanced techniques it named (A1 ... in the skill), so the next tracks choose others.
        known = technique_ids()
        named = [t for t in rec.get("techniques") or [] if isinstance(t, str) and t.strip().upper() in known]
        for t in dict.fromkeys(t.strip().upper() for t in named):
            self.recent_techniques.append(t)
        if named:
            parts["techniques"] = ", ".join(f"{t} {known[t]}" for t in dict.fromkeys(t.strip().upper() for t in named))
        if parts["arc"] and not piece.idea:
            piece.idea = parts["arc"][:200]
        line = "🎨 Recommendation" + "".join(f"\n   {k}: {v}" for k, v in parts.items() if v)
        self.log(line)
        runtime.append_thought(line.replace("\n   ", "  | "))
        runtime.ui_event("director", what="Recommendation", decision=parts["concept"] or parts["bold"] or parts["arc"],
                         reason=" ".join(f"{k}: {v}" for k, v in parts.items()
                                         if v and v != (parts["concept"] or parts["bold"] or parts["arc"])),
                         model=llm.resolve(self.model_id)[1] if self.model_id else "built-in",
                         seconds=round(float(data.get("_seconds", 0) or 0), 1))

    def _fresh_lead(self, piece: Piece, fallback: Piece, avoid: dict, notes: list[str]) -> None:
        """No lead voice twice within three tracks: it's the sound people hear first."""
        from .blocks import LIVE_SOUNDS
        from .plan import STYLE_SOUNDS

        recent_leads = [snd.get("lead") for snd in list(self.recent_sounds)[-2:]]
        lead = piece.sounds.get("lead")
        if not lead or lead not in recent_leads:
            return
        options = [fallback.sounds.get("lead")] + STYLE_SOUNDS.get(piece.style, {}).get("lead", []) \
            + list(LIVE_SOUNDS.get("lead", {}))
        new = next((o for o in options if o and o not in recent_leads and o != lead
                    and o not in avoid.get("sounds", ()) and o in LIVE_SOUNDS.get("lead", {})
                    and self._fits_style(piece.style, o)), None)
        if new:
            notes.append(f"lead {lead} repeats one of the last two tracks'; using {new}")
            piece.sounds["lead"] = new

    def review_piece(self, data: dict, fallback: Piece, avoid: dict) -> tuple[Piece, list[str]]:
        """The model's plan made playable: validated, with every correction
        noted (an empty list means it was right as it was)."""
        minutes = self._chosen_minutes(data, notes_out := [])
        piece, notes = validate_piece(data, fallback, minutes)
        notes[:0] = notes_out
        piece.minutes = minutes
        if piece.chromatic and self._last_chromatic:
            notes.append(f"chromatic {piece.chromatic} dropped: the last track was chromatic")
            piece.chromatic = ""
            write_piece_hook(piece, self.rng, [h.split(": ", 1)[-1] for h in self.recent_hooks])
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

        # It's the set's home, not a cage: an exploring track may roam to
        # another subgenre of the style (music/explore.py).
        wanted = subgenre_from_theme(piece.style, self.theme)
        roams = (self._stance == "explore" and piece.subgenre != wanted
                 and piece.subgenre in _SUBGENRES.get(piece.style, {}))
        if roams:
            lo, hi = _SUBGENRES[piece.style][piece.subgenre]["bpm"]
            piece.bpm = float(min(hi, max(lo, piece.bpm)))
        elif wanted in _SUBGENRES.get(piece.style, {}) and piece.subgenre != wanted:
            notes.append(f"the theme asks for {wanted} {piece.style}, not {piece.subgenre or 'none'}")
            piece.subgenre = wanted
            lo, hi = _SUBGENRES[piece.style][wanted]["bpm"]
            piece.bpm = float(min(hi, max(lo, piece.bpm)))
        signature_role = (piece.hook or {}).get("role") or "lead"
        if wanted in _SUBGENRES.get(piece.style, {}) and piece.subgenre == wanted:
            # ...and its sounds (Saltwater's lead is the pluck or the nylon guitar, not any lead),
            # all but the hook's voice: the track's signature may come from anywhere (below).
            from .blocks import subgenre_sounds as _subgenre_sounds

            recent = {r: [snd.get(r) for snd in self.recent_sounds] for r in ("chords", "lead", "arp", "keys", "texture")}
            for role, options in _subgenre_sounds(piece.style, wanted).items():
                if role == signature_role:
                    continue
                if piece.sounds.get(role) and piece.sounds[role] not in options:
                    new = next((o for o in options if o not in recent.get(role, [])), options[0])
                    notes.append(f"{role} {piece.sounds[role]} isn't a {wanted} sound; using {new}")
                    piece.sounds[role] = new
        self._signature(piece, signature_role, avoid, notes)
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
        self._fresh_lead(piece, fallback, avoid, notes)
        # Never a sound the host pushed away (taste.DISLIKED): the fallback's, chosen without them.
        for role, synth in list(piece.sounds.items()):
            if synth in avoid.get("sounds", ()) and fallback.sounds.get(role, synth) != synth:
                notes.append(f"{role} {synth}: the host disliked it; using {fallback.sounds[role]}")
                piece.sounds[role] = fallback.sounds[role]
        if piece.sounds and any(piece.sounds == recent for recent in self.recent_sounds):
            # The same set as a recent track: the fallback's (themed, fresh) sound for one part.
            for role in ("lead", "arp", "keys", "chords"):
                if fallback.sounds.get(role) and fallback.sounds[role] != piece.sounds.get(role):
                    notes.append(f"sounds repeat a recent track's; {role} -> {fallback.sounds[role]}")
                    piece.sounds[role] = fallback.sounds[role]
                    break
        _distinct_sections(piece, self.rng)
        # A hook written against the key must fit the progression as it ended up
        # (the key or the chords may have been changed above); else a new one.
        if piece.hook.get("relative") == "key":
            clash = key_harmony_problem(piece.hook["pattern"], progression_degrees(piece), piece.cycles_per_chord)
            if clash:
                notes.append(f"the hook no longer fits {piece.progression} ({clash}); wrote one that does")
                write_piece_hook(piece, self.rng, [h.split(": ", 1)[-1] for h in self.recent_hooks])
        piece.narration = clean_narration(data.get("narration"))
        if not piece.narration:
            # Said or not, the listeners hear why: the concept the track is built on.
            rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
            piece.narration = clean_narration(rec.get("concept"))
        return piece, notes

    def _signature(self, piece: Piece, role: str, avoid: dict, notes: list[str]) -> None:
        """The hook's voice is the track's signature: none of the last tracks'
        (recent_signatures), from the style's whole palette, the subgenre's
        sounds first."""
        from .blocks import LIVE_SOUNDS, subgenre_sounds

        palette = LIVE_SOUNDS.get(role, {})
        now = piece.sounds.get(role)
        disliked = set(avoid.get("sounds", ()))
        if now in palette and now not in self.recent_signatures and now not in disliked \
                and self._fits_style(piece.style, now):
            return
        # Recordings first (blocks.REAL_SOUNDS: "real"): nothing is more its own voice.
        real = [o for o, about in palette.items() if "real" in about]
        options = [*self.rng.sample(real, len(real)), *subgenre_sounds(piece.style, piece.subgenre).get(role, []),
                   *palette]
        fresh = [o for o in dict.fromkeys(options) if o in palette and o not in self.recent_signatures
                 and o not in disliked and o != now and self._fits_style(piece.style, o)]
        if fresh:
            new = self.rng.choice(fresh[:4])
            if now:
                why = ("the host disliked it" if now in disliked else "a recent track's signature"
                       if now in self.recent_signatures else f"not a {piece.style} sound")
                notes.append(f"{role} {now}: {why}; using {new}")
            piece.sounds[role] = new

    @staticmethod
    def _fits_style(style: str, synth: str) -> bool:
        from .blocks import sound_fits_style

        return sound_fits_style(style, synth)

    def close_hook(self, pattern: str) -> tuple[str, float] | None:
        """The recent tune this one is too close to (and how close), or None:
        alike in its notes, or opening on the same two-bar rhythm."""
        from .hooks import opening_rhythm

        opening = opening_rhythm(pattern)
        if opening.count("|") == 1:
            for h in self.recent_hooks:
                if opening_rhythm(h.split(": ", 1)[-1]) == opening:
                    return h, 1.0
        scored = [(h, hook_similarity(pattern, h.split(": ", 1)[-1])) for h in self.recent_hooks]
        best = max(scored, key=lambda x: x[1], default=None)
        return best if best and best[1] >= HOOK_SIMILAR else None

    def weak_hook(self, piece: Piece) -> tuple[float, list[str]] | None:
        """(score, what it misses) for a phrase hook under music/hooks.GOOD, or None."""
        if not piece.hook or piece.hook.get("kind") != "phrase":
            return None
        key = piece.hook.get("relative") == "key"
        a = analyze_hook(piece.hook["pattern"], progression_degrees(piece) if key else None, piece.cycles_per_chord,
                         piece.melody)
        return (a["score"], a["problems"]) if a and a["score"] < HOOK_GOOD else None

    def _writer_instead(self, piece: Piece, weak: tuple[float, list[str]] | None) -> None:
        """A weak hook the model couldn't better: the hook writer's
        (music/hooks.py), when it scores higher and the track has a phrase part."""
        if weak is None or piece.surprise == "three_note_hook":
            return
        trial = type(piece).from_dict(piece.to_dict())
        if write_piece_hook(trial, self.rng, [h.split(": ", 1)[-1] for h in self.recent_hooks]) \
                and (self.weak_hook(trial) or (1.0,))[0] > weak[0]:
            self.log(f"🎣 Written hook instead: {trial.hook['pattern']}")
            piece.hook = trial.hook

    def _freshen_hook(self, piece: Piece, prompt: str) -> None:
        """A hook that is a recent one again (Claude, shown its last tunes,
        still tends to write their sibling): ask once more, for the hook
        alone, with the plan as it stands; keep the old one if the new one
        isn't fresher or doesn't fit."""
        if not piece.hook:
            return
        close = self.close_hook(piece.hook["pattern"])
        weak = self.weak_hook(piece)
        rejected = getattr(self, "_hook_rejected", "")
        self._hook_rejected = ""
        if close is None and weak is None and not rejected:
            return
        role, old = piece.hook["role"], piece.hook["pattern"]
        if rejected:
            # Its hook didn't fit the chords (the written one stands in): once more,
            # with which chord sounds in which bar.
            from .hooks import bar_chords

            weak = self.weak_hook(piece) or (1.0, [])
            self.log(f"[Director] the hook didn't fit the chords ({rejected[:120]}); asking for another")
            role = piece.hook["role"]
            why_new = (f"Your hook didn't fit the chords: {rejected}. Each chord lasts {piece.cycles_per_chord} "
                       f"bar{'s' if piece.cycles_per_chord > 1 else ''}: "
                       + bar_chords(piece.progression, progression_degrees(piece), piece.cycles_per_chord)
                       + ". On the beats play that bar's tones (key degrees).")
            old = "(rejected)"
        elif close is not None:
            self.log(f"[Director] the hook {old} is {close[1]:.0%} like a recent one ({close[0]}); asking for another")
            why_new = (f"It is too close to a tune played recently ({close[0]}): write one that differs from every "
                       "recent tune in its rhythm, its register and its ending.")
        else:
            self.log(f"[Director] the hook {old} isn't memorable enough (melody {weak[0]:.2f}: "
                     f"{'; '.join(p.split(':')[0] for p in weak[1])}); asking for another")
            what = "a hymn people sing along to" if piece.melody == "hymn" else "a hook memorable"
            why_new = (f"It misses what makes {what}: " + "; ".join(weak[1])
                       + ". Write one that has these, by the same rules.")
        ask = (f"{prompt}\n\n## One change to your plan\n\nYou planned '{piece.title}' in {piece.key} on "
               f"{piece.progression}, the hook on the {role}{' (a hymn)' if piece.melody == 'hymn' else ''}: {old}. "
               f"{why_new} Keep the rest of the plan; write "
               f"only a new hook for the {role}. Reply with JSON "
               f'only: {{"hook": {{"role": "{role}", "pattern": "..."}}, "reason": "what is new about it"}}')
        data = self._ask("hook", self.system, ask, cache_ttl="1h") if self._llm_available() else None
        hook, why = validate_hook((data or {}).get("hook"), piece.style, progression_degrees(piece),
                                  piece.cycles_per_chord)
        hook.pop("fitted", None)
        if rejected and hook and hook["role"] == role:
            a = analyze_hook(hook["pattern"], progression_degrees(piece) if hook.get("relative") == "key" else None,
                             piece.cycles_per_chord, piece.melody)
            if a and a["score"] >= weak[0] - 0.1:
                self.log(f"🎣 Hook, fitted to the chords: {hook['pattern']}")
                piece.hook = hook
            else:
                self.log("[Director] kept the written hook: the new one scores lower")
            return
        if not hook or hook["role"] != role:
            self.log(f"[Director] kept the hook: the new one didn't fit ({why or 'another part'})")
            return self._writer_instead(piece, weak)
        from .surprises import hook_notes

        if piece.surprise == "three_note_hook" and hook_notes(hook["pattern"]) > 3:
            self.log("[Director] kept the hook: the new one has more than three notes (three-note-hook surprise)")
            return self._writer_instead(piece, weak)
        again = self.close_hook(hook["pattern"])
        if close is not None and again and again[1] >= close[1]:
            self.log(f"[Director] kept the hook: the new one {hook['pattern']} is no fresher")
            return
        if weak is not None:
            new = self.weak_hook(type(piece).from_dict({**piece.to_dict(), "hook": hook}))
            if new is not None and new[0] <= weak[0]:
                self.log(f"[Director] kept the hook: the new one {hook['pattern']} scores no better ({new[0]:.2f})")
                return self._writer_instead(piece, weak)
        self.log(f"🎣 Fresher hook: {hook['pattern']}")
        piece.hook = hook

    def _pace_length(self, piece: Piece) -> None:
        """A subgenre that takes its time (blocks.SUBGENRES[..]["pace"]["minutes"],
        ambient house about 8.5) gets that length, as far as the set's time
        allows; its sections stretch to it, and the set plan refits the rest."""
        from .blocks import subgenre

        want = float(subgenre(piece.style, piece.subgenre).get("pace", {}).get("minutes") or 0)
        current = piece.minutes or self.track_minutes
        if self.session_end:
            want = min(want, max(0.0, (self.session_end - time.time()) / 60))
        if not want or want <= current + 0.25:
            return
        factor = want / current
        for section in piece.sections:
            section.bars = max(piece.period, round(section.bars * factor / piece.period) * piece.period)
        piece.minutes = self.track_minutes = round(want, 1)
        self.log(f"🗓 {piece.subgenre} takes its time: {piece.minutes:g} min")

    def plan_set(self) -> None:
        """Before each track of a set with an end time: the lengths of the
        tracks still to come (plan.set_plan), from the time actually left and
        the last assumption; this track gets the first. Without an end time,
        every track is target_minutes long."""
        if not self.session_end:
            self.track_minutes, self.set_plan = self.target_minutes, []
            return
        from .plan import TYPICAL_MINUTES, set_plan

        now = time.time()
        remaining = max(0.0, (self.session_end - now) / 60)
        elapsed = max(0.0, (now - self.session_start) / 60)
        typical = TYPICAL_MINUTES.get(self.style or "", self.target_minutes)
        self.set_plan = set_plan(remaining, elapsed, typical,
                                 [self.track_minutes] + self.assumed if self.assumed else None)
        self.track_minutes = self.set_plan[0]
        line = (f"🗓 Set plan: {len(self.set_plan)} track{'s' if len(self.set_plan) > 1 else ''} in the "
                f"{remaining:.0f} min left of {elapsed + remaining:.0f}: "
                + ", ".join(f"{m:g}" for m in self.set_plan) + " min")
        self.log(line)
        runtime.append_thought(line)

    def _set_line(self) -> str:
        """The set's time for the brief, or how long tracks are without one."""
        if not self.session_end:
            return (f"This stream has no end time: tracks are about {self.target_minutes:g} minutes. Leave "
                    "`minutes` and `set_plan` out.")
        now = time.time()
        remaining, elapsed = max(0.0, (self.session_end - now) / 60), max(0.0, (now - self.session_start) / 60)
        rest = self.set_plan[1:]
        return (f"This set runs {elapsed + remaining:.0f} minutes: {elapsed:.0f} played, {remaining:.0f} left. "
                f"The working assumption: this track {self.track_minutes:g} min"
                + (f", then {len(rest)} more: {', '.join(f'{m:g}' for m in rest)} min" if rest else
                   ", the last of the set")
                + ". Decide this track's length as `minutes` (2.5-10) and your assumption for the tracks after "
                  "it as `set_plan` (minutes each), adding up to the time left, so the set ends on time. A set "
                  "breathes like a club night: shorter tracks to open and close, longer ones at peak time; a "
                  "track with a journey (a long build, a long breakdown) earns its length, a punchy one doesn't "
                  "need it.")

    def _chosen_minutes(self, data: dict, notes: list[str]) -> float:
        """The track's length the Director chose (`minutes`) and its assumption
        for the rest of the set (`set_plan`), checked against the time left;
        the working assumption where they don't fit."""
        if not self.session_end:
            return self.track_minutes
        from .plan import TRACK_MINUTES

        lo, hi = TRACK_MINUTES
        remaining = max(lo, (self.session_end - time.time()) / 60)
        try:
            minutes = float(data.get("minutes") or self.track_minutes)
        except (TypeError, ValueError):
            minutes = self.track_minutes
        minutes = round(min(hi, max(lo, minutes), max(lo, remaining)), 1)
        rest = data.get("set_plan")
        if isinstance(rest, list) and all(isinstance(m, (int, float)) for m in rest):
            rest = [min(hi, max(lo, float(m))) for m in rest]
            if abs(minutes + sum(rest) - remaining) <= max(3.0, 0.1 * remaining):
                self.assumed = [round(m, 1) for m in rest]
            else:
                notes.append(f"set plan {minutes:g} + {sum(rest):g} min doesn't fit the {remaining:.0f} left; "
                             "keeping the working assumption")
                self.assumed = self.set_plan[1:]
        else:
            self.assumed = self.set_plan[1:]
        if minutes != self.track_minutes:
            self.log(f"🗓 This track: {minutes:g} min (the assumption was {self.track_minutes:g})")
        self.track_minutes = minutes
        return minutes

    def next_piece(self, previous: Piece | None) -> Piece:
        self.plan_set()
        avoid = feedback.disliked()
        avoid["sounds"] = taste.disliked_sounds()
        fallback = plan_piece(self.rng, previous, style=self.style,
                              avoid_progressions=set(self.recent_progressions),
                              disliked=avoid["progressions"],
                              target_minutes=self.track_minutes,
                              theme=self.theme, recent_sounds=list(self.recent_sounds),
                              disliked_sounds=avoid["sounds"])
        piece = fallback
        piece.minutes = self.track_minutes
        self._stance, self._spark, self._touchstone = "", "", None
        self.assumed = self.set_plan[1:]          # (kept unless the Director decides otherwise)
        data = None
        if self._llm_available():
            prompt = self.piece_prompt(previous, fallback, avoid)
            # A track is ~5 minutes: the 5-minute cache would just miss it.
            self.log(f"[Director] this track: {self._stance}")
            data = self._ask("piece", self.system, prompt, cache_ttl="1h")
            if data:
                piece, notes = self.review_piece(data, fallback, avoid)
                self._recommend(piece, data)
                self._hook_rejected = next((n[len("hook rejected ("):-1] for n in notes if n.startswith("hook rejected (")), "")
                self.choose_surprise(piece, data, notes)
                self._freshen_hook(piece, prompt)
                notes.extend(resolve_surprise_cues(piece))
                if notes:
                    self.log(f"[Director] corrected LLM piece plan: {'; '.join(notes)}")
                self._think("Next piece", f"'{piece.title}', {piece.style}, {piece.key}, {piece.bpm:.0f} BPM, "
                                          f"{piece.progression}, {piece.mood}"
                                          + (", a hymn" if piece.melody == "hymn" else "")
                                          + (f", chromatic {piece.chromatic}" if piece.chromatic else ""), data)
        if not data:
            self._fresh_lead(piece, piece, avoid, [])
            self.choose_surprise(piece, None, [])
            # The built-in titles come from a few words: not one of the last few.
            from .plan import _title, theme_moods
            for _ in range(8):
                if piece.title not in self.recent_titles:
                    break
                piece.title = _title(self.rng, theme_moods(self.theme))
        self._pace_length(piece)
        self.recent_progressions.append(piece.progression)
        self.recent_keys.append(piece.key)
        self.recent_bpms.append(piece.bpm)
        self.recent_frames.append(f"{piece.key} {piece.bpm:g} BPM{' ' + piece.subgenre if piece.subgenre else ''}")
        self.recent_titles.append(piece.title)
        self.tracks_this_set += 1
        if self._touchstone:
            self.recent_touchstones.append(self._touchstone["title"])
        if self._spark:
            self.recent_sparks.append(self._spark)
        self._remember(piece, data)
        signature = piece.sounds.get((piece.hook or {}).get("role") or "lead")
        if signature:
            self.recent_signatures.append(signature)
        self._record_choices(piece)
        if piece.sections:
            from .forms import shape

            self.recent_forms.append(shape(piece.sections))
        self._last_chromatic = bool(piece.chromatic)
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

    def _remember(self, piece: Piece, data: dict | None) -> None:
        """The track's plan, shown (her reasoning) and kept in her journal
        (music/journal.py), and held for developing it live."""
        rec = (data or {}).get("recommendation") if isinstance((data or {}).get("recommendation"), dict) else {}
        reasoning = " ".join(str((data or {}).get("reasoning") or "").split())[:900]
        techniques = [t.strip().upper() for t in rec.get("techniques") or [] if isinstance(t, str)
                      and t.strip().upper() in technique_ids()]
        self._plan = {"reasoning": reasoning, "concept": " ".join(str(rec.get("concept") or "").split())[:300],
                      "arc": piece.idea or " ".join(str(rec.get("arc") or "").split())[:300],
                      "techniques": techniques}
        self._track_t0 = time.time()
        if reasoning:
            self.log(f"🧠 Reasoning: {reasoning}")
            runtime.append_thought(f"🧠 {reasoning}")
            runtime.ui_event("director", what="Reasoning", decision=reasoning[:400], reason="", seconds=0,
                             model=llm.resolve(self.model_id)[1] if self.model_id else "built-in")
        journal.write("track", piece.title, reasoning=reasoning, concept=self._plan["concept"],
                      techniques=techniques, stance=self._stance or "built-in", key=piece.key, bpm=piece.bpm,
                      style=piece.style, subgenre=piece.subgenre, sounds=dict(piece.sounds or {}),
                      form=" ".join(s.name for s in piece.sections or []))

    def develop_section(self, piece: Piece, plan: list, step: int, code: list[str], edits: list[str],
                        planned: list[dict]) -> dict | None:
        """Live: the track is at plan step `step`; hear what plays (`code`,
        the performer's `edits`, the host's reactions since it began) and
        decide how it develops into the next step. Returns {"decision",
        "listening", "cues": validated cues for that phase, or None to keep
        the plan}, or None without a model or when there's no next step."""
        if not (config.DIRECTOR_DEVELOP and self._llm_available()) or step + 1 >= len(plan):
            return None
        from .plan import validate_cues

        next_phase, bars = plan[step + 1][0], plan[step + 1][1]
        names = {"groove2": "peak"}
        form = " → ".join(("▶ " if i == step else "→ " if i == step + 1 else "") + f"{names.get(p, p)} {b}"
                          for i, (p, b) in enumerate(plan))
        up, down, last = taste.reactions_since(self._track_t0 or time.time())
        asked = [str(r.get("text", "")) for r in taste._records("requests.jsonl", self._track_t0 or time.time())
                 if r.get("text")]
        reactions = (f"{up} 👍, {down} 👎" + (f" (the last: {'; '.join(last)})" if last else "")
                     if up or down else "no thumbs yet") + (
            "; asked: " + " | ".join(f'"{a}"' for a in asked[-3:]) if asked else "")
        vocab = director_vocabulary(piece.style)
        options = {k: vocab[k] for k in ("blocks", "knobs") if k in vocab}
        options["sounds"] = {role: list(v) for role, v in (vocab.get("sounds") or {}).items()}
        prompt = _load_prompt("director_develop.md").format(
            title=piece.title, concept=self._plan.get("concept") or "(not written)",
            reasoning=self._plan.get("reasoning") or "(none)", arc=self._plan.get("arc") or piece.idea or "(none)",
            techniques=", ".join(f"{t} {technique_ids().get(t, '')}" for t in self._plan.get("techniques") or [])
            or "none named",
            frame=f"{piece.style}{' ' + piece.subgenre if piece.subgenre else ''}, {piece.key}, {piece.bpm:g} BPM",
            hook_role=(piece.hook or {}).get("role", "?"), hook=(piece.hook or {}).get("pattern", "(none)"),
            sounds=", ".join(f"{r} {s}" for r, s in (piece.sounds or {}).items()) or "the style's",
            form=form, code="\n".join(code) or "(silence)",
            edits="\n".join(f"- {e}" for e in edits[-8:]) or "- (nothing yet)",
            reactions=reactions,
            next_phase=names.get(next_phase, next_phase), bars=bars,
            planned=json.dumps(planned) if planned else "nothing yet: write it now",
            options=json.dumps(options))
        data = self._ask("develop", self.system, prompt, cache_ttl="1h")
        if not data:
            return None
        decision = " ".join(str(data.get("decision") or "").split())[:400]
        listening = " ".join(str(data.get("listening") or "").split())[:300]
        cues = None
        if not data.get("keep"):
            valid, notes = validate_cues({next_phase: data.get("cues") or []}, piece.style)
            cues = valid.get(next_phase) or None
            if notes:
                self.log(f"[Director] corrected live cues: {'; '.join(notes)}")
        self.log(f"🎛  Develop → {names.get(next_phase, next_phase)}: {decision or '(no decision)'}"
                 + ("" if cues else " (the plan stands)"))
        runtime.append_thought(f"🎛 {names.get(next_phase, next_phase)}: {decision}")
        runtime.ui_event("director", what=f"Develop: {names.get(next_phase, next_phase)}",
                         decision=decision or "the plan stands", reason=listening,
                         model=llm.resolve(self.model_id)[1] if self.model_id else "built-in",
                         seconds=round(float(data.get("_seconds", 0) or 0), 1))
        journal.write("develop", piece.title, phase=names.get(next_phase, next_phase), decision=decision,
                      listening=listening, cues=cues or [])
        return {"decision": decision, "listening": listening, "cues": cues, "phase": next_phase}

    def _record_choices(self, piece: Piece) -> None:
        """What the track used, for its thumbs to reward (music/explore.py)."""
        from . import explore

        try:
            mode = Key.parse(piece.key).mode
        except (ValueError, KeyError):
            mode = None
        lo, hi = STYLES[piece.style]["bpm"] if piece.style in STYLES else (piece.bpm, piece.bpm)
        explore.record(piece.title, self._stance or "built-in",
                       {"touchstone": self._touchstone["title"] if self._touchstone else None,
                        "spark": self._spark or None, "mode": mode,
                        "tempo": explore.tempo_band(piece.bpm, lo, hi)})
        if self._stance:
            runtime.ui_event("director", what="Explore or exploit", decision=self._stance,
                             reason=f"touchstone {self._touchstone['title'] if self._touchstone else 'none'}, "
                                    f"{mode or '?'} mode, {explore.tempo_band(piece.bpm, lo, hi)} tempo",
                             seconds=0)

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
