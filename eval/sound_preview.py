#!/usr/bin/env python3
"""
Listen to a sound before it goes on air: render short trance demos offline.

    python -m eval.sound_preview                 # -> recordings/preview/*.wav
    python -m eval.sound_preview --darkwave      # the darkwave voices (5_ ... 10_)
    python -m eval.sound_preview --saws          # saw lead and saw pad against today's sounds (11_ ... 15_)
    python -m eval.sound_preview --bell          # the bell lead's old inharmonic overtone against the new (16_, 17_)
    python -m eval.sound_preview --leads         # one melody on every lead voice (18_ ... 37_), M1 stabs (38_), a Saltwater-style choir (39_)

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
LEVELS = {"kick": 0, "bass": -3, "chords": -6, "lead": -4, "snare": -2, "arp": -7, "pad": -9}


def pad(synth: str) -> list:
    """Held chords, two bars each, for a pad bed."""
    return [[b * BAR, synth, note, BAR * 2 * 1.05, {}] for b in range(0, BARS, 2) for note in CHORDS[chord_at(b)]]


def bell_lead(partial: float) -> list:
    """"Orbital Lockout"'s lead (B minor, i-VII-VI-v, two bars a chord): the
    bell's phrase <[0 ~ 7 ~ 0 ~ 4 7] [7 ~ 4 ~ 0 ~ 7 0]> on each chord's root."""
    roots, scale = [71, 69, 67, 66], {0: 0, 4: 7, 7: 12}          # root, fifth, octave (in semitones)
    phrases = [[0, None, 7, None, 0, None, 4, 7], [7, None, 4, None, 0, None, 7, 0]]
    return [[b * BAR + i * BAR / 8, "ys_glass", roots[chord_at(b)] + scale[d], BAR / 8 * 0.9, {"partial": partial}]
            for b in range(BARS) for i, d in enumerate(phrases[b % 2]) if d is not None]


BELL_CHORDS = [[59, 62, 66, 71], [57, 61, 64, 69], [55, 59, 62, 67], [54, 57, 61, 66]]   # Bm, A, G, F#m


def bell_chords() -> list:
    return [[b * BAR, "ys_softpad", n, BAR * 2 * 1.05, {}] for b in range(0, BARS, 2) for n in BELL_CHORDS[chord_at(b)]]


# --bell: the bell lead's overtone, the old inharmonic one against the new.
BELL_DEMOS = {
    "16_bell_lead_before": {"kick": kick(), "chords": bell_chords(), "lead": bell_lead(2.76)},
    "17_bell_lead_after": {"kick": kick(), "chords": bell_chords(), "lead": bell_lead(3)},
}

# --leads: one melody on every lead voice, to compare them. "Dalt Vila Dawn"'s
# lead (Claude, A phrygian there; A minor here): two bars of 16ths, passing
# notes between the beats, a long note to close each bar; _ holds, None rests.
_PHRASE = [[0, None, 2, 3, 4, "_", None, 2, 0, None, None, None, 7, "_", "_", None],
           [2, None, 4, 3, 2, "_", None, 0, 4, None, None, None, 9, "_", "_", None]]
_MINOR = [0, 2, 3, 5, 7, 8, 10]


def phrase_lead(synth: str) -> list:
    roots = [57, 53, 48, 55]                      # Am, F, C, G as the chords' roots, an octave up for the lead
    events = []
    for b in range(BARS):
        root, steps = roots[chord_at(b)] + 12, _PHRASE[b % 2]
        degree_of_root = {57: 0, 53: 5, 48: 2, 55: 6}[roots[chord_at(b)]]
        for i, d in enumerate(steps):
            if d is None or d == "_":
                continue
            held = 1
            while i + held < 16 and steps[i + held] == "_":
                held += 1
            deg = degree_of_root + d
            note = 69 + 12 * (deg // 7) + _MINOR[deg % 7]       # A minor, from A4
            events.append([b * BAR + i * BAR / 16, synth, note, BAR / 16 * held * 0.95, {}])
    return events


LEAD_VOICES = ["ys_glass", "ys_pulselead", "ys_pluck", "ys_wavelead", "ys_coldguitar",
               "ys_flute", "ys_marimba", "ys_sitar", "ys_vox", "ys_fmlead",
               "ys_hoover", "ys_nylon", "ys_melodica", "ys_vibes", "ys_trumpet", "ys_harpsichord", "ys_epiano",
               "ys_sax", "ys_angelchoir", "ys_chipvox"]
LEAD_DEMOS = {f"{18 + i}_lead_{v[3:]}": {"kick": kick(), "chords": pad("ys_softpad"), "lead": phrase_lead(v)}
              for i, v in enumerate(LEAD_VOICES)}
# The M1 organ on the chords, as 90s house and rave stabs (the trance gate's rhythm).
LEAD_DEMOS["38_m1organ_stabs"] = {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_m1organ")}


def choir_line() -> list:
    """The choir's Celtic line (blocks choir_lead's first): long notes, a grace
    note into a held one, over A minor's i-VI-III-VII."""
    bars = [[4, "_", "_", "_", 2, "_", 0, "_"], [2, "_", "_", "_", None, None, None, None],
            [4, "_", "_", 5, 4, "_", 2, "_"], [0, "_", "_", "_", "_", "_", None, None]]
    roots = {57: 0, 53: 5, 48: 2, 55: 6}
    events = []
    for b in range(BARS):
        steps, deg0 = bars[b % 4], roots[[57, 53, 48, 55][chord_at(b)]]
        for i, d in enumerate(steps):
            if d is None or d == "_":
                continue
            held = 1
            while i + held < 8 and steps[i + held] == "_":
                held += 1
            deg = deg0 + d
            events.append([b * BAR + i * BAR / 8, "ys_angelchoir", 69 + 12 * (deg // 7) + _MINOR[deg % 7],
                           BAR / 8 * held * 1.2, {}])
    return events


def nylon_arp() -> list:
    return [[b * BAR + i * BAR / 8, "ys_nylon", CHORDS[chord_at(b)][i % 4] + 12, BAR / 8 * 1.5, {}]
            for b in range(BARS) for i in range(8)]


# "Saltwater": the choir's Celtic line over a nylon guitar and a soft pad.
LEAD_DEMOS["39_saltwater_choir"] = {"kick": kick(), "chords": pad("ys_softpad"), "arp": nylon_arp(), "lead": choir_line()}

# --saws: the saw voices done properly, against today's sounds and the old saws.
SAW_DEMOS = {
    "11_saw_lead": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_housepiano"), "lead": lead("ys_sawlead")},
    "12_square_lead_today": {"kick": kick(), "bass": bass(), "chords": gated_chords("ys_housepiano"),
                             "lead": lead("ys_pulselead")},
    "13_saw_pad": {"kick": kick(), "bass": bass(), "pad": pad("ys_sawpad"), "lead": lead("ys_glass")},
    "14_soft_pad_today": {"kick": kick(), "bass": bass(), "pad": pad("ys_softpad"), "lead": lead("ys_glass")},
    "15_old_saw_pad": {"kick": kick(), "bass": bass(), "pad": pad("ys_pad"), "lead": lead("ys_glass")},
}

# Darkwave (--darkwave): eight bars at 132 BPM in A minor, the same chords,
# a kick on 1 and 3, the gated snare on 2 and 4, the chorused bass in 8ths,
# the chorused guitar arpeggio and the string machine.
DARK_BAR = 240 / 132


def dark_drums() -> dict:
    return {"kick": [[b * DARK_BAR + k * DARK_BAR / 4, "ys_kick909", 36, 0.4, {}] for b in range(BARS) for k in (0, 2)],
            "snare": [[b * DARK_BAR + k * DARK_BAR / 4, "ys_gatesnare", 60, 0.3, {}]
                      for b in range(BARS) for k in (1, 3)]}


def dark_bass() -> list:
    # "0 0 7 0 4 0 2 0": root, octave, fifth, third, in 8ths.
    steps = [0, 0, 12, 0, 7, 0, 3, 0]
    return [[b * DARK_BAR + i * DARK_BAR / 8, "ys_chorusbass", ROOTS[chord_at(b)] + 12 + st, DARK_BAR / 8 * 0.8, {}]
            for b in range(BARS) for i, st in enumerate(steps)]


def dark_arp() -> list:
    # {updown} through the chord in 8ths, ringing on.
    events = []
    for b in range(BARS):
        notes = CHORDS[chord_at(b)]
        run = notes + notes[-2:0:-1]
        for i in range(8):
            events.append([b * DARK_BAR + i * DARK_BAR / 8, "ys_coldguitar", run[i % len(run)] + 12,
                           DARK_BAR / 8 * 1.5, {}])
    return events


def dark_strings() -> list:
    return [[b * DARK_BAR, "ys_coldstrings", note, DARK_BAR * 2 * 1.05, {}]
            for b in range(0, BARS, 2) for note in CHORDS[chord_at(b)]]


def wave_lead() -> list:
    # Long held notes: E, D, C, B (two bars each), legato 1.9, so each rings into the next.
    notes = [76, 74, 72, 71]
    return [[b * DARK_BAR, "ys_wavelead", notes[(b // 2) % 4], DARK_BAR * 1.9 * (1 if b % 2 == 0 else 0.5), {}]
            for b in range(BARS)]


def wave_arp() -> list:
    # 8ths up the chord with the wave synth: each note rings on into the next.
    events = []
    for b in range(BARS):
        notes = CHORDS[chord_at(b)]
        for i in range(8):
            events.append([b * DARK_BAR + i * DARK_BAR / 8, "ys_wavesynth", notes[i % len(notes)] + 12,
                           DARK_BAR / 8, {}])
    return events


DARK_DEMOS = {
    "5_darkwave_full": {**dark_drums(), "bass": dark_bass(), "arp": dark_arp(), "chords": dark_strings()},
    "6_darkwave_bass_drums": {**dark_drums(), "bass": dark_bass()},
    "7_darkwave_guitar": {"arp": dark_arp()},
    "8_darkwave_strings": {"chords": dark_strings()},
    "9_darkwave_wave_lead": {**dark_drums(), "bass": dark_bass(), "chords": dark_strings(), "lead": wave_lead()},
    "10_darkwave_wave_arp": {"chords": dark_strings(), "arp": wave_arp()},
}


def score(events: list) -> str:
    rows = []
    for i, (t, synth, note, sus, extra) in enumerate(events):
        args = ", ".join(f"\\{k}, {v}" for k, v in extra.items())
        rows.append(f"[{t + 0.01:.4f}, [\\s_new, \\{synth}, {2000 + i}, 0, 0, \\freq, {note}.midicps, "
                    f"\\sustain, {sus:.4f}, \\pan, 0{', ' + args if args else ''}]]")
    return ",\n".join(rows)


def main() -> None:
    dark = "--darkwave" in sys.argv[1:]
    demos, bar = ((DARK_DEMOS, DARK_BAR) if dark else (SAW_DEMOS, BAR) if "--saws" in sys.argv[1:]
                  else (BELL_DEMOS, BAR) if "--bell" in sys.argv[1:]
                  else (LEAD_DEMOS, BAR) if "--leads" in sys.argv[1:] else (DEMOS, BAR))
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
    length = BARS * bar + 3
    jobs = []
    for demo, stems in demos.items():
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
    for demo, stems in demos.items():
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
