"""
Curated sound palette.

Each layer role has a small set of presets with fixed, hand-balanced mix
settings. The LLM may only pick preset *names*; it never sets gain, filters or
synth names itself, so the mix stays balanced no matter what it says.

Gains follow SuperDirt's curve (amplitude ~ gain^4), so 0.9 is about -7 dB
relative to 1.0. Energy moves a layer's gain within +/-3 dB only.

The ys_* synth levels were measured by rendering each SynthDef offline
(scsynth NRT) and the gains set for a mix of, per voice: melody ~-21 LUFS,
bass ~-19, arp ~-24, keys ~-25, pads ~-29 (a sustained chord adds up, so
pads sit in the background), textures ~-33..-38. Pads and keys are further
compensated for chord size and pulled back in busy sections (templates.py).

Every non-bass role has a high-pass filter so low end stays clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Channel/orbit per role: fixed so each role has its own reverb/delay bus and
# the overlay/state can refer to layers by role.
ROLE_CHANNELS = {
    "drums": 1,
    "bass": 2,
    "pad": 3,
    "keys": 4,
    "melody": 5,
    "arp": 6,
    "texture": 7,
    "free": 8,
}
ROLES = list(ROLE_CHANNELS)

# Order in which layers are brought in as energy rises.
ENERGY_ORDER = ["pad", "texture", "bass", "keys", "drums", "melody", "arp"]


@dataclass(frozen=True)
class Preset:
    name: str
    role: str
    synth: str
    gain: float
    fx: dict[str, float] = field(default_factory=dict)
    legato: float = 1.0
    register: tuple[int, int] = (-8, 12)
    description: str = ""
    # Stock SuperDirt fallback if the custom SynthDefs aren't loaded.
    fallback_synth: str | None = None


def _p(name, role, synth, gain, fx=None, **kw) -> Preset:
    return Preset(name=name, role=role, synth=synth, gain=gain, fx=fx or {}, **kw)


PRESETS: dict[str, dict[str, Preset]] = {
    "pad": {
        "warm_pad": _p("warm_pad", "pad", "ys_pad", 0.84,
                       {"hpf": 140, "lpf": 3200, "room": 0.45, "size": 0.85},
                       legato=1.05, register=(-8, 12), description="lush detuned analog pad",
                       fallback_synth="superpiano"),
        "air_pad": _p("air_pad", "pad", "ys_air", 1.0,
                      {"hpf": 180, "room": 0.6, "size": 0.9},
                      legato=1.1, register=(-5, 14), description="breathy noise pad",
                      fallback_synth="superpiano"),
        "strings_pad": _p("strings_pad", "pad", "ys_strings", 0.83,
                          {"hpf": 150, "lpf": 2800, "room": 0.5, "size": 0.9},
                          legato=1.05, register=(-10, 14), description="soft bowed string ensemble",
                          fallback_synth="superpiano"),
        "organ_pad": _p("organ_pad", "pad", "ys_organ", 0.8,
                        {"hpf": 140, "lpf": 2600, "room": 0.35, "size": 0.7},
                        legato=1.0, register=(-10, 12), description="warm drawbar organ chords",
                        fallback_synth="superhammond"),
        "choir_pad": _p("choir_pad", "pad", "ys_choir", 0.82,
                        {"hpf": 180, "lpf": 3200, "room": 0.6, "size": 0.9},
                        legato=1.05, register=(-8, 14), description="airy 'aah' choir",
                        fallback_synth="superpiano"),
        "gated_saw": _p("gated_saw", "pad", "ys_gatesaw", 0.9,
                        {"hpf": 200, "lpf": 3000, "resonance": 0.15, "room": 0.35, "size": 0.8,
                         "delay": 0.2, "delaytime": 0.326, "delayfeedback": 0.3},
                        legato=1.0, register=(-8, 14), description="supersaw chords through a real trance gate",
                        fallback_synth="supersaw"),
        "saw_pad": _p("saw_pad", "pad", "ys_supersaw", 0.79,
                      {"hpf": 200, "lpf": 2400, "resonance": 0.1, "room": 0.55, "size": 0.9},
                      legato=1.02, register=(-8, 14), description="wide supersaw chords (trance)",
                      fallback_synth="superpiano"),
        "piano_pad": _p("piano_pad", "pad", "superpiano", 0.78,
                        {"hpf": 120, "lpf": 2600, "room": 0.5, "size": 0.9},
                        legato=1.0, register=(-8, 10), description="soft sustained piano chords"),
    },
    "keys": {
        "rhodes": _p("rhodes", "keys", "ys_epiano", 0.92,
                     {"hpf": 150, "lpf": 3500, "room": 0.3, "size": 0.6},
                     legato=0.9, register=(-7, 9), description="mellow electric piano",
                     fallback_synth="superfm"),
        "felt": _p("felt", "keys", "ys_keys", 0.92,
                   {"hpf": 130, "lpf": 3000, "room": 0.35, "size": 0.7},
                   legato=1.0, register=(-7, 9), description="soft felt piano",
                   fallback_synth="superpiano"),
        "deep_stab": _p("deep_stab", "keys", "ys_epiano", 0.93,
                        {"hpf": 180, "lpf": 2800, "room": 0.3, "size": 0.7,
                         "delay": 0.3, "delaytime": 0.375, "delayfeedback": 0.35},
                        legato=0.35, register=(-7, 9), description="short deep-house chord stabs with echo",
                        fallback_synth="superfm"),
        "organ_stab": _p("organ_stab", "keys", "ys_organ", 0.9,
                         {"hpf": 180, "lpf": 3000, "room": 0.25, "size": 0.6,
                          "delay": 0.25, "delaytime": 0.375, "delayfeedback": 0.3},
                         legato=0.35, register=(-7, 9), description="short house organ stabs",
                         fallback_synth="superhammond"),
        "piano": _p("piano", "keys", "superpiano", 0.8,
                    {"hpf": 120, "lpf": 4000, "room": 0.3, "size": 0.7},
                    legato=0.9, register=(-7, 9), description="classic piano"),
    },
    "bass": {
        "sub": _p("sub", "bass", "ys_sub", 0.95, {"lpf": 500},
                  legato=0.95, register=(-24, -13), description="round sine sub",
                  fallback_synth="superfm"),
        "deep_bass": _p("deep_bass", "bass", "ys_sub", 0.96, {"lpf": 420},
                        legato=0.75, register=(-24, -13), description="syncopated deep-house sub bass",
                        fallback_synth="superfm"),
        "acid_line": _p("acid_line", "bass", "ys_acid", 0.98, {"room": 0.15, "size": 0.4},
                        legato=0.6, register=(-24, -13), description="squelchy 16th acid line (Switch Angel style)",
                        fallback_synth="supersaw"),
        "rolling_bass": _p("rolling_bass", "bass", "ys_tbass", 1.07, {"lpf": 1200},
                           legato=0.5, register=(-24, -13), description="rolling 16th trance bass",
                           fallback_synth="superfm"),
        "pluck_bass": _p("pluck_bass", "bass", "ys_pluck", 1.1, {"lpf": 900, "hpf": 35},
                         legato=0.8, register=(-24, -13), description="warm plucked bass",
                         fallback_synth="supermandolin"),
    },
    "melody": {
        "felt_lead": _p("felt_lead", "melody", "ys_keys", 1.03,
                        {"hpf": 250, "lpf": 5000, "room": 0.35, "size": 0.7,
                         "delay": 0.25, "delaytime": 0.375, "delayfeedback": 0.3},
                        legato=1.0, register=(0, 19), description="felt piano melody",
                        fallback_synth="superpiano"),
        "glass": _p("glass", "melody", "ys_glass", 1.02,
                    {"hpf": 300, "room": 0.45, "size": 0.85,
                     "delay": 0.3, "delaytime": 0.375, "delayfeedback": 0.35},
                    legato=1.0, register=(0, 19), description="soft glassy bell",
                    fallback_synth="superpiano"),
        "epiano_lead": _p("epiano_lead", "melody", "ys_epiano", 1.02,
                          {"hpf": 250, "lpf": 4500, "room": 0.3, "size": 0.6,
                           "delay": 0.2, "delaytime": 0.25, "delayfeedback": 0.25},
                          legato=1.0, register=(0, 17), description="electric piano lead",
                          fallback_synth="superfm"),
        "saw_lead": _p("saw_lead", "melody", "ys_supersaw", 1.1,
                       {"hpf": 300, "lpf": 4500, "resonance": 0.15, "room": 0.4, "size": 0.8,
                        "delay": 0.3, "delaytime": 0.326, "delayfeedback": 0.35},
                       legato=1.0, register=(0, 19), description="soaring supersaw lead (trance)",
                       fallback_synth="superpiano"),
        "mandolin": _p("mandolin", "melody", "supermandolin", 0.8,
                       {"hpf": 250, "room": 0.35, "size": 0.7},
                       legato=1.0, register=(0, 17), description="gentle mandolin"),
    },
    "arp": {
        "pluck_arp": _p("pluck_arp", "arp", "ys_pluck", 1.05,
                        {"hpf": 300, "lpf": 4000, "room": 0.35, "size": 0.7,
                         "delay": 0.3, "delaytime": 0.375, "delayfeedback": 0.4},
                        legato=1.0, register=(-2, 19), description="plucked arpeggio",
                        fallback_synth="supermandolin"),
        "acid_hi": _p("acid_hi", "arp", "ys_acid", 0.9,
                      {"hpf": 200, "room": 0.25, "size": 0.6, "delay": 0.25, "delaytime": 0.331,
                       "delayfeedback": 0.35},
                      legato=0.5, register=(-12, -1), description="a second, higher acid line",
                      fallback_synth="supersaw"),
        "trance_gate": _p("trance_gate", "arp", "ys_supersaw", 1.13,
                          {"hpf": 250, "lpf": 3200, "resonance": 0.3, "room": 0.3, "size": 0.7,
                           "delay": 0.25, "delaytime": 0.326, "delayfeedback": 0.35},
                          legato=0.8, register=(-5, 19), description="16th supersaw arp through a trance gate",
                          fallback_synth="supersaw"),
        "glass_arp": _p("glass_arp", "arp", "ys_glass", 0.94,
                        {"hpf": 350, "room": 0.5, "size": 0.85,
                         "delay": 0.35, "delaytime": 0.25, "delayfeedback": 0.35},
                        legato=0.8, register=(0, 22), description="twinkling bell arp",
                        fallback_synth="superpiano"),
    },
    "drums": {
        "lofi_kit": _p("lofi_kit", "drums", "", 0.9, {"lpf": 5000, "room": 0.15, "size": 0.3},
                       description="dusty swung lofi kit"),
        "soft_pulse": _p("soft_pulse", "drums", "", 0.85, {"lpf": 3500, "room": 0.25, "size": 0.5},
                         description="minimal ambient pulse"),
        "downtempo_kit": _p("downtempo_kit", "drums", "", 0.9, {"lpf": 7000, "room": 0.15, "size": 0.35},
                            description="round downtempo groove"),
        "house_kit": _p("house_kit", "drums", "", 0.92, {"lpf": 9000, "room": 0.1, "size": 0.3},
                        description="four-on-the-floor deep house groove"),
        "trance_kit": _p("trance_kit", "drums", "", 0.95, {"room": 0.1, "size": 0.3},
                         description="pumping trance kick, off-beat open hats, claps"),
    },
    "texture": {
        "vinyl": _p("vinyl", "texture", "ys_vinyl", 1.1, {"hpf": 300},
                    legato=1.0, description="vinyl crackle bed", fallback_synth=None),
        "air": _p("air", "texture", "ys_air", 0.92, {"hpf": 200, "room": 0.7, "size": 0.95},
                  legato=1.0, register=(-12, 0), description="airy drone", fallback_synth=None),
    },
    "free": {
        "free_glass": _p("free_glass", "free", "ys_glass", 0.96,
                         {"hpf": 300, "room": 0.5, "size": 0.85, "delay": 0.3,
                          "delaytime": 0.375, "delayfeedback": 0.3},
                         legato=1.0, register=(0, 19), description="LLM-composed counter-melody",
                         fallback_synth="superpiano"),
    },
}

# Drum patterns: per kit, per intensity (0 = sparse .. 2 = full).
# Each entry is a list of (sample, mini-notation, gain) played together.
DRUM_PATTERNS: dict[str, list[list[tuple[str, str, float]]]] = {
    "lofi_kit": [
        [("bd", "bd ~ ~ ~ ~ ~ ~ ~ bd ~ ~ ~ ~ ~ ~ ~", 0.95), ("hh27", "~ ~ hh27 ~ ~ ~ hh27 ~ ~ ~ hh27 ~ ~ ~ hh27 ~", 0.72)],
        [("bd", "bd ~ ~ ~ ~ ~ ~ bd ~ ~ bd ~ ~ ~ ~ ~", 0.95), ("sn", "~ ~ ~ ~ sn ~ ~ ~ ~ ~ ~ ~ sn ~ ~ ~", 0.8),
         ("hh27", "hh27*8", 0.7), ("sn:2", "~ ~ ~ ~ ~ ~ ~ sn:2? ~ ~ ~ ~ ~ ~ sn:2? ~", 0.5)],
        [("bd", "bd ~ ~ ~ ~ ~ ~ bd ~ ~ bd ~ ~ ~ ~ ~", 0.97), ("sn", "~ ~ ~ ~ sn ~ ~ ~ ~ ~ ~ ~ sn ~ ~ [~ sn]", 0.82),
         ("hh27", "hh27*8", 0.74), ("hh27:3", "~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ hh27:3 ~", 0.68),
         ("sn:2", "~ ~ ~ ~ ~ ~ ~ sn:2? ~ ~ ~ sn:2? ~ ~ sn:2? ~", 0.5)],
    ],
    "soft_pulse": [
        [("bd", "bd ~ ~ ~", 0.85)],
        [("bd", "bd ~ ~ ~", 0.88), ("hh", "~ hh ~ hh", 0.62)],
        [("bd", "bd ~ [~ bd] ~", 0.9), ("hh", "hh*4", 0.62), ("cp", "~ ~ cp ~", 0.7)],
    ],
    "trance_kit": [
        [("ys_kick", "ys_kick*4", 1.05), ("808oh", "[~ 808oh]*4", 0.66)],
        [("ys_kick", "ys_kick*4", 1.05), ("808oh", "[~ 808oh]*4", 0.68), ("cp", "~ cp ~ cp", 0.78)],
        [("ys_kick", "ys_kick*4", 1.08), ("808oh", "[~ 808oh]*4", 0.7), ("cp", "~ cp ~ cp", 0.8),
         ("hh", "hh*16", 0.52)],
    ],
    "downtempo_kit": [
        [("808bd", "808bd ~ ~ ~ ~ ~ ~ ~", 0.95), ("808hc", "~ 808hc ~ 808hc ~ 808hc ~ 808hc", 0.66)],
        [("808bd", "808bd ~ ~ 808bd ~ ~ ~ ~", 0.95), ("808sd", "~ ~ 808sd ~", 0.78),
         ("808hc", "808hc*8", 0.64)],
        [("808bd", "808bd ~ ~ 808bd ~ ~ 808bd ~", 0.97), ("808sd", "~ ~ 808sd ~", 0.8),
         ("808hc", "808hc*8", 0.66), ("808oh", "~ ~ ~ ~ ~ ~ ~ 808oh", 0.6)],
    ],
    "house_kit": [
        [("bd", "bd*4", 0.97), ("hh", "[~ hh]*4", 0.68)],
        [("bd", "bd*4", 0.97), ("hh", "[~ hh]*4", 0.7), ("cp", "~ cp ~ cp", 0.78),
         ("808lc", "[~ ~ ~ 808lc] [~ 808mc ~ ~] [~ ~ 808lc ~] ~", 0.6)],
        [("bd", "bd*4", 1.0), ("808oh", "[~ 808oh]*4", 0.64), ("cp", "~ cp ~ cp", 0.8),
         ("hh", "hh*16", 0.52), ("808lc", "[~ ~ ~ 808lc] [~ 808mc ~ ~] [~ ~ 808lc ~] [808mc ~ ~ ~]", 0.62)],
    ],
}

# Played on the last bar of every 8-bar phrase (replaces the groove).
DRUM_FILLS: dict[str, list[tuple[str, str, float]]] = {
    "trance_kit": [("ys_kick", "ys_kick*4", 1.05), ("808oh", "[~ 808oh]*4", 0.66),
                   ("sn:1", "~ ~ ~ [sn:1 sn:1 sn:1 sn:1]", 0.72)],
    "lofi_kit": [("bd", "bd ~ ~ ~ ~ ~ ~ ~ bd ~ ~ ~ ~ ~ ~ ~", 0.95),
                 ("sn", "~ ~ ~ ~ sn ~ ~ ~ ~ ~ sn ~ [sn sn] ~ sn sn", 0.78), ("hh27", "hh27*8", 0.66)],
    "soft_pulse": [("bd", "bd ~ ~ ~", 0.85), ("hh", "~ hh [hh hh] hh", 0.6)],
    "downtempo_kit": [("808bd", "808bd ~ ~ 808bd ~ ~ ~ ~", 0.95),
                      ("808sd", "~ ~ 808sd ~ ~ ~ [808sd 808sd] 808sd", 0.78), ("808hc", "808hc*8", 0.62)],
    "house_kit": [("bd", "bd*4", 0.97), ("cp", "~ cp ~ [cp cp]", 0.8), ("hh", "hh*16", 0.55),
                  ("sn:1", "~ ~ ~ [~ sn:1 sn:1 sn:1]", 0.7)],
}

# Added on the 4th bar of every 4 (a small turnaround, not a full fill).
DRUM_VARIATIONS: dict[str, list[tuple[str, str, float]]] = {
    "trance_kit": [("cp", "~ ~ ~ [~ cp]", 0.6)],
    "lofi_kit": [("hh27:3", "~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ hh27:3", 0.62)],
    "soft_pulse": [("hh", "~ ~ ~ [~ hh]", 0.55)],
    "downtempo_kit": [("808oh", "~ ~ ~ ~ ~ ~ ~ 808oh", 0.55)],
    "house_kit": [("808oh", "~ ~ ~ [~ 808oh]", 0.6)],
}

# Snare/clap used for the roll at the end of a build.
DRUM_ROLL_SAMPLE = {"lofi_kit": "sn", "soft_pulse": "hh", "downtempo_kit": "808sd", "house_kit": "cp",
                    "trance_kit": "sn:1"}

# Drum voices that are ystreamer synths, with a sample to use when the custom
# SynthDefs aren't loaded.
DRUM_SYNTH_FALLBACK = {"ys_kick": "bd"}

# Kits that get small random timing offsets (a laid-back, human feel).
LOOSE_KITS = {"lofi_kit", "downtempo_kit"}


# Instruments the legacy composer may use. Harsh demo synths are mapped to
# softer equivalents by the normaliser.
LEGACY_INSTRUMENT_WHITELIST = {
    "superpiano", "superfm", "supermandolin", "superhammond", "supervibe", "superpwm",
    "ys_organ", "ys_epiano", "ys_keys", "ys_sub", "ys_pluck", "ys_glass", "ys_air", "ys_vinyl",
    "bd", "sn", "hh", "hh27", "cp", "808bd", "808sd", "808hc", "808oh", "pad", "padlong", "arpy",
}
LEGACY_INSTRUMENT_REPLACEMENTS = {
    "supersaw": "ys_organ",      # no saw sounds (ys_pad is detuned saws too)
    "ys_pad": "ys_organ",
    "ys_strings": "ys_organ",
    "ys_supersaw": "ys_organ",
    "ys_gatesaw": "ys_organ",
    "superzow": "ys_organ",
    "superchip": "ys_glass",
    "superhoover": "ys_organ",
    "hoover": "ys_organ",
    "superstatic": "ys_air",
    "supersquare": "ys_epiano",
    "superreese": "ys_sub",
    "supersiren": "ys_glass",
    "superpanic": "ys_air",
}


def get(role: str, name: str | None) -> Preset | None:
    if not name:
        return None
    return PRESETS.get(role, {}).get(name)


def default_for(role: str) -> Preset:
    return next(iter(PRESETS[role].values()))


def names(role: str) -> list[str]:
    return list(PRESETS.get(role, {}))


def energy_gain(base: float, energy: float) -> float:
    """Scale a preset gain by energy within +/-3 dB (SuperDirt amp ~ gain^4)."""
    db = (max(0.0, min(1.0, energy)) - 0.5) * 6.0
    return round(base * 10 ** (db / 80.0), 3)
