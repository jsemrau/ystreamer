#!/usr/bin/env python3
"""
Headless driver for ystreamer.

Default mode runs the harmony engine forever: the Director (LLM or
deterministic) plans pieces and sections, the engine renders in-key Tidal
code and schedules it on phrase boundaries.

    python orchestrator.py                                   # local llama.cpp model (default)
    python orchestrator.py --llm claude --free-layer         # Claude (Anthropic API)
    python orchestrator.py --llm none                        # deterministic director, no LLM
    python orchestrator.py --llm jevtypesafe                 # jevtypesafeai.com (see .env)
    python orchestrator.py --llm claude --model claude-sonnet-5   # pick a specific model
    python orchestrator.py --resume                          # continue after a crash
    python orchestrator.py --dry-run --pieces 1              # print code, no audio
    python orchestrator.py --selftest                        # type-check all templates with GHCi
    python orchestrator.py --legacy "a relaxing lofi song"   # old free-form agent loop
"""

import argparse
import itertools
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime

import config


class _Tee:
    """Duplicate writes across multiple streams (stdout + a logfile)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


STYLE_CHOICES = ["lofi", "ambient", "downtempo", "piano", "deephouse"]


def parse_args():
    parser = argparse.ArgumentParser(description="Endless AI-directed TidalCycles music.")
    parser.add_argument("prompt", nargs="?", help="Legacy mode: musical goal / initial tasks.")
    parser.add_argument("--prompt-file", help="Legacy mode: read the prompt from a file.")
    parser.add_argument("--legacy", action="store_true", help="Run the old free-form composer agent loop.")

    engine = parser.add_argument_group("engine mode")
    engine.add_argument("--llm", choices=["none", "claude", "local", "jevtypesafe"],
                        default=os.getenv("YS_LLM", "local"),
                        help="Which LLM directs the music (default: local, or YS_LLM; 'none' = deterministic director).")
    engine.add_argument("--model", default=os.getenv("YS_LLM_MODEL", ""),
                        help="Model name for the chosen --llm (default: that provider's default model).")
    engine.add_argument("--director-model", default=os.getenv("YS_DIRECTOR_MODEL", ""),
                        help="Full model id, overrides --llm/--model (e.g. claude/claude-opus-5, "
                             "local/qwen-agentworld-35b, jevtypesafe/<model>, or any LiteLLM id).")
    engine.add_argument("--narrate", choices=["off", "pieces", "sections"],
                        default=os.getenv("YS_NARRATION") or "sections",
                        help="Speak the Director's lines: at every section, only when a piece starts, or off "
                             "(default: YS_NARRATION, else sections). Needs YS_TTS_VOICE.")
    engine.add_argument("--show-llm", action="store_true",
                        help="Also print the LLM's full raw replies (its decisions and reasons are always shown).")
    engine.add_argument("--style", choices=STYLE_CHOICES, default=os.getenv("YS_STYLE") or None,
                        help="Lock the stream to one style (default: YS_STYLE, else rotate).")
    engine.add_argument("--theme",
                        default=os.getenv("YS_THEME") or "chill music to relax, study and sleep to",
                        help="Free-text brief for the LLM Director (default: YS_THEME).")
    engine.add_argument("--piece-minutes", type=float, default=12.0)
    engine.add_argument("--free-layer", action="store_true",
                        help="Let the LLM compose one extra counter-melody layer (key-safe, type-checked).")
    engine.add_argument("--no-custom-synths", action="store_true",
                        help="Use stock SuperDirt synths instead of the ystreamer SynthDefs.")
    engine.add_argument("--seed", type=int, default=None)
    engine.add_argument("--resume", action="store_true", help="Resume the piece saved in runtime/state.json.")
    engine.add_argument("--record", action="store_true", help="Also record to MP3 (off by default; YouTube archives the stream).")
    engine.add_argument("--dry-run", action="store_true", help="Print the generated code on a simulated clock; no audio.")
    engine.add_argument("--pieces", type=int, default=None, help="Stop after N pieces (default: run forever).")
    engine.add_argument("--selftest", action="store_true", help="Type-check every template/preset with GHCi and exit.")

    legacy = parser.add_argument_group("legacy mode")
    legacy.add_argument("--composer-model", default="local/qwen-agentworld-35b")
    legacy.add_argument("--legacy-director-model", default="local/qwen-agentworld-35b")
    legacy.add_argument("--director-interval", type=int, default=config.LEGACY_DIRECTOR_INTERVAL)
    legacy.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()
    if args.style and args.style not in STYLE_CHOICES:  # defaults (YS_STYLE) aren't checked by argparse
        parser.error(f"YS_STYLE={args.style!r} is not one of {', '.join(STYLE_CHOICES)}")
    return args


def setup_logging() -> str:
    config.ensure_dirs()
    log_path = os.path.join(config.LOGS_DIR, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    log_file = open(log_path, "a")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)
    return log_path


# ---------------------------------------------------------------------------
# Legacy mode
# ---------------------------------------------------------------------------

def run_legacy(args) -> None:
    import tidal_backend as backend

    if not args.prompt and not args.prompt_file:
        sys.exit("legacy mode needs a PROMPT or --prompt-file")
    prompt_text = open(args.prompt_file).read() if args.prompt_file else args.prompt
    print(f"[orchestrator] Legacy mode, prompt: {prompt_text!r}")

    def handle_shutdown(signum, _frame):
        print(f"\n[orchestrator] Received signal {signum}, stopping...")
        if backend.get_is_running():
            backend.stop_system()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print(backend.start_system(prompt_text, args.test_mode, args.director_interval,
                               args.composer_model, args.legacy_director_model))
    try:
        while backend.get_is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    if backend.get_is_running():
        backend.stop_system()


# ---------------------------------------------------------------------------
# Engine mode
# ---------------------------------------------------------------------------

def director_model_id(args) -> str | None:
    if args.director_model:
        return args.director_model
    if args.llm and args.llm != "none":
        return f"{args.llm}/{args.model}" if args.model else args.llm
    return None


def build_director(args, rng):
    from music import llm
    from music.director import Director

    from music.plan import style_from_theme

    if not args.style:
        themed = style_from_theme(args.theme)
        if themed:
            args.style = themed
            print(f"[orchestrator] Theme {args.theme!r} → style {themed} (use --style to choose another)")
    model_id = director_model_id(args)
    if model_id:
        provider, model = llm.resolve(model_id)
        print(f"[orchestrator] Director LLM: {provider} / {model}")
    else:
        print("[orchestrator] Director LLM: none (deterministic director)")
    return Director(model_id, rng, theme=args.theme, show_llm=args.show_llm,
                    target_minutes=args.piece_minutes, style=args.style)


def run_dry(args) -> None:
    from music.engine import MusicEngine

    class SimClock:
        t = 0.0

        def now(self):
            return self.t

        def sleep(self, seconds):
            self.t += seconds

    sim = SimClock()
    rng = random.Random(args.seed if args.seed is not None else 1)

    def send(lines):
        stamp = time.strftime("%H:%M:%S", time.gmtime(sim.t))
        for line in lines:
            print(f"[{stamp}] {line}")
        return None

    from music.narrator import Narrator

    narrator = Narrator(mode=args.narrate, speak=False)
    engine = MusicEngine(send, build_director(args, rng), narrator=narrator,
                         use_custom_synths=not args.no_custom_synths,
                         free_layer=args.free_layer, now=sim.now, sleep=sim.sleep,
                         max_pieces=args.pieces or 1, director_grace=config.LLM_TIMEOUT_SECONDS + 10)
    engine.run()
    print(f"[dry-run] simulated {sim.t / 60:.1f} minutes")


def run_selftest(args) -> int:
    from music import presets as P
    from music.templates import HarmonyContext, render_layer, with_orbit
    from music.theory import PROGRESSIONS, Key, progression_chords
    from music.validator import TidalTypeChecker

    checker = TidalTypeChecker()
    if not checker.available:
        print("[selftest] GHCi with the Tidal library is not available; cannot type-check.")
        return 2
    rng = random.Random(0)
    failures = 0
    checked = 0
    seen: set[str] = set()
    variants_done: set[str] = set()  # modes whose ninths/fills/builds were checked
    for prog in PROGRESSIONS:
        for mode in prog.modes:
            key = Key(rng.randrange(12), mode)
            # The new constructs are the same for every progression, so check
            # them on the first progression of each mode only.
            full = mode not in variants_done
            variants_done.add(mode)
            # Plain; a mid-phrase verse (fills, turnarounds, movement); and a
            # build into a chorus (roll, kick/bass drop, filter rise).
            contexts = ({}, {"start": 12, "bars": 16, "section": "verse", "variation": 1, "movement": "lively"},
                        {"start": 40, "bars": 16, "section": "build", "next_section": "chorus"})
            for cpc, color, extra in itertools.product((1, 2), ("sevenths", "ninths"), contexts):
                if not full and (color != "sevenths" or extra):
                    continue
                ctx = HarmonyContext(key, progression_chords(key, prog, color=color), cpc,
                                     seed=rng.randrange(1000), **extra)
                for role, presets in P.PRESETS.items():
                    for preset in presets.values():
                        for energy in (0.2, 0.6, 0.9):
                            kwargs = {"notes_by_cycle": ["[0 _ ~ 2 ~ ~ 4 _]"] * (4 * cpc)} if role == "free" else {}
                            for custom in (True, False):
                                expr = render_layer(role, ctx, preset, energy, custom, **kwargs)
                                if expr is None or expr in seen:
                                    continue
                                seen.add(expr)
                                checked += 1
                                if checked % 500 == 0:
                                    print(f"[selftest] {checked} patterns checked...", flush=True)
                                error = checker.check(with_orbit(role, expr))
                                if error:
                                    failures += 1
                                    print(f"[selftest] FAIL {role}/{preset.name}: {expr}\n{error}\n")
    # Transition statements themselves need the BootTidal helpers, so check the
    # underlying Tidal transition functions exist with the expected types.
    for fn in ("Sound.Tidal.Transition.jumpMod", "Sound.Tidal.Transition.xfadeIn"):
        out = checker._run([f":t {fn}"])
        if out is None or "error" in out.lower():
            failures += 1
            print(f"[selftest] FAIL missing {fn}: {out}")
    checker.close()
    print(f"[selftest] checked {checked} patterns, {failures} failures")
    return 1 if failures else 0


def run_engine(args) -> None:
    import audio_stack
    from music import runtime
    from music.engine import MusicEngine

    audio_stack.set_logger(print)
    rng = random.Random(args.seed)
    director = build_director(args, rng)
    from music import llm

    model_id = director_model_id(args)
    uses_local_llm = bool(model_id) and llm.resolve(model_id)[0] == "local"
    if uses_local_llm:
        # Launch now so the model loads while SuperCollider and Tidal boot.
        audio_stack.ensure_local_llm_server(wait=False)

    print(audio_stack.start_superdirt())
    tidal = audio_stack.TidalSession()
    print(tidal.start())
    if tidal.boot_error:
        print("\n[orchestrator] Tidal could not start, so there is nothing to play.")
        print(f"[orchestrator] {tidal.boot_error}")
        tidal.stop()
        print(audio_stack.stop_superdirt())
        sys.exit(1)
    if uses_local_llm:
        # The first piece is planned by the LLM, so give the model time to load.
        audio_stack.wait_for_local_llm(config.LLM_LOAD_TIMEOUT)
    if args.record:
        print(audio_stack.start_recording()[0])

    type_checker = None
    if args.free_layer:
        from music.validator import TidalTypeChecker

        type_checker = TidalTypeChecker()
        print(f"[orchestrator] GHCi type checker available: {type_checker.available}")

    def send(lines):
        if not tidal.running():
            print("[orchestrator] Tidal session died; restarting it")
            tidal.start()
        return tidal.send(lines)

    from music.narrator import Narrator, tidal_ducker

    narrator = Narrator(duck=tidal_ducker(send), mode=args.narrate)
    if narrator.available:
        print(f"[orchestrator] Narration: {narrator.mode}, {narrator.style} voice ({os.path.basename(narrator.voice)})")
    engine = MusicEngine(send, director, use_custom_synths=not args.no_custom_synths, narrator=narrator,
                         free_layer=args.free_layer, type_checker=type_checker, max_pieces=args.pieces)

    def shutdown(signum, _frame):
        print(f"\n[orchestrator] Received signal {signum}, stopping...")
        engine.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    resume_state = runtime.load_state() if args.resume else None
    if args.resume and not resume_state:
        print("[orchestrator] --resume given but no runtime/state.json found; starting fresh")

    worker = threading.Thread(target=engine.run, kwargs={"resume_state": resume_state}, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(timeout=1)

    print("[orchestrator] Shutting down audio...")
    if args.record:
        print(audio_stack.stop_recording())
    print(tidal.stop())
    print(audio_stack.stop_superdirt())
    print("[orchestrator] Done.")


def main():
    args = parse_args()
    if args.selftest:
        sys.exit(run_selftest(args))
    if args.dry_run:
        run_dry(args)
        return
    log_path = setup_logging()
    print(f"[orchestrator] Logging to {log_path}")
    if args.legacy or args.prompt or args.prompt_file:
        run_legacy(args)
    else:
        run_engine(args)


if __name__ == "__main__":
    main()
