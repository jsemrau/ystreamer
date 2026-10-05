"""Mix balance and sound variety."""

import math
import os
import random
import re

from music import presets as P
from music.plan import STYLES, arrange, plan_piece, validate_piece
from music.templates import HarmonyContext, render_layer
from music.theory import PROGRESSIONS_BY_NAME, Key, progression_chords

# Single-voice loudness of each synth, measured by rendering the SynthDefs
# offline with scsynth (LUFS): one table, in music/mix.py (eval/synth_levels.sh).
from music.mix import SYNTH_LUFS as MEASURED_LUFS  # noqa: E402


def in_mix(preset):
    return MEASURED_LUFS[preset.synth] + 80 * math.log10(preset.gain)


def test_pads_sit_behind_keys_and_melody():
    pads = [p for p in P.PRESETS["pad"].values() if p.synth in MEASURED_LUFS and p.synth != "ys_air"]
    melodies = [p for p in P.PRESETS["melody"].values() if p.synth in MEASURED_LUFS]
    keys = [p for p in P.PRESETS["keys"].values() if p.synth in MEASURED_LUFS]
    loudest_pad = max(in_mix(p) for p in pads)
    assert loudest_pad < min(in_mix(p) for p in keys) - 2
    assert loudest_pad < min(in_mix(p) for p in melodies) - 5


def test_every_synth_used_by_a_preset_exists_in_the_synthdefs():
    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        defined = set(re.findall(r"SynthDef\(\\(\w+)", f.read()))
    used = {p.synth for role in P.PRESETS.values() for p in role.values() if p.synth.startswith("ys_")}
    assert used <= defined, used - defined
    assert {"ys_strings", "ys_organ", "ys_choir"} <= defined


def ctx(n_notes_color="sevenths", busy=0):
    key = Key.parse("D minor")
    chords = progression_chords(key, PROGRESSIONS_BY_NAME["i-VI-III-VII"], color=n_notes_color)
    return HarmonyContext(key, chords, 1, seed=2, busy=busy)


def gain_of(expr):
    return float(re.search(r"# gain ([\d.]+)", expr).group(1))


def test_pad_gain_compensates_chord_size_and_busy_mix():
    pad = P.PRESETS["pad"]["warm_pad"]
    sevenths = gain_of(render_layer("pad", ctx("sevenths"), pad, 0.5))
    ninths = gain_of(render_layer("pad", ctx("ninths"), pad, 0.5))
    busy = gain_of(render_layer("pad", ctx("sevenths", busy=5), pad, 0.5))
    assert ninths < sevenths         # 5-note chords are pulled down
    assert busy < sevenths * 0.95    # and the pad backs off in a full mix


def test_pieces_start_differently():
    rng = random.Random(3)
    previous, openings, pads = None, [], []
    for _ in range(12):
        piece = plan_piece(rng, previous, style="lofi")
        openings.append(tuple(piece.opening))
        pads.append(piece.instruments["pad"])
        intro = arrange(piece, piece.sections[0], rng)
        assert set(intro.active_roles()) <= set(piece.opening) | {"pad", "texture"}
        if previous is not None:
            assert tuple(piece.opening) != tuple(previous.opening)
            assert piece.instruments["pad"] != previous.instruments["pad"]
        previous = piece
    assert len(set(openings)) >= 3 and len(set(pads)) >= 3


def test_instruments_come_from_the_style_palette():
    for style, cfg in STYLES.items():
        piece = plan_piece(random.Random(1), style=style)
        for role, name in piece.instruments.items():
            options = cfg.get("palette", {}).get(role) or [cfg["presets"][role]]
            assert name in options


def test_llm_can_choose_instruments_and_opening():
    fallback = plan_piece(random.Random(2), style="lofi")
    piece, notes = validate_piece({"instruments": {"pad": "air_pad", "keys": "not-a-preset"},
                                   "opening": ["melody", "texture"]}, fallback)
    assert piece.instruments["pad"] == "air_pad"
    assert piece.opening == ["melody", "texture"]
    assert any("keys" in n for n in notes)


def test_no_unwanted_sounds_anywhere():
    """No saw or organ sounds: not in the live blocks, the styles' defaults
    or palettes, the Director's vocabulary, or what the Director may choose."""
    import json

    from music.blocks import BLOCKS
    from music.plan import STYLES, UNWANTED_PRESETS, UNWANTED_SYNTHS, describe_options

    for style, blocks in BLOCKS.items():
        for b in blocks:
            assert not any(s in b.body + json.dumps(b.patterns) for s in UNWANTED_SYNTHS), (style, b.name)
    for style, cfg in STYLES.items():
        assert not set(cfg["presets"].values()) & UNWANTED_PRESETS, style
        assert not {n for names in cfg["palette"].values() for n in names} & UNWANTED_PRESETS, style
    assert not any(n in json.dumps(describe_options()["presets"]) for n in UNWANTED_PRESETS)
    fallback = plan_piece(random.Random(2), style="trance")
    piece, notes = validate_piece({"instruments": {"pad": "organ_pad", "melody": "saw_lead"}}, fallback)
    assert piece.instruments["pad"] not in UNWANTED_PRESETS and piece.instruments["melody"] not in UNWANTED_PRESETS


# --------------------------------------------------------------- live mix

def test_live_blocks_enter_at_their_roles_level():
    from music import mix
    from music.blocks import BLOCKS

    for style, blocks in BLOCKS.items():
        for block in blocks:
            if "struct" in block.body:
                continue
            for seed in range(3):
                line = block.make(random.Random(seed))
                synth = mix.synth_of(block.body.replace('"{synth}"', f'"{line.patterns.get("synth", "")}"'))
                gain = float(dict(line.fx).get("gain", 1))
                level = mix.level_db(synth, "{chords}" in block.body, gain) if synth else None
                target = mix.ROLE_TARGET_LUFS.get(block.role)
                if level is None or target is None or synth in mix.UNCALIBRATED or gain >= 1.2:
                    continue
                assert abs(level - target) < 0.5, (style, block.name, synth, level, target)


def test_beds_make_room_when_the_front_is_busy():
    from music.blocks import find_block
    from music.livecode import Edit, Program, Track, apply_edit, changed_statements

    program = Program(Track("t", "piano", "C major", 70, [0, 5, 3, 4], 2))
    for name in ("air_bed", "piano_chords"):
        b = find_block("piano", name)
        program = apply_edit(program, Edit("add", role=b.role, line=b.make(random.Random(0))))
    assert "|* gain" not in program.statement("chords")
    lead = find_block("piano", "piano_melody")
    fuller = apply_edit(program, Edit("add", role="lead", line=lead.make(random.Random(0))))
    assert fuller.statement("chords").endswith("|* gain 0.93")
    # adding the lead re-sends the bed at its new level
    assert fuller.statement("chords") in changed_statements(program, fuller)


def test_lines_fade_in_and_out():
    from music.live_engine import LiveEngine
    from music.performer import Performer

    class Clock:
        t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    sent = []
    clock = Clock()
    engine = LiveEngine(lambda lines: sent.extend(lines), Performer(random.Random(3), style="lofi",
                                                                    log=lambda *_: None),
                        log=lambda *_: None, now=clock.now, sleep=clock.sleep, max_tracks=1)
    engine.run()
    fades_in = [s for s in sent if s.startswith("xfadeIn") and not s.endswith("silence")]
    fades_out = [s for s in sent if s.startswith("xfadeIn") and s.endswith("silence")]
    assert fades_in and fades_out
    assert all(re.match(r"xfadeIn (\d) 4 \$ \(.*\) \|< orbit (\d)$", s) for s in fades_in)
    for s in fades_in:
        ch, orbit = re.match(r"xfadeIn (\d) .* orbit (\d)$", s).groups()
        assert int(orbit) == int(ch) - 1


def test_a_resumed_track_loses_sounds_that_are_out():
    """A track saved while it still played organ or supersaw lines comes
    back with the style's current blocks in those roles."""
    from music.live_engine import LiveEngine
    from music.livecode import LineSpec, Program, Track
    from music.performer import Performer

    program = Program(Track("t", "trance", "A minor", 138, [0, 5, 2, 6], 2))
    program.lines["chords"] = LineSpec("chords", 'n (scale "{mode}" "<{chords}>" {root}) # s "ys_organ"',
                                       block="organ_chords")
    program.lines["lead"] = LineSpec("lead", 'n "0" # s "ys_supersaw"', block="saw_lead", muted=True)
    engine = LiveEngine(lambda lines: None, Performer(random.Random(0), style="trance", log=lambda *_: None),
                        log=lambda *_: None)
    fixed = engine._without_unwanted_sounds(program)
    assert fixed.lines["chords"].block == "piano_stabs" and "ys_housepiano" in fixed.lines["chords"].body
    assert fixed.lines["lead"].block == "glass_lead" and fixed.lines["lead"].muted


def test_the_directors_sounds_are_1990s_and_real():
    """Every sound the Director may pick exists, has a measured level, and
    is neither a saw nor the organ."""
    from music.blocks import LIVE_SOUNDS
    from music.mix import SYNTH_LUFS
    from music.plan import UNWANTED_SYNTHS

    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        defined = set(re.findall(r"SynthDef\(\\(\w+)", f.read()))
    for role, sounds in LIVE_SOUNDS.items():
        for synth in sounds:
            assert synth in defined and synth in SYNTH_LUFS and synth not in UNWANTED_SYNTHS, (role, synth)
    assert {"ys_housepiano", "ys_fmlead"} <= set().union(*LIVE_SOUNDS.values())
    # Off air after listening (eval.sound_preview 12 and 13): the square lead and the saw pad.
    assert not {"ys_pulselead", "ys_sawpad"} & set().union(*LIVE_SOUNDS.values())
    assert "ys_pulsepad" not in set().union(*LIVE_SOUNDS.values())         # out of rotation: too buzzy


def test_the_director_picks_the_sounds_and_the_track_plays_them():
    from music.livecode import Program
    from music.performer import Performer

    fallback = plan_piece(random.Random(2), style="trance")
    # The built-in planner picks too (and a pad bed where the subgenre has one).
    assert {"chords", "keys", "lead", "arp"} <= set(fallback.sounds) <= {"chords", "keys", "lead", "arp", "texture"}
    piece, notes = validate_piece({"sounds": {"chords": "ys_housepiano", "lead": "ys_supersaw",
                                              "drums": "ys_epiano"}}, fallback)
    assert piece.sounds["chords"] == "ys_housepiano"
    assert piece.sounds["lead"] == fallback.sounds["lead"]                 # a saw isn't on offer
    assert sum("rejected" in n for n in notes) == 2

    class Director:
        style = "trance"

        def next_piece(self, previous):
            p = plan_piece(random.Random(0), style="trance")
            p.sounds = {"chords": "ys_housepiano", "lead": "ys_fmlead", "arp": "ys_glass"}
            return p

    performer = Performer(random.Random(1), style="trance", log=lambda *_: None, director=Director())
    program = Program(performer.new_track(None))
    assert program.track.sounds["chords"] == "ys_housepiano"
    for role, synth in (("chords", "ys_housepiano"), ("lead", "ys_fmlead"), ("arp", "ys_glass")):
        line = performer._add(program, role).line
        assert f'# s "{synth}"' in line.body or line.patterns.get("synth") == synth, (role, line.body)


def test_pads_are_atmosphere_never_the_main_chords():
    """The soft pad and air only play far back (texture); the chords are
    pianos; and the Director's pick for a part only changes blocks that
    already play one of that part's sounds (the high acid stays acid)."""
    from music.blocks import BLOCKS, LIVE_SOUNDS, find_block
    from music.mix import synth_of

    assert not {"ys_softpad", "ys_air"} & set(LIVE_SOUNDS["chords"])
    for style in ("trance", "deephouse", "darkwave", "techno", "hardcore"):
        for b in BLOCKS[style]:
            if b.role == "chords":
                assert not {synth_of(b.body), *b.patterns.get("synth", [])} & {"ys_softpad", "ys_air"}, (style, b.name)
    assert find_block("trance", "soft_bed").role == "texture"
    sounds = {"arp": "ys_pluck", "texture": "ys_softpad", "chords": "ys_epiano"}
    assert '# s "ys_acid"' in find_block("trance", "high_acid").make(random.Random(0), sounds).body
    assert '# s "ys_vinyl"' in find_block("deephouse", "vinyl").make(random.Random(0), sounds).body
    assert '# s "ys_epiano"' in find_block("trance", "piano_stabs").make(random.Random(0), sounds).body


def test_every_oscillator_sits_on_a_harmonic_of_its_note():
    # An oscillator at an inharmonic multiple of the note (ys_glass's old 2.76:
    # an octave and a tritone up, 42 cents flat) sounds sour on every note of a
    # melody. A little stretch (a piano's 6.08) is fine; filter cutoffs aren't pitches.
    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        src = f.read()
    for name, body in re.findall(r"SynthDef\(\\(\w+)(.*?)\}\)\.add;", src, re.S):
        for ratio in re.findall(r"(?:SinOsc|LFTri|LFSaw|Saw|Pulse|VarSaw|SawDPW)\.ar\(\s*fr? \* ([\d.]+)", body):
            r = float(ratio)
            harmonic = max(round(r * 2) / 2, 0.5)
            assert abs(1200 * math.log2(r / harmonic)) < 30, (name, r)
