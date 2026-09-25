#!/usr/bin/env python3
"""
Build a fine-tuning dataset from what the stream actually played, keeping
only sections that weren't rated down (by humans or the quality monitor).

    python -m eval.export_dataset > dataset.jsonl

Each line: {"instruction": <musical intent>, "input": <song state>,
            "output": <Tidal code that played>}.
This replaces the old random-template tidal_10k_dataset.csv, which taught the
invalid `scale "<root> <mode>"` idiom and single-note patterns.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from music import feedback  # noqa: E402


def main() -> None:
    history_path = os.path.join(config.RUNTIME_DIR, "history.jsonl")
    if not os.path.exists(history_path):
        sys.exit("no runtime/history.jsonl yet")
    downvoted = set()
    for rec in feedback.load():
        if rec.get("rating", 0) < 0 and rec.get("piece"):
            downvoted.add((rec["piece"].get("title"), rec["piece"].get("seed"), rec.get("section_index")))
            downvoted.add((rec["piece"].get("title"), rec["piece"].get("seed"), None))
    count = 0
    with open(history_path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            piece, idx = rec["piece"], rec["section_index"]
            if (piece["title"], piece.get("seed"), idx) in downvoted or \
                    (piece["title"], piece.get("seed"), None) in downvoted:
                continue
            section = piece["sections"][idx]
            layers = {r: p for r, p in rec["arrangement"]["layers"].items() if p}
            instruction = (f"{piece['style']} {section['name']} section, {piece['mood']}, energy "
                           f"{section['energy']}: layers {', '.join(f'{r} ({p})' for r, p in layers.items())}")
            state = f"KEY: {piece['key']}\nPROGRESSION: {piece['progression']}\nBPM: {piece['bpm']}"
            print(json.dumps({"instruction": instruction, "input": state, "output": "\n".join(rec["code"])}))
            count += 1
    print(f"exported {count} sections", file=sys.stderr)


if __name__ == "__main__":
    main()
