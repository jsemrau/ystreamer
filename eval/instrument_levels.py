#!/usr/bin/env python3
"""
The sampled instruments' loudness (music/instruments.py), measured as
eval/synth_levels.sh measures the models: four notes, rendered offline with
the stream's SynthDefs (scsynth NRT), integrated LUFS; and the `gain` that
puts each at its target (the model's level for one it replaces, else -21,
the real harp's).

    python -m eval.instrument_levels             # every installed instrument
    python -m eval.instrument_levels ys_oboe     # only these

Then set each `gain` in tidal/synthdefs.scd (multiply the one there by the
suggestion) and its level in music/mix.SYNTH_LUFS.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from music import instruments  # noqa: E402
from music.mix import SYNTH_LUFS  # noqa: E402

# The note each plays (its usual register) and how long.
NOTES = {"ys_grandpiano": (60, 1), "ys_violins": (67, 2), "ys_cellos": (48, 2), "ys_pizz": (67, 1),
         "ys_violin": (72, 2), "ys_concertflute": (76, 1), "ys_oboe": (72, 1), "ys_clarinet": (67, 1),
         "ys_trumpet": (67, 1), "ys_horn": (60, 2), "ys_vibes": (72, 1), "ys_marimba": (72, 1),
         "ys_dantranh": (67, 1), "ys_harmonica": (72, 1), "ys_ocarina": (76, 1), "ys_mbira": (67, 1),
         "ys_glockenspiel": (88, 1), "ys_chimes": (67, 1), "ys_harp": (67, 1), "ys_kalimba": (72, 1),
         "ys_uprightbass": (40, 0.5), "ys_femalevox": (67, 1), "ys_voxchop": (64, 0.4), "ys_crowdchoir": (60, 2)}
TARGET = -21.0
# Real instruments already levelled in mix.SYNTH_LUFS: the yardstick.
REFERENCES = ("ys_harp", "ys_kalimba")


def lufs(path: str) -> float:
    out = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    return float(found[-1]) if found else float("nan")


def render(synths: list[str]) -> dict[str, str]:
    tmp = tempfile.mkdtemp()
    defs = open(os.path.join(config.REPO_DIR, "tidal", "synthdefs.scd")).read()
    defs = re.sub(r"DirtPan\.ar\((.*), numChannels, pan, (env)\)", r"~ysPan.(\1, pan, \2)", defs)
    defs = defs.replace("var numChannels = ~dirt.numChannels;",
                        "var numChannels = 2;\n~ysPan = { |sig, pan, env| var a = sig.asArray; "
                        "if(a.size > 1) { Balance2.ar(a[0], a[1], pan) * env } { Pan2.ar(a[0], pan) * env } };")
    with open(os.path.join(tmp, "defs.scd"), "w") as f:
        f.write(defs)
    jobs = ",".join(f'[\\{s}, {NOTES[s][0]}, {NOTES[s][1]}]' for s in synths)
    script = f"""(
{instruments.nrt_sclang()}
"{tmp}/defs.scd".load;
Routine {{
  [{jobs}].do {{ |job|
    var name = job[0], sus = job[2], cond = Condition.new, gap = sus + 1.5;
    // Four notes around its register (one short note's loudness wobbles with
    // the widener's random delays), a second in: the recordings load first.
    var notes = [-5, 0, 4, 7].collect {{ |d, i|
      [1.0 + (i * gap), [\\s_new, name, 1000 + i, 0, 0, \\freq, (job[1] + d).midicps, \\sustain, sus, \\pan, 0]] }};
    Score(~bufMsgs ++ [[0.0, [\\d_recv, SynthDescLib.global.at(name).def.asBytes]]] ++ notes
      ++ [[1.0 + (4 * gap) + 2, [\\c_set, 0, 0]]]).recordNRT(nil, "{tmp}/" ++ name ++ ".wav", headerFormat: "wav",
      sampleFormat: "float", options: ServerOptions.new.numOutputBusChannels_(2), duration: 1.0 + (4 * gap) + 2,
      action: {{ cond.unhang }});
    cond.hang;
  }};
  0.exit;
}}.play;
)"""
    with open(os.path.join(tmp, "render.scd"), "w") as f:
        f.write(script)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_DISABLE_SANDBOX="1")
    run = subprocess.run(["timeout", "300", "sclang", os.path.join(tmp, "render.scd")], env=env,
                         capture_output=True, text=True)
    for line in run.stdout.splitlines():
        if "ERROR" in line or "FAILURE" in line:
            print(line)
    return {s: os.path.join(tmp, f"{s}.wav") for s in synths}


def main(argv: list[str]) -> int:
    installed = {i.synth for i in instruments.INSTRUMENTS.values() if instruments.installed(i.name)}
    synths = [s for s in (argv or NOTES) if s in installed and s in NOTES and (argv or s not in REFERENCES)]
    if not synths:
        print("no installed instruments to measure (python -m music.instruments)")
        return 1
    refs = [r for r in REFERENCES if r in installed]
    paths = render(list(dict.fromkeys([*synths, *refs])))
    levels = {s: lufs(p) if os.path.exists(p) else float("nan") for s, p in paths.items()}
    # Four notes measure louder than synth_levels' one: the references (set
    # in SYNTH_LUFS already) say by how much.
    offset = (sum(levels[r] - SYNTH_LUFS[r] for r in refs) / len(refs)) if refs else 0.0
    print(f"(four notes measure {offset:+.1f} dB against one, by {', '.join(refs) or 'nothing'})")
    for synth in synths:
        level = levels[synth] - offset
        target = SYNTH_LUFS.get(synth, TARGET)
        print(f"{synth:17s} {level:6.1f} LUFS   target {target:6.1f}   gain x{10 ** ((target - level) / 20):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
