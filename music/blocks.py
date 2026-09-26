"""
The block library: short, idiomatic Tidal lines a performer can add to the
program, per style, with the pattern variants it can later swap in.

Melodic lines are written in scale degrees relative to the chord root
(`"0 ~ 2 4" |+ "<{roots}>"`), so they follow the progression and can't leave
the key. Levels come from the offline measurements in music/presets.py.

Lofi deliberately has no sustained pad: chords are short Rhodes/felt comping.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import mix
from .livecode import LineSpec


@dataclass
class Block:
    name: str
    role: str
    body: str
    patterns: dict[str, list[str]]           # slot -> variants (first is default)
    fx: list[list[str]] = field(default_factory=list)
    pfs: list[list[str]] = field(default_factory=list)
    wrappers: list[str] = field(default_factory=list)
    drums: bool = False
    # Degree pool for mutating melodic patterns (numbers the performer may use).
    pool: list[int] = field(default_factory=list)

    def make(self, rng: random.Random) -> LineSpec:
        patterns = {slot: rng.choice(options) for slot, options in self.patterns.items()}
        fx = [list(p) for p in self.fx]
        # Level from the measured loudness of the synth actually chosen
        # (a block may offer several), so the line enters at its role's level.
        synth = mix.synth_of(self.body.replace('"{synth}"', f'"{patterns.get("synth", "")}"'))
        # Short chord hits (struct rhythms) aren't comparable with a sustained
        # measurement, so those keep their tuned gain.
        gain = None if "struct" in self.body else mix.calibrated_gain(synth, self.role, chord="{chords}" in self.body)
        if gain is not None:
            fx = [[n, f"{gain:g}"] if n == "gain" else [n, v] for n, v in fx]
        return LineSpec(
            role=self.role, body=self.body, patterns=patterns,
            fx=fx, pfs=[list(p) for p in self.pfs],
            wrappers=list(self.wrappers), drums=self.drums, block=self.name,
            base_gain=dict((n, v) for n, v in fx).get("gain", ""),
        )


def _melodic(name, role, synth, pattern_slot, variants, fx, octave="", pool=None, rhythm_body=None, **kw):
    body = rhythm_body or (
        f'n (scale "{{mode}}" ("{{{pattern_slot}}}" |+ "<{{roots}}>") {{root}}{octave}) # s "{synth}"')
    # Bass and acid lines play chord tones only (see livecode.harmony_problem);
    # melodies may pass through other scale notes between the strong steps.
    default_pool = [0, 2, 4, 6, 7] if role in ("bass", "acid") else [0, 1, 2, 3, 4, 5, 7]
    return Block(name, role, body, {pattern_slot: variants}, fx=fx, pool=pool or default_pool, **kw)


BLOCKS: dict[str, list[Block]] = {
    # ------------------------------------------------------------------ lofi
    "lofi": [
        Block("dusty_beat", "beat", 's "[{kick}, {snare}, {hats}]"',
              {"kick": ["bd ~ ~ ~ ~ bd ~ ~", "bd ~ ~ bd ~ ~ bd ~", "bd ~ [~ bd] ~ ~ ~ bd ~"],
               "snare": ["~ sn ~ sn", "~ sn ~ [sn ~ ~ sn:2]", "~ sn:1 ~ sn"],
               "hats": ["hh27*8", "[~ hh27]*4", "hh27*8?", "[hh27 hh27:3]*4"]},
              fx=[["gain", "0.92"], ["lpf", "5000"]], wrappers=["swingBy (1/8) 4"], drums=True),
        _melodic("sub_walk", "bass", "ys_sub", "rhythm",
                 ["0 ~ ~ 0 ~ ~ 4 ~", "0 ~ 0 ~ ~ 4 ~ ~", "0 ~ ~ ~ 0 ~ -1 ~"],
                 [["gain", "0.95"], ["lpf", "500"], ["legato", "0.9"]], octave=" - 24", pool=[0, 4, -1, -3, 7]),
        Block("rhodes_comp", "keys",
              'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_epiano"',
              {"comp": ["t ~ ~ t ~ ~ t ~", "~ t ~ ~ t ~ ~ ~", "t ~ ~ ~ ~ t ~ ~", "~ ~ t ~ ~ ~ t ~"]},
              fx=[["gain", "0.9"], ["legato", "0.5"], ["room", "0.3"], ["lpf", "3200"]]),
        Block("felt_comp", "keys",
              'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_keys"',
              {"comp": ["t ~ ~ ~ t ~ ~ ~", "~ ~ t ~ ~ ~ t ~", "t ~ ~ t ~ ~ ~ ~"]},
              fx=[["gain", "0.9"], ["legato", "0.8"], ["room", "0.35"]]),
        _melodic("felt_melody", "lead", "ys_keys", "melody",
                 ["0 ~ 2 ~ 4 ~ 2 ~", "~ 4 ~ 2 0 ~ ~ ~", "4 ~ ~ 2 ~ 0 ~ -1", "0 2 ~ 4 ~ ~ 2 ~",
                  "<[0 ~ 2 ~ 4 ~ ~ ~] [4 ~ 2 ~ 0 ~ -1 ~]>"],
                 [["gain", "1.02"], ["room", "0.35"], ["delay", "0.25"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.3"]], octave=" + 12"),
        _melodic("glass_melody", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ 2 ~ ~ ~", "0 ~ ~ 2 ~ ~ 4 ~", "~ ~ 7 ~ 4 ~ 2 ~"],
                 [["gain", "1.0"], ["room", "0.45"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.35"]], octave=" + 12"),
        _melodic("pluck_arp", "acid", "ys_pluck", "arp",
                 ["0 2 4 7 4 2 0 ~", "[0 4] [2 7] [4 2] ~", "0 ~ 4 ~ 7 ~ 4 ~"],
                 [["gain", "1.0"], ["room", "0.3"], ["lpf", "4000"], ["pan", "(slow 8 $ range 0.3 0.7 sine)"]]),
        Block("vinyl", "texture", 'slow 4 $ note "0" # s "ys_vinyl"', {},
              fx=[["gain", "1.1"], ["hpf", "300"], ["legato", "1.2"]]),
        Block("congas", "perc", 's "{perc}"',
              {"perc": ["~ ~ 808mc ~ ~ 808lc ~ ~", "~ 808lc ~ ~ 808mc ~ ~ 808lc", "[~ 808mc]*2 ~ 808lc"]},
              fx=[["gain", "0.7"], ["pan", "0.35"], ["room", "0.2"]], drums=True),
    ],
    # ------------------------------------------------------------- deep house
    "deephouse": [
        Block("house_beat", "beat", 's "[{kick}, {hats}, {clap}]"',
              {"kick": ["bd*4"], "hats": ["[~ hh]*4", "[~ hh]*4, hh*16?", "[~ 808oh]*4"],
               "clap": ["~ cp ~ cp", "~ cp ~ [cp cp]", "~"]},
              fx=[["gain", "0.95"]], wrappers=["swingBy (1/24) 4"], drums=True),
        _melodic("deep_bass", "bass", "ys_sub", "rhythm",
                 ["~ 0 ~ 0 ~ 0 ~ 0", "0 ~ ~ 0 ~ 0 ~ ~", "~ 0 0 ~ ~ 0 ~ 7"],
                 [["gain", "0.96"], ["lpf", "420"], ["legato", "0.75"]], octave=" - 24", pool=[0, 7, 4, -1]),
        Block("deep_stab", "keys",
              'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "{synth}"',
              {"comp": ["~ ~ t ~ ~ ~ t ~", "t ~ ~ t ~ ~ t ~", "~ t ~ t ~ ~ t ~"], "synth": ["ys_epiano", "ys_organ"]},
              fx=[["gain", "0.92"], ["legato", "0.35"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.35"], ["lpf", "2800"]]),
        _melodic("glass_hook", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ ~ 2 ~ ~", "0 ~ ~ 4 ~ ~ 2 ~", "~ ~ 7 ~ ~ 4 ~ ~",
                  "<[~ 4 ~ ~ ~ 2 ~ ~] [~ 7 ~ ~ 4 ~ 2 ~]>"],
                 [["gain", "1.0"], ["room", "0.4"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.3"]], octave=" + 12"),
        Block("strings", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_strings"', {},
              fx=[["gain", "0.8"], ["legato", "1.05"], ["room", "0.5"], ["lpf", "2600"]]),
        Block("glass_arp", "arp",
              'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "ys_glass"',
              {"arp": ["0 4 7 9 7 4", "0 2 4 7", "7 4 2 4 0"]},
              fx=[["gain", "0.8"], ["room", "0.4"], ["delay", "0.35"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.4"], ["pan", "(slow 8 $ range 0.35 0.65 sine)"]],
              pool=[0, 2, 4, 7, 9]),
        Block("congas", "perc", 's "{perc}"',
              {"perc": ["[~ ~ ~ 808lc] [~ 808mc ~ ~] [~ ~ 808lc ~] ~", "~ 808mc ~ 808lc"]},
              fx=[["gain", "0.68"], ["pan", "0.6"]], drums=True),
        Block("vinyl", "texture", 'slow 4 $ note "0" # s "ys_vinyl"', {},
              fx=[["gain", "1.05"], ["hpf", "300"], ["legato", "1.2"]]),
    ],
    # ---------------------------------------------------------------- trance
    "trance": [
        Block("trance_beat", "beat", 's "[{kick}, {hats}, {clap}]"',
              {"kick": ["ys_kick*4"], "hats": ["[~ 808oh]*4", "[~ 808oh]*4, hh*16", "[~ hh]*4"],
               "clap": ["~", "~ cp ~ cp"]},
              fx=[["gain", "1.0"]], drums=True),
        Block("acid", "acid",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
              {"motif": ["0 4 0 9 7", "0 7 0 2", "0 2 0 4 7", "0 4 0 9 7 11", "0 0 7 2 4"]},
              fx=[["gain", "0.98"], ["legato", "0.6"]], pfs=[["acid", "(slow 16 $ range 0.35 0.75 sine)"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9, 11]),
        _melodic("rolling_bass", "bass", "ys_tbass", "rhythm",
                 ["[~ 0 0 0]*4", "[~ 0 0 0]*3 [~ 0 0 7]", "[~ ~ 0 ~]*4"],
                 [["gain", "1.05"], ["lpf", "1200"], ["legato", "0.5"]], octave=" - 24", pool=[0, 7],
                 wrappers=['(|* gain "[0.67 0.91 1 1]*4")']),
        Block("gated_saw", "chords",
              'n (scale "{mode}" "<{chords}>" {root}) # s "ys_gatesaw" # pI "gatepat" {gate}'
              ' # pF "gatetime" {bar} # pF "beattime" {beat}',
              {"gate": ["1", "2", "3", "0"]},
              fx=[["gain", "0.88"], ["legato", "1"], ["lpf", "3000"], ["room", "0.35"]],
              pfs=[["gatedepth", "0.95"], ["duckdepth", "0.8"]]),
        _melodic("saw_lead", "lead", "ys_supersaw", "melody",
                 ["0 ~ ~ 2 ~ ~ 4 ~", "4 ~ 2 ~ 0 ~ ~ ~", "7 ~ ~ 4 ~ 2 ~ 0",
                  "<[0 ~ ~ 2 ~ ~ 4 ~] [4 ~ ~ 2 ~ ~ 7 ~]>", "<[7 ~ 4 ~ 2 ~ 0 ~] [4 ~ 2 ~ 0 ~ -1 ~]>",
                  "<[0 ~ 4 ~ 7 ~ 4 ~] [9 ~ 7 ~ 4 ~ 2 ~] [7 ~ 4 ~ 2 ~ 0 ~] [2 ~ 4 ~ -1 ~ ~ ~]>"],
                 [["gain", "1.05"], ["lpf", "4500"], ["room", "0.4"], ["delay", "0.3"], ["delaytime", "0.33"],
                  ["delayfeedback", "0.35"]], octave=" + 12",
                 pfs=[["duckdepth", "0.8"]]),
        Block("high_acid", "arp",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root}) # s "ys_acid"',
              {"motif": ["0 4 0 9 7", "7 4 0 4", "0 2 4 7 9 7 4 2", "9 7 4 0 4"]},
              fx=[["gain", "0.8"], ["legato", "0.5"], ["delay", "0.25"], ["delaytime", "0.1875"],
                  ["delayfeedback", "0.3"], ["pan", "(slow 4 $ range 0.3 0.7 sine)"]],
              pfs=[["acid", "(slow 12 $ range 0.3 0.65 cosine)"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9, 11]),
        Block("pluck_16", "arp",
              'n (scale "{mode}" ("<{arp}>*16" |+ "<{roots}>") {root} + 12) # s "ys_pluck"',
              {"arp": ["0 2 4 7", "0 4 7 9 7 4", "7 4 2 0", "0 7 2 9 4"]},
              fx=[["gain", "0.85"], ["lpf", "(slow 8 $ range 1500 5000 sine)"], ["delay", "0.3"],
                  ["delaytime", "0.375"], ["delayfeedback", "0.35"], ["room", "0.3"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9]),
        Block("air", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.92"], ["hpf", "200"], ["legato", "1.3"], ["room", "0.7"]]),
    ],
    # --------------------------------------------------------------- ambient
    "ambient": [
        Block("air_pad", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "{synth}"',
              {"synth": ["ys_air", "ys_strings", "ys_choir"]},
              fx=[["gain", "0.85"], ["legato", "1.1"], ["room", "0.6"], ["size", "0.9"], ["lpf", "2400"]]),
        _melodic("glass_bells", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ ~ ~ 2 ~", "0 ~ ~ ~ 7 ~ ~ ~", "~ ~ 4 ~ ~ 2 ~ ~"],
                 [["gain", "0.98"], ["room", "0.6"], ["delay", "0.4"], ["delaytime", "0.5"],
                  ["delayfeedback", "0.45"]], octave=" + 12"),
        Block("felt_notes", "keys", 'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_keys"',
              {"comp": ["t ~ ~ ~ ~ ~ ~ ~", "~ ~ ~ ~ t ~ ~ ~"]},
              fx=[["gain", "0.88"], ["legato", "1.5"], ["room", "0.5"]]),
        _melodic("sub_drone", "bass", "ys_sub", "rhythm", ["0 ~ ~ ~ ~ ~ ~ ~", "0 ~ ~ ~ 4 ~ ~ ~"],
                 [["gain", "0.9"], ["legato", "1"], ["lpf", "300"]], octave=" - 24", pool=[0, 4]),
        Block("pulse", "beat", 's "{kick}"', {"kick": ["bd ~ ~ ~", "bd ~ ~ ~ ~ ~ ~ ~"]},
              fx=[["gain", "0.85"], ["lpf", "2500"], ["room", "0.3"]], drums=True),
        Block("air", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.92"], ["hpf", "200"], ["legato", "1.3"], ["room", "0.7"]]),
    ],
    # ----------------------------------------------------------------- piano
    "piano": [
        Block("piano_chords", "keys", 'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "superpiano"',
              {"comp": ["t ~ ~ ~ t ~ ~ ~", "t ~ ~ t ~ ~ t ~", "t ~ ~ ~ ~ ~ ~ ~"]},
              fx=[["gain", "0.8"], ["legato", "1"], ["room", "0.4"]]),
        _melodic("piano_melody", "lead", "superpiano", "melody",
                 ["0 ~ 2 ~ 4 ~ ~ ~", "~ 4 2 ~ 0 ~ ~ ~", "4 ~ ~ 7 ~ 4 2 ~"],
                 [["gain", "0.85"], ["room", "0.4"]], octave=" + 12"),
        _melodic("felt_bass", "bass", "ys_keys", "rhythm", ["0 ~ ~ ~ 4 ~ ~ ~", "0 ~ ~ ~ ~ ~ ~ ~"],
                 [["gain", "0.95"], ["legato", "1"]], octave=" - 12", pool=[0, 4]),
        Block("strings", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_strings"', {},
              fx=[["gain", "0.78"], ["legato", "1.05"], ["room", "0.5"], ["lpf", "2400"]]),
        Block("air", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.9"], ["hpf", "200"], ["legato", "1.3"]]),
    ],
    # ------------------------------------------------------------- downtempo
    "downtempo": [
        Block("slow_beat", "beat", 's "[{kick}, {snare}, {hats}]"',
              {"kick": ["808bd ~ ~ 808bd ~ ~ ~ ~", "808bd ~ ~ ~ ~ ~ 808bd ~"],
               "snare": ["~ ~ 808sd ~", "~ ~ 808sd [~ 808sd]"], "hats": ["808hc*8", "[~ 808hc]*4"]},
              fx=[["gain", "0.95"], ["lpf", "7000"]], drums=True),
        _melodic("pluck_bass", "bass", "ys_pluck", "rhythm", ["0 ~ ~ 0 ~ ~ 4 ~", "0 ~ ~ ~ 0 ~ ~ 7"],
                 [["gain", "1.1"], ["lpf", "900"], ["legato", "0.8"]], octave=" - 24", pool=[0, 4, 7]),
        Block("felt_comp", "keys", 'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_keys"',
              {"comp": ["t ~ ~ t ~ ~ ~ ~", "~ ~ t ~ ~ ~ t ~"]},
              fx=[["gain", "0.9"], ["legato", "0.8"], ["room", "0.35"]]),
        _melodic("epiano_melody", "lead", "ys_epiano", "melody",
                 ["0 ~ 2 4 ~ ~ 2 ~", "~ 4 ~ 2 ~ 0 ~ ~"],
                 [["gain", "1.0"], ["room", "0.3"], ["delay", "0.2"], ["delaytime", "0.25"],
                  ["delayfeedback", "0.25"]], octave=" + 12"),
        Block("vinyl", "texture", 'slow 4 $ note "0" # s "ys_vinyl"', {},
              fx=[["gain", "1.1"], ["hpf", "300"], ["legato", "1.2"]]),
    ],
}



def _techno_blocks(hard: bool) -> list[Block]:
    """Techno (hard=False) or hardcore/gabber (hard=True): the kick is the
    lead instrument, distorted harder for hardcore."""
    pump = '(|* gain "[0.67 0.91 1 1]*4")'
    if hard:
        kick = Block("gabber_kick", "beat", 's "{kick}"',
                     {"kick": ["ys_kick*4", "ys_kick*4", "ys_kick ys_kick ys_kick [ys_kick ys_kick]"]},
                     fx=[["gain", "1.0"], ["shape", "0.45"], ["lpf", "6000"]], drums=True)
    else:
        kick = Block("techno_kick", "beat", 's "{kick}"',
                     {"kick": ["ys_kick*4", "ys_kick*4", "ys_kick ys_kick ys_kick [ys_kick ~ ~ ys_kick]"]},
                     fx=[["gain", "1.0"], ["shape", "0.2"], ["lpf", "5000"]], drums=True)
    return [
        kick,
        Block("hats", "perc", 's "[{hats}, {clap}]"',
              {"hats": ["hh*16", "[~ 808oh]*4, hh*16", "[~ hh]*8", "[~ 808oh]*4"],
               "clap": ["~ cp ~ cp", "~", "~ ~ ~ cp"]},
              fx=[["gain", "0.82"], ["hpf", "400"], ["room", "0.15"]],
              wrappers=['(|* gain "[0.8 1]*8")'], drums=True),
        _melodic("rumble", "bass", "ys_tbass", "rhythm",
                 ["[~ 0]*4", "[~ 0 0 0]*4", "[~ 0 ~ 0]*4", "[~ 0 0 ~]*4"],
                 [["gain", "1.05"], ["lpf", "450"], ["legato", "0.4"]], octave=" - 24", pool=[0, 7, -3],
                 wrappers=[pump]),
        Block("acid", "acid",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
              {"motif": ["0 0 2 0 4 0 2 0", "0 7 0 6 0 4", "0 2 0 0 4 2", "0 0 7 0 0 6 4 2"]},
              fx=[["gain", "0.95"], ["legato", "0.5"]],
              pfs=[["acid", "(slow 16 $ range 0.3 0.7 sine)" if not hard else "(slow 8 $ range 0.45 0.85 sine)"],
                   ["acidres", "0.25"]],
              wrappers=[pump], pool=[0, 2, 4, 6, 7]),
        Block("stab", "chords",
              'struct "{stab}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_supersaw" # pF "beattime" {beat}',
              {"stab": ["~ ~ t ~ ~ ~ ~ ~", "~ t ~ ~ ~ ~ t ~", "~ ~ ~ t ~ ~ t ~", "[~ t]*4"]},
              fx=[["gain", "0.78"], ["legato", "0.25"], ["lpf", "2200"], ["room", "0.35"]],
              pfs=[["duckdepth", "0.6"]]),
        _melodic("hoover", "lead", "ys_supersaw", "melody",
                 ["0 ~ ~ 0 ~ ~ 2 ~", "7 ~ 6 ~ 4 ~ 0 ~", "0 ~ 7 ~ 6 ~ 4 ~",
                  "<[0 ~ ~ 0 ~ ~ 2 ~] [0 ~ ~ 0 ~ ~ 6 4]>"],
                 [["gain", "0.9"], ["lpf", "3500"], ["room", "0.3"]], octave="",
                 pfs=[["duckdepth", "0.7"]], pool=[0, 2, 4, 6, 7]),
        Block("pluck_seq", "arp",
              'n (scale "{mode}" ("<{arp}>*16" |+ "<{roots}>") {root}) # s "ys_pluck"',
              {"arp": ["0 7 0 4", "0 0 7 0 6", "0 4 7 4", "7 0 6 0 4 0"]},
              fx=[["gain", "0.8"], ["lpf", "(slow 16 $ range 800 3500 sine)"], ["delay", "0.3"],
                  ["delaytime", "0.1875"], ["delayfeedback", "0.4"], ["room", "0.2"]],
              wrappers=[pump], pool=[0, 2, 4, 6, 7]),
        Block("noise", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.85"], ["hpf", "500"], ["legato", "1.3"], ["room", "0.8"]]),
    ]


# ------------------------------------------------------------- darkwave
# Cold electro / coldwave / minimal synth: a stiff drum machine, a 16th-note
# sequencer bass, a cold string-machine bed, arpeggios lost in delay.
BLOCKS["darkwave"] = [
    Block("drum_machine", "beat", 's "[{kick}, {snare}, {hats}]"',
          {"kick": ["808bd ~ 808bd ~", "808bd*4", "808bd ~ ~ 808bd ~ ~ 808bd ~"],
           "snare": ["~ 808sd ~ 808sd", "~ cp ~ cp", "~ 808sd ~ [808sd cp]"],
           "hats": ["808hc*16", "808hc*8", "[~ 808hc]*8", "[808hc 808hc 808oh 808hc]*4"]},
          fx=[["gain", "0.95"], ["room", "0.2"], ["lpf", "7000"]], drums=True),
    _melodic("sequencer_bass", "bass", "ys_tbass", "rhythm",
             ["0*16", "[0 0 7 0]*4", "[0 ~ 0 7]*4", "[0 0 0 4]*4"],
             [["gain", "1.0"], ["lpf", "900"], ["legato", "0.5"]], octave=" - 24", pool=[0, 7, 4, -3]),
    Block("cold_strings", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "{synth}"',
          {"synth": ["ys_strings", "ys_choir", "ys_organ"]},
          fx=[["gain", "0.68"], ["legato", "1.05"], ["lpf", "1800"], ["room", "0.5"], ["size", "0.8"]]),
    _melodic("cold_arp", "lead", "ys_pluck", "melody",
             ["0 2 4 7 4 2 0 2", "[0 4 7 4]*2", "7 4 2 0 ~ 0 2 4", "0 ~ 7 ~ 4 ~ 7 ~"],
             [["gain", "0.9"], ["lpf", "3500"], ["room", "0.4"], ["delay", "0.35"], ["delaytime", "0.375"],
              ["delayfeedback", "0.45"]], octave=" + 12", pool=[0, 2, 3, 4, 5, 7, 12]),
    _melodic("glass_line", "keys", "ys_glass", "melody",
             ["4 ~ ~ ~ 2 ~ ~ ~", "7 ~ ~ 4 ~ ~ 2 ~", "~ ~ 0 ~ ~ ~ 2 ~"],
             [["gain", "0.9"], ["room", "0.6"], ["delay", "0.4"], ["delaytime", "0.5"],
              ["delayfeedback", "0.4"]], octave=" + 12", pool=[0, 2, 3, 4, 7]),
    Block("cold_seq", "arp",
          'n (scale "{mode}" ("<{arp}>*16" |+ "<{roots}>") {root} + 12) # s "ys_pluck"',
          {"arp": ["0 4 7 4", "0 7 4 2", "7 4 0 4 2", "0 2 4 7 9 7"]},
          fx=[["gain", "0.78"], ["lpf", "(slow 16 $ range 1200 4000 sine)"], ["delay", "0.4"],
              ["delaytime", "0.375"], ["delayfeedback", "0.5"], ["room", "0.4"], ["pan", "0.6"]],
          pool=[0, 2, 4, 7, 9]),
    Block("static", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
          fx=[["gain", "0.85"], ["hpf", "400"], ["legato", "1.3"], ["room", "0.8"]]),
]

BLOCKS["techno"] = _techno_blocks(hard=False)
BLOCKS["hardcore"] = _techno_blocks(hard=True)

# Per style: bars between edits (about 20-30 s; override with --edit-bars), how many lines a track builds up to, and
# which roles open a track.
STYLE_SETTINGS = {
    "lofi": {"edit_bars": 8, "max_lines": 6, "openers": [["beat"], ["keys"], ["bass", "beat"]]},
    "deephouse": {"edit_bars": 16, "max_lines": 7, "openers": [["beat"], ["beat", "bass"]]},
    "trance": {"edit_bars": 16, "max_lines": 7, "openers": [["acid"], ["beat"], ["acid", "beat"]]},
    "ambient": {"edit_bars": 8, "max_lines": 5, "openers": [["chords"], ["texture"], ["keys"]]},
    "piano": {"edit_bars": 8, "max_lines": 4, "openers": [["keys"]]},
    "downtempo": {"edit_bars": 8, "max_lines": 5, "openers": [["beat"], ["keys"]]},
    "darkwave": {"edit_bars": 12, "max_lines": 7, "openers": [["bass"], ["beat"], ["bass", "beat"]]},
    "techno": {"edit_bars": 16, "max_lines": 7, "openers": [["beat"], ["beat", "perc"]]},
    "hardcore": {"edit_bars": 16, "max_lines": 6, "openers": [["beat"]]},
}


def blocks_for(style: str, role: str | None = None) -> list[Block]:
    items = BLOCKS.get(style, BLOCKS["lofi"])
    return [b for b in items if role is None or b.role == role]


def find_block(style: str, name: str) -> Block | None:
    return next((b for b in blocks_for(style) if b.name == name), None)
