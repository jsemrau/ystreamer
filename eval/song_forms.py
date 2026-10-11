#!/usr/bin/env python3
"""
Song forms from real songs: which sections a genre uses, in what order, how
long, so tracks stop following one shape (music/forms.py plays from what
this learns).

    python -m eval.song_forms HARMONIX_DIR            # -> music/data/song_forms.json
    python -m eval.song_forms HARMONIX_DIR --tabs tabs/   # ...with the tabs' section markers too
    python -m eval.song_forms --report                # what's in the data file

HARMONIX_DIR is the Harmonix Set's `dataset` folder (github.com/urinieto/
harmonixset, MIT licence): 912 songs with section labels and downbeats, so
every section's length is counted in bars. Tabs and scores with section
markers ("Verse", "Chorus" in Guitar Pro) add their own forms.

Section labels come in many spellings ("prechorus", "instchorus", "gtr");
they are folded into the engine's section types (music/plan.SECTION_TYPES).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "music", "data", "song_forms.json")

# Genre families: the Harmonix genres behind each, and the engine styles they speak for.
FAMILIES = {
    "dance": {"genres": {"Dance/Electronic", "Funk/Disco"}, "styles": ["trance", "techno", "hardcore", "deephouse"]},
    "rock": {"genres": {"Rock", "Alternative", "Metal", "Grunge", "Punk", "Indie Rock", "Classic Rock", "Prog",
                        "Pop-Rock", "Blues"}, "styles": ["darkwave"]},
    "groove": {"genres": {"Hip-Hop", "R&B", "Reggaeton"}, "styles": ["lofi", "downtempo"]},
    "pop": {"genres": {"Pop", "Country", "World"}, "styles": ["ambient", "piano"]},
}


def section_type(label: str, bars: int) -> str | None:
    """A label as one of the engine's section types, or None (silence, count-ins)."""
    lab = re.sub(r"[\d_\s-]+$", "", label.lower()).replace("-", "").replace(" ", "").replace("_", "")
    if not lab or lab in ("silence", "end", "fadein", "count", "countin") or bars <= 0:
        return None
    if lab.startswith(("intro", "opening")):
        return "intro"
    if lab.startswith(("outro", "coda", "fadeout", "ending")):
        return "outro"
    if lab.startswith(("prechorus", "preverse", "build", "rise", "lift", "postverse")):
        return "lift"
    if lab.startswith(("postchorus", "tag", "hook")):
        return "tag"
    if "inst" in lab or lab.startswith(("solo", "gtr", "guitar", "mainriff", "riff", "interlude", "transition",
                                        "section", "drop")):
        return "interlude"
    if lab.startswith(("break", "bre", "quiet", "slow", "stutter", "stop")):
        return "break" if bars <= 4 else "breakdown"
    if "chorus" in lab or lab.startswith("refrain"):
        return "chorus"
    if lab.startswith(("verse", "raps", "rap", "miniverse", "versepart", "introverse")):
        return "verse"
    if lab.startswith(("bridge", "middle")):
        return "bridge"
    return None


def family_of(genre: str) -> str:
    return next((f for f, v in FAMILIES.items() if genre in v["genres"]), "pop")


def harmonix_forms(root: str) -> list[tuple[str, list[tuple[str, int]]]]:
    """(family, [(section type, bars), ...]) per song: neighbours of one type merged."""
    out = []
    with open(os.path.join(root, "metadata.csv"), encoding="utf-8") as f:
        meta = list(csv.DictReader(f))
    for row in meta:
        seg = os.path.join(root, "segments", f"{row['File']}.txt")
        beats = os.path.join(root, "beats_and_downbeats", f"{row['File']}.txt")
        if not (os.path.exists(seg) and os.path.exists(beats)) or not row.get("Time Signature", "").startswith("4"):
            continue
        # Columns: time, beat in the bar, bar number; a downbeat is beat 1.
        downs = [float(l.split()[0]) for l in open(beats) if len(l.split()) >= 2 and l.split()[1] == "1"]
        marks = [(float(l.split()[0]), l.split()[1]) for l in open(seg) if len(l.split()) >= 2]
        form = []
        for (t, label), (t2, _) in zip(marks, marks[1:]):
            bars = bisect.bisect_left(downs, t2 - 0.1) - bisect.bisect_left(downs, t - 0.1)
            kind = section_type(label, bars)
            if kind:
                _append(form, kind, bars)
        if len(form) >= 3:
            out.append((family_of(row.get("Genre", "")), form))
    return out


def tab_forms(paths: list[str], family: str = "rock") -> list[tuple[str, list[tuple[str, int]]]]:
    """Forms from tabs and scores that mark their sections (eval/tabs.py)."""
    from eval.tab_corpus import files
    from eval.tabs import load

    out = []
    for path in files(paths):
        try:
            song = load(path)
        except Exception:
            continue
        form = []
        for label, bars in song.sections:
            kind = section_type(label, bars)
            if kind:
                _append(form, kind, bars)
        if len(form) >= 3:
            out.append((family, form))
    return out


def _append(form: list, kind: str, bars: int) -> None:
    if form and form[-1][0] == kind:
        form[-1] = (kind, form[-1][1] + bars)
    else:
        form.append((kind, bars))


def learn(songs: list[tuple[str, list[tuple[str, int]]]]) -> dict:
    """Per family: the forms themselves (type and bars), the moves from one
    section type to the next (with "start" and "end"), each type's lengths,
    and how often a form has an intro, a bridge, a pre-chorus..."""
    out = {}
    for family in FAMILIES:
        forms = [f for fam, f in songs if fam == family]
        if not forms:
            continue
        moves: dict[str, Counter] = defaultdict(Counter)
        lengths: dict[str, Counter] = defaultdict(Counter)
        for form in forms:
            names = ["start"] + [k for k, _ in form] + ["end"]
            for a, b in zip(names, names[1:]):
                moves[a][b] += 1
            for kind, bars in form:
                lengths[kind][min(bars, 64)] += 1
        has = lambda kind: round(sum(any(k == kind for k, _ in f) for f in forms) / len(forms), 3)
        out[family] = {
            "songs": len(forms),
            "styles": FAMILIES[family]["styles"],
            "forms": [[list(s) for s in form] for form in forms],
            "moves": {a: dict(c) for a, c in moves.items()},
            "lengths": {k: dict(sorted(c.items())) for k, c in lengths.items()},
            "share_with": {k: has(k) for k in ("intro", "lift", "tag", "bridge", "interlude", "break",
                                                "breakdown", "outro")},
            "sections_median": sorted(len(f) for f in forms)[len(forms) // 2],
        }
    return out


def report(data: dict) -> str:
    lines = []
    for family, d in data.items():
        if family.startswith("_"):
            continue
        shapes = Counter(" ".join(k for k, _ in f) for f in d["forms"])
        lines.append(f"\n{family} ({d['songs']} songs, for {', '.join(d['styles'])}): "
                     f"{len(shapes)} different section orders, {d['sections_median']} sections (median)")
        lines.append("  songs with: " + ", ".join(f"{k} {v:.0%}" for k, v in d["share_with"].items()))
        lines.append("  first section: " + ", ".join(f"{k} {v}" for k, v in
                                                     Counter(d["moves"]["start"]).most_common(4)))
        lines.append("  last section: " + ", ".join(
            f"{a} {c['end']}" for a, c in sorted(d["moves"].items(), key=lambda kv: -kv[1].get("end", 0))[:4]
            if c.get("end")))
        for kind in ("verse", "chorus", "lift", "bridge"):
            if kind in d["moves"]:
                nxt = Counter(d["moves"][kind]).most_common(4)
                total = sum(d["moves"][kind].values())
                lines.append(f"  after a {kind}: " + ", ".join(f"{k} {v / total:.0%}" for k, v in nxt))
        for kind, counts in d["lengths"].items():
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
            lines.append(f"  {kind} bars: " + ", ".join(f"{b} ({n})" for b, n in top))
        lines.append("  commonest orders: " + " | ".join(f"{s} ({n})" for s, n in shapes.most_common(3)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("harmonix", nargs="?", help="the Harmonix Set's dataset folder")
    ap.add_argument("--tabs", nargs="*", default=[], help="tabs and scores with section markers")
    ap.add_argument("--out", default=DATA)
    ap.add_argument("--report", action="store_true", help="only describe the data file")
    args = ap.parse_args(argv)
    if args.report:
        print(report(json.load(open(args.out))))
        return 0
    songs = harmonix_forms(args.harmonix) if args.harmonix else []
    songs += tab_forms(args.tabs) if args.tabs else []
    if not songs:
        print("no forms read", file=sys.stderr)
        return 1
    data = {"_source": "Harmonix Set (Nieto et al., ISMIR 2019; MIT licence), section labels and downbeats"
                       + (", and tabs' section markers" if args.tabs else ""),
            **learn(songs)}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(report(data))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
