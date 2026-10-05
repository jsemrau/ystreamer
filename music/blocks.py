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
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import config

from . import mix
from .livecode import (DEFAULT_VOICING, NOTE_BUDGET, ROLE_FX, LineSpec, arp_sequences, line_controls,
                       within_budget)

_ARP_TOKEN_RE = re.compile(r"\{(up|down|updown|pedal)\}")


def trance_gate(seed: int, density: float = 1.0, cycles: int = 1) -> str:
    """Switch Angel's trancegate as a struct pattern: 16 steps a bar, each
    on with probability 1 - 0.5 / (density + 0.5) (her rand * density,
    rounded), seeded so it repeats; each hit holds until the next one (her
    fill) and the line's legato shortens it (her clip .7). Over several
    cycles it alternates bar by bar."""
    rng = random.Random(seed)
    bars = []
    for _ in range(cycles):
        steps = [rng.random() * (density + 0.5) >= 0.5 for _ in range(16)]
        steps[0] = steps[0] or not any(steps)
        tokens, started = [], False
        for on in steps:
            tokens.append("t" if on else ("_" if started else "~"))
            started = started or on
        bars.append(" ".join(tokens))
    return bars[0] if cycles == 1 else "<" + " ".join(f"[{b}]" for b in bars) + ">"


# Her favourite gate seeds (tgate's table) at a few densities.
TRANCE_GATES = [trance_gate(45, 1.0), trance_gate(116, 1.0), trance_gate(99, 1.5), trance_gate(53, 0.7),
                trance_gate(107, 1.0, cycles=2), trance_gate(8, 1.5, cycles=2)]

# The sounds the Director picks from, per part, for each track: the toolkit of
# 1990s trance and house, without saws or organ, and darkwave's string
# machine and chorused guitar. The chosen sound replaces the
# synth of whichever block plays that part.
LIVE_SOUNDS = {
    "chords": {"ys_housepiano": "M1-style house piano chords",
               "ys_epiano": "DX7 / Rhodes electric piano chords",
               "ys_glass": "DX7-style bell chords",
               "ys_coldstrings": "string machine (Solina) chords, darkwave",
               "ys_m1organ": "M1 organ stabs (Robin S, 90s house and rave)"},
    "keys": {"ys_marimba": "marimba: woody mallet, short and bright (Balearic)",
             "ys_vibes": "vibraphone: metal bars, long ring, motor tremolo (Thievery Corporation, Nightmares on Wax)",
             "ys_harpsichord": "harpsichord: bright quill pluck (Massive Attack's Teardrop)",
             "ys_m1organ": "M1 organ stabs (90s house and rave)",
             "ys_housepiano": "M1-style house piano stabs",
             "ys_epiano": "DX7 / Rhodes electric piano",
             "ys_keys": "felt piano"},
    "lead": {"ys_pulselead": "square-wave trance lead",
             "ys_glass": "DX7-style bell lead",
             "ys_pluck": "plucked lead",
             "ys_coldguitar": "chorused clean guitar melody, post-punk",
             "ys_wavelead": "lingering wave lead: swells in, glides, slow vibrato, rings out long (darkwave)",
             "ys_flute": "breathy flute: pan flute with a soft chiff and a late vibrato (90s Ibiza, Enigma)",
             "ys_marimba": "marimba: woody mallet, short and bright (Balearic, progressive)",
             "ys_sitar": "sitar: twangy string with a buzzing bridge, slides into each note (Goa, psy)",
             "ys_vox": "vocal lead: a voice singing aah into ooh (Delerium, 90s trance)",
             "ys_fmlead": "FM lead: DX7, bright on the attack then hollow, like a digital clarinet",
             "ys_epiano": "Rhodes melody: warm, soulful (Nightmares on Wax, Axelle Red)",
             "ys_keys": "felt piano melody: soft, intimate",
             "ys_nylon": "nylon-string guitar: warm Balearic pluck (Chicane)",
             "ys_melodica": "melodica: nasal dub reed (Thievery Corporation, Augustus Pablo)",
             "ys_vibes": "vibraphone: metal bars, long ring, motor tremolo (lounge, Yonderboi)",
             "ys_trumpet": "muted trumpet: jazz, a harmon mute's bite (Yonderboi, lounge)",
             "ys_harpsichord": "harpsichord: bright quill pluck (Massive Attack's Teardrop)",
             "ys_sax": "saxophone: a soaring, bending sax (Guru Josh's Infinity)",
             "ys_angelchoir": "angel choir: ethereal layered female voices on long notes (Chicane's Saltwater, Celtic)",
             "ys_chipvox": "sped-up chipmunk vocal: a bright sung ya (happy hardcore, Wonderful Days)",
             "ys_housepiano": "house piano melody (happy hardcore, piano house)",
             "ys_cafepluck": "Cafe del Mar pluck: a soft plucked synth through a resonant filter, in a long dotted "
                             "delay (Energy 52, 90s Balearic trance)"},
    "arp": {"ys_pluck": "plucked arp (Balearic / Cafe del Mar)",
            "ys_glass": "bell arp",
            "ys_pulselead": "square-wave arp",
            "ys_coldguitar": "chorused clean guitar arpeggio, post-punk",
            "ys_wavesynth": "wave synth arpeggio: each note rings for seconds into a shifting wash (darkwave)",
            "ys_marimba": "marimba arpeggio: woody, bouncing (Balearic)",
            "ys_sitar": "sitar arpeggio: twangy, buzzing (Goa, psy)",
            "ys_fmlead": "FM arpeggio: bright digital plucks (DX7)",
            "ys_nylon": "nylon guitar arpeggio: Balearic (Chicane)",
            "ys_vibes": "vibraphone arpeggio: lounge, ringing",
            "ys_harpsichord": "harpsichord arpeggio: baroque, dark (Massive Attack)",
            "ys_cafepluck": "Cafe del Mar pluck arpeggio: soft, resonant, in dotted delay (90s Balearic)"},
    # (ys_hypersaw, the trance supersaw, joins chords, lead and arp below
    # when config.HYPERSAW is on.)
    # Atmosphere only: quiet, far behind the chords and the lead.
    "texture": {"ys_air": "airy breath, far in the background",
                "ys_softpad": "soft pad bed, far in the background",
                "ys_angelchoir": "angel choir bed: ethereal female voices, far behind"},
}
# The saws done properly (band-limited, filtered: ys_hypersaw, ys_sawlead,
# ys_sawpad) go on air once they've been heard (eval.sound_preview --saws).
if config.HYPERSAW:
    LIVE_SOUNDS["chords"]["ys_hypersaw"] = "trance supersaw chords (seven detuned saws)"
    LIVE_SOUNDS["lead"]["ys_hypersaw"] = "trance supersaw lead"
    LIVE_SOUNDS["arp"]["ys_hypersaw"] = "supersaw arp"
    LIVE_SOUNDS["lead"]["ys_sawlead"] = "saw lead: two band-limited saws, the filter opening on each note (90s trance, hardcore)"
    LIVE_SOUNDS["texture"]["ys_sawpad"] = "lush saw pad bed (seven band-limited saws, dark), behind everything"
    LIVE_SOUNDS["lead"]["ys_hoover"] = "the hoover: swooping detuned saws (The Prodigy, 90s rave and hardcore)"

# Real instruments with no modelled SynthDef (music/instruments.py: the
# FreePats harp and kalimba, CC0) are offered only where they're installed.
from . import instruments as _instruments  # noqa: E402

if _instruments.installed("harp"):
    LIVE_SOUNDS["arp"]["ys_harp"] = "concert harp: real, plucked, ringing (Balearic, ambient, cinematic)"
    LIVE_SOUNDS["keys"]["ys_harp"] = "concert harp: real, plucked, ringing"
if _instruments.installed("kalimba"):
    LIVE_SOUNDS["arp"]["ys_kalimba"] = "kalimba: real, soft metal tines, a little out of tune (Balearic, lounge, ambient)"
    LIVE_SOUNDS["keys"]["ys_kalimba"] = "kalimba: real, soft metal tines"

# Sounds taken off air after listening to them (eval.sound_preview): the
# square lead (12_square_lead_today), the saw pad (13_saw_pad) and darkwave's
# chorused clean guitar (7_darkwave_guitar, model and recording). Their
# SynthDefs stay, for old sessions; no track picks them, and a resumed line
# that plays one gets another (live_engine, plan.UNWANTED_SYNTHS).
RETIRED_SOUNDS = {"ys_pulselead", "ys_sawpad", "ys_coldguitar"}
for _palette in LIVE_SOUNDS.values():
    for _synth in RETIRED_SOUNDS & set(_palette):
        del _palette[_synth]


# Each sound's character, so the theme can choose among the sounds the style
# allows (the style sets the frame, the theme the details): the Director reads
# the words, the built-in planner matches the moods against the theme's words
# (plan.THEME_MOODS).
SOUND_CHARACTER = {
    "ys_housepiano": ("bright", "warm", "euphoric", "retro"),
    "ys_epiano": ("warm", "dreamy", "retro"),
    "ys_glass": ("dreamy", "cold", "bright", "eerie"),             # a music box, in the dark
    "ys_keys": ("warm", "melancholic", "dreamy"),
    "ys_coldstrings": ("dark", "cold", "melancholic", "cinematic", "eerie"),
    "ys_pulselead": ("bright", "retro", "hard"),
    "ys_pluck": ("bright", "warm", "dreamy"),
    "ys_coldguitar": ("dark", "melancholic", "cold"),
    "ys_wavelead": ("dark", "dreamy", "melancholic", "cinematic", "eerie"),   # scoop and vibrato: a theremin
    "ys_wavesynth": ("dreamy", "dark", "cold", "eerie"),
    "ys_air": ("dreamy", "cold", "eerie"),
    "ys_softpad": ("warm", "dreamy"),
    "ys_hypersaw": ("euphoric", "bright", "hard"),
    "ys_sawlead": ("hard", "euphoric", "retro"),
    "ys_sawpad": ("euphoric", "cinematic", "warm"),
    "ys_flute": ("dreamy", "warm", "mystic", "melancholic"),
    "ys_marimba": ("bright", "warm", "dreamy"),
    "ys_sitar": ("mystic", "hard", "bright"),
    "ys_vox": ("euphoric", "dreamy", "cinematic", "mystic", "eerie"),
    "ys_fmlead": ("bright", "retro", "cold"),
    "ys_hoover": ("hard", "retro", "euphoric"),
    "ys_m1organ": ("euphoric", "retro", "warm"),
    "ys_nylon": ("warm", "bright", "dreamy", "melancholic"),
    "ys_melodica": ("warm", "melancholic", "mystic"),
    "ys_vibes": ("dreamy", "warm", "retro"),
    "ys_trumpet": ("melancholic", "warm", "cinematic", "dark"),
    "ys_harpsichord": ("dark", "melancholic", "cinematic", "eerie"),
    "ys_sax": ("warm", "euphoric", "retro", "melancholic"),
    "ys_angelchoir": ("dreamy", "mystic", "cinematic", "euphoric", "eerie"),
    "ys_chipvox": ("euphoric", "bright", "retro"),
    "ys_cafepluck": ("dreamy", "warm", "bright", "retro"),
    "ys_harp": ("dreamy", "cinematic", "mystic", "warm"),
    "ys_kalimba": ("bright", "warm", "dreamy", "mystic"),
}


# Parts that can carry the Director's hook, and how their tune slot looks in
# a block body: '("<{motif}>*16" |+ "<{roots}>")' or '("{melody}" |+ "<{roots}>")'.
HOOK_ROLES = ("acid", "lead", "arp", "keys")
# Parts whose starting pattern the Director's sketch can set: tunes, the
# bass line, and chord rhythms ('struct "{stab}"').
SKETCH_ROLES = ("acid", "lead", "arp", "keys", "bass", "chords")
_STRUCT_SLOT_RE = re.compile(r'struct "\{(\w+)\}"')
_TUNE_SLOT_RE = re.compile(r'\("(<)?\{(\w+)\}(?:>\*\d+)?" \|\+ "<\{roots\}>"\)')


def sketch_kinds(style: str) -> dict[str, str]:
    """Role -> kind of pattern the Director's sketch can give it in this
    style: "cycle", "phrase", "bassline" (tunes) or "rhythm" (chord hits)."""
    out: dict[str, str] = {}
    for block in BLOCKS.get(style, BLOCKS["lofi"]):
        slot = block.sketch_slot()
        if slot and block.role not in out:
            out[block.role] = slot[1]
    return out


def hook_kinds(style: str) -> dict[str, str]:
    """Role -> kind ("cycle" or "phrase") of the hooks a style's blocks can play."""
    out: dict[str, str] = {}
    for block in BLOCKS.get(style, BLOCKS["lofi"]):
        tune = block.hook_slot()
        if tune and block.role not in out:
            out[block.role] = tune[1]
    return out


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
    # A block built around its voice (its patterns are written for it) keeps
    # that voice whatever sound the track picked for the part.
    keep_sound: bool = False
    # How this block's voice phrases a tune it is given (the hook, the
    # sketch): e.g. the sax holds notes where a synth runs 16ths.
    phrasing: Callable[[str], str] | None = None
    # Its note budget a bar (0: its role's, livecode.NOTE_BUDGET): a tune it
    # is given (the hook, the sketch) is thinned to it.
    max_notes: int = 0

    def hook_slot(self) -> tuple[str, str] | None:
        """(slot, kind) of the pattern that carries this block's tune, or None:
        kind "cycle" is a note cycle played as 16ths ("<{motif}>*16"),
        "phrase" a melody over the bar ("{melody}")."""
        if self.drums or self.role not in HOOK_ROLES:
            return None
        m = _TUNE_SLOT_RE.search(self.body)
        if not m or m.group(2) not in self.patterns:
            return None
        return m.group(2), "cycle" if m.group(1) else "phrase"

    def sketch_slot(self) -> tuple[str, str] | None:
        """(slot, kind) of the pattern the Director's sketch sets for this
        block: its tune ("cycle", "phrase", or "bassline" for the bass) or its
        chord rhythm ("rhythm", t for a hit, ~ for a rest)."""
        if self.drums or self.role not in SKETCH_ROLES:
            return None
        m = _TUNE_SLOT_RE.search(self.body)
        if m and m.group(2) in self.patterns:
            return m.group(2), "cycle" if m.group(1) else ("bassline" if self.role == "bass" else "phrase")
        m = _STRUCT_SLOT_RE.search(self.body)
        if m and m.group(1) in self.patterns:
            return m.group(1), "rhythm"
        return None

    def make(self, rng: random.Random, sounds: dict[str, str] | None = None,
             hook: dict[str, str] | None = None, parts: dict | None = None,
             motion: dict | None = None, voicing: str = DEFAULT_VOICING, kit: dict | None = None) -> LineSpec:
        """A new line from this block; `sounds` (part -> synth, the Director's
        choice for the track) sets the synth of melodic parts, the track's
        `hook` becomes the tune of the part it was written for, `parts` (role
        -> {"kind", "pattern"}, the Director's sketch) the starting pattern of
        the others, and `motion` (role -> {control: value}) sets knobs that
        move on their own."""
        patterns = {slot: rng.choice(options) for slot, options in self.patterns.items()}
        # The subgenre's kit: its hat, clap and bass-rhythm variants instead.
        for slot in patterns:
            if kit and kit.get(slot) and (self.drums or self.role == "bass"):
                patterns[slot] = rng.choice(kit[slot])
        # Arp variants name a run through the track's voicing ("{up}", "{down}",
        # ...: Switch Angel's trancearp), so they play its chord tones only.
        arps = arp_sequences(voicing)
        patterns = {slot: _ARP_TOKEN_RE.sub(lambda m: arps.get(m.group(1), m.group(0)), v)
                    for slot, v in patterns.items()}
        tune = self.hook_slot()
        slot = self.sketch_slot()
        if hook and tune and hook.get("role") == self.role and hook.get("kind", tune[1]) == tune[1]:
            patterns[tune[0]] = hook["pattern"]
            if self.phrasing:
                patterns[tune[0]] = self.phrasing(patterns[tune[0]])
        elif parts and slot and (parts.get(self.role) or {}).get("kind") == slot[1]:
            patterns[slot[0]] = parts[self.role]["pattern"]
            if self.phrasing:
                patterns[slot[0]] = self.phrasing(patterns[slot[0]])
        body = self.body
        sound = (sounds or {}).get(self.role)
        palette = LIVE_SOUNDS.get(self.role, {})
        own = {mix.synth_of(body), *(self.patterns.get("synth") or [])}
        # Only a block that already plays one of the part's sounds changes: the
        # vinyl crackle stays vinyl, the high acid line stays acid.
        if sound in palette and own & set(palette) and not self.drums and not self.keep_sound:
            if "synth" in patterns:
                patterns["synth"] = sound
            else:
                body = re.sub(r'# s "ys_\w+"', f'# s "{sound}"', body)
        fx = [list(p) for p in self.fx]
        # Level from the measured loudness of the synth actually chosen
        # (a block may offer several), so the line enters at its role's level.
        synth = mix.synth_of(body.replace('"{synth}"', f'"{patterns.get("synth", "")}"'))
        # Short chord hits (struct rhythms) aren't comparable with a sustained
        # measurement, so those keep their tuned gain.
        gain = None if "struct" in body else mix.calibrated_gain(synth, self.role, chord="{chords}" in body)
        if gain is not None:
            fx = [[n, f"{gain:g}"] if n == "gain" else [n, v] for n, v in fx]
        pfs = [list(p) for p in self.pfs]
        for control, value in ((motion or {}).get(self.role) or {}).items():
            if control == "gain":
                continue
            if any(n == control for n, _ in pfs):
                pfs = [[n, value if n == control else v] for n, v in pfs]
            elif any(n == control for n, _ in fx):
                fx = [[n, value if n == control else v] for n, v in fx]
            elif control in ROLE_FX.get(self.role, ()):
                fx.append([control, value])
            elif control in line_controls(LineSpec(self.role, body, patterns)):
                pfs.append([control, value])     # a control the synth takes (e.g. acidres)
        # Less is more: the tune within the line's note budget.
        budget = self.max_notes or NOTE_BUDGET.get(self.role, 0)
        if budget and not self.drums:
            for name in dict.fromkeys(s[0] for s in (tune, slot) if s):
                patterns[name] = within_budget(body, name, patterns[name], budget)
        return LineSpec(
            role=self.role, body=body, patterns=patterns,
            fx=fx, pfs=pfs,
            wrappers=list(self.wrappers), drums=self.drums, block=self.name,
            base_gain=dict((n, v) for n, v in fx).get("gain", ""), max_notes=self.max_notes,
        )


def _melodic(name, role, synth, pattern_slot, variants, fx, octave="", pool=None, rhythm_body=None, **kw):
    body = rhythm_body or (
        f'n (scale "{{mode}}" ("{{{pattern_slot}}}" |+ "<{{roots}}>") {{root}}{octave}) # s "{synth}"')
    # Bass and acid lines play chord tones only (see livecode.harmony_problem);
    # melodies may pass through other scale notes between the strong steps.
    default_pool = [0, 2, 4, 6, 7] if role in ("bass", "acid") else [0, 1, 2, 3, 4, 5, 7]
    return Block(name, role, body, {pattern_slot: variants}, fx=fx, pool=pool or default_pool, **kw)


# Samples from the project's own banks (samples/, loaded by superdirt_boot.scd;
# SuperDirt numbers a bank's audio files in SuperCollider's sort order).
# Funk breaks, two bars at 93-108 BPM each, stretched to two cycles (loopAt), so
# they slow down and drop in pitch at lofi tempos. They measure -12 to -17 LUFS;
# amp 0.29 puts them at the dusty kit's -17 LUFS (Funky Drummer 2 dB above).
BREAK_LOOPS = ["break:3", "break:9", "break:18", "break:22"]   # Funky Drummer, King Of The Beats, A Funky Song, It's A New Day


def _break_loop() -> Block:
    return Block("break_loop", "beat", 'loopAt 2 $ chop 16 $ s "{loop}" # amp 0.29', {"loop": BREAK_LOOPS},
                 fx=[["gain", "0.92"], ["lpf", "5000"]], drums=True)


def _foley_bed() -> Block:
    """Steady foley (air, hiss, rain-like: loudness range under 3.5 LU), far behind."""
    return Block("foley_bed", "texture", 'slow 2 $ s "{foley}" # amp 0.07',
                 {"foley": ["foley:157", "foley:172", "foley:227"]},
                 fx=[["gain", "0.9"], ["room", "0.5"], ["lpf", "6000"]], drums=True)


# Blood Sport's vocal phrases (samples/bsvocals, SuperCollider's sort order:
# 2 "I am here", 3 "Imagine the feeling", 4 "Meeting", 5 "Hush", 11 "What is
# work", 12 "Swimming in chlorine"; 0.7-2.2 s, -13 to -16 LUFS raw). Spoken
# and half-sung, no key to keep, so they come in as phrases a bar long, one
# step a bar, lost in reverb and delay. amp 0.2 puts them at -20 LUFS, just
# above the lead (SuperDirt plays at amp 0.4: -14.3 - 14 + 8).
def _vocal_chop(phrases: list[str]) -> Block:
    return Block("vocal_chop", "vocal", 'slow 4 $ s "{vox}" # amp 0.2', {"vox": phrases},
                 fx=[["gain", "1.0"], ["room", "0.4"], ["size", "0.8"], ["delay", "0.3"], ["delaytime", "0.375"],
                     ["delayfeedback", "0.35"], ["hpf", "200"]], drums=True)


def held_phrase(pattern: str) -> str:
    """A tune as a slow wind player phrases it: at most one new note a beat.
    A note between the beats (a bar of 8 or 16 steps) becomes a hold of the
    note before it (_), so the line sings in long notes instead of running
    8ths or 16ths. The notes on the beats (chord tones) stay, so the harmony
    doesn't move; rests stay rests (the breaths), and a note right after one
    (a pickup) stays."""
    def bar(steps: str) -> str:
        tokens = steps.split()
        if len(tokens) not in (8, 16):
            return steps
        beat = len(tokens) // 4
        out = []
        for i, t in enumerate(tokens):
            if i % beat and t not in ("~", "_") and out and out[-1] != "~":
                t = "_"
            out.append(t)
        return " ".join(out)
    if "[" in pattern:
        return re.sub(r"\[([^\[\]]*)\]", lambda m: "[" + bar(m.group(1)) + "]", pattern)
    return bar(pattern)


# The sax (the real one, music/instruments.py, or its model): slow and deep,
# an octave below the other leads (a tenor's warm middle), at most a note a
# beat, long notes held into the next (legato 1; the SynthDef scoops into
# each and lets a held one's vibrato in), a breath between phrases, a soft
# room and a little echo, not pumping with the kick (Guru Josh's "Infinity",
# lounge, trip-hop). Written for the sax, so a track whose lead is the sax
# plays this line, and the track's hook is phrased the sax's way.
def _sax_lead() -> Block:
    return _melodic("sax_lead", "lead", "ys_sax", "melody",
                    ["<[4 _ _ _ _ _ 2 _] [0 _ _ _ _ _ ~ ~]>", "<[0 _ _ _ 4 _ _ _] [7 _ _ _ _ _ ~ ~]>",
                     "<[7 _ _ _ _ _ _ _] [6 _ _ _ 4 _ ~ ~]>", "<[~ ~ 2 _ 4 _ _ _] [2 _ _ _ 0 _ ~ ~]>"],
                    [["gain", "1.0"], ["legato", "1.0"], ["room", "0.4"], ["size", "0.7"], ["delay", "0.15"],
                     ["delaytime", "0.375"], ["delayfeedback", "0.2"]], octave=" - 12", keep_sound=True,
                    phrasing=held_phrase)


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
              fx=[["gain", "0.9"], ["legato", "0.5"], ["room", "0.3"], ["lpf", "(slow 8 $ range 2000 3600 sine)"]]),
        Block("felt_comp", "keys",
              'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_keys"',
              {"comp": ["t ~ ~ ~ t ~ ~ ~", "~ ~ t ~ ~ ~ t ~", "t ~ ~ t ~ ~ ~ ~"]},
              fx=[["gain", "0.9"], ["legato", "0.8"], ["room", "0.35"], ["lpf", "(slow 8 $ range 1800 3400 sine)"]]),
        _melodic("felt_melody", "lead", "ys_keys", "melody",
                 ["0 ~ 2 ~ 4 ~ 2 ~", "~ 4 ~ 2 0 ~ ~ ~", "4 ~ ~ 2 ~ 0 ~ -1", "0 2 ~ 4 ~ ~ 2 ~",
                  "<[0 ~ 2 ~ 4 ~ ~ ~] [4 ~ 2 ~ 0 ~ -1 ~]>"],
                 [["gain", "1.02"], ["room", "0.35"], ["delay", "0.25"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.3"]], octave=" + 12"),
        _melodic("glass_melody", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ 2 ~ ~ ~", "0 ~ ~ 2 ~ ~ 4 ~", "~ ~ 7 ~ 4 ~ 2 ~",
                  "<[~ 4 ~ ~ 2 ~ ~ ~] [~ 7 ~ ~ 4 ~ 2 ~]>"],
                 [["gain", "1.0"], ["room", "0.45"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.35"]], octave=" + 12"),
        _melodic("pluck_arp", "acid", "ys_pluck", "arp",
                 ["0 2 4 7 4 2 0 ~", "[0 4] [2 7] [4 2] ~", "0 ~ 4 ~ 7 ~ 4 ~"],
                 [["gain", "1.0"], ["room", "0.3"], ["lpf", "4000"], ["pan", "(slow 8 $ range 0.3 0.7 sine)"]]),
        Block("glass_twinkle", "arp",
              'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "ys_glass"',
              {"arp": ["0 4 7 4", "7 4 2 4", "0 2 4 7 4 2"]},
              fx=[["gain", "0.72"], ["room", "0.45"], ["delay", "0.3"], ["delaytime", "0.375"], ["delayfeedback", "0.35"], ["pan", "(slow 8 $ range 0.3 0.7 sine)"]],
              pool=[0, 2, 4, 7, 9]),
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
              {"comp": ["~ ~ t ~ ~ ~ t ~", "t ~ ~ t ~ ~ t ~", "~ t ~ t ~ ~ t ~"], "synth": ["ys_epiano", "ys_keys"]},
              fx=[["gain", "0.92"], ["legato", "0.35"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.35"], ["lpf", "(slow 16 $ range 1600 3200 sine)"]]),
        _melodic("glass_hook", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ ~ 2 ~ ~", "0 ~ ~ 4 ~ ~ 2 ~", "~ ~ 7 ~ ~ 4 ~ ~",
                  "<[~ 4 ~ ~ ~ 2 ~ ~] [~ 7 ~ ~ 4 ~ 2 ~]>"],
                 [["gain", "1.0"], ["room", "0.4"], ["delay", "0.3"], ["delaytime", "0.375"],
                  ["delayfeedback", "0.3"]], octave=" + 12"),
        Block("rhodes_chords", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_epiano"', {},
              fx=[["gain", "0.8"], ["legato", "1.0"], ["room", "0.4"], ["lpf", "(slow 16 $ range 1800 3000 sine)"]]),
        Block("soft_bed", "texture", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_softpad"', {},
              fx=[["gain", "0.6"], ["legato", "1.1"], ["lpf", "1800"], ["room", "0.6"], ["size", "0.9"]]),
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
        # The kick alone, on every beat; hats and claps on their own line,
        # quieter and above the low end, so neither fights the kick.
        Block("trance_beat", "beat", 's "{kick}"', {"kick": ["ys_kick909*4"]},
              fx=[["gain", "1.0"]], drums=True),
        Block("trance_hats", "perc", 's "[{hats}, {clap}]"',
              {"hats": ["[~ 808oh]*4", "[~ 808oh]*4, hh*16", "[~ hh]*4"], "clap": ["~ cp ~ cp", "~"]},
              fx=[["gain", "0.8"], ["hpf", "500"]], drums=True),
        Block("acid", "acid",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
              {"motif": ["0 4 0 9 7", "0 7 0 2", "0 2 0 4 7", "0 4 0 9 7 11", "0 0 7 2 4"]},
              fx=[["gain", "0.98"], ["legato", "0.6"]],
              # The 303's accent on off-beat 16ths gives the line its bite.
              pfs=[["acid", "(slow 16 $ range 0.35 0.75 sine)"], ["accent", '"[0 0 1 0 0 1 0 0]*2"'],
                   ["drive", "1.4"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9, 11]),
        _melodic("rolling_bass", "bass", "ys_tbass", "rhythm",
                 ["[~ 0 0 0]*4", "[~ 0 0 0]*3 [~ 0 0 7]", "[~ ~ 0 ~]*4"],
                 [["gain", "1.05"], ["lpf", "(slow 16 $ range 800 1600 sine)"], ["legato", "0.85"]], octave=" - 24",
                 pool=[0, 7],
                 wrappers=['(|* gain "[0.67 0.91 1 1]*4")']),
        # No saw or organ sounds: 90s M1 piano stabs carry the chords (a pad
        # is only a quiet bed, see soft_bed), and a glass lead.
        # The chords through Switch Angel's trance gate (TRANCE_GATES), each
        # hit held to the next and clipped to 70%; the voicing is the track's.
        Block("piano_stabs", "chords",
              'struct "{stab}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_housepiano"',
              # Switch Angel's gates, the offbeat, and the 90s piano riffs (Robin S,
              # Lush 3-5): a track's chords don't always chop the same way.
              {"stab": TRANCE_GATES + ["~ t ~ t ~ t ~ t", "t ~ ~ t ~ ~ t ~", "~ ~ t ~ ~ t ~ t", "t ~ t ~ ~ t ~ ~"]},
              fx=[["gain", "0.8"], ["legato", "0.7"], ["room", "0.3"], ["lpf", "(slow 16 $ range 2500 4500 sine)"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")']),
        # Melodies, not arpeggios: steps with passing notes between the beats,
        # a call and its answer, long held notes (_) to close a phrase.
        _melodic("glass_lead", "lead", "ys_glass", "melody",
                 ["0 ~ ~ 2 ~ ~ 4 ~", "4 3 2 ~ 0 _ _ ~", "7 ~ ~ 4 ~ 2 ~ 0",
                  "<[4 3 2 ~ 0 _ _ ~] [2 ~ 4 5 4 _ 2 ~]>", "<[7 _ 6 ~ 4 ~ 2 3] [4 _ _ ~ 2 ~ 0 ~]>",
                  "<[0 ~ 2 3 4 _ _ ~] [7 ~ 6 ~ 4 3 2 ~] [4 ~ 2 ~ 0 _ 2 ~] [-1 _ 0 _ _ ~ ~ ~]>",
                  "[4 _ _ _ 2 _ 0 _ 2 _ _ _ 4 _ _ _]"],
                 [["gain", "1.05"], ["lpf", "4500"], ["room", "0.4"], ["delay", "0.3"], ["delaytime", "0.33"],
                  ["delayfeedback", "0.35"]], octave=" + 12",
                 wrappers=['(|* gain "[0.67 0.91 1 1]*4")']),
        # The 90s Balearic lead (Energy 52's "Cafe del Mar", Chicane): the tune
        # an octave up on a soft pluck, or the nylon guitar, in a dotted-8th
        # echo (Track.make puts it on the beat) that answers each note once or
        # twice and dies away (more piled the echoes over the next notes), some
        # room, not pumping with the kick.
        _melodic("cafe_lead", "lead", "ys_cafepluck", "melody",
                 ["<[4 3 2 ~ 0 _ _ ~] [2 ~ 4 5 4 _ 2 ~]>", "<[0 ~ 2 3 4 _ _ ~] [7 ~ 6 ~ 4 _ _ ~]>",
                  "<[7 _ 6 ~ 4 ~ 2 3] [4 _ _ ~ 2 ~ 0 ~]>", "4 3 2 ~ 0 _ _ ~"],
                 [["gain", "1.0"], ["legato", "0.9"], ["lpf", "6000"], ["room", "0.3"], ["size", "0.6"],
                  ["delay", "0.28"], ["delaytime", "0.34"], ["delayfeedback", "0.25"]], octave=" + 12"),
        Block("high_acid", "arp",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root}) # s "ys_acid"',
              {"motif": ["0 4 0 9 7", "7 4 0 4", "0 2 4 7 9 7 4 2", "9 7 4 0 4"]},
              fx=[["gain", "0.8"], ["legato", "0.5"], ["delay", "0.25"], ["delaytime", "0.1875"],
                  ["delayfeedback", "0.3"], ["pan", "(slow 4 $ range 0.3 0.7 sine)"]],
              pfs=[["acid", "(slow 12 $ range 0.3 0.65 cosine)"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9, 11]),
        # The angel choir's line: long Celtic notes (pentatonic steps, a grace
        # note into a held one), the way Moya Brennan's voice floats over
        # "Saltwater"; written for the choir, so it keeps its voice.
        _melodic("choir_lead", "lead", "ys_angelchoir", "melody",
                 ["<[4 _ _ _ 2 _ 0 _] [2 _ _ _ ~ ~ ~ ~] [4 _ _ 5 4 _ 2 _] [0 _ _ _ _ _ ~ ~]>",
                  "<[0 _ _ _ 2 _ 4 _] [7 _ _ _ _ _ ~ ~] [4 _ _ _ 2 _ _ 1] [0 _ _ _ _ _ ~ ~]>",
                  "<[7 _ _ _ 4 _ _ _] [2 _ 4 _ 2 _ 0 _] [-1 _ _ _ 0 _ _ _] [0 _ _ _ _ _ ~ ~]>"],
                 [["gain", "0.9"], ["legato", "1.2"], ["lpf", "6000"], ["room", "0.7"], ["size", "0.95"],
                  ["delay", "0.35"], ["delaytime", "0.5"], ["delayfeedback", "0.4"]],
                 pool=[0, 2, 4, 7, -1], keep_sound=True),
        # Switch Angel's trancearp: 16ths running through the voicing's notes.
        Block("pluck_16", "arp",
              'n (scale "{mode}" ("<{arp}>*16" |+ "<{roots}>") {root} + 12) # s "ys_pluck"',
              {"arp": ["{up}", "{updown}", "{down}", "{pedal}"]},
              fx=[["gain", "0.85"], ["lpf", "(slow 8 $ range 1500 5000 sine)"], ["delay", "0.3"],
                  ["delaytime", "0.375"], ["delayfeedback", "0.09"], ["room", "0.3"]],
              wrappers=['(|* gain "[0.67 0.91 1 1]*4")'], pool=[0, 2, 4, 6, 7, 9]),
        Block("soft_bed", "texture", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_softpad"', {},
              fx=[["gain", "0.6"], ["legato", "1.1"], ["lpf", "1800"], ["room", "0.6"], ["size", "0.9"]]),
        Block("air", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.92"], ["hpf", "200"], ["legato", "1.3"], ["room", "0.7"]], keep_sound=True),
    ],
    # --------------------------------------------------------------- ambient
    "ambient": [
        Block("air_pad", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "{synth}"',
              {"synth": ["ys_air", "ys_softpad"]},
              fx=[["gain", "0.85"], ["legato", "1.1"], ["room", "0.6"], ["size", "0.9"],
                  ["lpf", "(slow 16 $ range 1400 2800 sine)"]]),
        _melodic("glass_bells", "lead", "ys_glass", "melody",
                 ["~ 4 ~ ~ ~ ~ 2 ~", "0 ~ ~ ~ 7 ~ ~ ~", "~ ~ 4 ~ ~ 2 ~ ~",
                  "<[~ 4 ~ ~ ~ ~ 2 ~] [~ ~ 7 ~ ~ 4 ~ ~]>"],
                 [["gain", "0.98"], ["room", "0.6"], ["delay", "0.4"], ["delaytime", "0.5"],
                  ["delayfeedback", "0.45"]], octave=" + 12"),
        Block("felt_notes", "keys", 'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_keys"',
              {"comp": ["t ~ ~ ~ ~ ~ ~ ~", "~ ~ ~ ~ t ~ ~ ~"]},
              fx=[["gain", "0.88"], ["legato", "1.5"], ["room", "0.5"]]),
        Block("slow_glass", "arp",
              'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "ys_glass"',
              {"arp": ["0 4 7 9", "7 4 2 0", "0 2 4 7 9 7"]},
              fx=[["gain", "0.7"], ["room", "0.6"], ["size", "0.9"], ["delay", "0.45"], ["delaytime", "0.5"], ["delayfeedback", "0.45"], ["pan", "(slow 16 $ range 0.25 0.75 sine)"]],
              pool=[0, 2, 4, 7, 9]),
        _melodic("sub_drone", "bass", "ys_sub", "rhythm", ["0 ~ ~ ~ ~ ~ ~ ~", "0 ~ ~ ~ 4 ~ ~ ~"],
                 [["gain", "0.9"], ["legato", "1"], ["lpf", "300"]], octave=" - 24", pool=[0, 4]),
        Block("pulse", "beat", 's "{kick}"', {"kick": ["bd ~ ~ ~", "bd ~ ~ ~ ~ ~ ~ ~"]},
              fx=[["gain", "0.85"], ["lpf", "2500"], ["room", "0.3"]], drums=True),
        Block("air", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
              fx=[["gain", "0.92"], ["hpf", "200"], ["legato", "1.3"], ["room", "0.7"]], keep_sound=True),
    ],
    # ----------------------------------------------------------------- piano
    "piano": [
        Block("piano_chords", "keys", 'struct "{comp}" $ n (scale "{mode}" "<{chords}>" {root}) # s "superpiano"',
              {"comp": ["t ~ ~ ~ t ~ ~ ~", "t ~ ~ t ~ ~ t ~", "t ~ ~ ~ ~ ~ ~ ~"]},
              fx=[["gain", "0.8"], ["legato", "1"], ["room", "0.4"]]),
        _melodic("piano_melody", "lead", "superpiano", "melody",
                 ["0 ~ 2 ~ 4 ~ ~ ~", "~ 4 2 ~ 0 ~ ~ ~", "4 ~ ~ 7 ~ 4 2 ~",
                  "<[0 ~ 2 ~ 4 ~ ~ ~] [4 ~ 2 ~ 0 ~ -1 ~]>"],
                 [["gain", "0.85"], ["room", "0.4"]], octave=" + 12"),
        Block("piano_arp", "arp",
              'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "superpiano"',
              {"arp": ["0 4 7 4", "0 2 4 7", "7 4 2 4"]},
              fx=[["gain", "0.62"], ["room", "0.45"], ["pan", "(slow 8 $ range 0.35 0.65 sine)"]],
              pool=[0, 2, 4, 7, 9]),
        _melodic("felt_bass", "bass", "ys_keys", "rhythm", ["0 ~ ~ ~ 4 ~ ~ ~", "0 ~ ~ ~ ~ ~ ~ ~"],
                 [["gain", "0.95"], ["legato", "1"]], octave=" - 12", pool=[0, 4]),
        Block("air_bed", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_air"', {},
              fx=[["gain", "0.78"], ["legato", "1.05"], ["room", "0.5"], ["lpf", "(slow 16 $ range 1600 2800 sine)"]]),
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
              fx=[["gain", "0.9"], ["legato", "0.8"], ["room", "0.35"], ["lpf", "(slow 8 $ range 1800 3600 sine)"]]),
        Block("pluck_arp8", "arp",
              'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root}) # s "ys_pluck"',
              {"arp": ["0 4 7 4", "0 2 4 7", "7 4 0 4"]},
              fx=[["gain", "0.8"], ["lpf", "(slow 8 $ range 1200 3000 sine)"], ["delay", "0.3"], ["delaytime", "0.375"], ["delayfeedback", "0.35"], ["room", "0.3"]],
              pool=[0, 2, 4, 7, 9]),
        _melodic("epiano_melody", "lead", "ys_epiano", "melody",
                 ["0 ~ 2 4 ~ ~ 2 ~", "~ 4 ~ 2 ~ 0 ~ ~", "<[0 ~ 2 4 ~ ~ 2 ~] [4 ~ 2 ~ 0 ~ -1 ~]>"],
                 [["gain", "1.0"], ["room", "0.3"], ["delay", "0.2"], ["delaytime", "0.25"],
                  ["delayfeedback", "0.25"]], octave=" + 12"),
        Block("vinyl", "texture", 'slow 4 $ note "0" # s "ys_vinyl"', {},
              fx=[["gain", "1.1"], ["hpf", "300"], ["legato", "1.2"]]),
    ],
}



BLOCKS["lofi"] += [_break_loop(), _foley_bed()]
BLOCKS["downtempo"].append(_break_loop())
BLOCKS["ambient"].append(_foley_bed())
# Trip-hop: a voice in the dark between the beats.
BLOCKS["downtempo"].append(_vocal_chop(["~ ~ ~ bsvocals:12", "~ bsvocals:2 ~ bsvocals:3", "~ ~ bsvocals:5 ~"]))


def _techno_blocks(hard: bool) -> list[Block]:
    """Techno (hard=False) or hardcore/gabber (hard=True): the kick is the
    lead instrument, distorted harder for hardcore."""
    pump = '(|* gain "[0.67 0.91 1 1]*4")'
    if hard:
        kick = Block("gabber_kick", "beat", 's "{kick}"',
                     {"kick": ["ys_gabber*4", "ys_gabber*4", "ys_gabber ys_gabber ys_gabber [ys_gabber ys_gabber]"]},
                     fx=[["gain", "1.0"], ["shape", "0.2"], ["lpf", "7000"]], drums=True)
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
                 [["gain", "1.1"], ["lpf", "(slow 16 $ range 600 1400 sine)"], ["legato", "0.8"]], octave=" - 24",
                 pool=[0, 7, -3],
                 wrappers=[pump]),
        Block("acid", "acid",
              'n (scale "{mode}" ("<{motif}>*16" |+ "<{roots}>") {root} - 12) # s "ys_acid"',
              {"motif": ["0 0 2 0 4 0 2 0", "0 7 0 6 0 4", "0 2 0 0 4 2", "0 0 7 0 0 6 4 2"]},
              fx=[["gain", "0.95"], ["legato", "0.5"]],
              pfs=[["acid", "(slow 16 $ range 0.3 0.7 sine)" if not hard else "(slow 8 $ range 0.45 0.85 sine)"],
                   ["acidres", "0.25"]],
              wrappers=[pump], pool=[0, 2, 4, 6, 7]),
        Block("stab", "chords",
              'struct "{stab}" $ n (scale "{mode}" "<{chords}>" {root}) # s "ys_epiano"',
              {"stab": ["~ ~ t ~ ~ ~ ~ ~", "~ t ~ ~ ~ ~ t ~", "~ ~ ~ t ~ ~ t ~", "[~ t]*4"]},
              fx=[["gain", "0.78"], ["legato", "0.25"], ["lpf", "(slow 8 $ range 1400 3200 sine)"], ["room", "0.35"]],
              wrappers=[pump]),
        _melodic("glass_lead", "lead", "ys_glass", "melody",
                 ["0 ~ ~ 0 ~ ~ 2 ~", "7 ~ 6 ~ 4 ~ 0 ~", "0 ~ 7 ~ 6 ~ 4 ~",
                  "<[0 ~ ~ 0 ~ ~ 2 ~] [0 ~ ~ 0 ~ ~ 6 4]>"],
                 [["gain", "0.9"], ["lpf", "3500"], ["room", "0.3"]], octave="",
                 wrappers=[pump], pool=[0, 2, 4, 6, 7]),
        # The anthem lead: the tune an octave up in long notes that ring into
        # the reverb over the kick instead of pumping with it (Thunderdome,
        # Wonderful Days), so a track's lead isn't always the same short,
        # pumping line in the same register.
        _melodic("anthem_lead", "lead", "ys_glass", "melody",
                 ["0 _ _ ~ 2 _ 4 ~", "<[4 _ _ _ 2 _ 0 ~] [2 _ _ _ ~ ~ ~ ~]>", "7 _ 6 _ 4 _ ~ ~"],
                 [["gain", "0.9"], ["legato", "1.4"], ["lpf", "6000"], ["room", "0.45"], ["size", "0.8"],
                  ["delay", "0.3"], ["delaytime", "0.375"], ["delayfeedback", "0.4"]], octave=" + 12",
                 pool=[0, 2, 4, 6, 7, 9]),
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
# Darkwave / coldwave / post-punk / minimal synth (She Past Away, Twin Tribes,
# Linea Aspera, Kalte Nacht, Rue Oberkampf, Kirlian Camera): a stiff drum
# machine with a gated snare, a picked bass through a chorus pedal playing
# the tune in 8ths (or a 16th-note sequencer bass), a chorused clean guitar
# arpeggio, a string machine bed, a cold square lead lost in delay. The
# subgenre picks the bass and the arp (SUBGENRES "blocks").
BLOCKS["darkwave"] = [
    Block("drum_machine", "beat", 's "[{kick}, {snare}, {hats}]"',
          {"kick": ["808bd ~ 808bd ~", "808bd*4", "808bd ~ ~ 808bd ~ ~ 808bd ~"],
           "snare": ["~ ys_gatesnare ~ ys_gatesnare", "~ 808sd ~ 808sd", "~ cp ~ cp", "~ 808sd ~ [808sd cp]"],
           "hats": ["808hc*16", "808hc*8", "[~ 808hc]*8", "[808hc 808hc 808oh 808hc]*4"]},
          fx=[["gain", "0.95"], ["room", "0.2"], ["lpf", "7000"]], drums=True),
    # Central Processing Unit's electro 808 (samples/cpu): each part at the level
    # of the 808 kit above (amp; the kick is 10 dB hotter, the hat 10 dB quieter).
    Block("electro_kit", "beat",
          'stack [s "{kick}" # amp 0.13, s "{snare}" # amp 0.16, s "{handclap}" # amp 0.4, s "{hats}" # amp 1.26]',
          {"kick": ["cpu:0 ~ cpu:0 ~", "cpu:0 ~ ~ cpu:0 ~ ~ cpu:0 ~", "cpu:0*4"],
           "snare": ["~ cpu:2 ~ cpu:2", "~", "~ ~ ~ cpu:2"],
           "handclap": ["~ cpu:3 ~ cpu:3", "~", "~ cpu:3 ~ [cpu:3 ~ ~ cpu:3]"],
           "hats": ["cpu:4*16", "[~ cpu:4]*8", "cpu:4*8"]},
          fx=[["gain", "0.95"], ["room", "0.15"], ["lpf", "8000"]], drums=True),
    _melodic("sequencer_bass", "bass", "ys_tbass", "rhythm",
             ["0*16", "[0 0 7 0]*4", "[0 ~ 0 7]*4", "[0 0 0 4]*4"],
             [["gain", "1.0"], ["lpf", "(slow 8 $ range 600 1400 sine)"], ["legato", "0.5"]], octave=" - 24",
             pool=[0, 7, 4, -3]),
    # The post-punk bass: 8ths that carry the tune, octave and fifth jumps.
    _melodic("chorus_bass", "bass", "ys_chorusbass", "rhythm",
             ["0*8", "0 0 7 0 4 0 2 0", "0 0 0 0 4 4 2 2", "<[0 0 7 0 0 0 4 0] [0 0 7 0 4 2 0 -3]>"],
             [["gain", "0.95"], ["lpf", "(slow 16 $ range 1400 3200 sine)"], ["legato", "0.8"]], octave=" - 24",
             pool=[0, 2, 4, 7, -3]),
    Block("cold_chords", "chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "{synth}"',
          {"synth": ["ys_coldstrings", "ys_epiano", "ys_glass"]},
          fx=[["gain", "0.68"], ["legato", "1.05"], ["lpf", "(slow 16 $ range 1100 2400 sine)"], ["room", "0.5"],
              ["size", "0.8"]]),
    _melodic("cold_arp", "lead", "ys_fmlead", "melody",
             ["0 ~ 4 7 ~ 4 2 ~", "0 ~ 4 ~ 7 _ 4 ~", "7 ~ 4 _ 2 ~ 0 _", "0 ~ 7 ~ 4 ~ 7 ~",
              "<[0 ~ 7 ~ 4 ~ 7 ~] [2 ~ 9 ~ 7 ~ 4 ~]>", "<[4 ~ ~ 2 ~ ~ 0 ~] [-1 ~ ~ ~ 0 ~ ~ ~]>"],
             [["gain", "0.9"], ["lpf", "3500"], ["room", "0.4"], ["delay", "0.35"], ["delaytime", "0.375"],
              ["delayfeedback", "0.45"]], octave=" + 12", pool=[0, 2, 3, 4, 5, 7, 12]),
    # The lingering wave lead: long held notes that swell, glide and ring on
    # into the delay, a filter breathing slowly under them.
    _melodic("wave_lead", "lead", "ys_wavelead", "melody",
             ["4 ~ ~ ~ ~ ~ 2 ~", "0 ~ ~ ~ 4 ~ ~ ~", "<[7 ~ ~ ~ ~ ~ ~ ~] [4 ~ ~ ~ 2 ~ ~ ~]>",
              "<[9 ~ ~ ~ 7 ~ ~ ~] [4 ~ ~ ~ ~ ~ ~ ~]>",
              "<[4 ~ ~ ~ ~ ~ ~ ~] [2 ~ ~ ~ ~ ~ 0 ~] [-1 ~ ~ ~ ~ ~ ~ ~] [0 ~ ~ ~ ~ ~ ~ ~]>"],
             [["gain", "0.9"], ["legato", "1.9"], ["lpf", "(slow 16 $ range 1200 3200 sine)"], ["room", "0.6"],
              ["size", "0.9"], ["delay", "0.45"], ["delaytime", "0.375"], ["delayfeedback", "0.5"],
              ["pan", "(slow 8 $ range 0.35 0.65 sine)"]], octave=" + 12", pool=[0, 2, 4, 7, 9, -1], keep_sound=True),
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
    # The wave synth: 8ths through the chord whose notes ring for seconds, so
    # they overlap into a slowly shifting wash, lost in a long delay.
    Block("wave_arp", "arp",
          'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "ys_wavesynth"',
          {"arp": ["{up}", "{updown}", "0 ~ 4 ~ 7 ~ 4 ~", "0 4 7 9 7 4", "{pedal}"]},
          fx=[["gain", "0.8"], ["lpf", "(slow 16 $ range 1500 4500 sine)"], ["delay", "0.4"], ["delaytime", "0.5"],
              ["delayfeedback", "0.45"], ["room", "0.6"], ["size", "0.9"], ["pan", "(slow 12 $ range 0.3 0.7 sine)"]],
          pool=[0, 2, 4, 7, 9], keep_sound=True),
    Block("soft_bed", "texture", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_softpad"', {},
          fx=[["gain", "0.6"], ["legato", "1.1"], ["lpf", "1800"], ["room", "0.6"], ["size", "0.9"]]),
    Block("static", "texture", 'n (scale "{mode}" "<{roots}>" {root} - 12) # s "ys_air"', {},
          fx=[["gain", "0.85"], ["hpf", "400"], ["legato", "1.3"], ["room", "0.8"]]),
    # Blood Sport's guitar noise (samples/bsguitar, bsnoise): a shard every few
    # bars, atonal (amp slaps, distortion, noisy guitar), -10 to -13 LUFS raw.
    Block("guitar_shards", "perc", 's "{shard}" # amp 0.1',
          {"shard": ["<~ ~ ~ bsguitar:5>", "<~ ~ bsnoise:2 ~>", "<~ ~ ~ [~ ~ ~ bsnoise:19]>",
                     "<~ bsnoise:20 ~ ~ ~ ~ ~ bsguitar:5>"]},
          fx=[["gain", "0.9"], ["room", "0.5"], ["hpf", "200"], ["pan", "0.35"]], drums=True),
    # Blood Sport's voices over the drum machine.
    _vocal_chop(["~ ~ ~ bsvocals:11", "~ bsvocals:2 ~ bsvocals:3", "~ ~ bsvocals:5 ~", "~ ~ ~ [~ bsvocals:4]",
                 "bsvocals:12 ~ ~ ~"]),
]

BLOCKS["techno"] = _techno_blocks(hard=False)
# The warehouse voice: a phrase every few bars, a stutter now and then.
BLOCKS["techno"].append(_vocal_chop(["~ ~ ~ bsvocals:11", "~ ~ ~ [bsvocals:5 bsvocals:5]", "~ bsvocals:4 ~ ~",
                                     "~ ~ bsvocals:2 ~"]))
BLOCKS["hardcore"] = _techno_blocks(hard=True)
# Rave voices over the gabber kick: short phrases (a bar is 1.3-1.5 s here).
BLOCKS["hardcore"].append(_vocal_chop(["~ ~ ~ bsvocals:11", "~ ~ bsvocals:5 ~", "~ bsvocals:2 ~ ~",
                                       "~ ~ ~ [bsvocals:5 bsvocals:5]"]))


# The real kalimba (music/instruments.py) plays slowly: a note on the beat or
# less, ringing until the next and no further (they overlapped into a blur),
# one soft echo, not the 16ths of a synth arp.
# Written for it, so a track whose arp sound is the kalimba brings this line in.
def _kalimba_arp() -> Block:
    return Block("kalimba_arp", "arp",
                 'n (scale "{mode}" ("<{arp}>*8" |+ "<{roots}>") {root} + 12) # s "ys_kalimba"',
                 {"arp": ["0 ~ 4 ~ 7 ~ 4 ~", "0 ~ ~ 4 ~ ~ 7 ~", "7 ~ 4 ~ ~ 2 ~ ~", "0 ~ 4 ~ 2 ~ ~ ~"]},
                 fx=[["gain", "0.9"], ["legato", "1"], ["room", "0.4"], ["size", "0.7"], ["delay", "0.15"],
                     ["delaytime", "0.5"], ["delayfeedback", "0.15"], ["pan", "(slow 12 $ range 0.35 0.65 sine)"]],
                 pool=[0, 2, 4, 7, 9], keep_sound=True, max_notes=4)


for _style in ("deephouse", "lofi", "downtempo"):
    BLOCKS[_style].append(_sax_lead())

if _instruments.installed("kalimba"):
    for _style in ("trance", "ambient", "lofi"):
        BLOCKS[_style].append(_kalimba_arp())

# Subgenres: a style's variants, each with its own tempo, drum kit (hat and
# clap patterns), bass rhythms, preferred sounds and blocks, and chord voicings, so
# tracks of one style don't all sound alike. The kit's patterns replace the
# block's own variants for those slots (Block.make), and the Performer's drum
# edits may use the kit's sounds. Hypersaw sounds only count when
# config.HYPERSAW is on (subgenre_sounds).
SUBGENRES = {
    "trance": {
        "progressive": {
            "about": "deep and rolling: offbeat bass, soft hats, e-piano and bells, long filter rides",
            "bpm": (126, 132),
            "kit": {"hats": ["[~ hh27]*4", "[~ hh27]*4, [~ ~ hh27 ~]*4", "[~ 808oh]*2 [~ hh27]*2"],
                    "clap": ["~ ~ ~ cp", "~ cp ~ cp"],
                    "rhythm": ["[~ 0]*4", "[~ 0 ~ 0]*4", "[~ 0 0 ~]*4"]},
            "sounds": {"chords": ["ys_epiano", "ys_glass"],
                       "lead": ["ys_glass", "ys_pluck", "ys_flute", "ys_marimba", "ys_vox", "ys_nylon"],
                       "arp": ["ys_pluck", "ys_glass", "ys_marimba", "ys_nylon"], "texture": ["ys_softpad"]},
            "voicings": ["open triad", "spread triad", "triad"],
        },
        "uplifting": {
            "about": "big and euphoric: rolling bass, supersaw chords and lead, epic voicings, a long breakdown",
            "bpm": (136, 140),
            "kit": {"hats": ["[~ 808oh]*4, hh*16", "[~ 808oh]*4"],
                    "clap": ["~ cp ~ cp", "~ cp ~ <cp cp cp [cp cp]>"],
                    "rhythm": ["[~ 0 0 0]*4", "[~ 0 0 0]*3 [~ 0 0 7]"]},
            "sounds": {"chords": ["ys_hypersaw", "ys_housepiano"],
                       "lead": ["ys_hypersaw", "ys_sawlead", "ys_vox", "ys_fmlead"],
                       "arp": ["ys_pluck", "ys_glass"], "texture": ["ys_softpad"]},
            "voicings": ["epic stack", "anthem", "add octave"],
        },
        "psy": {
            "about": "driving and hypnotic: galloping bass on the three 16ths after every kick, busy hats, "
                     "phrygian, acid and square-wave leads, few chords",
            "bpm": (142, 146),
            "kit": {"kick": ["ys_kick*4"], "hats": ["hh*16", "[~ hh]*4, hh*16", "[hh hh 808oh hh]*4"],
                    "clap": ["~ cp ~ cp", "~"],
                    "rhythm": ["[~ 0 0 0]*4", "[~ 0 0 0]*3 [~ 0 7 0]"]},
            "sounds": {"lead": ["ys_sawlead", "ys_glass", "ys_sitar", "ys_fmlead"],
                       "arp": ["ys_pluck", "ys_sitar"],
                       "chords": ["ys_glass", "ys_epiano"]},
            "voicings": ["triad", "open triad"],
        },
        "acid": {
            "about": "the 303 up front: the acid line carries the track, house piano stabs, a plain kick",
            "bpm": (134, 140),
            "kit": {"kick": ["ys_kick*4", "ys_kick909*4"], "hats": ["[~ 808oh]*4", "[~ hh]*4, hh*8"],
                    "clap": ["~ cp ~ cp", "~ ~ ~ cp"],
                    "rhythm": ["[~ 0 0 0]*4", "[~ 0 ~ 0]*4"]},
            "sounds": {"chords": ["ys_housepiano", "ys_epiano", "ys_m1organ"],
                       "lead": ["ys_glass", "ys_fmlead", "ys_hoover"],
                       "arp": ["ys_pluck"]},
            "voicings": ["triad", "add octave"],
        },
        "balearic": {
            "about": "Chicane's Saltwater: Balearic trance at sunset, a nylon guitar, an ethereal choir of female "
                     "voices singing long Celtic lines (pentatonic steps, held notes, a grace note into them), "
                     "supersaw stabs, a long breakdown",
            "bpm": (130, 136),
            "modes": ["dorian", "minor"],
            "kit": {"hats": ["[~ hh27]*4", "[~ 808oh]*4"], "clap": ["~ cp ~ cp", "~ ~ ~ cp"],
                    "rhythm": ["[~ 0 0 0]*4", "[~ 0]*4"]},
            # The lead: the Cafe del Mar pluck or the nylon guitar playing the tune
            # (the choir sings as the bed behind, not the lead).
            "blocks": {"lead": ["cafe_lead"]},
            "sounds": {"lead": ["ys_cafepluck", "ys_nylon"], "arp": ["ys_nylon", "ys_harp", "ys_kalimba", "ys_pluck"],
                       "chords": ["ys_hypersaw", "ys_housepiano", "ys_glass"],
                       "texture": ["ys_angelchoir", "ys_softpad"]},
            "voicings": ["open triad", "spread triad", "add octave"],
        },
        "lush": {
            "about": "C.J. Bolland's Lush 3-5 (R&S, 1993): Belgian techno-trance, a hypnotic 16th sequence that "
                     "never stops, lush detuned pads swelling over it, acid underneath, a rolling bass, few chords",
            "bpm": (128, 134),
            "modes": ["minor", "phrygian"],
            "kit": {"hats": ["hh*16", "[~ hh]*8"], "clap": ["~ cp ~ cp"],
                    "rhythm": ["[~ 0 0 0]*4", "[~ 0 ~ 0]*4"]},
            "sounds": {"arp": ["ys_pluck", "ys_fmlead"], "lead": ["ys_fmlead", "ys_glass"],
                       "chords": ["ys_hypersaw", "ys_glass", "ys_epiano"], "texture": ["ys_softpad"]},
            "voicings": ["spread triad", "open triad"],
        },
    },
    "hardcore": {
        "gabber": {
            "about": "Rotterdam gabber: the distorted kick is the lead, acid and hoovers, dark minor keys",
            "bpm": (165, 185),
            "modes": ["minor", "phrygian"],
            "sounds": {"lead": ["ys_hoover", "ys_sawlead", "ys_glass"]},
        },
        "happy": {
            "about": "happy hardcore (Charly Lownoise & Mental Theo's Wonderful Days, 1995): a bouncing kick, piano "
                     "chords, a sped-up chipmunk vocal singing the hook, a sing-along melody in a major key, hoovers",
            "bpm": (168, 180),
            "modes": ["major", "mixolydian"],
            "sounds": {"chords": ["ys_housepiano"], "keys": ["ys_housepiano"],
                       "lead": ["ys_chipvox", "ys_housepiano", "ys_hoover"], "arp": ["ys_pluck", "ys_glass"]},
            "voicings": ["triad", "add octave"],
        },
    },
    "deephouse": {
        "deep": {
            "about": "deep house: warm Rhodes and soft stabs, off-beat hats, a round bass",
            "bpm": (118, 124),
        },
        "piano house": {
            "about": "piano house (Guru Josh's Infinity, 1990): M1 piano chords and organ stabs, a soaring "
                     "saxophone lead, euphoric",
            "bpm": (120, 124),
            "sounds": {"chords": ["ys_housepiano", "ys_m1organ"], "keys": ["ys_housepiano", "ys_m1organ"],
                       "lead": ["ys_sax", "ys_housepiano"]},
        },
    },
    "darkwave": {
        "post-punk": {
            "about": "She Past Away, Twin Tribes: the chorused bass carries the tune in driving 8ths, a jangly "
                     "chorused guitar arpeggio, a big gated snare on 2 and 4, a string machine far behind, few chords; "
                     "the lead a guitar line or a lingering wave synth over it",
            "bpm": (128, 140),
            "kit": {"kick": ["808bd ~ 808bd ~", "808bd*4", "808bd ~ [~ 808bd] ~"],
                    "snare": ["~ ys_gatesnare ~ ys_gatesnare", "~ ys_gatesnare ~ [ys_gatesnare ~ ~ ys_gatesnare]"],
                    "hats": ["808hc*8", "[808hc 808hc 808oh 808hc]*2", "808hc*16"],
                    "rhythm": ["0*8", "0 0 7 0 4 0 2 0", "0 0 0 0 4 4 2 2", "<[0 0 7 0 0 0 4 0] [0 0 7 0 4 2 0 -3]>"]},
            # (The chorused clean guitar was taken off air: the arp is the sequencer pluck.)
            "blocks": {"beat": ["drum_machine"], "bass": ["chorus_bass"], "arp": ["cold_seq"],
                       "lead": ["cold_arp", "wave_lead"]},
            "sounds": {"chords": ["ys_coldstrings"], "arp": ["ys_pluck"],
                       "lead": ["ys_wavelead", "ys_fmlead"]},
            "voicings": ["triad", "open triad"],
        },
        "minimal synth": {
            "about": "Linea Aspera, Kalte Nacht, Rue Oberkampf: a 16th-note analog sequencer bass, a dry electro "
                     "808 kit with claps, lingering wave-synth leads (long held notes that swell and glide) or a cold "
                     "square lead, arps lost in delay, little else",
            "bpm": (112, 124),
            # The drums are the electro kit's own patterns (its parts have their own levels).
            "kit": {"rhythm": ["0*16", "[0 0 7 0]*4", "[0 ~ 0 7]*4", "[0 0 0 4]*4"]},
            "blocks": {"beat": ["electro_kit"], "bass": ["sequencer_bass"], "arp": ["cold_seq", "wave_arp"],
                       "lead": ["wave_lead", "cold_arp"]},
            "sounds": {"lead": ["ys_wavelead"], "arp": ["ys_wavesynth", "ys_pluck"],
                       "chords": ["ys_coldstrings", "ys_glass"]},
            "voicings": ["triad"],
        },
        "ethereal": {
            "about": "Kirlian Camera, Deus Ex Lumina: slower and cinematic, a wide string machine and bells, a "
                     "rubbery chorused bass, a melancholy lingering wave lead (long held notes), wave-synth arps "
                     "ringing into each other, half-time gated snare, long reverb",
            "bpm": (100, 116),
            "kit": {"kick": ["808bd ~ ~ ~ 808bd ~ ~ ~", "808bd ~ 808bd ~"],
                    "snare": ["~ ys_gatesnare ~ ys_gatesnare", "~ ~ ys_gatesnare ~"],
                    "hats": ["808hc*8", "[~ 808hc]*4", "808hc*8, [~ 808oh]*2"],
                    "rhythm": ["0*8", "0 ~ 0 0 0 ~ 0 0", "0 0 0 0 7 7 4 4"]},
            "blocks": {"beat": ["drum_machine"], "bass": ["chorus_bass"], "arp": ["wave_arp"],
                       "lead": ["wave_lead"]},
            "sounds": {"chords": ["ys_coldstrings"],
                       "lead": ["ys_wavelead", "ys_glass", "ys_vox", "ys_flute", "ys_harpsichord"],
                       "arp": ["ys_wavesynth", "ys_glass"]},
            "voicings": ["spread triad", "open triad", "add octave"],
        },
    },
}


def subgenre(style: str, name: str | None) -> dict:
    return SUBGENRES.get(style, {}).get(name or "", {})


def subgenre_blocks(style: str, name: str | None, role: str, track=None) -> list[Block]:
    """The style's blocks for `role`, the subgenre's own first choice if it has one."""
    options = blocks_for(style, role, track)
    preferred = subgenre(style, name).get("blocks", {}).get(role, [])
    return [b for b in options if b.name in preferred] or options


def track_blocks(style: str, subgenre_name: str | None, sounds: dict | None, role: str, track=None) -> list[Block]:
    """The blocks a track can bring in for `role`: the subgenre's, and of those
    the ones written for the sound the track picked for the part, if any (a
    wave lead plays the long held notes written for it, not a choppy phrase)."""
    options = subgenre_blocks(style, subgenre_name, role, track)
    sound = (sounds or {}).get(role)
    own = [b for b in options if b.keep_sound and mix.synth_of(b.body) == sound]
    return own or [b for b in options if not b.keep_sound or mix.synth_of(b.body) == sound] or options


def subgenre_sounds(style: str, name: str | None) -> dict[str, list[str]]:
    """The subgenre's preferred sounds that can play now (the supersaw only
    once it's switched on)."""
    out = {}
    for role, options in subgenre(style, name).get("sounds", {}).items():
        playable = [o for o in options if o in LIVE_SOUNDS.get(role, {})]
        if playable:
            out[role] = playable
    return out


# Per style: bars between edits (about 20-30 s; override with --edit-bars), how many lines a track builds up to,
# how complex the peak gets (livecode.complexity: 1 per line, +0.5 per wrapper, moving knob, phrase that changes
# bar to bar or 16th stream; a Switch Angel trance peak, 6 lines with gates and moving knobs, is about 16-18), how many melodic lines (acid, arp,
# lead, keys) may play at once above the bass and chords (her trance has one or two), and which roles open a track.
STYLE_SETTINGS = {
    "lofi": {"edit_bars": 8, "max_lines": 7, "max_melodic": 2, "peak_complexity": 14, "openers": [["beat"], ["keys"], ["bass", "beat"]]},
    "deephouse": {"edit_bars": 16, "max_lines": 7, "max_melodic": 2, "peak_complexity": 20, "openers": [["beat"], ["beat", "bass"]]},
    "trance": {"edit_bars": 16, "max_lines": 7, "max_melodic": 2, "peak_complexity": 17, "openers": [["acid"], ["beat"], ["acid", "beat"]]},
    "ambient": {"edit_bars": 8, "max_lines": 6, "max_melodic": 2, "peak_complexity": 10, "openers": [["chords"], ["texture"], ["keys"]]},
    "piano": {"edit_bars": 8, "max_lines": 5, "max_melodic": 2, "peak_complexity": 8, "openers": [["keys"]]},
    "downtempo": {"edit_bars": 8, "max_lines": 6, "max_melodic": 2, "peak_complexity": 14, "openers": [["beat"], ["keys"]]},
    "darkwave": {"edit_bars": 12, "max_lines": 7, "max_melodic": 2, "peak_complexity": 18, "openers": [["bass"], ["beat"], ["bass", "beat"]]},
    "techno": {"edit_bars": 16, "max_lines": 7, "max_melodic": 2, "peak_complexity": 20, "openers": [["beat"], ["beat", "perc"]]},
    "hardcore": {"edit_bars": 16, "max_lines": 6, "max_melodic": 2, "peak_complexity": 18, "openers": [["beat"]]},
}


def blocks_for(style: str, role: str | None = None, track=None) -> list[Block]:
    """The style's blocks; a track that sings an acapella (Track.vocal) has
    it as its vocal part instead of the style's vocal chops."""
    items = BLOCKS.get(style, BLOCKS["lofi"])
    if track is not None and getattr(track, "vocal", None):
        from .vocals import acapella_block

        items = [b for b in items if b.role != "vocal"] + [acapella_block(track.vocal)]
    return [b for b in items if role is None or b.role == role]


def find_block(style: str, name: str, track=None) -> Block | None:
    return next((b for b in blocks_for(style, track=track) if b.name == name), None)
