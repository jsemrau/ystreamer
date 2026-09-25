#!/usr/bin/env python3
"""
Human feedback: rate what's playing right now.

    python -m eval.feedback down "melody too busy"
    python -m eval.feedback up "love this one"

Records the current piece + arrangement (from runtime/state.json) in
runtime/feedback.jsonl. Two net thumbs-down on a progression or preset and
the Director stops choosing it.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music import feedback, runtime  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rating", choices=["up", "down"])
    parser.add_argument("reason", nargs="?", default="")
    args = parser.parse_args()
    state = runtime.load_state()
    if not state:
        sys.exit("No runtime/state.json - is the engine running?")
    feedback.append({"source": "human", "rating": 1 if args.rating == "up" else -1, "reason": args.reason,
                     "piece": state["piece"], "arrangement": state["arrangement"],
                     "section_index": state.get("section_index")})
    piece = state["piece"]
    print(f"Recorded thumbs-{args.rating} for '{piece['title']}' ({piece['key']}, {piece['progression']})")


if __name__ == "__main__":
    main()
