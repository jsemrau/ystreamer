#!/usr/bin/env python3
"""
Listen to a sound before it goes on air: render short trance demos offline.

    python -m eval.sound_preview                 # -> recordings/preview/*.wav

Eight bars at 138 BPM in A minor (i-VI-III-VII, two bars a chord): the 909
kick, the rolling offbeat bass, chords on Switch Angel's trance gate and,
in one demo, a lead. Each demo puts one sound up front, against what plays
now, so they can be compared side by side:

    1_hypersaw_chords.wav    the new supersaw (ys_hypersaw) on the chords
    2_hypersaw_lead.wav      the new supersaw as the lead, house piano chords
    3_old_supersaw.wav       the old ys_supersaw on the chords (banned: buzzy)
    4_house_piano.wav        today's house piano chords, for comparison

Rendered with scsynth in non-realtime mode (no SuperDirt, no stream), each
part as a stem, mixed and loudness-normalised to the stream's -14 LUFS.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from music.blocks import TRANCE_GATES  # noqa: E402

BPM = 138
BAR = 240 / BPM
BARS = 8
OUT_DIR = os.path.join(config.REPO_DIR, "recordings", "preview")

# A minor, i-VI-III-VII, voiced root-third-fifth-octave, two bars each.
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [55, 60, 64, 67], [55, 59, 62, 67]]
ROOTS = [33, 29, 36, 31]
# A two-bar lead phrase in eighths (None = rest), chord tones on the beats.
LEAD = [[76, None, 72, None, 69, 72, 76, None, 77, None, 76, None, 72, None, None, None],
        [72, None, 69, None, 72, 74, 76, None, 74, None, 72, None, 71, None, None, None],
        [76, None, 79, None, 76, 72, 76, None, 79, None, 81, None, 79, None, None, None],
        [74, None, 71, None, 74, 79, 74, None, 71, None, 67, None, 71, None, None, None]]


def chord_at(bar: int) -> int:
    return (bar // 2) % 4


def kick() -> list:
    return [[b * BAR + k * BAR / 4, "ys_kick909", 36, 0.4, {}] for b in range(BARS) for k in range(4)]


def bass() -> list:
    # [~ 0 0 0]*4: the three 16ths after every beat.
    return [[b * BAR + (k * 4 + s) * BAR / 16, "ys_tbass", ROOTS[chord_at(b)], 0.1, {}]
            for b in range(BARS) for k in range(4) for s in (1, 2, 3)]


def gated_chords(synth: str, extra: dict | None = None) -> list:
    """The chords on the first trance gate: each hit held to the next, 70%."""
    gate = TRANCE_GATES[0].split()
    onsets = [i for i, t in enumerate(gate) if t == "t"]
    events = []
    for b in range(BARS):
        for n, i in enumerate(onsets):
            nxt = onsets[n + 1] if n + 1 < len(onsets) else 16
            length = (nxt - i) * BAR / 16 * 0.7
            for note in CHORDS[chord_at(b)]:
                events.append([b * BAR + i * BAR / 16, synth, note, length, dict(extra or {})])
    return events


def lead(synth: str) -> list:
    events = []
    for b in range(BARS):
        phrase = LEAD[(b // 2) % 4]
        half = (b % 2) * 8
        for i, note in enumerate(phrase[half:half + 8]):
            if note is not None:
                events.append([b * BAR + i * BAR / 8, synth, note, BAR / 8 * 0.9, {}])
    return events


DEMOS = {
    "1_hypersaw_chords": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_hypersaw", {"detune": 0.3})},
    "2_hypersaw_lead": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_housepiano"),
                        "lead": lead("ys_hypersaw")},
    "3_old_supersaw": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_supersaw")},
    "4_house_piano": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_housepiano")},
}
# Stem levels in the mix (dB), the way the stream balances its lines.
LEVELS = {"kick": 0, "bass": -3, "chords": -6, "lead": -4}


def score(events: list) -> str:
    rows = []
    for i, (t, synth, note, sus, extra) in enumerate(events):
        args = ", ".join(f"\\{k}, {v}" for k, v in extra.items())
        rows.append(f"[{t + 0.01:.4f}, [\\s_new, \\{synth}, {2000 + i}, 0, 0, \\freq, {note}.midicps, "
                    f"\\sustain, {sus:.4f}, \\pan, 0{', ' + args if args else ''}]]")
    return ",\n".join(rows)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp()
    defs = open(os.path.join(config.REPO_DIR, "tidal", "synthdefs.scd")).read()
    # SuperDirt isn't running: its panner becomes a plain one (stereo sounds stay stereo).
    defs = re.sub(r"DirtPan\.ar\((.*), numChannels, pan, (env)\)", r"~ysPan.(\1, pan, \2)", defs)
    # (After the block's one `var` line: SuperCollider wants declarations first.)
    defs = defs.replace("var numChannels = ~dirt.numChannels;",
                        "var numChannels = 2;\n~ysPan = { |sig, pan, env| var a = sig.asArray; "
                        "if(a.size > 1) { Balance2.ar(a[0], a[1], pan) * env } { Pan2.ar(a[0], pan) * env } };")
    with open(os.path.join(tmp, "defs.scd"), "w") as f:
        f.write(defs)
    length = BARS * BAR + 3
    jobs = []
    for demo, stems in DEMOS.items():
        for stem, events in stems.items():
            names = ", ".join("\\" + s for s in sorted({e[1] for e in events}))
            jobs.append(f'["{tmp}/{demo}__{stem}.wav", [{names}], [{score(events)}]]')
    render = f"""(
"{tmp}/defs.scd".load;
~jobs = [{",".join(jobs)}];
Routine {{
  ~jobs.do {{ |job|
    var path = job[0], names = job[1], events = job[2], cond = Condition.new;
    var defs = names.collect {{ |n| [0.0, [\\d_recv, SynthDescLib.global.at(n).def.asBytes]] }};
    Score(defs ++ events ++ [[{length:.3f}, [\\c_set, 0, 0]]]).recordNRT(nil, path, headerFormat: "wav",
      sampleFormat: "float", options: ServerOptions.new.numOutputBusChannels_(2).memSize_(65536), duration: {length:.3f},
      action: {{ cond.unhang }});
    cond.hang;
  }};
  0.exit;
}}.play;
)"""
    with open(os.path.join(tmp, "render.scd"), "w") as f:
        f.write(render)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_DISABLE_SANDBOX="1")
    run = subprocess.run(["timeout", "180", "sclang", os.path.join(tmp, "render.scd")], env=env,
                         capture_output=True, text=True)
    errors = [l for l in run.stdout.splitlines() if "ERROR" in l or "FAILURE" in l]
    if errors:
        print("\n".join(errors[:10]))
    for demo, stems in DEMOS.items():
        inputs, filters = [], []
        for n, stem in enumerate(stems):
            path = f"{tmp}/{demo}__{stem}.wav"
            if not os.path.exists(path):
                sys.exit(f"{path} wasn't rendered (see the errors above)")
            inputs += ["-i", path]
            filters.append(f"[{n}:a]volume={LEVELS[stem]}dB[s{n}]")
        mix = "".join(f"[s{n}]" for n in range(len(stems)))
        graph = ";".join(filters) + f";{mix}amix=inputs={len(stems)}:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=9[out]"
        out = os.path.join(OUT_DIR, f"{demo}.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                        "-ar", "48000", out], check=True)
        print(out)


if __name__ == "__main__":
    main()
