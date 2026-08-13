#!/usr/bin/env python3
"""
Headless, autonomous driver for the Tidal music-generation backend.

Runs the composer / creative-director agent loop from an initial musical
prompt with no interactive UI. Logs to stdout and a per-run logfile.
"""

import argparse
import os
import signal
import sys
import time
from datetime import datetime

import tidal_backend as backend


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the Tidal music agent autonomously from an initial prompt."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Musical goal / initial prompt. May include 'KEY: ...' / 'SECTION: ...' "
             "metadata lines followed by one task per line.",
    )
    parser.add_argument(
        "--prompt-file",
        help="Read the (possibly multi-line) prompt from a file instead of the PROMPT argument.",
    )
    parser.add_argument("--composer-model", default="local/qwen-agentworld-35b")
    parser.add_argument("--director-model", default="local/qwen-agentworld-35b")
    parser.add_argument("--director-interval", type=int, default=8)
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()

    if not args.prompt and not args.prompt_file:
        parser.error("either PROMPT or --prompt-file is required")

    return args


def main():
    args = parse_args()
    prompt_text = open(args.prompt_file).read() if args.prompt_file else args.prompt

    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    log_file = open(log_path, "a")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)

    print(f"[orchestrator] Logging to {log_path}")
    print(f"[orchestrator] Starting with prompt: {prompt_text!r}")

    def handle_shutdown(signum, _frame):
        print(f"\n[orchestrator] Received signal {signum}, stopping...")
        if backend.get_is_running():
            backend.stop_system()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    result = backend.start_system(
        prompt_text,
        args.test_mode,
        args.director_interval,
        args.composer_model,
        args.director_model,
    )
    print(f"[orchestrator] {result}")

    try:
        while backend.get_is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    if backend.get_is_running():
        backend.stop_system()

    print("[orchestrator] Done.")


if __name__ == "__main__":
    main()
