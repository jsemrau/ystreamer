#!/usr/bin/env python3
"""
Turn the session logs (sessions/*.jsonl) into learning examples: for every
edit that played, the state of the artefact before it, the move, and why.

    python -m eval.export_edits > edits.jsonl                  # every edit
    python -m eval.export_edits --style trance --source llm    # the model's own moves in trance
    python -m eval.export_edits --summary                      # what's in the memory so far

Each line: {"session", "style", "track", "phase", "program_before" (Tidal
code), "edit" (the op as JSON), "describe", "listening", "reason",
"source", "complexity_before", "complexity_after", "judgement"}. Useful as
retrieval memory (past moves in a similar state) or as a fine-tuning set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music import sessionlog  # noqa: E402

SOURCES = {"llm": ("LLM", "LLM, judged"), "judged": ("LLM, judged", "built-in, judged"),
           "builtin": ("built-in", "built-in, judged"), "jevtypesafe": ("jevtypesafe",)}


def examples(directory: str | None = None, style: str | None = None, source: str | None = None):
    """(state before, edit, why) for every edit that played, in order."""
    before: dict[str, list[str]] = {}      # session -> code before the next edit
    for record in sessionlog.iter_records(directory, {"track_start", "edit"}):
        session = record["session"]
        if record["type"] == "track_start":
            before[session] = record.get("code", [])
            continue
        code_before = before.get(session, [])
        before[session] = record.get("code", [])
        if style and record.get("style") != style:
            continue
        if source and record.get("source") not in SOURCES[source]:
            continue
        yield {
            "session": session, "style": record.get("style"), "track": record.get("track"),
            "phase": record.get("phase"), "program_before": "\n".join(code_before),
            "edit": {k: record.get(k) for k in ("op", "role", "name", "value", "block") if record.get(k)},
            "describe": record.get("describe"), "listening": record.get("listening"),
            "reason": record.get("reason"), "source": record.get("source"),
            "complexity_before": record.get("complexity_before"),
            "complexity_after": record.get("complexity_after"), "judgement": record.get("judgement"),
        }


def summary(directory: str | None = None) -> str:
    sessions, tracks, rejected = set(), 0, Counter()
    edits, by_style = Counter(), Counter()
    for record in sessionlog.iter_records(directory):
        sessions.add(record["session"])
        if record["type"] == "track_start":
            tracks += 1
        elif record["type"] == "edit":
            edits[record.get("source", "?")] += 1
            by_style[record.get("style", "?")] += 1
        elif record["type"] == "rejected":
            rejected[record.get("why", "?").split(":")[0]] += 1
    lines = [f"{len(sessions)} sessions, {tracks} tracks, {sum(edits.values())} edits "
             f"({sum(rejected.values())} rejected)"]
    lines.append("edits by source: " + ", ".join(f"{k} {v}" for k, v in edits.most_common()))
    lines.append("edits by style:  " + ", ".join(f"{k} {v}" for k, v in by_style.most_common()))
    if rejected:
        lines.append("rejected:        " + ", ".join(f"{k} {v}" for k, v in rejected.most_common()))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="session log directory (default: sessions/)")
    ap.add_argument("--style", default=None)
    ap.add_argument("--source", choices=sorted(SOURCES), default=None)
    ap.add_argument("--summary", action="store_true", help="print what the memory holds and exit")
    args = ap.parse_args()
    if not sessionlog.session_files(args.dir):
        sys.exit(f"no session logs in {args.dir or sessionlog.SESSIONS_DIR} yet")
    if args.summary:
        print(summary(args.dir))
        return
    for example in examples(args.dir, args.style, args.source):
        print(json.dumps(example, ensure_ascii=False))


if __name__ == "__main__":
    main()
